from __future__ import annotations

"""Measured test flattening and VCarve-oriented DXF export."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any

import numpy as np

from .conditioning import curve_length, signed_area_xy, validate_topology
from .models import AcceptedSurfaceGrid, ConditionedCurve, FlattenedCurve


def _distribution(values: np.ndarray, *, p50: bool = False) -> dict[str, float]:
    finite = np.asarray(values, dtype=np.float64)[np.isfinite(values)]
    if not len(finite):
        result = {"mean": 0.0, "rms": 0.0, "p95": 0.0, "max": 0.0}
        if p50:
            result["p50"] = 0.0
        return result
    result = {
        "mean": float(np.mean(finite)),
        "rms": float(np.sqrt(np.mean(finite ** 2))),
        "p95": float(np.percentile(finite, 95)),
        "max": float(np.max(finite)),
    }
    if p50:
        result["p50"] = float(np.percentile(finite, 50))
    return result


@dataclass(slots=True)
class FlatteningContext:
    origin_mm: np.ndarray
    u_axis: np.ndarray
    v_axis: np.ndarray
    normal: np.ndarray
    plane_metrics: dict[str, Any]
    distortion_metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    status: str = "PASS"


class FlatteningStrategy(ABC):
    name: str

    @abstractmethod
    def fit(self, surface: AcceptedSurfaceGrid, config: dict[str, Any]) -> FlatteningContext:
        raise NotImplementedError

    @abstractmethod
    def flatten_points(self, points_mm: np.ndarray, context: FlatteningContext) -> np.ndarray:
        raise NotImplementedError


def _interior_mask(mask: np.ndarray) -> np.ndarray:
    if min(mask.shape) < 3:
        return mask.copy()
    interior = mask.copy()
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            shifted = np.zeros_like(mask)
            r0 = max(0, dr); r1 = mask.shape[0] + min(0, dr)
            c0 = max(0, dc); c1 = mask.shape[1] + min(0, dc)
            shifted[r0:r1, c0:c1] = mask[r0 - dr:r1 - dr, c0 - dc:c1 - dc]
            interior &= shifted
    return interior


def _surface_points(surface: AcceptedSurfaceGrid, limit: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = _interior_mask(surface.accepted_mask) & np.isfinite(surface.floor_height_mm)
    rows, cols = np.nonzero(valid)
    if len(rows) > limit:
        indices = np.linspace(0, len(rows) - 1, limit, dtype=np.int64)
        rows = rows[indices]; cols = cols[indices]
    points = np.column_stack((
        surface.x_min_mm + (cols + 0.5) * surface.resolution_mm,
        surface.y_min_mm + (rows + 0.5) * surface.resolution_mm,
        surface.floor_height_mm[rows, cols],
    ))
    return points, rows, cols


def _robust_plane(points: np.ndarray, iterations: int) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 3:
        raise ValueError("At least three accepted interior surface samples are required for planar flattening")
    working = points
    origin = np.median(working, axis=0)
    normal = np.array([0.0, 0.0, 1.0])
    for _ in range(max(1, iterations)):
        origin = np.mean(working, axis=0)
        covariance = (working - origin).T @ (working - origin) / max(len(working), 1)
        _values, vectors = np.linalg.eigh(covariance)
        normal = vectors[:, 0]
        if normal[2] < 0.0:
            normal = -normal
        residual = np.abs((points - origin) @ normal)
        cutoff = np.percentile(residual, 95)
        working = points[residual <= cutoff]
    return origin, normal / np.linalg.norm(normal)


def _plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    world_x = np.array([1.0, 0.0, 0.0])
    u = world_x - np.dot(world_x, normal) * normal
    if np.linalg.norm(u) <= 1e-9:
        world_y = np.array([0.0, 1.0, 0.0])
        u = world_y - np.dot(world_y, normal) * normal
    u /= np.linalg.norm(u)
    v = np.cross(normal, u); v /= np.linalg.norm(v)
    return u, v


def _surface_normal_variation(surface: AcceptedSurfaceGrid, normal: np.ndarray) -> dict[str, float]:
    z = surface.floor_height_mm.astype(np.float64)
    valid = _interior_mask(surface.accepted_mask) & np.isfinite(z)
    dzdy, dzdx = np.gradient(z, surface.resolution_mm, surface.resolution_mm)
    normals = np.stack((-dzdx[valid], -dzdy[valid], np.ones(np.count_nonzero(valid))), axis=1)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    angles = np.degrees(np.arccos(np.clip(normals @ normal, -1.0, 1.0)))
    return _distribution(angles)


def _neighbor_distortion(
    surface: AcceptedSurfaceGrid,
    origin: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    limit: int,
) -> dict[str, Any]:
    mask = _interior_mask(surface.accepted_mask) & np.isfinite(surface.floor_height_mm)
    strain_chunks: list[np.ndarray] = []
    for dr, dc in ((0, 1), (1, 0)):
        a = mask[:mask.shape[0] - dr or None, :mask.shape[1] - dc or None]
        b = mask[dr:, dc:]
        valid = a & b
        rows, cols = np.nonzero(valid)
        if len(rows) > limit // 2:
            chosen = np.linspace(0, len(rows) - 1, limit // 2, dtype=np.int64)
            rows = rows[chosen]; cols = cols[chosen]
        p = np.column_stack((
            surface.x_min_mm + (cols + 0.5) * surface.resolution_mm,
            surface.y_min_mm + (rows + 0.5) * surface.resolution_mm,
            surface.floor_height_mm[rows, cols],
        ))
        q = np.column_stack((
            surface.x_min_mm + (cols + dc + 0.5) * surface.resolution_mm,
            surface.y_min_mm + (rows + dr + 0.5) * surface.resolution_mm,
            surface.floor_height_mm[rows + dr, cols + dc],
        ))
        length_3d = np.linalg.norm(q - p, axis=1)
        delta = q - p
        length_2d = np.hypot(delta @ u, delta @ v)
        strain_chunks.append(100.0 * (length_2d - length_3d) / np.maximum(length_3d, 1e-12))
    strain = np.concatenate(strain_chunks) if strain_chunks else np.empty(0)
    absolute = np.abs(strain)
    return {
        "representative_neighbor_count": int(len(strain)),
        "mean_strain_percent": float(np.mean(strain)) if len(strain) else 0.0,
        "rms_strain_percent": float(np.sqrt(np.mean(strain ** 2))) if len(strain) else 0.0,
        "p95_absolute_strain_percent": float(np.percentile(absolute, 95)) if len(strain) else 0.0,
        "maximum_absolute_strain_percent": float(np.max(absolute)) if len(strain) else 0.0,
    }


class PlanarFlattening(FlatteningStrategy):
    name = "planar"

    def fit(self, surface: AcceptedSurfaceGrid, config: dict[str, Any]) -> FlatteningContext:
        cfg = config["flattening"]
        points, _rows, _cols = _surface_points(surface, int(cfg["plane_sample_limit"]))
        origin, normal = _robust_plane(points, int(cfg["plane_trim_iterations"]))
        u, v = _plane_axes(normal)
        signed_residual = (points - origin) @ normal
        absolute_residual = np.abs(signed_residual)
        plane_metrics = {
            "accepted_interior_sample_count": int(len(points)),
            "surface_to_plane_distance_mm": _distribution(absolute_residual, p50=True),
            "broad_surface_normal_variation_deg": _surface_normal_variation(surface, normal),
            "origin_mm": origin.tolist(),
            "u_axis": u.tolist(),
            "v_axis": v.tolist(),
            "normal": normal.tolist(),
            "rotation_from_world_xy_deg": float(math.degrees(math.acos(float(np.clip(normal[2], -1.0, 1.0))))),
            "u_dot_world_x": float(u[0]),
            "v_dot_world_y": float(v[1]),
            "right_handed_determinant": float(np.linalg.det(np.column_stack((u, v, normal)))),
            "orientation": "U aligned toward world +X; V=normal×U and corresponds toward world +Y",
        }
        distortion = _neighbor_distortion(surface, origin, u, v, int(cfg["plane_sample_limit"]))
        p95 = float(distortion["p95_absolute_strain_percent"])
        rms = float(distortion["rms_strain_percent"])
        maximum = float(distortion["maximum_absolute_strain_percent"])
        warning_exceeded = (
            p95 > float(cfg["distortion_warning_p95_percent"])
            or rms > float(cfg["distortion_warning_rms_percent"])
            or maximum > float(cfg["distortion_warning_maximum_percent"])
        )
        failure_exceeded = (
            p95 > float(cfg["distortion_failure_p95_percent"])
            or rms > float(cfg["distortion_failure_rms_percent"])
            or maximum > float(cfg["distortion_failure_maximum_percent"])
        )
        warnings: list[str] = []
        if warning_exceeded:
            warnings.append(
                "PLANAR FLATTENING WARNING: surface is not sufficiently planar for reliable planar flattening."
            )
        status = "FAIL" if failure_exceeded else ("WARN" if warning_exceeded else "PASS")
        return FlatteningContext(origin, u, v, normal, plane_metrics, distortion, warnings, status)

    def flatten_points(self, points_mm: np.ndarray, context: FlatteningContext) -> np.ndarray:
        delta = np.asarray(points_mm, dtype=np.float64) - context.origin_mm
        return np.column_stack((delta @ context.u_axis, delta @ context.v_axis, np.zeros(len(delta))))


class MeshSurfaceFlattening(FlatteningStrategy):
    name = "mesh_surface_experimental"

    def fit(self, surface: AcceptedSurfaceGrid, config: dict[str, Any]) -> FlatteningContext:
        raise RuntimeError(
            "EXPERIMENTAL MESH FLATTENING UNAVAILABLE: no tested LSCM/ARAP library is installed; "
            "no custom solver was substituted."
        )

    def flatten_points(self, points_mm: np.ndarray, context: FlatteningContext) -> np.ndarray:
        raise RuntimeError("Mesh surface flattening has no valid fitted parameterization")


def flatten_curves(
    conditioned: list[ConditionedCurve],
    strategy: FlatteningStrategy,
    context: FlatteningContext,
    mm_per_input_unit: float,
    config: dict[str, Any],
) -> list[FlattenedCurve]:
    output: list[FlattenedCurve] = []
    warning_limit = float(config["flattening"]["distortion_warning_p95_percent"])
    failure_limit = float(config["flattening"]["distortion_failure_p95_percent"])
    for index, item in enumerate(conditioned, 1):
        smooth_mm = item.smoothed_curve.points_input * mm_per_input_unit
        flat = strategy.flatten_points(smooth_mm, context)
        length_3d = curve_length(smooth_mm); length_2d = curve_length(flat)
        length_change = 100.0 * (length_2d - length_3d) / max(length_3d, 1e-12)
        source_area = signed_area_xy(smooth_mm) if item.smoothed_curve.closed else 0.0
        flat_area = signed_area_xy(flat) if item.smoothed_curve.closed else 0.0
        orientation_preserved = not item.smoothed_curve.closed or source_area == 0.0 or flat_area == 0.0 or np.sign(source_area) == np.sign(flat_area)
        validation = validate_topology(flat, item.smoothed_curve.closed, source_area if item.smoothed_curve.closed else None)
        magnitude = abs(length_change)
        if not validation["valid"] or not orientation_preserved or magnitude > failure_limit:
            status = "INVALID"
        elif item.status != "GOOD" or magnitude > warning_limit or context.status != "PASS":
            status = "NEEDS_REVIEW"
        else:
            status = "GOOD"
        flattened_id = f"flat-{index:04d}-{item.raw_curve.name.lower().replace(' ', '_')}"
        layer = flat_layer_name(item.raw_curve.layer)
        metrics = {
            "raw_curve_id": item.raw_curve_id,
            "smoothed_curve_id": item.smoothed_curve_id,
            "flattened_curve_id": flattened_id,
            "smooth_3d_length_mm": length_3d,
            "flat_2d_length_mm": length_2d,
            "length_change_percent": length_change,
            "smooth_xy_signed_area_mm2": source_area,
            "flat_signed_area_mm2": flat_area,
            "orientation_preserved": bool(orientation_preserved),
            "validation": validation,
        }
        output.append(FlattenedCurve(item, flattened_id, flat, layer, item.smoothed_curve.closed, status, metrics))
    return output


def flat_layer_name(layer: str) -> str:
    name = layer.upper()
    for prefix in ("AUTODECK::SMOOTH_3D::", "AUTODECK::RAW_3D::", "AUTODECK::"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return "AD_" + name.replace("::", "_").replace(" ", "_")[:28]


def _fit_circle(points: np.ndarray) -> tuple[np.ndarray, float, float] | None:
    xy = np.asarray(points[:, :2], dtype=np.float64)
    if len(xy) < 5:
        return None
    a = np.column_stack((2.0 * xy[:, 0], 2.0 * xy[:, 1], np.ones(len(xy))))
    b = np.sum(xy ** 2, axis=1)
    try:
        cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    radius_sq = c + cx * cx + cy * cy
    if radius_sq <= 0.0:
        return None
    center = np.array([cx, cy]); radius = math.sqrt(radius_sq)
    error = float(np.max(np.abs(np.linalg.norm(xy - center, axis=1) - radius)))
    return center, radius, error


def _line_error(points: np.ndarray) -> float:
    if len(points) <= 2:
        return 0.0
    start = points[0, :2]; end = points[-1, :2]; delta = end - start
    if np.linalg.norm(delta) <= 1e-9:
        return float("inf")
    offsets = points[1:-1, :2] - start
    cross = delta[0] * offsets[:, 1] - delta[1] * offsets[:, 0]
    return float(np.max(np.abs(cross) / np.linalg.norm(delta)))


def _geometry_key(points: np.ndarray, closed: bool) -> bytes:
    xy = np.round(points[:, :2], 6)
    if closed and len(xy) > 1 and np.array_equal(xy[0], xy[-1]):
        xy = xy[:-1]
    if not len(xy):
        return b""
    if closed:
        candidates = np.flatnonzero(np.all(xy == np.min(xy, axis=0), axis=1))
        start = int(candidates[0]) if len(candidates) else int(np.lexsort((xy[:, 1], xy[:, 0]))[0])
        forward = np.roll(xy, -start, axis=0)
        reversed_xy = xy[::-1]
        reverse_start = int(np.lexsort((reversed_xy[:, 1], reversed_xy[:, 0]))[0])
        reverse = np.roll(reversed_xy, -reverse_start, axis=0)
        return min(forward.tobytes(), reverse.tobytes())
    return min(xy.tobytes(), xy[::-1].tobytes())


def write_dxf(path: Path, curves: list[FlattenedCurve], fit_error_mm: float) -> dict[str, Any]:
    """Legacy V0.2 flat export retained for API compatibility, now via ezdxf.

    V0.3 manufacturing exports use :mod:`autodeck.dxf_export` after span fitting.
    """
    import ezdxf
    from ezdxf import units

    document = ezdxf.new("R2010", setup=True); document.units = units.MM
    document.header["$MEASUREMENT"] = 1; modelspace = document.modelspace()
    counts = {"LINE": 0, "ARC": 0, "CIRCLE": 0, "LWPOLYLINE": 0, "SPLINE": 0}
    layer_counts: dict[str, int] = {}
    exported: list[str] = []
    duplicate_count = 0
    seen_geometry: set[bytes] = set()
    for curve in curves:
        points = curve.points_mm
        if len(points) < 2 or curve.status == "INVALID":
            continue
        geometry_key = _geometry_key(points, curve.closed)
        if geometry_key in seen_geometry:
            duplicate_count += 1
            continue
        seen_geometry.add(geometry_key)
        layer = curve.layer
        if layer not in document.layers:
            document.layers.add(layer)
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        exported.append(curve.flattened_curve_id)
        base = points[:-1] if curve.closed and np.linalg.norm(points[0] - points[-1]) <= 1e-9 else points
        circle = _fit_circle(base)
        if curve.closed and circle is not None and circle[2] <= fit_error_mm:
            center, radius, _error = circle
            modelspace.add_circle(center, radius, dxfattribs={"layer": layer})
            counts["CIRCLE"] += 1
        elif not curve.closed and _line_error(points) <= fit_error_mm:
            modelspace.add_line(points[0], points[-1], dxfattribs={"layer": layer})
            counts["LINE"] += 1
        else:
            modelspace.add_lwpolyline(base[:, :2], close=curve.closed, dxfattribs={"layer": layer})
            counts["LWPOLYLINE"] += 1
    document.saveas(path)
    reopened = ezdxf.readfile(path); auditor = reopened.audit()
    bbox_points = np.vstack([curve.points_mm for curve in curves if len(curve.points_mm) and curve.status != "INVALID"]) if exported else np.empty((0, 3))
    return {
        "path": path.name,
        "units": "millimetres",
        "insunits_code": 4,
        "entity_counts": counts,
        "total_entity_count": int(sum(counts.values())),
        "layer_entity_counts": layer_counts,
        "exported_flattened_curve_ids": exported,
        "duplicate_geometry_skipped": duplicate_count,
        "bbox_mm": [bbox_points.min(axis=0)[:2].tolist(), bbox_points.max(axis=0)[:2].tolist()] if len(bbox_points) else None,
        "test_verification_geometry": True,
        "cnc_ready": False,
        "ezdxf_version": ezdxf.__version__,
        "auditor_error_count": len(auditor.errors),
        "roundtrip_valid": reopened.units == units.MM and not auditor.errors,
    }


def write_scale_check_dxf(path: Path, size_mm: float = 100.0) -> dict[str, Any]:
    from .dxf_export import write_scale_check_dxf as _write_scale_check_dxf

    return _write_scale_check_dxf(path, size_mm)


def export_flattened_preview_3dm(path: Path, curves: list[FlattenedCurve]) -> tuple[bool, str | None, int]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return False, "rhino3dm unavailable; flattened_preview.3dm was not written", 0
    model = rhino3dm.File3dm(); layer_indices: dict[str, int] = {}
    colors = [(255,145,0,255),(35,145,255,255),(255,40,40,255),(40,220,80,255),(180,55,255,255),(130,40,220,255)]
    for index, layer_name in enumerate(sorted({curve.layer for curve in curves})):
        layer = rhino3dm.Layer(); layer.Name = f"AUTODECK::FLAT_2D::{layer_name}"; layer.Visible = True
        try:
            layer.Color = colors[index % len(colors)]
        except Exception:
            pass
        layer_indices[layer_name] = model.Layers.Add(layer)
    count = 0
    for curve in curves:
        if len(curve.points_mm) < 2 or curve.status == "INVALID":
            continue
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices[curve.layer]; attrs.Name = curve.flattened_curve_id
        polyline = rhino3dm.Polyline([rhino3dm.Point3d(float(p[0]), float(p[1]), 0.0) for p in curve.points_mm])
        model.Objects.AddPolyline(polyline, attrs); count += 1
    if not model.Write(str(path), 8):
        return False, "rhino3dm failed to write flattened_preview.3dm", 0
    return True, None, count
