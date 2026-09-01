from __future__ import annotations

"""Focused V0.3.1 reports and stage-by-stage perimeter length evidence."""

from pathlib import Path
from typing import Any

import numpy as np

from .conditioning import curve_length
from .curve_fit import ManufacturingCurve
from .development import DevelopmentResult
from .models import FlattenedCurve


def make_length_audit(
    curve_id: str,
    conditioned_length_mm: float,
    mapped_source_mesh_points_mm: np.ndarray,
    mapped_development_base_points_mm: np.ndarray,
    flat_raw_points_mm: np.ndarray,
    cam_points_mm: np.ndarray | None,
) -> dict[str, Any]:
    stages = {
        "conditioned_3d_mm": float(conditioned_length_mm),
        "mapped_source_mesh_3d_mm": curve_length(mapped_source_mesh_points_mm),
        "mapped_development_base_3d_mm": curve_length(mapped_development_base_points_mm),
        "flat_raw_developed_mm": curve_length(flat_raw_points_mm),
        "cam_fit_mm": None if cam_points_mm is None else curve_length(cam_points_mm),
    }
    order = list(stages)
    transitions: list[dict[str, Any]] = []
    for before, after in zip(order[:-1], order[1:]):
        before_length = stages[before]; after_length = stages[after]
        if before_length is None or after_length is None:
            continue
        change = float(after_length - before_length)
        transitions.append({
            "from": before,
            "to": after,
            "change_mm": change,
            "change_percent": 100.0 * change / max(float(before_length), 1e-12),
        })
    largest = max(transitions, key=lambda item: abs(float(item["change_percent"])), default=None)
    return {
        "curve_id": curve_id,
        "stages": stages,
        "transitions": transitions,
        "largest_absolute_transition": largest,
        "diagnosis": (
            "No complete stage chain was available."
            if largest is None else
            f"The largest perimeter-length change occurs from {largest['from']} to {largest['to']} "
            f"({float(largest['change_percent']):+.4f}%)."
        ),
    }


def _status(manufacturing: list[ManufacturingCurve]) -> str:
    if not manufacturing or not any(curve.status != "INVALID" for curve in manufacturing):
        return "INVALID"
    return "NEEDS_REVIEW" if any(curve.status != "GOOD" for curve in manufacturing) else "GOOD"


def _attempt_cell(attempt: dict[str, Any], model: str) -> str:
    if not attempt.get("attempted", model == "POLYLINE"):
        return f"not attempted — {attempt.get('rejection_reason', attempt.get('reason', 'higher-priority model accepted'))}"
    outcome = "ACCEPT" if attempt.get("accepted", False) else "REJECT"
    values: list[str] = []
    if model == "SPLINE":
        values.extend([
            f"degree={attempt.get('degree', 'n/a')}",
            f"CP={attempt.get('control_point_count', 'n/a')}",
            f"knots={attempt.get('knot_count', 'n/a')}",
        ])
    if attempt.get("max_error_mm") is not None:
        values.append(f"max={float(attempt['max_error_mm']):.4f} mm")
    if attempt.get("rms_error_mm") is not None:
        values.append(f"RMS={float(attempt['rms_error_mm']):.4f} mm")
    if attempt.get("forbidden_side_violation_count") is not None:
        values.append(f"forbidden={int(attempt['forbidden_side_violation_count'])}")
    reason = attempt.get("acceptance_reason") or attempt.get("rejection_reason") or attempt.get("reason")
    if reason:
        values.append(str(reason))
    return f"{outcome} — " + "; ".join(values)


