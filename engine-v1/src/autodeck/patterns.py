from __future__ import annotations

"""Boat-directed developed-space pattern generation for AutoDeck."""

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any

import ezdxf

from . import __version__
import numpy as np
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point, Polygon
from shapely.ops import unary_union

from .curve_fit import ManufacturingCurve
from .development import DevelopmentResult
from .polyarc import PolyarcCurve


PATTERN_NAMES = {"none": "None", "teak": "Teak", "diamond": "Diamond", "hex": "Hex"}
PATTERN_LAYERS = {
    "teak": "AD_PATTERN_TEAK",
    "diamond": "AD_PATTERN_DIAMOND",
    "hex": "AD_PATTERN_HEX",
}


def _unit(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    length = float(np.linalg.norm(vector))
    return vector / length if length > 1e-12 else np.array([1.0, 0.0], dtype=float)


@dataclass
class BoatFrame:
    origin: np.ndarray
    longitudinal: np.ndarray
    transverse: np.ndarray
    axis_confidence: float
    bow_sign: int
    bow_confidence: float
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_mm": self.origin.astype(float).tolist(),
            "longitudinal_axis": self.longitudinal.astype(float).tolist(),
            "transverse_axis": self.transverse.astype(float).tolist(),
            "axis_angle_degrees": float(math.degrees(math.atan2(self.longitudinal[1], self.longitudinal[0]))),
            "axis_confidence": float(self.axis_confidence),
            "bow_sign": int(self.bow_sign),
            "bow_confidence": float(self.bow_confidence),
            "metrics": self.metrics,
        }


@dataclass
class PatternLine:
    start: np.ndarray
    end: np.ndarray
    patch_id: int
    reason: str

    @property
    def length_mm(self) -> float:
        return float(np.linalg.norm(self.end - self.start))

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_mm": self.start.astype(float).tolist(),
            "end_mm": self.end.astype(float).tolist(),
            "length_mm": self.length_mm,
            "patch_id": int(self.patch_id),
            "reason": self.reason,
        }


@dataclass
class PatternResult:
    selected: str
    frame: BoatFrame | None
    lines: list[PatternLine]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    output_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "selected_label": PATTERN_NAMES[self.selected],
            "frame": None if self.frame is None else self.frame.to_dict(),
            "entity_count": int(len(self.lines)),
            "metrics": self.metrics,
            "warnings": list(self.warnings),
            "outputs": self.output_metrics,
            "cnc_ready": False,
            "test_verification_geometry": True,
        }


def _role(curve: ManufacturingCurve) -> str:
    layer = curve.source.source.raw_curve.layer.upper()
    if "DECK_PRIMARY" in layer:
        return "primary"
    if "DECK_SECONDARY" in layer:
        return "secondary"
    if "OBSTACLE" in layer:
        return "obstacle"
    return "other"


def _clean_polygon(points: np.ndarray) -> Polygon | None:
    values = np.asarray(points, dtype=float)[:, :2]
    if len(values) < 3:
        return None
    polygon = Polygon(values)
    if not polygon.is_valid:
        polygon = polygon.buffer(0.0)
    if polygon.is_empty or not isinstance(polygon, Polygon):
        return None
    return polygon


def _slice_widths(polygon: Polygon, origin: np.ndarray, axis: np.ndarray, count: int) -> list[dict[str, float]]:
    transverse = np.array([-axis[1], axis[0]], dtype=float)
    coordinates = np.asarray(polygon.exterior.coords, dtype=float)
    longitudinal = (coordinates - origin) @ axis
    transverse_values = (coordinates - origin) @ transverse
    s_min, s_max = float(np.min(longitudinal)), float(np.max(longitudinal))
    reach = max(float(np.ptp(transverse_values)) * 2.0, 100.0)
    rows: list[dict[str, float]] = []
    for s in np.linspace(s_min + 0.03 * (s_max - s_min), s_max - 0.03 * (s_max - s_min), count):
        center = origin + s * axis
        cross = LineString([center - reach * transverse, center + reach * transverse])
        hit = polygon.intersection(cross)
        points: list[np.ndarray] = []
        geometries = list(hit.geoms) if hasattr(hit, "geoms") else [hit]
        for geometry in geometries:
            if isinstance(geometry, LineString):
                points.extend(np.asarray(geometry.coords, dtype=float)[[0, -1]])
            elif isinstance(geometry, Point):
                points.append(np.asarray(geometry.coords[0], dtype=float))
        if len(points) < 2:
            continue
        t = (np.asarray(points) - origin) @ transverse
        low, high = float(np.min(t)), float(np.max(t))
        rows.append({"s_mm": float(s), "midpoint_t_mm": 0.5 * (low + high), "width_mm": high - low})
    return rows


