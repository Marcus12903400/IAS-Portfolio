from __future__ import annotations

"""Robust physical-reference curves for V0.3.3 manufacturing fitting.

The reference is deliberately not CAM geometry.  It estimates the persistent
physical contour beneath raster stair-steps and isolated scanner spikes while
keeping every protected manufacturing landmark exact.
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from shapely import contains_xy, intersects_xy
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points

from .curve_fit import (
    ManufacturingCurve,
    _collapse_consecutive_samples,
    _symmetric_deviation,
    map_points_to_indices,
)


@dataclass
class RobustReferenceCurve:
    curve_id: str
    candidate_id: int
    closed: bool
    raw_points: np.ndarray
    points: np.ndarray
    hard_corner_indices: list[int]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    status: str = "TEST_REFERENCE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "candidate_id": int(self.candidate_id),
            "closed": bool(self.closed),
            "point_count": int(len(self.points)),
            "hard_corner_indices": [int(value) for value in self.hard_corner_indices],
            "metrics": self.metrics,
            "warnings": list(self.warnings),
            "status": self.status,
        }


def _weighted_local_polynomial(
    offsets: np.ndarray,
    values: np.ndarray,
    radius: float,
) -> np.ndarray:
    """Return the robust quadratic estimate at offset zero."""

    if len(values) < 2:
        return values[0].copy()
    degree = 2 if len(values) >= 5 else 1
    design = np.column_stack([offsets ** power for power in range(degree + 1)])
    normalized = np.clip(np.abs(offsets) / max(radius, 1e-9), 0.0, 1.0)
    spatial = (1.0 - normalized ** 3) ** 3
    spatial = np.maximum(spatial, 1e-4)
    robust = np.ones(len(values), dtype=float)
    coefficients = np.zeros((degree + 1, values.shape[1]), dtype=float)
    for _iteration in range(3):
        weights = spatial * robust
        root = np.sqrt(weights)[:, None]
        coefficients = np.linalg.lstsq(design * root, values * root, rcond=None)[0]
        residual = np.linalg.norm(values - design @ coefficients, axis=1)
        median = float(np.median(residual))
        scale = max(1.4826 * float(np.median(np.abs(residual - median))), 0.05)
        huber = 1.345 * scale
        robust = np.ones(len(values), dtype=float)
        outside = residual > huber
        robust[outside] = huber / np.maximum(residual[outside], 1e-12)
    return coefficients[0]


def _arc_positions(points: np.ndarray, closed: bool) -> tuple[np.ndarray, float]:
    values = np.asarray(points, dtype=float)
    if len(values) < 2:
        return np.zeros(len(values)), 0.0
    lengths = np.linalg.norm(np.diff(values, axis=0), axis=1)
    positions = np.concatenate(([0.0], np.cumsum(lengths)))
    perimeter = float(positions[-1])
    if closed:
        perimeter += float(np.linalg.norm(values[0] - values[-1]))
    return positions, perimeter


def _smooth_scale(points: np.ndarray, closed: bool, support_mm: float) -> np.ndarray:
    values = np.asarray(points, dtype=float)
    if len(values) < 3:
        return values.copy()
    positions, perimeter = _arc_positions(values, closed)
    radius = max(float(support_mm) * 0.5, 1.0)
    result = np.empty_like(values)
    for index, center in enumerate(positions):
        if closed and perimeter > 0.0:
            offsets = (positions - center + 0.5 * perimeter) % perimeter - 0.5 * perimeter
        else:
            offsets = positions - center
        neighbors = np.flatnonzero(np.abs(offsets) <= radius + 1e-9)
        if len(neighbors) < 5:
            neighbors = np.argsort(np.abs(offsets))[: min(5, len(values))]
        local_radius = max(radius, float(np.max(np.abs(offsets[neighbors]), initial=radius)))
        result[index] = _weighted_local_polynomial(
            offsets[neighbors], values[neighbors], local_radius,
        )
    return result


def _smooth_open_span(points: np.ndarray, scales_mm: list[float]) -> tuple[np.ndarray, list[np.ndarray]]:
    values = np.asarray(points, dtype=float)
    estimates = [_smooth_scale(values, False, scale) for scale in scales_mm]
    combined = np.average(
        np.stack(estimates, axis=0), axis=0,
        weights=np.asarray(scales_mm, dtype=float),
    )
    if len(combined):
        combined[0] = values[0]
        combined[-1] = values[-1]
    return combined, estimates


def _reference_points(
    raw: np.ndarray,
    closed: bool,
    anchors: list[int],
    scales_mm: list[float],
    curvature_threshold_per_mm: float,
    minimum_regime_length_mm: float,
) -> tuple[np.ndarray, list[np.ndarray]]:
    if len(raw) < 3:
        return raw.copy(), [raw.copy() for _ in scales_mm]
    if not anchors and not closed:
        positions, _perimeter = _arc_positions(raw, False)
        delta = np.diff(raw, axis=0)
        lengths = np.linalg.norm(delta, axis=1)
        directions = np.divide(delta, lengths[:, None], out=np.zeros_like(delta), where=lengths[:, None] > 1e-9)
        turn = np.arctan2(
            directions[:-1, 0] * directions[1:, 1] - directions[:-1, 1] * directions[1:, 0],
            np.clip(np.sum(directions[:-1] * directions[1:], axis=1), -1.0, 1.0),
        )
        curvature = np.zeros(len(raw), dtype=float)
        curvature[1:-1] = np.abs(turn) / np.maximum(0.5 * (lengths[:-1] + lengths[1:]), 1e-9)
        filtered = curvature.copy()
        for index in range(1, len(raw) - 1):
            neighborhood = np.flatnonzero(np.abs(positions - positions[index]) <= 7.5)
            filtered[index] = float(np.median(curvature[neighborhood])) if len(neighborhood) else curvature[index]
        curved = filtered >= float(curvature_threshold_per_mm)
        curved[0] = curved[1]; curved[-1] = curved[-2]
        # Physical-persistence cleanup removes spike-scale regime chatter.
        changed = True
        while changed:
            changed = False
            starts = np.r_[0, np.flatnonzero(curved[1:] != curved[:-1]) + 1]
            ends = np.r_[starts[1:], len(curved)]
            for run_index, (start, end) in enumerate(zip(starts, ends)):
                length = float(positions[end - 1] - positions[start])
                if length >= minimum_regime_length_mm or len(starts) == 1:
                    continue
                replacement = (
                    curved[ends[run_index]] if run_index == 0
                    else curved[starts[run_index] - 1]
                )
                curved[start:end] = replacement
                changed = True
                break
        transitions = (np.flatnonzero(curved[1:] != curved[:-1]) + 1).astype(int).tolist()
        if transitions:
            combined = raw.copy()
            scale_outputs = [raw.copy() for _ in scales_mm]
            cuts = [0, *transitions, len(raw) - 1]
            for start, end in zip(cuts[:-1], cuts[1:]):
                if end <= start:
                    continue
                span, span_scales = _smooth_open_span(raw[start:end + 1], scales_mm)
                combined[start:end + 1] = span
                for scale_index, estimate in enumerate(span_scales):
                    scale_outputs[scale_index][start:end + 1] = estimate
            combined[cuts] = raw[cuts]
            for output in scale_outputs:
                output[cuts] = raw[cuts]
            return combined, scale_outputs
    if not anchors:
        estimates = [_smooth_scale(raw, closed, scale) for scale in scales_mm]
        combined = np.average(
            np.stack(estimates, axis=0), axis=0,
            weights=np.asarray(scales_mm, dtype=float),
        )
        if not closed:
            combined[0] = raw[0]
            combined[-1] = raw[-1]
        return combined, estimates

    combined = raw.copy()
    scale_outputs = [raw.copy() for _ in scales_mm]
    cuts = sorted(set(int(value) % len(raw) for value in anchors))
    if not closed:
        cuts = sorted(set([0, len(raw) - 1, *cuts]))
    pairs = list(zip(cuts[:-1], cuts[1:]))
    if closed:
        pairs.append((cuts[-1], cuts[0] + len(raw)))
    for start, end in pairs:
        indices = np.arange(start, end + 1, dtype=int) % len(raw)
        span, span_scales = _smooth_open_span(raw[indices], scales_mm)
        combined[indices] = span
        for scale_index, estimate in enumerate(span_scales):
            scale_outputs[scale_index][indices] = estimate
    combined[cuts] = raw[cuts]
    for output in scale_outputs:
        output[cuts] = raw[cuts]
    return combined, scale_outputs


def _distribution(values: np.ndarray) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if not len(array):
        return {"mean_mm": 0.0, "rms_mm": 0.0, "p95_mm": 0.0, "p99_mm": 0.0, "maximum_mm": 0.0}
    return {
        "mean_mm": float(np.mean(array)),
        "rms_mm": float(np.sqrt(np.mean(array * array))),
        "p95_mm": float(np.percentile(array, 95)),
        "p99_mm": float(np.percentile(array, 99)),
        "maximum_mm": float(np.max(array)),
    }


def _signed_safe_reference(
    raw: np.ndarray,
    reference: np.ndarray,
    feature_side: str,
    allowance_mm: float,
) -> tuple[np.ndarray, int, float]:
    """Project only genuinely forbidden reference samples to the safe set."""

    if feature_side not in {"outer", "obstacle"} or len(raw) < 3:
        return reference.copy(), 0, 0.0
    polygon = Polygon(raw)
    if not polygon.is_valid:
        polygon = polygon.buffer(0.0)
    if polygon.is_empty:
        return reference.copy(), 0, 0.0
    if feature_side == "outer":
        safe = polygon.buffer(max(float(allowance_mm), 0.0))
        invalid = ~intersects_xy(safe, reference[:, 0], reference[:, 1])
        target_boundary = safe.boundary
    else:
        forbidden = polygon.buffer(-max(float(allowance_mm), 0.0))
        if forbidden.is_empty:
            return reference.copy(), 0, 0.0
        invalid = contains_xy(forbidden, reference[:, 0], reference[:, 1])
        target_boundary = forbidden.boundary
    result = reference.copy()
    displacement = np.zeros(len(reference), dtype=float)
    for index in np.flatnonzero(invalid):
        projected = nearest_points(Point(reference[index]), target_boundary)[1]
        point = np.asarray(projected.coords[0], dtype=float)
        displacement[index] = float(np.linalg.norm(point - reference[index]))
        result[index] = point
    return result, int(np.count_nonzero(invalid)), float(np.max(displacement, initial=0.0))


def fit_robust_reference_curve(
    curve: ManufacturingCurve,
    configuration: dict[str, Any] | None = None,
) -> RobustReferenceCurve:
    settings = dict(configuration or {})
    scales = [float(value) for value in settings.get("persistence_scales_mm", [5.0, 10.0, 20.0, 40.0])]
    if not scales or any(value <= 0.0 for value in scales):
        raise ValueError("robust_reference.persistence_scales_mm must contain positive physical distances")
    closed = bool(curve.closed)
    raw, _removed = _collapse_consecutive_samples(
        np.asarray(curve.source.points_mm, dtype=float),
        closed,
        float(settings.get("minimum_numerical_sample_separation_mm", 0.10)),
    )
    raw = raw[:, :2]
    # curve.anchor_indices are indices into curve_fit.py's own (possibly
    # smoothed/resampled) corner-detection array, not into `raw` above, which
    # this function collapses independently. Resolve by nearest coordinate
    # match instead of reusing the indices directly (see map_points_to_indices).
    anchors = (
        map_points_to_indices(
            curve.anchor_points_mm, raw,
            context=f"robust_reference[{curve.cam_curve_id}] hard-corner anchor",
        )
        if len(raw)
        else []
    )
    reference, scale_outputs = _reference_points(
        raw,
        closed,
        anchors,
        scales,
        float(settings.get("curvature_regime_threshold_per_mm", 0.002)),
        float(settings.get("minimum_curvature_regime_length_mm", 20.0)),
    )
    layer = curve.source.source.raw_curve.layer.upper()
    feature_side = "obstacle" if "OBSTACLE" in layer else (
        "outer" if "DECK_PRIMARY" in layer or "DECK_SECONDARY" in layer else "none"
    )
    reference, safety_adjustment_count, maximum_safety_adjustment = _signed_safe_reference(
        raw,
        reference,
        feature_side,
        float(settings.get("signed_safety_allowance_mm", 0.25)),
    )
    if anchors:
        reference[anchors] = raw[anchors]
    raw_to_reference = cKDTree(reference).query(raw, k=1)[0] if len(reference) else np.empty(0)
    reference_to_raw = cKDTree(raw).query(reference, k=1)[0] if len(raw) else np.empty(0)
    symmetric = _symmetric_deviation(raw, reference)
    per_scale = []
    for scale, output in zip(scales, scale_outputs):
        displacement = np.linalg.norm(output - raw, axis=1)
        per_scale.append({"scale_mm": scale, "displacement": _distribution(displacement)})
    corner_error = (
        float(np.max(np.linalg.norm(reference[anchors] - raw[anchors], axis=1), initial=0.0))
        if anchors else 0.0
    )
    warnings: list[str] = []
    maximum_reference_shift = float(settings.get("maximum_reference_shift_warning_mm", 8.0))
    if float(np.max(raw_to_reference, initial=0.0)) > maximum_reference_shift:
        warnings.append(
            f"robust reference moves more than {maximum_reference_shift:.3f} mm from raw at isolated locations"
        )
    metrics = {
        "method": "multi-scale robust local quadratic with Huber IRLS and physical-support-weighted scale consensus",
        "persistence_scales_mm": scales,
        "raw_point_count": int(len(raw)),
        "reference_point_count": int(len(reference)),
        "protected_corner_count": int(len(anchors)),
        "maximum_protected_corner_error_mm": corner_error,
        "raw_to_reference": _distribution(raw_to_reference),
        "reference_to_raw": _distribution(reference_to_raw),
        "symmetric_deviation": symmetric,
        "scale_diagnostics": per_scale,
        "original_raw_preserved": True,
        "signed_feature_side": feature_side,
        "signed_safety_adjustment_count": safety_adjustment_count,
        "maximum_signed_safety_adjustment_mm": maximum_safety_adjustment,
    }
    return RobustReferenceCurve(
        curve_id=f"robust-{curve.cam_curve_id}",
        candidate_id=int(curve.cam_curve_id.split("-")[1]) if curve.cam_curve_id.startswith("cam-") else 0,
        closed=closed,
        raw_points=raw,
        points=reference,
        hard_corner_indices=anchors,
        metrics=metrics,
        warnings=warnings,
    )


def fit_robust_reference_curves(
    curves: list[ManufacturingCurve],
    configuration: dict[str, Any] | None = None,
) -> list[RobustReferenceCurve]:
    return [fit_robust_reference_curve(curve, configuration) for curve in curves]
