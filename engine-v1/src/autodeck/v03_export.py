from __future__ import annotations

"""Rhino verification exports for V0.3 development and CAM lineage."""

from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from .curve_fit import ManufacturingCurve
from .development import DevelopmentResult
from .models import FeatureCurve, FlattenedCurve
from .polyarc import PolyarcCurve, PolyarcPrimitive
from .robust_reference import RobustReferenceCurve


def _add_layer(model: Any, rhino3dm: Any, name: str, color: tuple[int, int, int, int], visible: bool = True) -> int:
    for index, existing in enumerate(model.Layers):
        if existing.Name == name:
            return index
    layer = rhino3dm.Layer(); layer.Name = name; layer.Visible = visible
    try:
        layer.Color = color
    except Exception:
        pass
    return model.Layers.Add(layer)


def _add_polyline(model: Any, rhino3dm: Any, points: np.ndarray, layer: int, name: str) -> bool:
    if len(points) < 2:
        return False
    attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer; attrs.Name = name
    polyline = rhino3dm.Polyline([rhino3dm.Point3d(float(p[0]), float(p[1]), float(p[2])) for p in points])
    model.Objects.AddPolyline(polyline, attrs)
    return True


def _add_native_span(model: Any, rhino3dm: Any, span: Any, layer: int, name: str) -> bool:
    attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer; attrs.Name = name
    if span.kind == "LINE":
        start, end = span.sampled_points_mm[0], span.sampled_points_mm[-1]
        model.Objects.AddLine(
            rhino3dm.Point3d(float(start[0]), float(start[1]), 0.0),
            rhino3dm.Point3d(float(end[0]), float(end[1]), 0.0), attrs,
        )
        return True
    if span.kind == "ARC":
        points = span.sampled_points_mm
        arc = rhino3dm.Arc(
            rhino3dm.Point3d(float(points[0, 0]), float(points[0, 1]), 0.0),
            rhino3dm.Point3d(float(points[len(points) // 2, 0]), float(points[len(points) // 2, 1]), 0.0),
            rhino3dm.Point3d(float(points[-1, 0]), float(points[-1, 1]), 0.0),
        )
        model.Objects.AddArc(arc, attrs)
        return True
    if span.kind == "CIRCLE":
        center = span.center_mm
        circle = rhino3dm.Circle(rhino3dm.Point3d(float(center[0]), float(center[1]), 0.0), float(span.radius_mm))
        model.Objects.AddCircle(circle, attrs)
        return True
    if span.kind == "SPLINE":
        curve = rhino3dm.NurbsCurve(int(span.degree), int(len(span.control_points_mm)))
        for index, point in enumerate(span.control_points_mm):
            curve.Points[index] = rhino3dm.Point4d(float(point[0]), float(point[1]), 0.0, 1.0)
        # openNURBS omits the two mathematically superfluous end knots.
        for index, knot in enumerate(np.asarray(span.knots, dtype=np.float64)[1:-1]):
            curve.Knots[index] = float(knot)
        model.Objects.AddCurve(curve, attrs)
        return True
    return _add_polyline(model, rhino3dm, span.sampled_points_mm, layer, name)


def _add_polyarc_primitive(
    model: Any,
    rhino3dm: Any,
    primitive: PolyarcPrimitive,
    layer: int,
    name: str,
) -> bool:
    attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer; attrs.Name = name
    if primitive.kind == "LINE":
        model.Objects.AddLine(
            rhino3dm.Point3d(float(primitive.start[0]), float(primitive.start[1]), 0.0),
            rhino3dm.Point3d(float(primitive.end[0]), float(primitive.end[1]), 0.0),
            attrs,
        )
        return True
    sampled = primitive.sample(max(0.25, primitive.length_mm / 64.0))
    arc = rhino3dm.Arc(
        rhino3dm.Point3d(float(sampled[0, 0]), float(sampled[0, 1]), 0.0),
        rhino3dm.Point3d(float(sampled[len(sampled) // 2, 0]), float(sampled[len(sampled) // 2, 1]), 0.0),
        rhino3dm.Point3d(float(sampled[-1, 0]), float(sampled[-1, 1]), 0.0),
    )
    model.Objects.AddArc(arc, attrs)
    return True


def export_development_preview(
    path: Path,
    results: dict[int, DevelopmentResult],
    flat_raw: list[FlattenedCurve],
) -> dict[str, Any]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return {"written": False, "object_count": 0, "warning": "rhino3dm unavailable"}
    model = rhino3dm.File3dm()
    mesh_layer = _add_layer(model, rhino3dm, "AUTODECK::DEVELOPMENT::PATCH_MESH", (80, 120, 180, 255), True)
    raw_layer = _add_layer(model, rhino3dm, "AUTODECK::DEVELOPMENT::FLAT_RAW", (255, 145, 0, 255), True)
    mesh_count = 0; curve_count = 0
    for patch_id, result in results.items():
        if not len(result.uv_mm):
            continue
        mesh = rhino3dm.Mesh()
        for u, v in result.uv_mm:
            mesh.Vertices.Add(float(u), float(v), 0.0)
        for a, b, c in result.mesh.faces:
            mesh.Faces.AddFace(int(a), int(b), int(c))
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = mesh_layer; attrs.Name = f"development_patch_{patch_id:03d}_{result.strategy}"
        model.Objects.AddMesh(mesh, attrs); mesh_count += 1
    for curve in flat_raw:
        curve_count += int(_add_polyline(model, rhino3dm, curve.points_mm, raw_layer, curve.flattened_curve_id))
    written = bool(model.Write(str(path), 8))
    return {"written": written, "mesh_object_count": mesh_count, "curve_object_count": curve_count,
            "object_count": len(model.Objects) if written else 0}


def export_flattened_preview(
    path: Path,
    flat_raw: list[FlattenedCurve],
    cam_curves: list[ManufacturingCurve],
    polyarc_curves: list[PolyarcCurve] | None = None,
    robust_references: list[RobustReferenceCurve] | None = None,
) -> dict[str, Any]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return {"written": False, "object_count": 0, "warning": "rhino3dm unavailable"}
    model = rhino3dm.File3dm()
    raw_layer = _add_layer(model, rhino3dm, "FLAT_RAW_DEVELOPED", (145, 145, 145, 255), True)
    robust_layer = _add_layer(model, rhino3dm, "FLAT_ROBUST_REFERENCE", (255, 190, 40, 255), True)
    _add_layer(model, rhino3dm, "FLAT_RAW", (145, 145, 145, 255), False)
    old_style_layer = _add_layer(model, rhino3dm, "CAM_POLYLINE_OLD_STYLE", (110, 110, 110, 255), False)
    cam_layer = _add_layer(model, rhino3dm, "CAM_NATIVE_SMOOTH", (255, 145, 0, 255), False)
    spline_v031_layer = _add_layer(model, rhino3dm, "CAM_SPLINE_V031", (255, 145, 0, 255), False)
    polyarc_layer = _add_layer(model, rhino3dm, "FLAT_CAM_LINEARC", (0, 230, 120, 255), True)
    invalid_polyarc_layer = _add_layer(
        model, rhino3dm, "FLAT_CAM_LINEARC_INVALID_REVIEW", (255, 0, 180, 255), True
    )
    _add_layer(model, rhino3dm, "CAM_POLYARC_V033", (0, 230, 120, 255), False)
    _add_layer(model, rhino3dm, "CAM_POLYARC_V032", (40, 150, 100, 255), False)
    fallback_layer = _add_layer(model, rhino3dm, "CAM_POLYLINE_FALLBACK_NEEDS_REVIEW", (255, 0, 180, 255), True)
    corner_layer = _add_layer(model, rhino3dm, "PROTECTED_HARD_CORNERS", (255, 40, 40, 255), True)
    _add_layer(model, rhino3dm, "HARD_CORNERS", (255, 40, 40, 255), False)
    _add_layer(model, rhino3dm, "PROTECTED_CORNERS", (255, 40, 40, 255), False)
    soft_join_layer = _add_layer(model, rhino3dm, "POLYARC_SOFT_JOINS", (60, 180, 255, 255), False)
    _add_layer(model, rhino3dm, "SOFT_TANGENT_JOINS", (60, 180, 255, 255), False)
    corridor_layer = _add_layer(model, rhino3dm, "FIT_CORRIDOR", (190, 90, 255, 255), False)
    maximum_layer = _add_layer(model, rhino3dm, "MAX_DEVIATION_POINTS", (255, 255, 0, 255), False)
    _add_layer(model, rhino3dm, "FIT_DEVIATION_DEBUG", (255, 255, 0, 255), False)
    span_layer = _add_layer(model, rhino3dm, "AUTODECK_DEBUG::FIT_SPANS", (60, 220, 255, 255), False)
    raw_count = 0; robust_count = 0; old_style_count = 0; cam_count = 0; fallback_count = 0; corner_count = 0; span_count = 0
    polyarc_count = 0; invalid_polyarc_count = 0; soft_join_count = 0; spline_v031_count = 0
    corridor_count = 0; maximum_point_count = 0
    for curve in flat_raw:
        raw_count += int(_add_polyline(model, rhino3dm, curve.points_mm, raw_layer, curve.flattened_curve_id))
    for reference in robust_references or []:
        points = np.column_stack((reference.points[:, :2], np.zeros(len(reference.points))))
        robust_count += int(_add_polyline(model, rhino3dm, points, robust_layer, reference.curve_id))
    for curve in cam_curves:
        if curve.status == "INVALID":
            continue
        old_style_count += int(_add_polyline(
            model, rhino3dm, curve.points_mm, old_style_layer, f"{curve.cam_curve_id}_polyline_reference",
        ))
        for index, point in enumerate(curve.anchor_points_mm):
            attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = corner_layer; attrs.Name = f"{curve.cam_curve_id}_corner_{index:03d}"
            model.Objects.AddPoint(rhino3dm.Point3d(float(point[0]), float(point[1]), 0.0), attrs); corner_count += 1
        for index, span in enumerate(curve.spans, 1):
            if span.kind == "POLYLINE":
                fallback_count += int(_add_native_span(
                    model, rhino3dm, span, fallback_layer, f"{curve.cam_curve_id}_{index:03d}_POLYLINE_FALLBACK",
                ))
            else:
                cam_count += int(_add_native_span(
                    model, rhino3dm, span, cam_layer, f"{curve.cam_curve_id}_{index:03d}_{span.kind}",
                ))
                spline_v031_count += int(_add_native_span(
                    model, rhino3dm, span, spline_v031_layer,
                    f"{curve.cam_curve_id}_{index:03d}_{span.kind}_V031_REFERENCE",
                ))
            span_count += int(_add_polyline(model, rhino3dm, span.sampled_points_mm, span_layer, f"{curve.cam_curve_id}_{index:03d}_{span.kind}"))
    for curve in polyarc_curves or []:
        curve_id = f"polyarc-{curve.candidate_id:04d}"
        display_layer = invalid_polyarc_layer if curve.status == "INVALID" else polyarc_layer
        for index, primitive in enumerate(curve.primitives, 1):
            added = int(_add_polyarc_primitive(
                model,
                rhino3dm,
                primitive,
                display_layer,
                f"{curve_id}_{index:03d}_{primitive.kind}",
            ))
            if curve.status == "INVALID":
                invalid_polyarc_count += added
            else:
                polyarc_count += added
        for join in curve.joins:
            if join.join_type != "SMOOTH_G1":
                continue
            attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = soft_join_layer
            attrs.Name = f"{curve_id}_soft_join_{join.index:03d}"
            model.Objects.AddPoint(
                rhino3dm.Point3d(float(join.point[0]), float(join.point[1]), 0.0), attrs,
            )
            soft_join_count += 1
        if curve.is_closed and len(curve.raw_points) >= 3:
            try:
                from shapely.geometry import Polygon

                polygon = Polygon(curve.raw_points)
                for offset in (-float(curve.metrics["fit_tolerance_mm"]), float(curve.metrics["fit_tolerance_mm"])):
                    boundary = polygon.buffer(offset).boundary
                    geometries = list(boundary.geoms) if hasattr(boundary, "geoms") else [boundary]
                    for boundary_index, geometry in enumerate(geometries):
                        coordinates = np.asarray(geometry.coords, dtype=float)
                        points3 = np.column_stack((coordinates[:, :2], np.zeros(len(coordinates))))
                        corridor_count += int(_add_polyline(
                            model, rhino3dm, points3, corridor_layer,
                            f"{curve_id}_corridor_{offset:+.3f}_{boundary_index:02d}",
                        ))
            except (ValueError, TypeError):
                pass
        if len(curve.raw_points) and len(curve.sampled_points):
            distance, nearest = cKDTree(curve.sampled_points[:, :2]).query(curve.raw_points[:, :2])
            worst = int(np.argmax(distance))
            for suffix, point in (
                ("raw", curve.raw_points[worst]),
                ("fit", curve.sampled_points[int(nearest[worst])]),
            ):
                attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = maximum_layer
                attrs.Name = f"{curve_id}_max_deviation_{suffix}"
                model.Objects.AddPoint(rhino3dm.Point3d(float(point[0]), float(point[1]), 0.0), attrs)
                maximum_point_count += 1
    written = bool(model.Write(str(path), 8))
    return {
        "written": written, "raw_curve_count": raw_count,
        "robust_reference_curve_count": robust_count,
        "old_style_polyline_count": old_style_count,
        "native_smooth_object_count": cam_count,
        "spline_v031_object_count": spline_v031_count,
        "polyarc_v032_object_count": polyarc_count,
        "polyarc_v033_object_count": polyarc_count,
        "invalid_polyarc_review_object_count": invalid_polyarc_count,
        "polyarc_soft_join_point_count": soft_join_count,
        "fit_corridor_curve_count": corridor_count,
        "maximum_deviation_point_count": maximum_point_count,
        "polyline_fallback_object_count": fallback_count,
        "cam_curve_count": cam_count,
        "protected_corner_point_count": corner_count, "debug_span_count": span_count,
        "object_count": len(model.Objects) if written else 0,
        "all_geometry_z_zero": True,
    }


def append_backprojected_to_verification(
    path: Path,
    curves: list[FeatureCurve],
    layer_name: str = "AUTODECK::CAM_BACKPROJECTED_3D",
    dense_layer_name: str = "AUTODECK_DEBUG::BACKPROJECTED_DENSE_SAMPLES",
) -> dict[str, Any]:
    try:
        import rhino3dm  # type: ignore
    except ImportError:
        return {"written": False, "curve_count": 0, "warning": "rhino3dm unavailable"}
    model = rhino3dm.File3dm.Read(str(path)) if path.exists() else rhino3dm.File3dm()
    if model is None:
        return {"written": False, "curve_count": 0, "warning": "verification.3dm could not be reopened"}
    layer = _add_layer(model, rhino3dm, layer_name, (0, 255, 255, 255), True)
    dense_layer = _add_layer(model, rhino3dm, dense_layer_name, (80, 120, 150, 255), False)
    existing_ids = [
        item.Attributes.Id for item in model.Objects
        if item.Attributes.LayerIndex in {layer, dense_layer}
    ]
    for object_id in existing_ids:
        model.Objects.Delete(object_id)
    count = 0; dense_count = 0
    for curve in curves:
        points = np.asarray(curve.points_input, dtype=float)
        attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = layer; attrs.Name = curve.name
        display_added = False
        if len(points) >= 4:
            try:
                nurbs = rhino3dm.NurbsCurve.CreateInterpolatedCurve(
                    [rhino3dm.Point3d(float(p[0]), float(p[1]), float(p[2])) for p in points],
                    3,
                )
                if nurbs is not None:
                    model.Objects.AddCurve(nurbs, attrs); display_added = True
            except (AttributeError, RuntimeError, TypeError):
                display_added = False
        if not display_added:
            display_added = _add_polyline(model, rhino3dm, points, layer, curve.name)
        count += int(display_added)
        dense_count += int(_add_polyline(model, rhino3dm, points, dense_layer, curve.name + "_dense_samples"))
    written = bool(model.Write(str(path), 8))
    return {
        "written": written,
        "curve_count": count,
        "smooth_display_curve_count": count,
        "hidden_dense_sample_curve_count": dense_count,
        "object_count": len(model.Objects) if written else 0,
    }