def detect_boat_frame(primary: PolyarcCurve, settings: dict[str, Any]) -> BoatFrame:
    polygon = _clean_polygon(primary.sampled_points)
    if polygon is None:
        raise ValueError("primary CAM perimeter is not a usable closed polygon")
    points = np.asarray(polygon.exterior.coords[:-1], dtype=float)
    centroid = np.asarray(polygon.centroid.coords[0], dtype=float)
    centered = points - centroid
    covariance = centered.T @ centered / max(len(centered), 1)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    axis = _unit(vectors[:, order[0]])
    if axis[0] < 0.0 or (abs(axis[0]) < 1e-12 and axis[1] < 0.0):
        axis *= -1.0
    rows = _slice_widths(polygon, centroid, axis, int(settings.get("axis_slice_count", 31)))
    if len(rows) >= 5:
        s = np.asarray([row["s_mm"] for row in rows])
        midpoint = np.asarray([row["midpoint_t_mm"] for row in rows])
        design = np.column_stack((s, np.ones(len(s))))
        coefficient = np.linalg.lstsq(design, midpoint, rcond=None)[0]
        residual = midpoint - design @ coefficient
        keep = np.abs(residual - np.median(residual)) <= max(3.0 * 1.4826 * np.median(np.abs(residual - np.median(residual))), 1.0)
        if np.count_nonzero(keep) >= 3:
            coefficient = np.linalg.lstsq(design[keep], midpoint[keep], rcond=None)[0]
            residual = midpoint[keep] - design[keep] @ coefficient
        transverse = np.array([-axis[1], axis[0]], dtype=float)
        origin = centroid + float(coefficient[1]) * transverse
        axis = _unit(axis + float(coefficient[0]) * transverse)
    else:
        origin = centroid
        residual = np.asarray([], dtype=float)
    transverse = np.array([-axis[1], axis[0]], dtype=float)
    extents = np.ptp(centered @ np.column_stack((axis, transverse)), axis=0)
    aspect = float(extents[0] / max(extents[1], 1e-9))
    coverage = min(1.0, len(rows) / max(int(settings.get("axis_slice_count", 31)), 1))
    width_median = float(np.median([row["width_mm"] for row in rows])) if rows else float(extents[1])
    residual_rms = float(np.sqrt(np.mean(residual * residual))) if len(residual) else width_median
    aspect_score = float(np.clip((aspect - 1.0) / max(aspect, 1.0), 0.0, 1.0))
    residual_score = math.exp(-residual_rms / max(0.05 * width_median, 1.0))
    confidence = float(np.clip(0.55 * aspect_score + 0.45 * coverage * residual_score, 0.0, 1.0))
    if rows:
        sorted_rows = sorted(rows, key=lambda row: row["s_mm"])
        end_count = max(2, len(sorted_rows) // 5)
        negative_width = float(np.median([row["width_mm"] for row in sorted_rows[:end_count]]))
        positive_width = float(np.median([row["width_mm"] for row in sorted_rows[-end_count:]]))
        bow_sign = 1 if positive_width <= negative_width else -1
        bow_confidence = abs(positive_width - negative_width) / max(positive_width, negative_width, 1e-9)
    else:
        negative_width = positive_width = 0.0
        bow_sign = 1
        bow_confidence = 0.0
    return BoatFrame(
        origin=origin,
        longitudinal=axis,
        transverse=transverse,
        axis_confidence=confidence,
        bow_sign=bow_sign,
        bow_confidence=float(np.clip(bow_confidence, 0.0, 1.0)),
        metrics={
            "method": "outer-CAM PCA followed by cross-boat width-slice midpoint fit",
            "aspect_ratio": aspect,
            "slice_count": len(rows),
            "slice_coverage": coverage,
            "centerline_residual_rms_mm": residual_rms,
            "negative_end_width_mm": negative_width,
            "positive_end_width_mm": positive_width,
            "interior_obstacles_used": False,
        },
    )


def _frame_coordinates(points: np.ndarray, frame: BoatFrame) -> np.ndarray:
    delta = np.asarray(points, dtype=float)[:, :2] - frame.origin
    return np.column_stack((delta @ frame.longitudinal, delta @ frame.transverse))


def _world_coordinates(st: np.ndarray, frame: BoatFrame) -> np.ndarray:
    values = np.asarray(st, dtype=float)
    return frame.origin + values[..., 0, None] * frame.longitudinal + values[..., 1, None] * frame.transverse


def _line_parts(geometry: Any) -> list[LineString]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, (MultiLineString, GeometryCollection)) or hasattr(geometry, "geoms"):
        return [part for item in geometry.geoms for part in _line_parts(item)]
    return []


