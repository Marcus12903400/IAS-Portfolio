"""Optimize the cached 32-piece primary as exact G1 chains between hard corners."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from autodeck.polyarc import (
    PolyarcPrimitive,
    _angle_mismatch_deg,
    _deviation_distribution,
    _sample_primitives,
)


def unit(vector: np.ndarray) -> np.ndarray:
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def tangent(primitive: dict, end: bool = False) -> np.ndarray:
    if primitive["kind"] == "LINE":
        return unit(np.asarray(primitive["end"]) - np.asarray(primitive["start"]))
    point = np.asarray(primitive["end"] if end else primitive["start"])
    radial = unit(point - np.asarray(primitive["center"]))
    left = np.array([-radial[1], radial[0]])
    return left if float(primitive["signed_sweep_deg"]) >= 0.0 else -left


def integrate(
    start: np.ndarray,
    kinds: list[str],
    source_ranges: list[tuple[int, int]],
    span: int,
    parameters: np.ndarray,
) -> tuple[list[PolyarcPrimitive], np.ndarray]:
    theta = float(parameters[0])
    cursor = 1
    point = np.asarray(start, dtype=float).copy()
    nodes = [point.copy()]
    primitives = []
    for kind, (source_start, source_end) in zip(kinds, source_ranges):
        length = math.exp(float(parameters[cursor]))
        cursor += 1
        direction = np.array([math.cos(theta), math.sin(theta)])
        if kind == "LINE":
            endpoint = point + length * direction
            primitive = PolyarcPrimitive(
                "LINE", point.copy(), endpoint.copy(), source_start, source_end, span,
            )
        else:
            sweep = float(parameters[cursor])
            cursor += 1
            signed_radius = length / sweep
            endpoint = point + signed_radius * np.array(
                [math.sin(theta + sweep) - math.sin(theta),
                 -math.cos(theta + sweep) + math.cos(theta)]
            )
            center = point + signed_radius * np.array([-math.sin(theta), math.cos(theta)])
            primitive = PolyarcPrimitive(
                "ARC", point.copy(), endpoint.copy(), source_start, source_end, span,
                center=center, radius_mm=abs(signed_radius), signed_sweep_rad=sweep,
            )
            theta += sweep
        point = endpoint
        nodes.append(point.copy())
        primitives.append(primitive)
    return primitives, np.asarray(nodes)


def optimize_span(
    payloads: list[dict],
    reference_extended: np.ndarray,
    span: int,
) -> tuple[list[PolyarcPrimitive], dict]:
    kinds = [item["kind"] for item in payloads]
    source_ranges = [
        (int(item["source_start_index"]), int(item["source_end_index"]))
        for item in payloads
    ]
    for index, (start, end) in enumerate(source_ranges):
        if end <= start:
            source_ranges[index] = (start, end + len(reference_extended) // 2)
    start_point = reference_extended[source_ranges[0][0]].copy()
    end_point = reference_extended[source_ranges[-1][1]].copy()
    target_nodes = np.asarray(
        [reference_extended[source_ranges[0][0]]]
        + [reference_extended[end] for _start, end in source_ranges],
        dtype=float,
    )
    initial_theta = math.atan2(tangent(payloads[0])[1], tangent(payloads[0])[0])
    initial = [initial_theta]
    lower = [initial[0] - math.pi]
    upper = [initial[0] + math.pi]
    initial_point = start_point.copy()
    initial_direction_angle = initial_theta
    for primitive_index, item in enumerate(payloads):
        target = target_nodes[primitive_index + 1]
        direction = np.array(
            [math.cos(initial_direction_angle), math.sin(initial_direction_angle)]
        )
        delta = target - initial_point
        if item["kind"] == "LINE":
            length = max(float(np.dot(delta, direction)), 1.0)
            initial_point = initial_point + length * direction
            sweep = None
        else:
            normal = np.array([-direction[1], direction[0]])
            denominator = 2.0 * float(np.dot(delta, normal))
            sweep = None
            if abs(denominator) > 1e-9:
                signed_radius = float(np.dot(delta, delta) / denominator)
                center = initial_point + signed_radius * normal
                start_angle = math.atan2(
                    initial_point[1] - center[1], initial_point[0] - center[0]
                )
                end_angle = math.atan2(target[1] - center[1], target[0] - center[0])
                if signed_radius > 0.0:
                    candidate_sweep = (end_angle - start_angle) % (2.0 * math.pi)
                else:
                    candidate_sweep = -((start_angle - end_angle) % (2.0 * math.pi))
                if abs(candidate_sweep) < math.pi - 1e-4:
                    sweep = candidate_sweep
                    length = max(abs(signed_radius * sweep), 1.0)
                    initial_point = target.copy()
                    initial_direction_angle += sweep
            if sweep is None:
                sweep = math.radians(float(item["signed_sweep_deg"]))
                length = max(float(item["length_mm"]), 1.0)
                signed_radius = length / sweep
                initial_point = initial_point + signed_radius * np.array(
                    [
                        math.sin(initial_direction_angle + sweep)
                        - math.sin(initial_direction_angle),
                        -math.cos(initial_direction_angle + sweep)
                        + math.cos(initial_direction_angle),
                    ]
                )
                initial_direction_angle += sweep
        initial.append(math.log(length))
        lower.append(math.log(max(length * 0.10, 0.25)))
        upper.append(math.log(length * 10.0))
        if item["kind"] == "ARC":
            assert sweep is not None
            initial.append(sweep)
            if sweep >= 0.0:
                lower.append(1e-4)
                upper.append(math.pi - 1e-4)
            else:
                lower.append(-math.pi + 1e-4)
                upper.append(-1e-4)
    initial_array = np.asarray(initial, dtype=float)

    def residual(parameters: np.ndarray) -> np.ndarray:
        primitives, nodes = integrate(
            start_point, kinds, source_ranges, span, parameters
        )
        values = []
        for primitive, (source_start, source_end) in zip(primitives, source_ranges):
            source = reference_extended[source_start : source_end + 1]
            if primitive.kind == "LINE":
                direction = unit(primitive.end - primitive.start)
                normal = np.array([-direction[1], direction[0]])
                local = source - primitive.start
                values.extend((local @ normal).tolist())
                projection = local @ direction
                overflow = np.maximum(-projection, 0.0) + np.maximum(
                    projection - primitive.length_mm, 0.0
                )
                values.extend((0.25 * overflow).tolist())
            else:
                radial = np.linalg.norm(source - np.asarray(primitive.center), axis=1)
                values.extend((radial - float(primitive.radius_mm)).tolist())
                values.extend(np.zeros(len(source)).tolist())
        # A modest node prior prevents equivalent circle solutions from jumping
        # to a different physical regime.  The hard-corner endpoint gets a much
        # stronger weight and therefore closes to numerical precision.
        values.extend((5.0 * (nodes[1:-1] - target_nodes[1:-1])).ravel().tolist())
        values.extend((5000.0 * (nodes[-1] - end_point)).tolist())
        values.extend((0.01 * (parameters - initial_array)).tolist())
        return np.asarray(values, dtype=float)

    result = least_squares(
        residual,
        initial_array,
        bounds=(np.asarray(lower), np.asarray(upper)),
        max_nfev=3000,
        loss="soft_l1",
        f_scale=1.0,
        xtol=1e-11,
        ftol=1e-11,
        gtol=1e-11,
        verbose=0,
    )
    primitives, nodes = integrate(start_point, kinds, source_ranges, span, result.x)
    return primitives, {
        "success": bool(result.success),
        "cost": float(result.cost),
        "optimality": float(result.optimality),
        "nfev": int(result.nfev),
        "endpoint_error_mm": float(np.linalg.norm(nodes[-1] - end_point)),
    }


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    run = repo / "work" / "key-west-adaptive-20260827-01"
    payloads = json.loads((run / "prototype_manual_2.95.json").read_text())
    with np.load(run / "primary_broad_reference_diagnostic.npz") as archive:
        reference = archive["points"]
    extended = np.vstack([reference, reference])
    result = []
    diagnostics = []
    for span in sorted(set(int(item["logical_span_index"]) for item in payloads)):
        group = [item for item in payloads if int(item["logical_span_index"]) == span]
        optimized, detail = optimize_span(group, extended, span)
        result.extend(optimized)
        diagnostics.append({"span": span, **detail})
        print("span", span, detail)
    sampled = _sample_primitives(result, 0.5)
    if float(np.linalg.norm(sampled[-1] - sampled[0])) > 1e-10:
        sampled = np.vstack([sampled, sampled[0]])
    closed_reference = np.vstack([reference, reference[0]])
    deviation = _deviation_distribution(closed_reference, sampled)
    gaps = [
        float(np.linalg.norm(before.end - after.start))
        for before, after in zip(result, result[1:] + result[:1])
    ]
    smooth_mismatches = [
        _angle_mismatch_deg(before.tangent_end(), after.tangent_start())
        for before, after in zip(result, result[1:] + result[:1])
        if before.logical_span_index == after.logical_span_index
    ]
    summary = {
        "primitive_count": len(result),
        "line_count": sum(item.kind == "LINE" for item in result),
        "arc_count": sum(item.kind == "ARC" for item in result),
        "deviation": deviation,
        "maximum_gap_mm": max(gaps, default=0.0),
        "maximum_smooth_tangent_mismatch_deg": max(smooth_mismatches, default=0.0),
        "span_diagnostics": diagnostics,
        "primitives": [item.to_dict() for item in result],
    }
    print(json.dumps({key: value for key, value in summary.items() if key != "primitives"}, indent=2))
    (run / "prototype_g1_optimized.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
