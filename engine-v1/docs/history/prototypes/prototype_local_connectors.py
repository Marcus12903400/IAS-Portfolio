"""Evaluate bounded local connectors on the cached 32-piece cockpit proposal."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from shapely import distance, intersects_xy, points
from shapely.geometry import LineString, Polygon

from autodeck.polyarc import (
    PolyarcPrimitive,
    _angle_mismatch_deg,
    _parameterized_biarc,
    _sample_primitives,
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
    start_fraction = float(np.clip(start_mm / max(length, 1e-12), 0.0, 0.40))
    end_fraction = float(np.clip(1.0 - end_mm / max(length, 1e-12), 0.60, 1.0))
    if primitive.kind == "LINE":
        delta = primitive.end - primitive.start
        return PolyarcPrimitive(
            "LINE", primitive.start + start_fraction * delta,
            primitive.start + end_fraction * delta,
            primitive.source_start_index, primitive.source_end_index,
            primitive.logical_span_index,
        )
    center = np.asarray(primitive.center, dtype=float)
    angle = np.arctan2(primitive.start[1] - center[1], primitive.start[0] - center[0])
    start_angle = angle + start_fraction * primitive.signed_sweep_rad
    sweep = (end_fraction - start_fraction) * primitive.signed_sweep_rad
    end_angle = start_angle + sweep
    radius = float(primitive.radius_mm)
    return PolyarcPrimitive(
        "ARC",
        center + radius * np.asarray([np.cos(start_angle), np.sin(start_angle)]),
        center + radius * np.asarray([np.cos(end_angle), np.sin(end_angle)]),
        primitive.source_start_index, primitive.source_end_index,
        primitive.logical_span_index, center.copy(), radius, float(sweep),
    )


def offset_primitive(primitive: PolyarcPrimitive, left_mm: float) -> PolyarcPrimitive:
    if primitive.kind == "LINE":
        shift = left_mm * np.array([
            -primitive.tangent_start()[1], primitive.tangent_start()[0],
        ])
        return PolyarcPrimitive(
            "LINE", primitive.start + shift, primitive.end + shift,
            primitive.source_start_index, primitive.source_end_index,
            primitive.logical_span_index,
        )
    center = np.asarray(primitive.center, dtype=float)
    sign = 1.0 if primitive.signed_sweep_rad >= 0.0 else -1.0
    radius = float(primitive.radius_mm) - sign * left_mm
    start_radial = (primitive.start - center) / float(primitive.radius_mm)
    end_radial = (primitive.end - center) / float(primitive.radius_mm)
    return PolyarcPrimitive(
        "ARC", center + radius * start_radial, center + radius * end_radial,
        primitive.source_start_index, primitive.source_end_index,
        primitive.logical_span_index, center.copy(), radius,
        primitive.signed_sweep_rad,
    )


def offset_chain(primitives: list[PolyarcPrimitive], inward_mm: float) -> list[PolyarcPrimitive]:
    sampled = _sample_primitives(primitives, 5.0)
    signed_area = 0.5 * float(np.sum(
        sampled[:, 0] * np.roll(sampled[:, 1], -1)
        - sampled[:, 1] * np.roll(sampled[:, 0], -1)
    ))
    left_mm = inward_mm if signed_area > 0.0 else -inward_mm
    offset = [offset_primitive(item, left_mm) for item in primitives]
    result: list[PolyarcPrimitive] = []
    special = max(item.logical_span_index for item in offset) + 1
    for index, before in enumerate(offset):
        result.append(before)
        after = offset[(index + 1) % len(offset)]
        if np.linalg.norm(before.end - after.start) > 1e-7:
            result.append(PolyarcPrimitive(
                "LINE", before.end.copy(), after.start.copy(),
                before.source_end_index, after.source_start_index, special,
            ))
            special += 1
    return result


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    run = repo / "output" / "key-west-v033-orientation"
    payload = json.loads((run / "debug_metrics.json").read_text())
    curve = payload["polyarc_fit"]["curves"][0]
    base = [primitive_from_dict(item) for item in curve["primitives"]]
    with np.load(run / "primary_broad_reference_diagnostic.npz") as archive:
        physical = archive["points"].astype(float)
    with np.load(run / "primary_reference_diagnostic.npz") as archive:
        raw = archive["raw_points"].astype(float)

    tree = cKDTree(physical)
    allowed = Polygon(raw).buffer(0.35)
    base_unsafe = []
    for index, item in enumerate(base):
        item_points = item.sample(0.5)
        count = int(np.count_nonzero(
            ~intersects_xy(allowed, item_points[:, 0], item_points[:, 1])
        ))
        base_unsafe.append({
            "index": index,
            "kind": item.kind,
            "span": item.logical_span_index,
            "length_mm": item.length_mm,
            "unsafe_samples": count,
        })
    rows = []
    for trim_mm in (0.0, 2.0, 5.0, 10.0, 20.0, 40.0, 80.0):
        trim_start = [0.0] * len(base)
        trim_end = [0.0] * len(base)
        for index, (before, after) in enumerate(zip(base, base[1:] + base[:1])):
            if before.logical_span_index == after.logical_span_index:
                trim_end[index] = trim_mm
                trim_start[(index + 1) % len(base)] = trim_mm
        trimmed = [
            trim(item, trim_start[index], trim_end[index])
            for index, item in enumerate(base)
        ]
        result: list[PolyarcPrimitive] = []
        failed: list[int] = []
        for index, before in enumerate(trimmed):
            result.append(before)
            after = trimmed[(index + 1) % len(trimmed)]
            if before.logical_span_index != after.logical_span_index:
                result.append(PolyarcPrimitive(
                    "LINE", before.end.copy(), after.start.copy(),
                    before.source_end_index, after.source_start_index,
                    1000 + index,
                ))
                continue
            candidates = []
            for ratio in (1.0, 0.5, 2.0, 0.25, 4.0, 0.125, 8.0, 0.0625, 16.0):
                connector = _parameterized_biarc(
                    before.end, after.start, before.tangent_end(), after.tangent_start(),
                    before.source_end_index, after.source_start_index,
                    before.logical_span_index, ratio,
                )
                if connector is None:
                    continue
                connector_points = _sample_primitives(connector, 0.5)
                forward = float(np.max(tree.query(connector_points, k=1)[0], initial=0.0))
                unsafe = int(np.count_nonzero(
                    ~intersects_xy(allowed, connector_points[:, 0], connector_points[:, 1])
                ))
                candidates.append((unsafe, forward, connector))
            if not candidates:
                failed.append(index)
            else:
                result.extend(min(candidates, key=lambda item: (item[0], item[1]))[2])

        sampled = _sample_primitives(result, 0.5)
        if np.linalg.norm(sampled[-1] - sampled[0]) > 1e-9:
            sampled = np.vstack((sampled, sampled[0]))
        else:
            sampled[-1] = sampled[0]
        pairs = list(zip(result, result[1:] + result[:1]))
        gaps = [float(np.linalg.norm(a.end - b.start)) for a, b in pairs]
        tangent = [
            _angle_mismatch_deg(a.tangent_end(), b.tangent_start())
            for a, b in pairs
            if a.logical_span_index == b.logical_span_index
        ]
        deviation = _symmetric_deviation(np.vstack((physical, physical[0])), sampled)
        forbidden = int(np.count_nonzero(
            ~intersects_xy(allowed, sampled[:, 0], sampled[:, 1])
        ))
        outside_distance = np.asarray(distance(
            points(sampled[:, 0], sampled[:, 1]), Polygon(raw)
        ), dtype=float)
        primitive_forward = [
            float(np.max(tree.query(item.sample(0.5), k=1)[0], initial=0.0))
            for item in result
        ]
        worst_index = int(np.argmax(primitive_forward))
        rows.append({
            "trim_mm": trim_mm,
            "failed_connector_joins": failed,
            "primitives": len(result),
            "lines": sum(item.kind == "LINE" for item in result),
            "arcs": sum(item.kind == "ARC" for item in result),
            "maximum_gap_mm": max(gaps, default=0.0),
            "maximum_smooth_tangent_mismatch_deg": max(tangent, default=0.0),
            "maximum_deviation_mm": float(deviation["maximum_mm"]),
            "p95_deviation_mm": float(deviation["p95_mm"]),
            "forbidden_side_violations": forbidden,
            "maximum_outside_raw_mm": float(np.max(outside_distance, initial=0.0)),
            "p95_outside_raw_mm": float(np.percentile(outside_distance, 95)),
            "self_intersection": not LineString(sampled).is_simple,
            "worst_primitive": {
                "index": worst_index,
                "kind": result[worst_index].kind,
                "logical_span_index": result[worst_index].logical_span_index,
                "forward_deviation_mm": primitive_forward[worst_index],
                "length_mm": result[worst_index].length_mm,
            },
        })
        if trim_mm == 5.0:
            rows[-1]["inward_offset_trials"] = []
            for inward_mm in (0.5, 1.0, 2.0, 3.0, 4.0, 5.0):
                offset_result = offset_chain(result, inward_mm)
                offset_sampled = _sample_primitives(offset_result, 0.5)
                if np.linalg.norm(offset_sampled[-1] - offset_sampled[0]) > 1e-9:
                    offset_sampled = np.vstack((offset_sampled, offset_sampled[0]))
                offset_deviation = _symmetric_deviation(
                    np.vstack((physical, physical[0])), offset_sampled,
                )
                rows[-1]["inward_offset_trials"].append({
                    "offset_mm": inward_mm,
                    "primitive_count": len(offset_result),
                    "maximum_deviation_mm": float(offset_deviation["maximum_mm"]),
                    "forbidden_side_violations": int(np.count_nonzero(
                        ~intersects_xy(
                            allowed, offset_sampled[:, 0], offset_sampled[:, 1],
                        )
                    )),
                    "self_intersection": not LineString(offset_sampled).is_simple,
                })
    print(json.dumps({
        "base_unsafe": sorted(
            base_unsafe, key=lambda item: item["unsafe_samples"], reverse=True,
        ),
        "join_trials": rows,
    }, indent=2))


if __name__ == "__main__":
    main()
