from __future__ import annotations

"""V0.3.2 polyarc evidence, comparison, and landmark-dimension reports."""

from pathlib import Path
from typing import Any

import numpy as np

from .curve_fit import ManufacturingCurve, map_points_to_indices
from .polyarc import PolyarcCurve


def _distance_rows(points: np.ndarray, closed: bool) -> list[dict[str, Any]]:
    values = np.asarray(points, dtype=float)
    if len(values) < 2:
        return []
    rows: list[dict[str, Any]] = []
    for first in range(len(values)):
        for second in range(first + 1, len(values)):
            separation = second - first
            neighboring = separation == 1 or (closed and first == 0 and second == len(values) - 1)
            rows.append({
                "first_corner": first,
                "second_corner": second,
                "relationship": "neighboring" if neighboring else "non-neighboring",
                "distance_mm": float(np.linalg.norm(values[second] - values[first])),
            })
    return rows


def make_landmark_dimension_audit(
    curve_id: str,
    raw_anchor_indices: list[int],
    mapped_base_3d_points_mm: np.ndarray,
    flat_raw_points_mm: np.ndarray,
    polyarc: PolyarcCurve,
) -> dict[str, Any]:
    # raw_anchor_indices (polyarc.hard_corner_indices) are indices into
    # polyarc's own internally collapsed point array, not into
    # flat_raw_points_mm (the uncollapsed source contour) below -- reusing
    # them directly silently aliased onto the wrong physical point whenever
    # the two arrays' lengths diverged. Resolve the actual hard-corner
    # locations (polyarc.hard_corner_points, exact) by nearest coordinate
    # match into flat_raw_points_mm instead, so mapped_base_3d_points_mm
    # (1:1 with flat_raw_points_mm) is sampled at the physically correct rows.
    del raw_anchor_indices  # superseded by exact hard_corner_points below
    flat_raw = np.asarray(flat_raw_points_mm, dtype=float)
    base_source = np.asarray(mapped_base_3d_points_mm, dtype=float)
    if len(flat_raw) != len(base_source):
        raise ValueError(
            f"{curve_id}: flat raw contour ({len(flat_raw)} points) and mapped 3D base "
            f"({len(base_source)} points) are not the same length; cannot correlate landmark corners."
        )
    hard_corner_points = np.asarray(polyarc.hard_corner_points, dtype=float)
    indices = map_points_to_indices(
        hard_corner_points, flat_raw,
        context=f"{curve_id} landmark-dimension hard corner",
    )
    base = base_source[indices]
    flat = flat_raw[indices]
    cam = np.asarray(polyarc.hard_corner_points, dtype=float)
    base_rows = _distance_rows(base, polyarc.is_closed)
    flat_rows = _distance_rows(flat, polyarc.is_closed)
    cam_rows = _distance_rows(cam, polyarc.is_closed)
    combined: list[dict[str, Any]] = []
    for base_row, flat_row, cam_row in zip(base_rows, flat_rows, cam_rows):
        combined.append({
            **{key: base_row[key] for key in ("first_corner", "second_corner", "relationship")},
            "development_base_3d_mm": base_row["distance_mm"],
            "flat_raw_mm": flat_row["distance_mm"],
            "polyarc_mm": cam_row["distance_mm"],
            "base_to_flat_change_mm": flat_row["distance_mm"] - base_row["distance_mm"],
            "flat_to_polyarc_change_mm": cam_row["distance_mm"] - flat_row["distance_mm"],
        })
    base_bbox = np.ptp(base, axis=0) if len(base) else np.zeros(3)
    flat_bbox = np.ptp(flat[:, :2], axis=0) if len(flat) else np.zeros(2)
    cam_bbox = np.ptp(cam[:, :2], axis=0) if len(cam) else np.zeros(2)
    return {
        "curve_id": curve_id,
        "hard_corner_count": len(indices),
        "corner_indices": indices,
        "pair_dimensions": combined,
        "development_base_3d_bbox_mm": base_bbox.astype(float).tolist(),
        "flat_raw_bbox_mm": flat_bbox.astype(float).tolist(),
        "polyarc_bbox_mm": cam_bbox.astype(float).tolist(),
        "development_base_3d_long_axis_mm": float(np.max(base_bbox, initial=0.0)),
        "flat_raw_long_axis_mm": float(np.max(flat_bbox, initial=0.0)),
        "polyarc_long_axis_mm": float(np.max(cam_bbox, initial=0.0)),
        "interpretation": (
            "Base-to-flat differences are development distortion; flat-to-polyarc differences measure "
            "manufacturing-fit influence. Protected landmark coordinates themselves remain exact in flat space."
        ),
    }


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.{digits}f}"
    return str(value)


