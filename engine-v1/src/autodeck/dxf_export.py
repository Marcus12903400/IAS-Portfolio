from __future__ import annotations

"""Audited ezdxf native and polyline manufacturing-geometry exports."""

from pathlib import Path
from typing import Any

import numpy as np
from scipy.interpolate import BSpline
from scipy.spatial import cKDTree

from .curve_fit import FitSpan, ManufacturingCurve, _rdp, _sample_polyline
from .polyarc import PolyarcCurve, PolyarcPrimitive


def _safe_layer(name: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "_-" else "_" for character in name.upper())
    return ("AD_CAM_" + cleaned.removeprefix("AD_"))[:64]


def _entity_attributes(layer: str) -> dict[str, Any]:
    return {"layer": layer, "color": 256}


def _tag_entity(entity: Any, curve_id: str, span_index: int) -> None:
    entity.set_xdata("AUTODECK", [(1000, curve_id), (1070, span_index)])


def _add_native_span(modelspace: Any, span: FitSpan, layer: str, curve_id: str, span_index: int, closed_whole: bool) -> Any:
    attributes = _entity_attributes(layer)
    if span.kind == "LINE":
        entity = modelspace.add_line(span.sampled_points_mm[0, :2], span.sampled_points_mm[-1, :2], dxfattribs=attributes)
    elif span.kind == "ARC":
        start = float(span.start_angle_deg); end = float(span.end_angle_deg)
        if span.clockwise:
            start, end = end, start
        entity = modelspace.add_arc(span.center_mm[:2], float(span.radius_mm), start, end, dxfattribs=attributes)
    elif span.kind == "CIRCLE":
        entity = modelspace.add_circle(span.center_mm[:2], float(span.radius_mm), dxfattribs=attributes)
    elif span.kind == "SPLINE":
        entity = modelspace.add_open_spline(
            span.control_points_mm,
            degree=int(span.degree),
            knots=span.knots,
            dxfattribs=attributes,
        )
        if closed_whole:
            entity.closed = True
    else:
        controls = span.control_points_mm
        if closed_whole and len(controls) > 1 and np.linalg.norm(controls[0, :2] - controls[-1, :2]) <= 1e-9:
            controls = controls[:-1]
        entity = modelspace.add_lwpolyline(controls[:, :2], close=closed_whole, dxfattribs=attributes)
    _tag_entity(entity, curve_id, span_index)
    return entity


def _document() -> Any:
    import ezdxf
    from ezdxf import units

    document = ezdxf.new("R2010", setup=True)
    document.units = units.MM
    document.header["$MEASUREMENT"] = 1
    if "AUTODECK" not in document.appids:
        document.appids.add("AUTODECK")
    return document


def _source_lookup(curves: list[ManufacturingCurve]) -> dict[tuple[str, int], np.ndarray]:
    return {
        (curve.cam_curve_id, span_index): span.sampled_points_mm
        for curve in curves
        for span_index, span in enumerate(curve.spans, 1)
    }