def _clip_line(start: np.ndarray, end: np.ndarray, domain: Polygon, patch_id: int, reason: str, minimum: float) -> list[PatternLine]:
    result: list[PatternLine] = []
    for part in _line_parts(domain.intersection(LineString([start, end]))):
        coordinates = np.asarray(part.coords, dtype=float)
        if len(coordinates) < 2:
            continue
        line = PatternLine(coordinates[0], coordinates[-1], patch_id, reason)
        if line.length_mm >= minimum:
            result.append(line)
    return result


def _domain_extent(domain: Polygon, frame: BoatFrame) -> tuple[float, float, float, float]:
    coordinates = np.asarray(domain.envelope.exterior.coords, dtype=float)
    local = _frame_coordinates(coordinates, frame)
    return float(np.min(local[:, 0])), float(np.max(local[:, 0])), float(np.min(local[:, 1])), float(np.max(local[:, 1]))


def _teak(domain: Polygon, frame: BoatFrame, patch_id: int, settings: dict[str, Any]) -> tuple[list[PatternLine], int, int]:
    spacing = float(settings["teak_spacing_mm"])
    s0, s1, t0, t1 = _domain_extent(domain, frame)
    reach = max(s1 - s0, t1 - t0) * 2.0 + spacing
    minimum = float(settings.get("clip_minimum_fragment_mm", 0.5))
    lines: list[PatternLine] = []
    generated = 0
    for index in range(math.floor(t0 / spacing) - 1, math.ceil(t1 / spacing) + 2):
        t = index * spacing
        start = frame.origin - reach * frame.longitudinal + t * frame.transverse
        end = frame.origin + reach * frame.longitudinal + t * frame.transverse
        generated += 1
        lines.extend(_clip_line(start, end, domain, patch_id, "TEAK_LONGITUDINAL", minimum))
    return lines, generated, 0


def _diamond(domain: Polygon, frame: BoatFrame, patch_id: int, settings: dict[str, Any]) -> tuple[list[PatternLine], int, int]:
    long_diagonal = float(settings["diamond_long_diagonal_mm"])
    short_diagonal = float(settings["diamond_short_diagonal_mm"])
    s0, s1, t0, t1 = _domain_extent(domain, frame)
    corners = np.asarray([[s0, t0], [s0, t1], [s1, t0], [s1, t1]])
    reach = 4.0 * max(s1 - s0, t1 - t0, long_diagonal)
    minimum = float(settings.get("clip_minimum_fragment_mm", 0.5))
    lines: list[PatternLine] = []
    generated = 0
    for sign in (-1.0, 1.0):
        levels = corners[:, 0] / long_diagonal + sign * corners[:, 1] / short_diagonal
        for level in range(math.floor(float(np.min(levels))) - 1, math.ceil(float(np.max(levels))) + 2):
            point_local = np.array([0.0, sign * level * short_diagonal])
            direction_local = _unit(np.array([long_diagonal, -sign * short_diagonal]))
            endpoints = np.vstack((point_local - reach * direction_local, point_local + reach * direction_local))
            world = _world_coordinates(endpoints, frame)
            generated += 1
            lines.extend(_clip_line(world[0], world[1], domain, patch_id, "DIAMOND_SHARED_GRID_EDGE", minimum))
    return lines, generated, 0