def write_polyarc_report(
    path: Path,
    polyarcs: list[PolyarcCurve],
    spline_references: list[ManufacturingCurve],
    dxf: dict[str, Any],
    preview: dict[str, Any],
    dimension_audits: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    settings = config["polyarc_fit"]
    v033 = any(
        curve.metrics.get("fit_reference") == "ROBUST_PHYSICAL_REFERENCE"
        for curve in polyarcs
    )
    version = "V0.3.3" if v033 else "V0.3.2"
    reference_label = "robust physical reference" if v033 else "developed raw contour"
    lines = [
        f"# AutoDeck {version} polyarc report",
        "",
        "> **TEST GEOMETRY — NOT CNC-READY.** Human overlay inspection and approval remain mandatory.",
        "",
        "## Locked acceptance policy",
        "",
        f"- Bidirectional maximum fit deviation from the {reference_label}: **<= {float(settings['absolute_fit_tolerance_mm']):.3f} mm**.",
        f"- Smooth-join tangent target / maximum: **{float(settings['tangent_target_deg']):.3f}° / {float(settings['tangent_max_deg']):.3f}°**.",
        "- Forbidden-side violations: **zero**; self-intersections: **zero**; closed-loop gap: **zero**.",
        "- Final primitives: **LINE and true circular ARC only**. Protected corners remain intentional G0 joins.",
        "- Selection order: zero hard failures, tolerance compliance, hard-corner preservation, primitive count, short-primitive penalties, RMS error.",
        "",
        "## Summary",
        "",
        "| Curve | Status | Hard corners | Logical spans | Primitives | LINE | ARC | Max dev (mm) | P95 (mm) | RMS (mm) | Forbidden | Self-X | Closure (mm) | Max smooth join (°) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for curve in polyarcs:
        m = curve.metrics
        lines.append(
            f"| polyarc-{curve.candidate_id:04d} | {curve.status} | {len(curve.hard_corner_indices)} | "
            f"{m['logical_span_count']} | {curve.primitive_count} | {curve.line_count} | {curve.arc_count} | "
            f"{m['max_deviation_mm']:.4f} | {m.get('p95_deviation_mm', float('nan')):.4f} | {m['rms_deviation_mm']:.4f} | "
            f"{m['forbidden_side_violations']} | {m['self_intersection']} | {m['closure_error_mm']:.8f} | "
            f"{m['maximum_smooth_tangent_mismatch_deg']:.6f} |"
        )
    if polyarcs:
        primary = polyarcs[0]
        if primary.status == "REVIEW":
            lines.extend([
                "",
                "## Primary disposition",
                "",
                f"**NEEDS_REVIEW — {version} success is not declared.** The strict geometry checks pass, but "
                f"the primary still contains {primary.primitive_count} primitives, including "
                f"{primary.metrics.get('micro_primitive_count', 0)} below 3 mm and "
                f"{primary.metrics.get('primitives_below_10mm_count', primary.metrics.get('short_primitive_count', 0))} below 10 mm. "
                "The per-primitive table and micro-primitive explanations below are retained for manual Rhino/VCarve review.",
            ])

    for curve in polyarcs:
        m = curve.metrics
        lines.extend([
            "",
            f"## polyarc-{curve.candidate_id:04d}",
            "",
            f"Status: **{curve.status}**. Hard corners: {len(curve.hard_corner_indices)}. "
            f"Logical spans: {m['logical_span_count']}. Final primitives: {curve.primitive_count}.",
            "",
            "### Primitive table",
            "",
            "| # | Span | Type | Length (mm) | Radius (mm) | Signed sweep (°) | Bulge | Source indices |",
            "|---:|---:|---|---:|---:|---:|---:|---|",
        ])
        for index, primitive in enumerate(curve.primitives, 1):
            lines.append(
                f"| {index} | {primitive.logical_span_index} | {primitive.kind} | {primitive.length_mm:.4f} | "
                f"{_fmt(primitive.radius_mm)} | {primitive.sweep_deg:.6f} | {primitive.bulge:.9f} | "
                f"{primitive.source_start_index}–{primitive.source_end_index} |"
            )
        lines.extend([
            "",
            "### Join table",
            "",
            "| # | Join class | Gap (mm) | Tangent mismatch (°) | Span before → after |",
            "|---:|---|---:|---:|---|",
        ])
        for join in curve.joins:
            lines.append(
                f"| {join.index} | {join.join_type} | {join.gap_mm:.9f} | "
                f"{_fmt(join.tangent_mismatch_deg, 6)} | {join.logical_span_before} → {join.logical_span_after} |"
            )
        lines.extend([
            "",
            "### Length, area, bounding box, and short-primitive audit",
            "",
            f"- Raw / polyarc perimeter: {m['raw_perimeter_length_mm']:.4f} / {m['polyarc_perimeter_length_mm']:.4f} mm "
            f"({m.get('perimeter_length_change_percent', float('nan')):+.4f}%).",
            f"- {reference_label.title()} / polyarc enclosed area: {m.get('reference_enclosed_area_mm2', m['raw_enclosed_area_mm2']):.4f} / {m['polyarc_enclosed_area_mm2']:.4f} mm² "
            f"({m.get('enclosed_area_change_percent', float('nan')):+.4f}%).",
            f"- Raw / polyarc bounding box: {m['raw_bbox_mm']} / {m['polyarc_bbox_mm']} mm; "
            f"change={m.get('bbox_change_mm', [])} mm.",
            f"- Primitive length minimum / P10 / median / P90 / maximum: "
            f"{m['minimum_primitive_length_mm']:.4f} / {m.get('p10_primitive_length_mm', float('nan')):.4f} / "
            f"{m.get('median_primitive_length_mm', float('nan')):.4f} / "
            f"{m.get('p90_primitive_length_mm', float('nan')):.4f} / {m['maximum_primitive_length_mm']:.4f} mm.",
            f"- Primitive counts below 3 / 5 / 10 / 25 mm: "
            f"{m.get('primitives_below_3mm_count', m['micro_primitive_count'])} / "
            f"{m.get('primitives_below_5mm_count', 'n/a')} / "
            f"{m.get('primitives_below_10mm_count', m['short_primitive_count'])} / "
            f"{m.get('primitives_below_25mm_count', m['primitives_below_preferred_count'])}.",
            f"- Hard-corner / smooth tangent joins: {m.get('hard_corner_join_count', 0)} / "
            f"{m.get('smooth_tangent_join_count', 0)}.",
            f"- Warnings: {'; '.join(curve.warnings) if curve.warnings else 'none'}.",
        ])
        if m.get("micro_primitive_explanations"):
            lines.extend([
                "",
                "### Micro-primitive explanations",
                "",
                "| Primitive | Length (mm) | Logical span | Source interval | Reason |",
                "|---:|---:|---:|---|---|",
            ])
            for item in m["micro_primitive_explanations"]:
                lines.append(
                    f"| {item['primitive_index']} | {item['length_mm']:.4f} | "
                    f"{item['logical_span_index']} | {item['source_start_index']}–{item['source_end_index']} | "
                    f"{item['reason']} |"
                )

    lines.extend([
        "",
        f"## V0.3.1 spline → {version} polyarc comparison",
        "",
        "| Curve | V0.3.1 types | V0.3.1 CP | V0.3.1 max/P95/RMS (mm) | V0.3.1 gap (mm) | V0.3.2 LINE+ARC | V0.3.2 max/P95/RMS (mm) | V0.3.2 gap (mm) | Hard corners |",
        "|---|---|---:|---|---:|---:|---|---:|---:|",
    ])
    for spline, polyarc in zip(spline_references, polyarcs):
        sm = spline.metrics; pm = polyarc.metrics
        counts = sm.get("span_type_counts", {})
        type_text = ", ".join(f"{key}:{value}" for key, value in counts.items() if value)
        deviation = sm.get("deviation", {})
        lines.append(
            f"| {spline.cam_curve_id} | {type_text or 'none'} | {sm.get('control_point_count', 0)} | "
            f"{_fmt(deviation.get('maximum_mm'))} / {_fmt(deviation.get('p95_mm'))} / {_fmt(deviation.get('rms_mm'))} | "
            f"{_fmt(sm.get('maximum_join_gap_mm'), 8)} | "
            f"{polyarc.primitive_count} ({polyarc.line_count}L+{polyarc.arc_count}A) | "
            f"{pm['max_deviation_mm']:.4f} / {pm.get('p95_deviation_mm', float('nan')):.4f} / {pm['rms_deviation_mm']:.4f} | "
            f"{pm['maximum_join_gap_mm']:.8f} | "
            f"{len(spline.anchor_indices)} → {len(polyarc.hard_corner_indices)} |"
        )

    lines.extend([
        "",
        "## Hard-corner landmark dimension audit",
        "",
    ])
    for audit in dimension_audits:
        lines.extend([
            f"### {audit['curve_id']}",
            "",
            f"Base-3D / flat-raw / polyarc long axis: {audit['development_base_3d_long_axis_mm']:.4f} / "
            f"{audit['flat_raw_long_axis_mm']:.4f} / {audit['polyarc_long_axis_mm']:.4f} mm.",
            "",
            "| Corners | Relationship | Base 3-D (mm) | Flat raw (mm) | Polyarc (mm) | Base→flat (mm) | Flat→polyarc (mm) |",
            "|---|---|---:|---:|---:|---:|---:|",
        ])
        for row in audit["pair_dimensions"]:
            lines.append(
                f"| {row['first_corner']}–{row['second_corner']} | {row['relationship']} | "
                f"{row['development_base_3d_mm']:.4f} | {row['flat_raw_mm']:.4f} | {row['polyarc_mm']:.4f} | "
                f"{row['base_to_flat_change_mm']:+.4f} | {row['flat_to_polyarc_change_mm']:+.4f} |"
            )
        lines.extend(["", audit["interpretation"], ""])

    lines.extend([
        "## Export round-trip audit",
        "",
        f"- Bulged polyarc DXF: `{dxf.get('polyarc', {}).get('path', 'missing')}` — valid={dxf.get('polyarc', {}).get('valid', False)}, "
        f"entities={dxf.get('polyarc', {}).get('total_entity_count', 0)}, nonzero bulges={dxf.get('polyarc', {}).get('nonzero_bulge_count', 0)}.",
        f"- Separate LINE/ARC DXF: `{dxf.get('linearc', {}).get('path', 'missing')}` — valid={dxf.get('linearc', {}).get('valid', False)}, "
        f"entities={dxf.get('linearc', {}).get('total_entity_count', 0)}.",
        f"- V0.3.1 spline reference DXF: `{dxf.get('spline_reference', {}).get('path', 'missing')}` — valid={dxf.get('spline_reference', {}).get('valid', False)}.",
        f"- Rhino preview: written={preview.get('written', False)}, V0.3.1 objects={preview.get('spline_v031_object_count', 0)}, "
        f"V0.3.2 objects={preview.get('polyarc_v032_object_count', 0)}.",
        "",
        "## Manufacturing judgment",
        "",
        f"The {version} result is **test manufacturing geometry only**. Passing the mathematical and round-trip checks does not make it CNC-ready. "
        "Overlay the polyarc against FLAT_RAW_DEVELOPED and the scan-derived 3-D boundary, inspect protected corners and short primitives, then approve or reject it manually.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")
