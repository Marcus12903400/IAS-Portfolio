"""Manual reconstruction prototype: long line regimes joined by tangent arcs."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.spatial import cKDTree

from autodeck.polyarc import (
    PolyarcPrimitive,
    _angle_mismatch_deg,
    _arc_from_start_tangent,
    _deviation_distribution,
    _parameterized_biarc,
    _sample_primitives,
)


def unit(vector: np.ndarray) -> np.ndarray:
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def point_to_polyline(points: np.ndarray, polyline: np.ndarray) -> np.ndarray:
    starts = polyline[:-1]
    vectors = polyline[1:] - starts
    denominator = np.sum(vectors * vectors, axis=1)
    result = []
    for point in np.asarray(points):
        relative = point - starts
        parameter = np.divide(
            np.sum(relative * vectors, axis=1), denominator,
            out=np.zeros_like(denominator), where=denominator > 1e-12,
        )
        parameter = np.clip(parameter, 0.0, 1.0)
        nearest = starts + parameter[:, None] * vectors
        result.append(float(np.min(np.linalg.norm(point - nearest, axis=1))))
    return np.asarray(result)


def densify(polyline: np.ndarray, spacing: float = 2.0) -> np.ndarray:
    chunks = []
    for index, (start, end) in enumerate(zip(polyline[:-1], polyline[1:])):
        length = float(np.linalg.norm(end - start))
        count = max(2, int(math.ceil(length / spacing)) + 1)
        values = np.linspace(start, end, count)
        chunks.append(values if index == 0 else values[1:])
    return np.vstack(chunks) if chunks else np.asarray(polyline).copy()


def deviation(reference: np.ndarray, primitives: list[PolyarcPrimitive]) -> dict[str, float]:
    sampled = _sample_primitives(primitives, 2.0)
    forward = cKDTree(sampled).query(reference, k=1)[0]
    reverse = cKDTree(reference).query(sampled, k=1)[0]
    combined = np.concatenate([forward, reverse])
    return {
        "maximum_mm": float(np.max(combined)),
        "rms_mm": float(np.sqrt(np.mean(combined * combined))),
        "p95_mm": float(np.percentile(combined, 95)),
    }


def line_intersection(
    first_origin: np.ndarray,
    first_direction: np.ndarray,
    second_origin: np.ndarray,
    second_direction: np.ndarray,
) -> tuple[float, float, np.ndarray] | None:
    matrix = np.column_stack((first_direction, -second_direction))
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) <= 1e-9:
        return None
    parameters = np.linalg.solve(matrix, second_origin - first_origin)
    point = first_origin + float(parameters[0]) * first_direction
    return float(parameters[0]), float(parameters[1]), point


def transition_objective(
    parameters: np.ndarray,
    first: dict,
    second: dict,
    reference: np.ndarray,
    arc_count: int,
) -> tuple[float, list[PolyarcPrimitive] | None, dict[str, float] | None]:
    first_origin = np.asarray(first["start"], dtype=float)
    second_origin = np.asarray(second["start"], dtype=float)
    first_direction = unit(np.asarray(first["end"]) - first_origin)
    second_direction = unit(np.asarray(second["end"]) - second_origin)
    start = first_origin + float(parameters[0]) * first_direction
    end = second_origin + float(parameters[1]) * second_direction
    if arc_count == 1:
        primitive = _arc_from_start_tangent(start, end, first_direction, 0, 0, 0)
        if primitive is None or primitive.kind != "ARC":
            return 1e12, None, None
        tangent_error = _angle_mismatch_deg(primitive.tangent_end(), second_direction)
        primitives = [primitive]
    else:
        primitives = _parameterized_biarc(
            start, end, first_direction, second_direction, 0, 0, 0,
            math.exp(float(parameters[2])),
        )
        if primitives is None:
            return 1e12, None, None
        tangent_error = 0.0
    metrics = deviation(reference, primitives)
    excess = max(metrics["maximum_mm"] - 2.85, 0.0)
    # Tangency is a hard objective for the single-arc candidate.  The biarc is
    # analytically G1 by construction.
    objective = (
        metrics["rms_mm"] ** 2
        + 80.0 * excess * excess
        + 20.0 * (tangent_error / 0.10) ** 2
        + 1e-6 * float(np.sum(parameters * parameters))
    )
    return objective, primitives, {**metrics, "tangent_error_deg": tangent_error}


def optimize_transition(
    first: dict,
    second: dict,
    reference: np.ndarray,
    source_start: int,
    source_end: int,
    index: int,
) -> dict:
    reference = densify(reference, 2.0)
    first_length = float(first["length_mm"])
    transition_length = float(np.linalg.norm(np.diff(reference, axis=0), axis=1).sum())
    allowance = max(50.0, min(500.0, 0.75 * transition_length + 50.0))
    initial_two = np.array([first_length, 0.0, 0.0], dtype=float)
    bounds_two = [
        (first_length - allowance, first_length + allowance),
        (-allowance, allowance),
        (math.log(0.10), math.log(10.0)),
    ]
    candidates = []
    attempts = []

    intersection = line_intersection(
        np.asarray(first["start"]),
        unit(np.asarray(first["end"]) - np.asarray(first["start"])),
        np.asarray(second["start"]),
        unit(np.asarray(second["end"]) - np.asarray(second["start"])),
    )
    if intersection is not None:
        first_parameter, second_parameter, point = intersection
        hard_path = np.vstack([reference[0], point, reference[-1]])
        metrics = deviation(reference, [
            PolyarcPrimitive("LINE", reference[0], point, 0, 0, index),
            PolyarcPrimitive("LINE", point, reference[-1], 0, 0, index + 1),
        ])
        turn = _angle_mismatch_deg(
            unit(np.asarray(first["end"]) - np.asarray(first["start"])),
            unit(np.asarray(second["end"]) - np.asarray(second["start"])),
        )
        if metrics["maximum_mm"] <= 2.95 and turn >= 4.0:
            candidates.append({
                "kind": "HARD_CORNER", "arc_count": 0,
                "start_parameter": first_parameter,
                "end_parameter": second_parameter,
                "point": point,
                "primitives": [], "metrics": {**metrics, "turn_deg": turn},
            })

    for arc_count in (1, 2):
        initial = initial_two[: 2 if arc_count == 1 else 3]
        bounds = bounds_two[: 2 if arc_count == 1 else 3]

        def scalar(parameters: np.ndarray) -> float:
            return transition_objective(
                parameters, first, second, reference, arc_count
            )[0]

        best_result = None
        for seed in (
            initial,
            initial + np.array([-0.20 * allowance, 0.20 * allowance, 0.0])[: len(initial)],
        ):
            result = minimize(
                scalar, seed, method="Nelder-Mead", bounds=bounds,
                options={"maxiter": 300, "xatol": 1e-5, "fatol": 1e-5},
            )
            if best_result is None or result.fun < best_result.fun:
                best_result = result
        assert best_result is not None
        objective, primitives, metrics = transition_objective(
            best_result.x, first, second, reference, arc_count
        )
        if primitives is None or metrics is None:
            continue
        first_origin = np.asarray(first["start"], dtype=float)
        second_origin = np.asarray(second["start"], dtype=float)
        first_direction = unit(np.asarray(first["end"]) - first_origin)
        second_direction = unit(np.asarray(second["end"]) - second_origin)
        attempt = {
            "kind": "TANGENT_ARC" if arc_count == 1 else "TANGENT_BIARC",
            "arc_count": arc_count,
            "start_parameter": float(best_result.x[0]),
            "end_parameter": float(best_result.x[1]),
            "point": None,
            "primitives": primitives,
            "metrics": metrics,
            "objective": float(objective),
        }
        attempts.append(attempt)
        if (
            metrics["maximum_mm"] <= 2.95
            and metrics["tangent_error_deg"] <= 0.10 + 1e-8
        ):
            candidates.append(attempt)
    if not candidates:
        # Return the least-bad optimized transition as a visible diagnostic
        # without certifying it.
        if attempts:
            selected = min(
                attempts,
                key=lambda item: (
                    item["metrics"]["maximum_mm"], item["metrics"]["rms_mm"]
                ),
            )
            selected["kind"] = "INVALID_" + selected["kind"]
            return selected
        return {
            "kind": "INVALID_BIARC", "arc_count": 0,
            "start_parameter": first_length, "end_parameter": 0.0,
            "point": None, "primitives": [],
            "metrics": {"maximum_mm": float("inf")}, "objective": 1e12,
        }
    return min(
        candidates,
        key=lambda item: (item["arc_count"], item["metrics"]["maximum_mm"], item["metrics"]["rms_mm"]),
    )


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    run = repo / "output" / "key-west-v033-orientation"
    payloads = json.loads((run / "prototype_manual_2.95.json").read_text())
    lines = [item for item in payloads if item["kind"] == "LINE"]
    with np.load(run / "primary_broad_reference_diagnostic.npz") as archive:
        reference = archive["points"]
    count = len(reference)
    extended = np.vstack([reference, reference, reference])
    transitions = []
    for index, first in enumerate(lines):
        second = lines[(index + 1) % len(lines)]
        source_start = int(first["source_end_index"])
        source_end = int(second["source_start_index"])
        while source_end <= source_start:
            source_end += count
        transition_reference = extended[source_start : source_end + 1]
        selected = optimize_transition(
            first, second, transition_reference, source_start, source_end, index
        )
        selected["source_start_index"] = source_start
        selected["source_end_index"] = source_end
        transitions.append(selected)
        print(
            "transition", index, selected["kind"], selected["arc_count"],
            selected["metrics"],
            flush=True,
        )

    chain = []
    for index, line in enumerate(lines):
        before = transitions[index - 1]
        after = transitions[index]
        origin = np.asarray(line["start"], dtype=float)
        direction = unit(np.asarray(line["end"]) - origin)
        start = (
            np.asarray(before["point"], dtype=float)
            if before["kind"] == "HARD_CORNER"
            else origin + float(before["end_parameter"]) * direction
        )
        end = (
            np.asarray(after["point"], dtype=float)
            if after["kind"] == "HARD_CORNER"
            else origin + float(after["start_parameter"]) * direction
        )
        chain.append(PolyarcPrimitive(
            "LINE", start, end, int(line["source_start_index"]),
            int(line["source_end_index"]), index,
        ))
        for arc in after["primitives"]:
            arc.logical_span_index = index
            arc.source_start_index = int(after["source_start_index"])
            arc.source_end_index = int(after["source_end_index"])
            chain.append(arc)
    sampled = _sample_primitives(chain, 0.5)
    if float(np.linalg.norm(sampled[-1] - sampled[0])) > 1e-8:
        sampled = np.vstack([sampled, sampled[0]])
    closed_reference = np.vstack([reference, reference[0]])
    metrics = _deviation_distribution(closed_reference, sampled)
    gaps = [
        float(np.linalg.norm(before.end - after.start))
        for before, after in zip(chain, chain[1:] + chain[:1])
    ]
    mismatches = [
        _angle_mismatch_deg(before.tangent_end(), after.tangent_start())
        for before, after in zip(chain, chain[1:] + chain[:1])
        if before.logical_span_index == after.logical_span_index
    ]
    summary = {
        "line_count": sum(item.kind == "LINE" for item in chain),
        "arc_count": sum(item.kind == "ARC" for item in chain),
        "primitive_count": len(chain),
        "transition_counts": {
            kind: sum(item["kind"] == kind for item in transitions)
            for kind in sorted(set(item["kind"] for item in transitions))
        },
        "deviation": metrics,
        "maximum_gap_mm": max(gaps, default=0.0),
        "maximum_smooth_tangent_mismatch_deg": max(mismatches, default=0.0),
        "transitions": [
            {key: value for key, value in item.items() if key != "primitives"}
            for item in transitions
        ],
        "primitives": [item.to_dict() for item in chain],
    }
    print(json.dumps({key: value for key, value in summary.items() if key not in {"primitives", "transitions"}}, indent=2))
    (run / "prototype_line_biarc.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