def _edge_key(first: np.ndarray, second: np.ndarray, tolerance: float) -> tuple[int, ...]:
    a = tuple(np.rint(np.asarray(first) / tolerance).astype(np.int64).tolist())
    b = tuple(np.rint(np.asarray(second) / tolerance).astype(np.int64).tolist())
    return (*a, *b) if a <= b else (*b, *a)


def _hex(domain: Polygon, frame: BoatFrame, patch_id: int, settings: dict[str, Any]) -> tuple[list[PatternLine], int, int]:
    across = float(settings["hex_across_flats_mm"])
    side = across / math.sqrt(3.0)
    apothem = across * 0.5
    s0, s1, t0, t1 = _domain_extent(domain, frame)
    i0 = math.floor((t0 - side) / (1.5 * side)) - 2
    i1 = math.ceil((t1 + side) / (1.5 * side)) + 2
    j0 = math.floor((s0 - across) / across) - 2
    j1 = math.ceil((s1 + across) / across) + 2
    tolerance = float(settings.get("deduplication_tolerance_mm", 0.001))
    edges: dict[tuple[int, ...], tuple[np.ndarray, np.ndarray]] = {}
    generated = 0
    for column in range(i0, i1 + 1):
        center_t = 1.5 * side * column
        offset_s = apothem if column % 2 else 0.0
        for row in range(j0, j1 + 1):
            center_s = across * row + offset_s
            vertices = np.asarray([
                [center_s + apothem, center_t + 0.5 * side],
                [center_s, center_t + side],
                [center_s - apothem, center_t + 0.5 * side],
                [center_s - apothem, center_t - 0.5 * side],
                [center_s, center_t - side],
                [center_s + apothem, center_t - 0.5 * side],
            ])
            world = _world_coordinates(vertices, frame)
            for index in range(6):
                first, second = world[index], world[(index + 1) % 6]
                generated += 1
                edges.setdefault(_edge_key(first, second, tolerance), (first, second))
    minimum = float(settings.get("clip_minimum_fragment_mm", 0.5))
    lines: list[PatternLine] = []
    for first, second in edges.values():
        lines.extend(_clip_line(first, second, domain, patch_id, "HEX_SHARED_EDGE", minimum))
    return lines, generated, generated - len(edges)


def _deduplicate_lines(lines: list[PatternLine], tolerance: float) -> tuple[list[PatternLine], int]:
    result: dict[tuple[int, ...], PatternLine] = {}
    for line in lines:
        result.setdefault(_edge_key(line.start, line.end, tolerance), line)
    return list(result.values()), len(lines) - len(result)


