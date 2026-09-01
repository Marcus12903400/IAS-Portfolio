from __future__ import annotations

from pathlib import Path

import numpy as np

from .models import AnalysisMesh, BoundaryLoop, CandidateRegion, ConditionedCurve, FeatureCurve, GeometryFields, Mesh
from .projection import lifted_loop_points_in_input_coordinates, loop_points_in_input_coordinates


def _raw_3d_layer(source_layer: str) -> str:
    upper = source_layer.upper()
    if "DECK_PRIMARY" in upper:
        return "RAW_3D::DECK_PRIMARY"
    if "DECK_SECONDARY" in upper:
        return "RAW_3D::DECK_SECONDARY"
    if "OBSTACLE" in upper:
        return "RAW_3D::OBSTACLES"
    return "RAW_3D::HARD_FEATURES"


def export_obj_curves(
    path: Path,
    loops: list[BoundaryLoop],
    analysis: AnalysisMesh,
    original: Mesh,
    fields: GeometryFields,
    lift_mm: float,
) -> None:
    lines = ["# AutoDeck V0.1 3-D verification curves", f"# coordinates remain in input units: {analysis.input_units}"]
    next_index = 1
    for loop_index, loop in enumerate(loops):
        master = loop_points_in_input_coordinates(loop, analysis, original)
        display = lifted_loop_points_in_input_coordinates(loop, analysis, original, fields, lift_mm)
        for variant, points in (("TRUE", master), ("DISPLAY_LIFT", display)):
            lines.append(f"o AUTODECK_{loop.kind.upper()}_{variant}_{loop_index:03d}")
            for point in points:
                lines.append(f"v {point[0]:.10g} {point[1]:.10g} {point[2]:.10g}")
            indices = " ".join(str(index) for index in range(next_index, next_index + len(points)))
            lines.append(f"l {indices}")
            next_index += len(points)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_3dm_optional(
    path: Path,
    loops: list[BoundaryLoop],
    analysis: AnalysisMesh,
    original: Mesh,
    fields: GeometryFields,
    lift_mm: float,
    seed_points_input: list[np.ndarray],
    paper_points_input: list[np.ndarray],
) -> tuple[bool, str | None]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return False, "rhino3dm unavailable; wrote Rhino-importable 3-D OBJ polylines instead"
    if not hasattr(rhino3dm, "File3dm"):
        return False, "rhino3dm is incomplete or incompatible; wrote Rhino-importable 3-D OBJ polylines instead"
    model = rhino3dm.File3dm()
    colors = {
        "PROPOSED_BOUNDARY": (255, 40, 40, 255),
        "INTERNAL_EXCLUSIONS": (255, 170, 0, 255),
        "DECK_SEEDS": (30, 255, 80, 255),
        "STRUCTURAL_BOUNDARIES": (255, 0, 255, 255),
        "LOW_CONFIDENCE": (255, 255, 0, 255),
        "MARKER_PAPERS": (0, 220, 255, 255),
        "DEBUG": (150, 150, 150, 255),
    }
    layer_indices: dict[str, int] = {}
    for name, color in colors.items():
        layer = rhino3dm.Layer(); layer.Name = f"AUTODECK::{name}"
        try:
            layer.Color = color
        except Exception:
            pass
        layer_indices[name] = model.Layers.Add(layer)

    def add_polyline(points: np.ndarray, layer_name: str, object_name: str) -> None:
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices[layer_name]; attrs.Name = object_name
        polyline = rhino3dm.Polyline([rhino3dm.Point3d(*point) for point in points])
        model.Objects.AddPolyline(polyline, attrs)

    for index, loop in enumerate(loops):
        layer_name = "PROPOSED_BOUNDARY" if loop.kind == "outer" else (
            "INTERNAL_EXCLUSIONS" if loop.kind == "internal_exclusion" else "LOW_CONFIDENCE"
        )
        master = loop_points_in_input_coordinates(loop, analysis, original)
        display = lifted_loop_points_in_input_coordinates(loop, analysis, original, fields, lift_mm)
        add_polyline(master, layer_name, f"{loop.kind}_true_{index:03d}")
        add_polyline(display, layer_name, f"{loop.kind}_display_{index:03d}")
    for index, point in enumerate(seed_points_input):
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices["DECK_SEEDS"]; attrs.Name = f"deck_seed_{index:03d}"
        model.Objects.AddPoint(rhino3dm.Point3d(*point), attrs)
    for index, point in enumerate(paper_points_input):
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices["MARKER_PAPERS"]; attrs.Name = f"marker_paper_{index:03d}"
        model.Objects.AddPoint(rhino3dm.Point3d(*point), attrs)
    if not model.Write(str(path), 8):
        return False, "rhino3dm failed to write verification.3dm"
    return True, None


