from __future__ import annotations

"""V0.3.3 manual-reconstruction evidence report."""

from pathlib import Path
from typing import Any

from .patterns import PATTERN_NAMES, PatternResult
from .polyarc import PolyarcCurve
from .robust_reference import RobustReferenceCurve


def write_v033_report(
    path: Path,
    references: list[RobustReferenceCurve],
    curves: list[PolyarcCurve],
    pattern: PatternResult,
    dxf: dict[str, Any],
    preview: dict[str, Any],
) -> None:
    lines = [
        "# AutoDeck V0.3.3 manual-style LINE + tangent-ARC reconstruction",
        "",
        "> **TEST / VERIFICATION GEOMETRY — NOT CNC-READY.** Real dimensional approval remains mandatory.",
        "",
        "The untouched developed detector contour, the robust physical reference, and the final LINE/ARC CAM chain are separate artifacts. The 3 mm corridor is ±3 mm around the robust reference (6 mm total), not ±6 mm.",
        "",
        "## CAM summary",
        "",
        "| Curve | Status | Reference | Lines | Arcs | Total | Hard corners | Tangent joins | Min / median / max primitive (mm) | Ref max / P95 (mm) | Forbidden |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|---:|",
    ]
    for curve in curves:
        metrics = curve.metrics
        lengths = metrics.get("primitive_lengths_mm", [])
        minimum = min(lengths, default=0.0)
        maximum = max(lengths, default=0.0)
        median = float(metrics.get("median_primitive_length_mm", 0.0))
        lines.append(
            f"| polyarc-{curve.candidate_id:04d} | {curve.status} | {metrics.get('fit_reference', 'n/a')} | "
            f"{curve.line_count} | {curve.arc_count} | {curve.primitive_count} | {metrics.get('hard_corner_count', 0)} | "
            f"{metrics.get('smooth_tangent_join_count', 0)} | {minimum:.3f} / {median:.3f} / {maximum:.3f} | "
            f"{metrics.get('max_deviation_mm', float('nan')):.4f} / {metrics.get('p95_deviation_mm', float('nan')):.4f} | "
            f"{metrics.get('forbidden_side_violations', 0)} |"
        )
    lines.extend(["", "## Robust physical references", ""])
    for reference in references:
        metrics = reference.metrics
        raw = metrics.get("raw_to_reference", {})
        lines.extend([
            f"### {reference.curve_id}",
            "",
            f"- Method: {metrics.get('method', 'n/a')}.",
            f"- Persistence scales: {metrics.get('persistence_scales_mm', [])} mm.",
            f"- Raw/reference points: {metrics.get('raw_point_count', 0)} / {metrics.get('reference_point_count', 0)}.",
            f"- Protected corners / maximum landmark movement: {metrics.get('protected_corner_count', 0)} / {metrics.get('maximum_protected_corner_error_mm', 0.0):.9f} mm.",
            f"- Raw→reference mean/RMS/P95/P99/max: {raw.get('mean_mm', 0.0):.4f} / {raw.get('rms_mm', 0.0):.4f} / {raw.get('p95_mm', 0.0):.4f} / {raw.get('p99_mm', 0.0):.4f} / {raw.get('maximum_mm', 0.0):.4f} mm.",
            "",
        ])
    lines.extend(["## RAW → CAM safety audit", ""])
    for curve in curves:
        raw = curve.metrics.get("raw_to_cam_deviation", {})
        lines.extend([
            f"### polyarc-{curve.candidate_id:04d}",
            "",
            f"- Mean: {raw.get('mean_mm', 0.0):.4f} mm",
            f"- RMS: {raw.get('rms_mm', 0.0):.4f} mm",
            f"- P95: {raw.get('p95_mm', 0.0):.4f} mm",
            f"- P99: {raw.get('p99_mm', 0.0):.4f} mm",
            f"- Maximum: {raw.get('maximum_mm', 0.0):.4f} mm",
            f"- Reference/CAM enclosed-area change: {curve.metrics.get('enclosed_area_change_mm2', 0.0):+.4f} mm² ({curve.metrics.get('enclosed_area_change_percent', 0.0):+.4f}%).",
            "",
        ])
        explanations = curve.metrics.get("micro_primitive_explanations", [])
        if explanations:
            lines.extend(["Short primitive reasons:", ""])
            for item in explanations:
                lines.append(
                    f"- #{item['primitive_index']} ({item['length_mm']:.4f} mm): {item['reason']}"
                )
            lines.append("")
    lines.extend([
        "## Pattern and preferred output",
        "",
        f"- Selected: **{PATTERN_NAMES[pattern.selected]}**.",
        f"- Pattern entities: {len(pattern.lines)}.",
        f"- Pattern dimension validation: {pattern.metrics.get('dimension_checks', {}).get('valid', pattern.selected == 'none')}.",
        f"- Preferred output: `{pattern.output_metrics.get('preferred_file', 'flattened_curves_polyarc.dxf')}`.",
        "",
        "## Export evidence",
        "",
        f"- Polyarc DXF status: {dxf.get('status', 'n/a')}.",
        f"- Flat raw/reference/CAM Rhino objects: {preview.get('raw_curve_count', 0)} / {preview.get('robust_reference_curve_count', 0)} / {preview.get('polyarc_v033_object_count', 0)}.",
        "- Final CAM uses only LINE and true circular ARC primitives. Soft joins are G1; protected hard corners are intentional G0.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")