def _entity_points(entity: Any, spacing: float) -> np.ndarray:
    entity_type = entity.dxftype()
    if entity_type == "LINE":
        points = np.asarray([entity.dxf.start, entity.dxf.end], dtype=np.float64)
        return _sample_polyline(points, spacing)
    if entity_type == "ARC":
        tool = entity.construction_tool()
        points = list(tool.flattening(max(spacing * 0.25, 0.01)))
        return np.asarray(points, dtype=np.float64)
    if entity_type == "CIRCLE":
        points = list(entity.flattening(max(spacing * 0.25, 0.01)))
        result = np.asarray(points, dtype=np.float64)
        return np.vstack((result, result[:1])) if len(result) and np.linalg.norm(result[0] - result[-1]) > 1e-9 else result
    if entity_type == "SPLINE":
        points = list(entity.construction_tool().flattening(max(spacing * 0.25, 0.01)))
        result = np.asarray(points, dtype=np.float64)
        if entity.closed and len(result) and np.linalg.norm(result[0] - result[-1]) > 1e-9:
            result = np.vstack((result, result[:1]))
        return result
    if entity_type == "LWPOLYLINE":
        # Reconstruct bulged segments explicitly.  DXF virtual ARC entities are
        # normalized to CCW and can lose traversal orientation for negative
        # bulges, which creates false cross-segment chords in a chain audit.
        vertices = list(entity.get_points("xyb"))
        pieces: list[np.ndarray] = []
        segment_count = len(vertices) if entity.closed else max(0, len(vertices) - 1)
        for index in range(segment_count):
            start = np.asarray(vertices[index][:2], dtype=float)
            end = np.asarray(vertices[(index + 1) % len(vertices)][:2], dtype=float)
            bulge = float(vertices[index][2])
            chord_vector = end - start
            chord = float(np.linalg.norm(chord_vector))
            if chord <= 1e-12:
                continue
            if abs(bulge) <= 1e-14:
                count = max(2, int(np.ceil(chord / max(spacing, 0.01))) + 1)
                sampled_2d = np.linspace(start, end, count)
            else:
                sweep = 4.0 * np.arctan(bulge)
                midpoint = 0.5 * (start + end)
                left = np.array([-chord_vector[1], chord_vector[0]]) / chord
                center_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
                center = midpoint + center_offset * left
                radius = float(np.linalg.norm(start - center))
                count = max(3, int(np.ceil(abs(sweep) * radius / max(spacing, 0.01))) + 1)
                start_angle = np.arctan2(start[1] - center[1], start[0] - center[0])
                angles = start_angle + np.linspace(0.0, sweep, count)
                sampled_2d = center[None, :] + radius * np.column_stack((np.cos(angles), np.sin(angles)))
                sampled_2d[0] = start; sampled_2d[-1] = end
            sampled = np.column_stack((sampled_2d, np.zeros(len(sampled_2d))))
            pieces.append(sampled if not pieces else sampled[1:])
        if pieces:
            result = np.vstack(pieces)
            if entity.closed and np.linalg.norm(result[0, :2] - result[-1, :2]) > 1e-9:
                result = np.vstack((result, result[:1]))
            return result
        points = np.asarray([[point[0], point[1], 0.0] for point in entity.get_points()], dtype=np.float64)
        return _sample_polyline(points, spacing, bool(entity.closed))
    return np.empty((0, 3))


def _deviation(a: np.ndarray, b: np.ndarray, spacing: float) -> float:
    if not len(a) or not len(b):
        return float("inf")
    a2 = _sample_polyline(np.asarray(a), spacing)[:, :2]
    b2 = _sample_polyline(np.asarray(b), spacing)[:, :2]
    return float(max(cKDTree(a2).query(b2, workers=-1)[0].max(), cKDTree(b2).query(a2, workers=-1)[0].max()))


def _point_chord_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    delta = end - start
    denominator = float(np.dot(delta, delta))
    if denominator <= 1e-18:
        return float(np.linalg.norm(point - start))
    parameter = float(np.clip(np.dot(point - start, delta) / denominator, 0.0, 1.0))
    return float(np.linalg.norm(point - (start + parameter * delta)))