def export_candidate_obj_curves(
    path: Path,
    candidates: list[CandidateRegion],
    analysis: AnalysisMesh,
    original: Mesh,
    fields: GeometryFields,
    lift_mm: float,
    outer_overrides: dict[int, FeatureCurve] | None = None,
) -> tuple[int, int]:
    lines = ["# AutoDeck V0.2 clean deck reference curves", f"# coordinates remain in input units: {analysis.input_units}"]
    next_index = 1
    curve_count = 0
    vertex_count = 0
    for candidate in candidates:
        override = (outer_overrides or {}).get(candidate.candidate_id)
        if override is not None:
            points = override.points_input
            role = "DECK_PRIMARY_OUTER" if candidate.is_primary else "DECK_SECONDARY"
            lines.append(f"o AUTODECK_{role}_{candidate.candidate_id:03d}_000")
            for point in points:
                lines.append(f"v {point[0]:.10g} {point[1]:.10g} {point[2]:.10g}")
            indices = " ".join(str(index) for index in range(next_index, next_index + len(points)))
            lines.append(f"l {indices}")
            next_index += len(points); curve_count += 1; vertex_count += len(points)
            continue
        for loop_index, loop in enumerate(candidate.outer_loops):
            master = loop_points_in_input_coordinates(loop, analysis, original)
            role = "DECK_PRIMARY_OUTER" if candidate.is_primary else "DECK_SECONDARY"
            lines.append(f"o AUTODECK_{role}_{candidate.candidate_id:03d}_{loop_index:03d}")
            for point in master:
                lines.append(f"v {point[0]:.10g} {point[1]:.10g} {point[2]:.10g}")
            indices = " ".join(str(index) for index in range(next_index, next_index + len(master)))
            lines.append(f"l {indices}")
            next_index += len(master)
            curve_count += 1
            vertex_count += len(master)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return curve_count, vertex_count


def export_feature_obj_curves(path: Path, curves: list[FeatureCurve], input_units: str) -> tuple[int, int]:
    lines = ["# AutoDeck V0.2 top-view-derived 3-D reference curves", f"# coordinates remain in input units: {input_units}"]
    next_index = 1; vertex_count = 0
    for index, curve in enumerate(curves):
        safe_layer = curve.layer.replace("::", "_").replace(" ", "_")
        lines.append(f"o {safe_layer}_{curve.name}_{index:03d}")
        for point in curve.points_input:
            lines.append(f"v {point[0]:.10g} {point[1]:.10g} {point[2]:.10g}")
        indices = " ".join(str(value) for value in range(next_index, next_index + len(curve.points_input)))
        lines.append(f"l {indices}")
        next_index += len(curve.points_input); vertex_count += len(curve.points_input)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(curves), vertex_count


