"""Prototype local G1 transition repair for the cached Key West 32-piece fit."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely import intersects_xy
from shapely.geometry import LineString, Polygon

from autodeck.curve_fit import _corridor_targets
from autodeck.polyarc import (
    PolyarcPrimitive,
    _angle_mismatch_deg,
    _fit_endpoint_arc,
    _manual_fit_span,
    _parameterized_biarc,
    _sample_primitives,
    _span_ranges,
    _symmetric_deviation,
)


def primitive_from_dict(item: dict) -> PolyarcPrimitive:
    return PolyarcPrimitive(
        item["kind"],
        np.asarray(item["start"], dtype=float),
        np.asarray(item["end"], dtype=float),
        int(item["source_start_index"]),
        int(item["source_end_index"]),
        int(item["logical_span_index"]),
        None if item.get("center") is None else np.asarray(item["center"], dtype=float),
        item.get("radius_mm"),
        np.deg2rad(float(item.get("signed_sweep_deg", 0.0))),
    )


def trim(primitive: PolyarcPrimitive, start_mm: float, end_mm: float) -> PolyarcPrimitive:
    length = primitive.length_mm
    a = float(np.clip(start_mm / max(length, 1e-12), 0.0, 0.45))
    b = float(np.clip(1.0 - end_mm / max(length, 1e-12), 0.55, 1.0))
    if primitive.kind == "LINE":
        delta = primitive.end - primitive.start
        return PolyarcPrimitive(
            "LINE", primitive.start + a * delta, primitive.start + b * delta,
            primitive.source_start_index, primitive.source_end_index,
            primitive.logical_span_index,
        )
    center = np.asarray(primitive.center)
    start_angle = np.arctan2(primitive.start[1] - center[1], primitive.start[0] - center[0])
    start = start_angle + a * primitive.signed_sweep_rad
    sweep = (b - a) * primitive.signed_sweep_rad
    finish = start + sweep
    radius = float(primitive.radius_mm)
    return PolyarcPrimitive(
        "ARC",
        center + radius * np.asarray([np.cos(start), np.sin(start)]),
        center + radius * np.asarray([np.cos(finish), np.sin(finish)]),
        primitive.source_start_index, primitive.source_end_index,
        primitive.logical_span_index, center.copy(), radius, float(sweep),
    )


def connected_base(payloads: list[dict], target: np.ndarray) -> list[PolyarcPrimitive]:
    count = len(target)
    extended = np.vstack((target, target))
    result: list[PolyarcPrimitive] = []
    for item in payloads:
        start_index = int(item["source_start_index"])
        end_index = int(item["source_end_index"])
        if end_index <= start_index:
            end_index += count
        values = extended[start_index : end_index + 1].copy()
        if item["kind"] == "LINE":
            primitive = PolyarcPrimitive(
                "LINE", values[0].copy(), values[-1].copy(), start_index,
                end_index, int(item["logical_span_index"]),
            )
        else:
            primitive = _fit_endpoint_arc(
                values, start_index, end_index, int(item["logical_span_index"]),
            )
            if primitive is None:
                primitive = primitive_from_dict(item)
                primitive.start = values[0].copy()
                primitive.end = values[-1].copy()
        result.append(primitive)
    return result


def repair(base: list[PolyarcPrimitive], trim_mm: float) -> list[PolyarcPrimitive] | None:
    soft_before = [False] * len(base)
    soft_after = [False] * len(base)
    for index, (before, after) in enumerate(zip(base, base[1:] + base[:1])):
        soft = before.logical_span_index == after.logical_span_index
        soft_after[index] = soft
        soft_before[(index + 1) % len(base)] = soft
    trimmed = [
        trim(
            primitive,
            min(trim_mm, 0.22 * primitive.length_mm) if soft_before[index] else 0.0,
            min(trim_mm, 0.22 * primitive.length_mm) if soft_after[index] else 0.0,
        )
        for index, primitive in enumerate(base)
    ]
    result: list[PolyarcPrimitive] = []
    for index, before in enumerate(trimmed):
        result.append(before)
        after = trimmed[(index + 1) % len(trimmed)]
        if not soft_after[index]:
            continue
        transition = None
        for ratio in (1.0, 0.5, 2.0, 0.25, 4.0, 0.125, 8.0):
            transition = _parameterized_biarc(
                before.end, after.start, before.tangent_end(), after.tangent_start(),
                before.source_end_index, after.source_start_index,
                before.logical_span_index, ratio,
            )
            if transition is not None:
                break
        if transition is None:
            return None
        result.extend(transition)
    return result


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    run = repo / "output" / "key-west-v033-orientation"
    payloads = json.loads((run / "prototype_manual_2.95.json").read_text())
    with np.load(run / "primary_reference_diagnostic.npz") as archive:
        physical = archive["points"].astype(float)
        raw = archive["raw_points"].astype(float)
        anchors = archive["anchors"].astype(int)
    allowed = Polygon(raw).buffer(0.35)
    closed_physical = np.vstack((physical, physical[0]))
    rows = []
    for offset in (1.5, 2.0, 2.5, 3.0):
        target, _ = _corridor_targets(physical, raw, "outer", offset, 8.0, 20.0)
        shift = target - physical
        shift_length = np.linalg.norm(shift, axis=1)
        over = shift_length > offset
        target[over] = physical[over] + shift[over] * (offset / shift_length[over])[:, None]
        target[anchors] = raw[anchors]
        primitives = []
        tolerance = 2.90
        for span, (start, end) in enumerate(_span_ranges(len(target), anchors.tolist(), True)):
            indices = np.arange(start, end + 1, dtype=int) % len(target)
            fitted = _manual_fit_span(target[indices], span, tolerance, 1.0, 2)
            for primitive in fitted:
                primitive.source_start_index = start + int(primitive.source_start_index)
                primitive.source_end_index = start + int(primitive.source_end_index)
            primitives.extend(fitted)
        sampled = _sample_primitives(primitives, 0.5)
        if np.linalg.norm(sampled[0] - sampled[-1]) > 1e-9:
            sampled = np.vstack((sampled, sampled[0]))
        deviation = _symmetric_deviation(closed_physical, sampled)
        gaps = [
            float(np.linalg.norm(a.end - b.start))
            for a, b in zip(primitives, primitives[1:] + primitives[:1])
        ]
        tangencies = [
            _angle_mismatch_deg(a.tangent_end(), b.tangent_start())
            for a, b in zip(primitives, primitives[1:] + primitives[:1])
            if a.logical_span_index == b.logical_span_index
        ]
        forbidden = int(np.count_nonzero(
            ~intersects_xy(allowed, sampled[:, 0], sampled[:, 1])
        ))
        rows.append({
            "offset_mm": offset,
            "fit_tolerance_to_safe_target_mm": tolerance,
            "constructed": True,
            "primitives": len(primitives),
            "lines": sum(p.kind == "LINE" for p in primitives),
            "arcs": sum(p.kind == "ARC" for p in primitives),
            "maximum_deviation_mm": float(deviation["maximum_mm"]),
            "p95_deviation_mm": float(deviation["p95_mm"]),
            "maximum_gap_mm": max(gaps, default=0.0),
            "maximum_tangent_mismatch_deg": max(tangencies, default=0.0),
            "forbidden": forbidden,
            "self_intersection": not LineString(sampled).is_simple,
        })
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