def _tessellate_spline(span: FitSpan, chord_error_mm: float, maximum_segment_mm: float) -> np.ndarray:
    spline = BSpline(span.knots, span.control_points_mm[:, :2], int(span.degree), extrapolate=False)
    values: list[np.ndarray] = []

    def append_interval(start_t: float, end_t: float, start: np.ndarray, end: np.ndarray, depth: int = 0) -> None:
        quarter_t = start_t + 0.25 * (end_t - start_t)
        middle_t = 0.5 * (start_t + end_t)
        three_quarter_t = start_t + 0.75 * (end_t - start_t)
        probes = spline(np.asarray([quarter_t, middle_t, three_quarter_t]))
        error = max(_point_chord_distance(point, start, end) for point in probes)
        if (
            depth >= 24
            or (error <= chord_error_mm and float(np.linalg.norm(end - start)) <= maximum_segment_mm)
        ):
            values.append(end)
            return
        middle = probes[1]
        append_interval(start_t, middle_t, start, middle, depth + 1)
        append_interval(middle_t, end_t, middle, end, depth + 1)

    domain_start = float(span.knots[int(span.degree)])
    domain_end = float(span.knots[-int(span.degree) - 1])
    breaks = np.unique(span.knots[(span.knots >= domain_start) & (span.knots <= domain_end)])
    if len(breaks) < 2:
        breaks = np.asarray([domain_start, domain_end])
    first = np.asarray(spline(breaks[0]), dtype=np.float64)
    values.append(first)
    for start_t, end_t in zip(breaks[:-1], breaks[1:]):
        start = np.asarray(spline(float(start_t)), dtype=np.float64)
        end = np.asarray(spline(float(end_t)), dtype=np.float64)
        append_interval(float(start_t), float(end_t), start, end)
    points = np.asarray(values, dtype=np.float64)
    return np.column_stack((points, np.zeros(len(points))))


def _compatibility_points(span: FitSpan, chord_error_mm: float, maximum_segment_mm: float) -> np.ndarray:
    # Reserve numeric headroom for DXF serialization and independent audit
    # sampling; the reported/requested corridor remains chord_error_mm.
    effective_chord_error = 0.75 * chord_error_mm
    if span.kind == "LINE":
        return _sample_polyline(span.sampled_points_mm[[0, -1]], maximum_segment_mm)
    if span.kind == "SPLINE":
        return _tessellate_spline(span, effective_chord_error, maximum_segment_mm)
    if span.kind in {"ARC", "CIRCLE"} and span.radius_mm is not None:
        radius = max(float(span.radius_mm), 1e-9)
        if span.kind == "CIRCLE":
            sweep = 2.0 * np.pi
            start = 0.0
        else:
            start = np.deg2rad(float(span.start_angle_deg))
            end = np.deg2rad(float(span.end_angle_deg))
            sweep = end - start
            if span.clockwise and sweep > 0.0:
                sweep -= 2.0 * np.pi
            elif not span.clockwise and sweep < 0.0:
                sweep += 2.0 * np.pi
        chord_angle = 2.0 * np.arccos(np.clip(1.0 - effective_chord_error / radius, -1.0, 1.0))
        segment_angle = 2.0 * np.arcsin(np.clip(maximum_segment_mm / (2.0 * radius), 0.0, 1.0))
        allowed = max(min(chord_angle, segment_angle), 1e-6)
        count = max(2, int(np.ceil(abs(sweep) / allowed)) + 1)
        angles = start + np.linspace(0.0, sweep, count)
        center = np.asarray(span.center_mm)[:2]
        return np.column_stack((
            center[0] + radius * np.cos(angles),
            center[1] + radius * np.sin(angles),
            np.zeros(count),
        ))
    simplified = _rdp(span.sampled_points_mm, effective_chord_error)
    return _sample_polyline(simplified, maximum_segment_mm)