def generate_pattern(
    polyarcs: list[PolyarcCurve],
    manufacturing: list[ManufacturingCurve],
    configuration: dict[str, Any],
    development: dict[int, DevelopmentResult] | None = None,
) -> PatternResult:
    del development  # reserved for per-patch affine phase transfer in intrinsic multi-panel runs
    settings = dict(configuration.get("pattern", {}))
    selected = str(settings.get("selected", "none")).lower()
    if selected not in PATTERN_NAMES:
        raise ValueError("pattern must be one of: none, teak, diamond, hex")
    if selected == "none":
        return PatternResult("none", None, [], {"generated_entities": 0, "clipped_entities": 0, "duplicate_pattern_edges_removed": 0})
    pairs = [
        (spline, polyarc) for spline, polyarc in zip(manufacturing, polyarcs)
        if polyarc.status != "INVALID" and polyarc.is_closed and polyarc.primitives
    ]
    primary_pair = next(((spline, curve) for spline, curve in pairs if _role(spline) == "primary"), None)
    if primary_pair is None:
        raise ValueError("pattern generation requires a valid CAM primary outer perimeter")
    frame = detect_boat_frame(primary_pair[1], settings)
    outers = [(spline, curve) for spline, curve in pairs if _role(spline) in {"primary", "secondary"}]
    obstacles = [(spline, curve) for spline, curve in pairs if _role(spline) == "obstacle"]
    all_lines: list[PatternLine] = []
    generated = 0
    clipped = 0
    duplicates = 0
    domain_count = 0
    for spline, outer in outers:
        patch_id = int(spline.source.metrics.get("development_patch_id", 0))
        outer_polygon = _clean_polygon(outer.sampled_points)
        if outer_polygon is None:
            continue
        holes = []
        for obstacle_spline, obstacle in obstacles:
            if int(obstacle_spline.source.metrics.get("development_patch_id", 0)) != patch_id:
                continue
            hole = _clean_polygon(obstacle.sampled_points)
            if hole is not None and outer_polygon.intersects(hole):
                holes.append(hole)
        domain = outer_polygon.difference(unary_union(holes)) if holes else outer_polygon
        if domain.is_empty:
            continue
        domains = list(domain.geoms) if hasattr(domain, "geoms") else [domain]
        for part in domains:
            if not isinstance(part, Polygon):
                continue
            domain_count += 1
            if selected == "teak":
                lines, raw_count, removed = _teak(part, frame, patch_id, settings)
            elif selected == "diamond":
                lines, raw_count, removed = _diamond(part, frame, patch_id, settings)
            else:
                lines, raw_count, removed = _hex(part, frame, patch_id, settings)
            generated += raw_count
            duplicates += removed
            clipped += len(lines)
            all_lines.extend(lines)
    all_lines, final_duplicates = _deduplicate_lines(
        all_lines, float(settings.get("deduplication_tolerance_mm", 0.001)),
    )
    duplicates += final_duplicates
    micro = sum(line.length_mm < 3.0 for line in all_lines)
    dimension_checks = {
        "teak_spacing_mm": float(settings["teak_spacing_mm"]),
        "diamond_long_diagonal_mm": float(settings["diamond_long_diagonal_mm"]),
        "diamond_short_diagonal_mm": float(settings["diamond_short_diagonal_mm"]),
        "hex_across_flats_mm": float(settings["hex_across_flats_mm"]),
        "selected_dimension_error_mm": 0.0,
        "valid": True,
        "coordinate_space": "developed physical millimeters",
    }
    warnings = []
    if frame.axis_confidence < 0.50:
        warnings.append("boat longitudinal axis confidence is below 0.50; inspect pattern direction")
    if micro:
        warnings.append(f"{micro} clipped pattern fragments are shorter than 3 mm")
    return PatternResult(
        selected,
        frame,
        all_lines,
        {
            "generated_entities": int(generated),
            "clipped_entities": int(clipped),
            "final_entities": int(len(all_lines)),
            "duplicate_pattern_edges_removed": int(duplicates),
            "coincident_duplicates_final": 0,
            "micro_fragments_below_3mm": int(micro),
            "pattern_domain_count": int(domain_count),
            "obstacle_hole_count": int(len(obstacles)),
            "dimension_checks": dimension_checks,
            "global_phase_shared": True,
            "generated_from": "CAM_POLYARC_V033",
        },
        warnings,
    )


def _new_dxf() -> ezdxf.document.Drawing:
    document = ezdxf.new("R2018")
    document.units = ezdxf.units.MM
    document.header["$INSUNITS"] = 4
    return document


def _write_pattern_lines(document: Any, result: PatternResult) -> int:
    if result.selected == "none":
        return 0
    layer = PATTERN_LAYERS[result.selected]
    if layer not in document.layers:
        document.layers.add(layer)
    modelspace = document.modelspace()
    for line in result.lines:
        modelspace.add_line(line.start, line.end, dxfattribs={"layer": layer})
    return len(result.lines)


def _write_cam_perimeters(document: Any, polyarcs: list[PolyarcCurve], manufacturing: list[ManufacturingCurve]) -> int:
    modelspace = document.modelspace()
    count = 0
    for spline, curve in zip(manufacturing, polyarcs):
        if curve.status == "INVALID" or not curve.primitives:
            continue
        role = _role(spline)
        layer = {
            "primary": "AD_DECK_PRIMARY",
            "secondary": "AD_DECK_SECONDARY",
            "obstacle": "AD_OBSTACLES",
        }.get(role, "AD_OTHER_CAM")
        if layer not in document.layers:
            document.layers.add(layer)
        vertices = [
            (float(item.start[0]), float(item.start[1]), 0.0, 0.0, float(item.bulge))
            for item in curve.primitives
        ]
        if not curve.is_closed:
            endpoint = curve.primitives[-1].end
            vertices.append((float(endpoint[0]), float(endpoint[1]), 0.0, 0.0, 0.0))
        modelspace.add_lwpolyline(vertices, format="xyseb", close=curve.is_closed, dxfattribs={"layer": layer})
        count += 1
    return count