def write_curve_fit_report(
    path: Path,
    manufacturing: list[ManufacturingCurve],
    config: dict[str, Any],
) -> None:
    fit_cfg = config["manufacturing_fit"]
    lines = [
        "# AutoDeck V0.3.1 curve-fit report",
        "",
        "> Native mathematical CAM geometry audit. Compatibility polylines are not used to judge smoothness.",
        "",
        "## Fit policy",
        "",
        f"- Target / absolute corridor: {float(fit_cfg['target_fit_deviation_mm']):.3f} / {float(fit_cfg['absolute_max_fit_deviation_mm']):.3f} mm",
        f"- Hard forbidden-side epsilon / constraint margin: {float(fit_cfg['forbidden_side_epsilon_mm']):.3f} / {float(fit_cfg['spline_safe_constraint_margin_mm']):.3f} mm",
        f"- Fairness weight: {float(fit_cfg['spline_fairness_weight']):.6g}",
        "- The 7.00 mm absolute ceiling is a measured KeyWest review bound, not a production standard: the clean 5 mm-development-mesh rerun and hard one-sided rule require about 6.97 mm at an isolated raster spike to replace all dense fallbacks with safe smooth splines. Any use of this ceiling remains NEEDS_REVIEW.",
        "",
    ]
    for curve in manufacturing:
        metrics = curve.metrics; counts = metrics.get("span_type_counts", {})
        lines.extend([
            f"## {curve.cam_curve_id}", "",
            f"- Status: **{curve.status}**",
            f"- Protected corners / logical spans: {metrics.get('protected_corner_count', 0)} / {metrics.get('logical_physical_span_count', 0)}",
            f"- LINE / ARC / CIRCLE / SPLINE / POLYLINE: {counts.get('LINE', 0)} / {counts.get('ARC', 0)} / {counts.get('CIRCLE', 0)} / {counts.get('SPLINE', 0)} / {counts.get('POLYLINE', 0)}",
            f"- NURBS control points: {metrics.get('nurbs_control_point_count', 0)}",
            f"- Polyline fallback fraction: {float(metrics.get('polyline_fallback_fraction', 0.0)):.6f}",
            f"- Maximum join gap: {float(metrics.get('maximum_join_gap_mm', 0.0)):.9f} mm",
            f"- Forbidden violations / self-intersection: {metrics.get('forbidden_side_violation_count', 0)} / {metrics.get('self_intersection', False)}",
            "",
            "| Span | Model | Result and measured reason |",
            "|---:|---|---|",
        ])
        for span_index, span in enumerate(curve.spans, 1):
            attempts = span.metrics.get("attempts", {})
            for model in ("LINE", "ARC", "SPLINE", "POLYLINE"):
                lines.append(f"| {span_index} | {model} | {_attempt_cell(attempts.get(model, {}), model)} |")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_development_report(
    path: Path,
    development: dict[int, DevelopmentResult],
    length_audits: list[dict[str, Any]],
) -> None:
    lines = ["# AutoDeck V0.3.1 development report", "", "## Patch geometry", ""]
    for patch_id, result in development.items():
        strain = result.distortion_metrics.get("absolute_edge_strain_percent", {})
        lines.extend([
            f"### Patch {patch_id}", "",
            f"- Strategy / status: `{result.strategy}` / **{result.status}**",
            f"- Vertices / triangles: {len(result.mesh.vertices_mm):,} / {len(result.mesh.faces):,}",
            f"- Base surface: `{result.mesh.base_surface_metrics}`",
            f"- Absolute edge strain P95 / max: {float(strain.get('p95', 0.0)):.6f}% / {float(strain.get('maximum', 0.0)):.6f}%",
            "",
        ])
    lines.extend(["## Perimeter length lineage", ""])
    for audit in length_audits:
        stages = audit["stages"]
        lines.extend([
            f"### {audit['curve_id']}", "",
            "| Stage | Length (mm) |",
            "|---|---:|",
            *[f"| {name} | {float(value):.6f} |" for name, value in stages.items() if value is not None],
            "", "| Transition | Change (mm) | Change (%) |", "|---|---:|---:|",
            *[
                f"| {item['from']} → {item['to']} | {float(item['change_mm']):+.6f} | {float(item['change_percent']):+.6f}% |"
                for item in audit["transitions"]
            ],
            "", f"- Diagnosis: {audit['diagnosis']}", "",
        ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_flatten_report(
    path: Path,
    flat_raw: list[FlattenedCurve],
    dxf: dict[str, Any],
    flat_preview: dict[str, Any],
) -> None:
    polyline = dxf.get("polyline", {})
    lines = [
        "# AutoDeck V0.3.1 flatten/export report", "",
        "> TEST / VERIFICATION GEOMETRY — no manufacturing offset has been applied.", "",
        f"- Flat raw curve count: {len(flat_raw)}",
        f"- Flat preview written / objects: {flat_preview.get('written', False)} / {flat_preview.get('object_count', 0)}",
        f"- Native DXF status / entities: {dxf.get('status', 'NOT_EVALUATED')} / {dxf.get('native', {}).get('total_entity_count', 0)}",
        f"- Compatibility tessellation: {polyline.get('tessellation_method', 'n/a')}; chord error={polyline.get('chord_error_mm', 'n/a')} mm; maximum segment cap={polyline.get('maximum_segment_length_mm', 'n/a')} mm",
        f"- Compatibility vertex count: {polyline.get('total_vertex_count', 0)}",
        "", "| Flat raw curve | Status | Points | Length (mm) |", "|---|---|---:|---:|",
    ]
    for curve in flat_raw:
        lines.append(f"| {curve.flattened_curve_id} | {curve.status} | {len(curve.points_mm)} | {curve_length(curve.points_mm):.6f} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manufacturing_report(
    path: Path,
    manufacturing: list[ManufacturingCurve],
    dxf: dict[str, Any],
    warnings: list[str],
) -> None:
    primary = next((curve for curve in manufacturing if "DECK_PRIMARY" in curve.source.source.raw_curve.layer.upper()), None)
    lines = [
        "# AutoDeck V0.3.1 manufacturing summary", "",
        "> TEST / VERIFICATION GEOMETRY ONLY — not CNC-ready.", "",
        f"- Manufacturing fit status: **{_status(manufacturing)}**",
        f"- Native DXF status: **{dxf.get('status', 'NOT_EVALUATED')}**",
    ]
    if primary is not None:
        metrics = primary.metrics; counts = metrics.get("span_type_counts", {}); deviation = metrics.get("deviation", {})
        lines.extend([
            "", "## Primary KeyWest motion-quality comparison", "",
            "| Metric | Previous V0.3 | V0.3.1 |", "|---|---:|---:|",
            f"| Protected corners | 9 | {metrics.get('protected_corner_count', 0)} |",
            f"| Logical physical spans | 9 | {metrics.get('logical_physical_span_count', 0)} |",
            f"| Approx. controls/vertices | 946 | {metrics.get('control_point_count', 0)} |",
            f"| LINE / ARC / SPLINE / POLYLINE | 0 / 0 / 0 / 9 | {counts.get('LINE', 0)} / {counts.get('ARC', 0)} / {counts.get('SPLINE', 0)} / {counts.get('POLYLINE', 0)} |",
            f"| Max / P95 deviation (mm) | n/a | {float(deviation.get('maximum_mm', 0.0)):.6f} / {float(deviation.get('p95_mm', 0.0)):.6f} |",
            f"| Forbidden violations | n/a | {metrics.get('forbidden_side_violation_count', 0)} |",
            f"| Maximum join gap (mm) | n/a | {float(metrics.get('maximum_join_gap_mm', 0.0)):.9f} |",
            f"| Polyline fallback fraction | 1.0 | {float(metrics.get('polyline_fallback_fraction', 0.0)):.6f} |",
        ])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in warnings) if warnings else lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_v031_reports(
    output_dir: Path,
    development: dict[int, DevelopmentResult],
    flat_raw: list[FlattenedCurve],
    manufacturing: list[ManufacturingCurve],
    dxf: dict[str, Any],
    flat_preview: dict[str, Any],
    warnings: list[str],
    length_audits: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    write_manufacturing_report(output_dir / "manufacturing_report.md", manufacturing, dxf, warnings)
    write_development_report(output_dir / "development_report.md", development, length_audits)
    write_curve_fit_report(output_dir / "curve_fit_report.md", manufacturing, config)
    write_flatten_report(output_dir / "flatten_report.md", flat_raw, dxf, flat_preview)