def audit_dxf(
    path: Path,
    source: dict[tuple[str, int], np.ndarray],
    geometry_tolerance_mm: float,
    allowed_types: set[str],
) -> dict[str, Any]:
    import ezdxf
    from ezdxf import units

    document = ezdxf.readfile(path)
    auditor = document.audit()
    counts: dict[str, int] = {}
    layer_counts: dict[str, int] = {}
    deviations: list[float] = []
    missing_lineage = 0
    unexpected_types: list[str] = []
    for entity in document.modelspace():
        kind = entity.dxftype(); counts[kind] = counts.get(kind, 0) + 1
        layer = entity.dxf.layer; layer_counts[layer] = layer_counts.get(layer, 0) + 1
        if kind not in allowed_types:
            unexpected_types.append(kind)
        try:
            tags = entity.get_xdata("AUTODECK")
            curve_id = str(tags[0].value); span_index = int(tags[1].value)
        except (ValueError, IndexError, TypeError):
            missing_lineage += 1; continue
        reference = source.get((curve_id, span_index))
        if reference is None:
            missing_lineage += 1; continue
        spacing = max(geometry_tolerance_mm * 0.25, 0.01)
        deviations.append(_deviation(reference, _entity_points(entity, spacing), spacing))
    maximum_deviation = max(deviations, default=float("inf") if source else 0.0)
    valid = bool(
        document.units == units.MM
        and not auditor.errors
        and not unexpected_types
        and missing_lineage == 0
        and len(deviations) == len(source)
        and maximum_deviation <= geometry_tolerance_mm
    )
    return {
        "valid": valid,
        "ezdxf_version": ezdxf.__version__,
        "dxf_version": document.dxfversion,
        "units": "millimetres" if document.units == units.MM else f"code-{document.units}",
        "insunits_code": int(document.units),
        "auditor_error_count": len(auditor.errors),
        "auditor_fix_count": len(auditor.fixes),
        "entity_counts": counts,
        "total_entity_count": int(sum(counts.values())),
        "layer_entity_counts": layer_counts,
        "unexpected_entity_types": sorted(set(unexpected_types)),
        "missing_lineage_entity_count": missing_lineage,
        "audited_span_count": len(deviations),
        "maximum_roundtrip_deviation_mm": maximum_deviation,
        "roundtrip_tolerance_mm": geometry_tolerance_mm,
    }


def write_native_dxf(path: Path, curves: list[ManufacturingCurve], config: dict[str, Any]) -> dict[str, Any]:
    document = _document(); modelspace = document.modelspace()
    exported = [curve for curve in curves if curve.status != "INVALID" and len(curve.points_mm)]
    for curve in exported:
        layer = _safe_layer(curve.layer)
        if layer not in document.layers:
            document.layers.add(layer)
        for span_index, span in enumerate(curve.spans, 1):
            _add_native_span(modelspace, span, layer, curve.cam_curve_id, span_index, curve.closed and len(curve.spans) == 1)
    document.saveas(path)
    tolerance = float(config["dxf"]["roundtrip_geometry_tolerance_mm"])
    audit = audit_dxf(path, _source_lookup(exported), tolerance, {"LINE", "ARC", "CIRCLE", "SPLINE", "LWPOLYLINE"})
    audit.update({"path": path.name, "format": "native", "curve_count": len(exported), "cnc_ready": False,
                  "test_verification_geometry": True})
    return audit


def write_polyline_dxf(path: Path, curves: list[ManufacturingCurve], config: dict[str, Any]) -> dict[str, Any]:
    document = _document(); modelspace = document.modelspace()
    exported = [curve for curve in curves if curve.status != "INVALID" and len(curve.points_mm)]
    source: dict[tuple[str, int], np.ndarray] = {}
    maximum_segment = float(config["dxf"]["polyline_maximum_segment_mm"])
    chord_error = float(config["dxf"]["polyline_chord_error_mm"])
    vertex_counts: list[int] = []
    for curve in exported:
        layer = _safe_layer(curve.layer)
        if layer not in document.layers:
            document.layers.add(layer)
        for span_index, span in enumerate(curve.spans, 1):
            points = _compatibility_points(span, chord_error, maximum_segment)
            vertex_counts.append(int(len(points)))
            closed_whole = curve.closed and len(curve.spans) == 1
            controls = points[:-1] if closed_whole and np.linalg.norm(points[0] - points[-1]) <= 1e-9 else points
            entity = modelspace.add_lwpolyline(controls[:, :2], close=closed_whole, dxfattribs=_entity_attributes(layer))
            _tag_entity(entity, curve.cam_curve_id, span_index)
            source[(curve.cam_curve_id, span_index)] = span.sampled_points_mm
    document.saveas(path)
    tolerance = float(config["dxf"]["roundtrip_geometry_tolerance_mm"])
    audit = audit_dxf(path, source, tolerance, {"LWPOLYLINE"})
    audit.update({"path": path.name, "format": "polyline", "curve_count": len(exported),
                  "tessellation_method": "adaptive chord error",
                  "chord_error_mm": chord_error,
                  "maximum_segment_length_mm": maximum_segment,
                  "total_vertex_count": int(sum(vertex_counts)),
                  "maximum_span_vertex_count": max(vertex_counts, default=0),
                  "cnc_ready": False,
                  "test_verification_geometry": True})
    return audit