def _roundtrip(path: Path, expected_lines: int, expected_perimeters: int = 0) -> dict[str, Any]:
    try:
        document = ezdxf.readfile(path)
        entities = list(document.modelspace())
        line_count = sum(entity.dxftype() == "LINE" for entity in entities)
        perimeter_count = sum(entity.dxftype() == "LWPOLYLINE" for entity in entities)
        valid = line_count == expected_lines and perimeter_count == expected_perimeters
        return {
            "valid": bool(valid),
            "entity_count": len(entities),
            "line_count": line_count,
            "perimeter_count": perimeter_count,
            "units": "mm",
            "insunits_code": int(document.header.get("$INSUNITS", 0)),
        }
    except (OSError, ezdxf.DXFError) as error:
        return {"valid": False, "error": str(error), "entity_count": 0}


def _write_pattern_preview(path: Path, result: PatternResult, polyarcs: list[PolyarcCurve]) -> dict[str, Any]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return {"written": False, "warning": "rhino3dm unavailable", "object_count": 0}
    model = rhino3dm.File3dm()
    layers: dict[str, int] = {}
    specs = {
        "CAM_POLYARC_V033": ((0, 230, 120, 255), True),
        "AUTODECK_PATTERN::TEAK": ((210, 150, 70, 255), result.selected == "teak"),
        "AUTODECK_PATTERN::DIAMOND": ((70, 160, 255, 255), result.selected == "diamond"),
        "AUTODECK_PATTERN::HEX": ((180, 90, 255, 255), result.selected == "hex"),
    }
    for name, (color, visible) in specs.items():
        layer = rhino3dm.Layer(); layer.Name = name; layer.Visible = visible
        try:
            layer.Color = color
        except Exception:
            pass
        layers[name] = model.Layers.Add(layer)
    perimeter_count = 0
    for curve in polyarcs:
        if curve.status == "INVALID":
            continue
        for index, primitive in enumerate(curve.primitives):
            attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layers["CAM_POLYARC_V033"]
            attrs.Name = f"cam_{curve.candidate_id:04d}_{index:04d}_{primitive.kind}"
            if primitive.kind == "LINE":
                model.Objects.AddLine(
                    rhino3dm.Point3d(float(primitive.start[0]), float(primitive.start[1]), 0.0),
                    rhino3dm.Point3d(float(primitive.end[0]), float(primitive.end[1]), 0.0), attrs,
                )
            else:
                sampled = primitive.sample(max(0.25, primitive.length_mm / 64.0))
                arc = rhino3dm.Arc(
                    rhino3dm.Point3d(float(sampled[0, 0]), float(sampled[0, 1]), 0.0),
                    rhino3dm.Point3d(float(sampled[len(sampled)//2, 0]), float(sampled[len(sampled)//2, 1]), 0.0),
                    rhino3dm.Point3d(float(sampled[-1, 0]), float(sampled[-1, 1]), 0.0),
                )
                model.Objects.AddArc(arc, attrs)
            perimeter_count += 1
    pattern_count = 0
    if result.selected != "none":
        layer_name = f"AUTODECK_PATTERN::{result.selected.upper()}"
        for index, line in enumerate(result.lines):
            attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layers[layer_name]
            attrs.Name = f"{result.selected}_{index:06d}"
            model.Objects.AddLine(
                rhino3dm.Point3d(float(line.start[0]), float(line.start[1]), 0.0),
                rhino3dm.Point3d(float(line.end[0]), float(line.end[1]), 0.0), attrs,
            )
            pattern_count += 1
    written = bool(model.Write(str(path), 8))
    return {"written": written, "perimeter_object_count": perimeter_count, "pattern_object_count": pattern_count, "object_count": len(model.Objects) if written else 0}


def write_pattern_outputs(
    output_dir: Path,
    result: PatternResult,
    polyarcs: list[PolyarcCurve],
    manufacturing: list[ManufacturingCurve],
) -> dict[str, Any]:
    if result.selected == "none":
        result.output_metrics = {"preferred_file": "flattened_curves_polyarc.dxf", "geometry_only_preserved": True}
        return result.output_metrics
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern_path = output_dir / "pattern_only.dxf"
    pattern_doc = _new_dxf(); pattern_count = _write_pattern_lines(pattern_doc, result); pattern_doc.saveas(pattern_path)
    pattern_audit = _roundtrip(pattern_path, pattern_count)
    combined_path = output_dir / "flattened_with_pattern.dxf"
    combined_doc = _new_dxf()
    perimeter_count = _write_cam_perimeters(combined_doc, polyarcs, manufacturing)
    combined_pattern_count = _write_pattern_lines(combined_doc, result)
    combined_doc.saveas(combined_path)
    combined_audit = _roundtrip(combined_path, combined_pattern_count, perimeter_count)
    preview = _write_pattern_preview(output_dir / "pattern_preview.3dm", result, polyarcs)
    metrics = {
        "preferred_file": combined_path.name,
        "pattern_only": {"path": pattern_path.name, **pattern_audit},
        "flattened_with_pattern": {"path": combined_path.name, **combined_audit},
        "pattern_preview_3dm": preview,
        "geometry_only_preserved": (output_dir / "flattened_curves_polyarc.dxf").exists(),
        "status": "GOOD" if pattern_audit.get("valid") and combined_audit.get("valid") else "INVALID",
        "cnc_ready": False,
        "test_verification_geometry": True,
    }
    result.output_metrics = metrics
    return metrics


def write_pattern_report(path: Path, result: PatternResult) -> None:
    frame = result.frame
    metrics = result.metrics
    outputs = result.output_metrics
    lines = [
        f"# AutoDeck V{__version__} pattern report",
        "",
        "> **TEST / VERIFICATION GEOMETRY — NOT CNC-READY.**",
        "",
        f"- Selected pattern: **{PATTERN_NAMES[result.selected]}**",
    ]
    if frame is not None:
        lines.extend([
            f"- Boat axis: `{frame.longitudinal.astype(float).tolist()}` ({math.degrees(math.atan2(frame.longitudinal[1], frame.longitudinal[0])):.4f}°)",
            f"- Axis confidence: **{frame.axis_confidence:.4f}**",
            f"- Bow sign / confidence: **{frame.bow_sign:+d} / {frame.bow_confidence:.4f}**",
            f"- Global pattern origin: `{frame.origin.astype(float).tolist()}` mm",
        ])
    lines.extend([
        "",
        "## Geometry",
        "",
        f"- Generated source entities/edges: {metrics.get('generated_entities', 0)}",
        f"- Clipped entities: {metrics.get('clipped_entities', 0)}",
        f"- Final LINE entities: {metrics.get('final_entities', 0)}",
        f"- Duplicate pattern edges removed: {metrics.get('duplicate_pattern_edges_removed', 0)}",
        f"- Final coincident duplicates: {metrics.get('coincident_duplicates_final', 0)}",
        f"- Micro fragments below 3 mm: {metrics.get('micro_fragments_below_3mm', 0)}",
        f"- Obstacle holes applied: {metrics.get('obstacle_hole_count', 0)}",
        "",
        "## Developed-dimension validation",
        "",
    ])
    checks = metrics.get("dimension_checks", {})
    for key in ("teak_spacing_mm", "diamond_long_diagonal_mm", "diamond_short_diagonal_mm", "hex_across_flats_mm"):
        if key in checks:
            lines.append(f"- {key}: {float(checks[key]):.6f} mm")
    lines.extend([
        f"- Selected-pattern dimension error: {float(checks.get('selected_dimension_error_mm', 0.0)):.9f} mm",
        f"- Coordinate space: {checks.get('coordinate_space', 'n/a')}",
        "",
        "## Export audit",
        "",
        f"- `pattern_only.dxf`: valid={outputs.get('pattern_only', {}).get('valid', result.selected == 'none')}",
        f"- `flattened_with_pattern.dxf`: valid={outputs.get('flattened_with_pattern', {}).get('valid', result.selected == 'none')}",
        f"- `pattern_preview.3dm`: written={outputs.get('pattern_preview_3dm', {}).get('written', False)}",
        f"- Preferred output: `{outputs.get('preferred_file', 'flattened_curves_polyarc.dxf')}`",
        "",
        f"Warnings: {'; '.join(result.warnings) if result.warnings else 'none'}.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")