def export_candidate_3dm_optional(
    path: Path,
    candidates: list[CandidateRegion],
    analysis: AnalysisMesh,
    original: Mesh,
    fields: GeometryFields,
    lift_mm: float,
    seed_points_input: list[np.ndarray] | None = None,
    paper_points_input: list[np.ndarray] | None = None,
    feature_curves: list[FeatureCurve] | None = None,
    outer_overrides: dict[int, FeatureCurve] | None = None,
    conditioned_curves: list[ConditionedCurve] | None = None,
) -> tuple[bool, str | None, int, int]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return False, "rhino3dm unavailable; wrote Rhino-importable 3-D OBJ polylines instead", 0, 0
    if not hasattr(rhino3dm, "File3dm"):
        return False, "rhino3dm is incomplete or incompatible; wrote Rhino-importable 3-D OBJ polylines instead", 0, 0
    model = rhino3dm.File3dm()
    layer_specs = {
        "DECK_PRIMARY_OUTER": ("AUTODECK::DECK_PRIMARY_OUTER", (255, 145, 0, 255), True),
        "DECK_SECONDARY": ("AUTODECK::DECK_SECONDARY", (35, 145, 255, 255), True),
        "AUTODECK::OBSTACLES_PRIMARY": ("AUTODECK::OBSTACLES_PRIMARY", (255, 40, 40, 255), True),
        "AUTODECK::NONSKID_MEDIUM": ("AUTODECK::NONSKID_MEDIUM", (40, 220, 80, 255), True),
        "AUTODECK::SEAMS_HIGH_CONFIDENCE": ("AUTODECK::SEAMS_HIGH_CONFIDENCE", (180, 55, 255, 255), True),
        "AUTODECK::HATCH_OR_CLOSED_SEAM": ("AUTODECK::HATCH_OR_CLOSED_SEAM", (130, 40, 220, 255), True),
        "AUTODECK_CALIBRATION::NONSKID_LOOSE": ("AUTODECK_CALIBRATION::NONSKID_LOOSE", (90, 255, 130, 255), False),
        "AUTODECK_CALIBRATION::NONSKID_STRICT": ("AUTODECK_CALIBRATION::NONSKID_STRICT", (20, 145, 60, 255), False),
        "AUTODECK_CALIBRATION::OBSTACLE_LOOSE": ("AUTODECK_CALIBRATION::OBSTACLE_LOOSE", (255, 125, 125, 255), False),
        "AUTODECK_CALIBRATION::OBSTACLE_STRICT": ("AUTODECK_CALIBRATION::OBSTACLE_STRICT", (170, 0, 0, 255), False),
        "AUTODECK_CALIBRATION::SEAMS_LOW_CONFIDENCE": ("AUTODECK_CALIBRATION::SEAMS_LOW_CONFIDENCE", (230, 170, 255, 255), False),
        "AUTODECK_DEBUG::RAW_RELIEF_SIGNAL": ("AUTODECK_DEBUG::RAW_RELIEF_SIGNAL", (150, 150, 150, 255), False),
        "AUTODECK_DEBUG::RAW_FEATURE_DENSITY": ("AUTODECK_DEBUG::RAW_FEATURE_DENSITY", (150, 150, 150, 255), False),
        "AUTODECK_DEBUG::HEIGHT_RESIDUAL": ("AUTODECK_DEBUG::HEIGHT_RESIDUAL", (150, 150, 150, 255), False),
        "AUTODECK_DEBUG::SLOPE_MASK": ("AUTODECK_DEBUG::SLOPE_MASK", (150, 150, 150, 255), False),
        "AUTODECK_DEBUG::MICRO_VOID_REJECTIONS": ("AUTODECK_DEBUG::MICRO_VOID_REJECTIONS", (150, 150, 150, 255), False),
        "AUTODECK_DEBUG::RAW_STRUCTURAL_SCORE": ("AUTODECK_DEBUG::RAW_STRUCTURAL_SCORE", (150, 150, 150, 255), False),
        "AUTODECK_DEBUG::OBSTACLE_ALTERNATIVES": ("AUTODECK_DEBUG::OBSTACLE_ALTERNATIVES", (255, 230, 40, 255), False),
        "DECK_SEEDS": ("AUTODECK_DEBUG::DECK_SEEDS", (30, 255, 80, 255), False),
        "MARKER_PAPERS": ("AUTODECK_DEBUG::MARKER_PAPERS", (0, 220, 255, 255), False),
        "SMOOTHING_DEVIATION": ("AUTODECK::DEBUG::SMOOTHING_DEVIATION", (255, 230, 40, 255), False),
    }
    for item in conditioned_curves or []:
        raw_layer = _raw_3d_layer(item.raw_curve.layer)
        smooth_layer = item.smoothed_curve.layer
        layer_specs.setdefault(raw_layer, (raw_layer, (145, 145, 145, 255), False))
        layer_specs.setdefault(smooth_layer, (smooth_layer, (255, 145, 0, 255), True))
    layer_indices: dict[str, int] = {}
    for key, (name, color, visible) in layer_specs.items():
        layer = rhino3dm.Layer(); layer.Name = name; layer.Visible = visible
        try:
            layer.Color = color
        except Exception:
            pass
        layer_indices[key] = model.Layers.Add(layer)

    curve_count = 0

    def add_polyline(points: np.ndarray, layer_name: str, object_name: str) -> None:
        nonlocal curve_count
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices[layer_name]; attrs.Name = object_name
        polyline = rhino3dm.Polyline([rhino3dm.Point3d(*point) for point in points])
        model.Objects.AddPolyline(polyline, attrs)
        curve_count += 1

    if conditioned_curves:
        for item in conditioned_curves:
            raw_layer = _raw_3d_layer(item.raw_curve.layer)
            add_polyline(item.raw_curve.points_input, raw_layer, item.raw_curve_id)
            add_polyline(item.smoothed_curve.points_input, item.smoothed_curve.layer, item.smoothed_curve_id)
            raw = item.resampled_points_input
            smooth = item.smoothed_curve.points_input
            if len(raw) and len(smooth):
                for debug_index in np.linspace(0, len(raw) - 1, min(5, len(raw)), dtype=np.int64):
                    nearest = int(np.argmin(np.linalg.norm(smooth[:, :2] - raw[debug_index, :2], axis=1)))
                    add_polyline(
                        np.vstack((raw[debug_index], smooth[nearest])),
                        "SMOOTHING_DEVIATION",
                        f"deviation_{item.raw_curve_id}_{debug_index:05d}",
                    )
    else:
        for candidate in candidates:
            override = (outer_overrides or {}).get(candidate.candidate_id)
            if override is not None:
                layer_name = "DECK_PRIMARY_OUTER" if candidate.is_primary else "DECK_SECONDARY"
                add_polyline(
                    override.points_input,
                    layer_name,
                    f"deck_{'primary' if candidate.is_primary else 'secondary'}_{candidate.candidate_id:03d}_raster_true",
                )
                continue
            for loop_index, loop in enumerate(candidate.outer_loops):
                layer_name = "DECK_PRIMARY_OUTER" if candidate.is_primary else "DECK_SECONDARY"
                master = loop_points_in_input_coordinates(loop, analysis, original)
                base_name = f"deck_{'primary' if candidate.is_primary else 'secondary'}_{candidate.candidate_id:03d}_{loop_index:03d}"
                add_polyline(master, layer_name, f"{base_name}_true")
    for curve in feature_curves or []:
        if len(curve.points_input) < 2:
            continue
        if conditioned_curves and curve.layer.startswith("AUTODECK::"):
            continue
        layer_name = curve.layer if curve.layer in layer_indices else "AUTODECK_DEBUG::RAW_RELIEF_SIGNAL"
        add_polyline(curve.points_input, layer_name, curve.name)
    for index, point in enumerate(seed_points_input or []):
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices["DECK_SEEDS"]; attrs.Name = f"deck_seed_{index:03d}"
        model.Objects.AddPoint(rhino3dm.Point3d(*point), attrs)
    for index, point in enumerate(paper_points_input or []):
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer_indices["MARKER_PAPERS"]; attrs.Name = f"marker_paper_{index:03d}"
        model.Objects.AddPoint(rhino3dm.Point3d(*point), attrs)
    if not model.Write(str(path), 8):
        return False, "rhino3dm failed to write verification.3dm", 0, 0
    return True, None, len(model.Objects), curve_count