def write_scale_check_dxf(path: Path, size_mm: float = 100.0) -> dict[str, Any]:
    document = _document(); layer = "SCALE_CHECK_100MM"
    if layer not in document.layers:
        document.layers.add(layer)
    entity = document.modelspace().add_lwpolyline(
        [(0.0, 0.0), (size_mm, 0.0), (size_mm, size_mm), (0.0, size_mm)],
        close=True, dxfattribs={"layer": layer},
    )
    _tag_entity(entity, "scale-check-100mm", 1)
    document.saveas(path)
    return {"path": path.name, "size_mm": size_mm, "entity_count": 1, "units": "millimetres",
            "separate_from_manufacturing_test_dxf": True}


def write_manufacturing_dxfs(output_dir: Path, curves: list[ManufacturingCurve], config: dict[str, Any]) -> dict[str, Any]:
    native = write_native_dxf(output_dir / "flattened_curves_native.dxf", curves, config)
    polyline = write_polyline_dxf(output_dir / "flattened_curves_polyline.dxf", curves, config)
    scale = write_scale_check_dxf(output_dir / "scale_check.dxf") if bool(config["dxf"].get("write_scale_check", True)) else None
    return {
        "status": "GOOD" if native["valid"] and polyline["valid"] and native["total_entity_count"] > 0 else "INVALID",
        "native": native,
        "polyline": polyline,
        "scale_check": scale,
        "total_entity_count": native["total_entity_count"],
        "entity_counts": native["entity_counts"],
        "layer_entity_counts": native["layer_entity_counts"],
        "units": native["units"],
        "insunits_code": native["insunits_code"],
    }


def _polyarc_curve_id(curve: PolyarcCurve) -> str:
    return f"polyarc-{int(curve.candidate_id):04d}"


def _polyarc_layer(curve: PolyarcCurve) -> str:
    return (
        "CAM_POLYARC_V033"
        if curve.metrics.get("fit_reference") == "ROBUST_PHYSICAL_REFERENCE"
        else "CAM_POLYARC_V032"
    )


def _add_polyarc_arc(modelspace: Any, primitive: PolyarcPrimitive, layer: str, curve_id: str, index: int) -> Any:
    center = np.asarray(primitive.center, dtype=float)
    start = float(np.degrees(np.arctan2(primitive.start[1] - center[1], primitive.start[0] - center[0])))
    end = float(np.degrees(np.arctan2(primitive.end[1] - center[1], primitive.end[0] - center[0])))
    if primitive.signed_sweep_rad < 0.0:
        start, end = end, start
    entity = modelspace.add_arc(
        center,
        float(primitive.radius_mm),
        start,
        end,
        dxfattribs=_entity_attributes(layer),
    )
    _tag_entity(entity, curve_id, index)
    return entity


def write_polyarc_linearc_dxf(path: Path, curves: list[PolyarcCurve], config: dict[str, Any]) -> dict[str, Any]:
    """Write separate native LINE and ARC entities for controller/debug review."""

    document = _document(); modelspace = document.modelspace()
    exported = [curve for curve in curves if curve.status != "INVALID" and curve.primitives]
    source: dict[tuple[str, int], np.ndarray] = {}
    radii: list[float] = []; sweeps: list[float] = []
    for curve in exported:
        layer = _polyarc_layer(curve)
        if layer not in document.layers:
            document.layers.add(layer)
        curve_id = _polyarc_curve_id(curve)
        for index, primitive in enumerate(curve.primitives, 1):
            if primitive.kind == "LINE":
                entity = modelspace.add_line(
                    primitive.start,
                    primitive.end,
                    dxfattribs=_entity_attributes(layer),
                )
                _tag_entity(entity, curve_id, index)
            else:
                _add_polyarc_arc(modelspace, primitive, layer, curve_id, index)
                radii.append(float(primitive.radius_mm or 0.0))
                sweeps.append(float(primitive.sweep_deg))
            source[(curve_id, index)] = primitive.sample(0.25)
    document.saveas(path)
    tolerance = float(config["dxf"]["roundtrip_geometry_tolerance_mm"])
    audit = audit_dxf(path, source, tolerance, {"LINE", "ARC"})
    audit.update({
        "path": path.name,
        "format": "separate LINE/ARC",
        "curve_count": len(exported),
        "arc_radii_mm": radii,
        "arc_signed_sweeps_deg": sweeps,
        "line_arc_only": set(audit["entity_counts"]).issubset({"LINE", "ARC"}),
        "cnc_ready": False,
        "test_verification_geometry": True,
    })
    return audit


def write_polyarc_bulge_dxf(path: Path, curves: list[PolyarcCurve], config: dict[str, Any]) -> dict[str, Any]:
    """Write one bulged LWPOLYLINE for each valid closed perimeter."""

    document = _document(); modelspace = document.modelspace()
    exported = [curve for curve in curves if curve.status != "INVALID" and curve.primitives]
    source: dict[tuple[str, int], np.ndarray] = {}
    bulges: list[float] = []; closed_flags: list[bool] = []
    for curve in exported:
        layer = _polyarc_layer(curve)
        if layer not in document.layers:
            document.layers.add(layer)
        vertices = [
            (
                float(primitive.start[0]),
                float(primitive.start[1]),
                0.0,
                0.0,
                float(primitive.bulge),
            )
            for primitive in curve.primitives
        ]
        if not curve.is_closed:
            endpoint = curve.primitives[-1].end
            vertices.append((float(endpoint[0]), float(endpoint[1]), 0.0, 0.0, 0.0))
        entity = modelspace.add_lwpolyline(
            vertices,
            format="xyseb",
            close=curve.is_closed,
            dxfattribs=_entity_attributes(layer),
        )
        curve_id = _polyarc_curve_id(curve)
        _tag_entity(entity, curve_id, 0)
        source[(curve_id, 0)] = curve.sampled_points
        bulges.extend(float(primitive.bulge) for primitive in curve.primitives)
        closed_flags.append(bool(entity.closed))
    document.saveas(path)
    tolerance = float(config["dxf"]["roundtrip_geometry_tolerance_mm"])
    audit = audit_dxf(path, source, tolerance, {"LWPOLYLINE"})
    audit.update({
        "path": path.name,
        "format": "closed bulged LWPOLYLINE",
        "curve_count": len(exported),
        "bulge_values": bulges,
        "nonzero_bulge_count": sum(abs(value) > 1e-12 for value in bulges),
        "closed_flags": closed_flags,
        "one_entity_per_curve": audit["total_entity_count"] == len(exported),
        "all_closed_perimeters_closed": all(
            flag for flag, curve in zip(closed_flags, exported) if curve.is_closed
        ),
        "cnc_ready": False,
        "test_verification_geometry": True,
    })
    return audit


def write_v032_dxfs(
    output_dir: Path,
    polyarc_curves: list[PolyarcCurve],
    spline_references: list[ManufacturingCurve],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Write and independently round-trip audit all V0.3.2 geometry files."""

    bulge = write_polyarc_bulge_dxf(output_dir / "flattened_curves_polyarc.dxf", polyarc_curves, config)
    linearc = write_polyarc_linearc_dxf(output_dir / "flattened_curves_linearc.dxf", polyarc_curves, config)
    spline = write_native_dxf(output_dir / "flattened_curves_spline_reference.dxf", spline_references, config)
    valid = bool(
        bulge["valid"] and linearc["valid"] and spline["valid"]
        and bulge["one_entity_per_curve"]
        and bulge["all_closed_perimeters_closed"]
        and linearc["line_arc_only"]
    )
    return {
        "status": "GOOD" if valid else "INVALID",
        "polyarc": bulge,
        "linearc": linearc,
        "spline_reference": spline,
        "cnc_ready": False,
        "test_verification_geometry": True,
    }
