"""V0.3.2 G1 polyarc manufacturing-geometry fitting.

The fitter deliberately operates downstream of the V0.3.1 manufacturing spline.
It preserves protected corner landmarks, builds every logical span from LINE and
true circular ARC primitives, and validates the result against the developed raw
perimeter rather than against the V0.3.1 spline.

The recursive construction is an equal-tangent-distance biarc method followed by
long-window primitive reduction.  It follows the established biarc pattern: a
pair of circular arcs interpolate two points and their endpoint tangent states,
with an exact G1 join.  Straight lines are retained as the degenerate case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Callable, Literal

import numpy as np
from scipy.spatial import cKDTree
import shapely
from shapely import contains_xy, intersects_xy
from shapely.geometry import LineString, Polygon

from .curve_fit import (
    ManufacturingCurve,
    _collapse_consecutive_samples,
    _corridor_targets,
    _feature_profile,
    _forbidden_violations,
    _sample_polyline,
    _symmetric_deviation,
    map_points_to_indices,
)


PrimitiveKind = Literal["LINE", "ARC"]


class SharpCornerRequiresReview(Exception):
    """Raised when no LINE/ARC/biarc candidate can satisfy a required G1
    tangent constraint within tolerance at the finest possible interval.

    This must never be papered over with a bare untangented chord (the
    historical tangent-losing-fallback bug): either add more geometry to
    close the gap, or flag the location for manual review and fall back to
    preserved raw geometry there -- never silently accept a kink.
    """

    def __init__(
        self,
        local_start: int,
        local_end: int,
        *,
        requires_start_tangent: bool,
        requires_end_tangent: bool,
    ) -> None:
        super().__init__(
            f"no tangent-preserving LINE/ARC/biarc candidate found for local interval "
            f"[{local_start}, {local_end}] (start tangent required={requires_start_tangent}, "
            f"end tangent required={requires_end_tangent})"
        )
        self.local_start = local_start
        self.local_end = local_end
        self.requires_start_tangent = requires_start_tangent
        self.requires_end_tangent = requires_end_tangent


def _unit(vector: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    value = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(value))
    if norm > 1e-12:
        return value / norm
    if fallback is not None:
        return _unit(fallback)
    return np.array([1.0, 0.0], dtype=float)


def _left(vector: np.ndarray) -> np.ndarray:
    return np.array([-float(vector[1]), float(vector[0])], dtype=float)


def _angle_mismatch_deg(first: np.ndarray, second: np.ndarray) -> float:
    a = _unit(first)
    b = _unit(second)
    return float(math.degrees(math.acos(float(np.clip(np.dot(a, b), -1.0, 1.0)))))


def _deviation_distribution(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    ref = _sample_polyline(np.asarray(reference), 0.25)[:, :2]
    cam = _sample_polyline(np.asarray(candidate), 0.25)[:, :2]
    combined = np.concatenate((cKDTree(cam).query(ref, k=1)[0], cKDTree(ref).query(cam, k=1)[0]))
    return {
        "mean_mm": float(np.mean(combined)),
        "rms_mm": float(np.sqrt(np.mean(combined * combined))),
        "p95_mm": float(np.percentile(combined, 95)),
        "p99_mm": float(np.percentile(combined, 99)),
        "maximum_mm": float(np.max(combined)),
    }


@dataclass
class PolyarcPrimitive:
    kind: PrimitiveKind
    start: np.ndarray
    end: np.ndarray
    source_start_index: int
    source_end_index: int
    logical_span_index: int
    center: np.ndarray | None = None
    radius_mm: float | None = None
    signed_sweep_rad: float = 0.0
    sharp_corner_review: bool = False

    @property
    def length_mm(self) -> float:
        if self.kind == "LINE":
            return float(np.linalg.norm(self.end - self.start))
        return float(abs(self.signed_sweep_rad) * float(self.radius_mm or 0.0))

    @property
    def sweep_deg(self) -> float:
        return float(math.degrees(self.signed_sweep_rad))

    @property
    def bulge(self) -> float:
        if self.kind == "LINE":
            return 0.0
        return float(math.tan(self.signed_sweep_rad / 4.0))

    def tangent_start(self) -> np.ndarray:
        if self.kind == "LINE":
            return _unit(self.end - self.start)
        radial = _unit(self.start - np.asarray(self.center, dtype=float))
        return _left(radial) if self.signed_sweep_rad >= 0.0 else -_left(radial)

    def tangent_end(self) -> np.ndarray:
        if self.kind == "LINE":
            return _unit(self.end - self.start)
        radial = _unit(self.end - np.asarray(self.center, dtype=float))
        return _left(radial) if self.signed_sweep_rad >= 0.0 else -_left(radial)

    def sample(self, spacing_mm: float = 0.5) -> np.ndarray:
        if self.kind == "LINE" or self.center is None or not self.radius_mm:
            count = max(2, int(math.ceil(self.length_mm / max(spacing_mm, 0.05))) + 1)
            return np.linspace(self.start, self.end, count)
        center = np.asarray(self.center, dtype=float)
        start_angle = math.atan2(self.start[1] - center[1], self.start[0] - center[0])
        count = max(3, int(math.ceil(self.length_mm / max(spacing_mm, 0.05))) + 1)
        angles = start_angle + np.linspace(0.0, self.signed_sweep_rad, count)
        points = center[None, :] + float(self.radius_mm) * np.column_stack(
            [np.cos(angles), np.sin(angles)]
        )
        points[0] = self.start
        points[-1] = self.end
        return points

    def reversed(self) -> "PolyarcPrimitive":
        return PolyarcPrimitive(
            kind=self.kind,
            start=self.end.copy(),
            end=self.start.copy(),
            source_start_index=self.source_start_index,
            source_end_index=self.source_end_index,
            logical_span_index=self.logical_span_index,
            center=None if self.center is None else self.center.copy(),
            radius_mm=self.radius_mm,
            signed_sweep_rad=-self.signed_sweep_rad,
            sharp_corner_review=self.sharp_corner_review,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": [float(v) for v in self.start],
            "end": [float(v) for v in self.end],
            "source_start_index": int(self.source_start_index),
            "source_end_index": int(self.source_end_index),
            "logical_span_index": int(self.logical_span_index),
            "length_mm": self.length_mm,
            "center": None if self.center is None else [float(v) for v in self.center],
            "radius_mm": self.radius_mm,
            "signed_sweep_deg": self.sweep_deg,
            "bulge": self.bulge,
            "sharp_corner_review": self.sharp_corner_review,
        }


@dataclass
class PolyarcJoin:
    index: int
    join_type: Literal["HARD_CORNER", "SMOOTH_G1"]
    point: np.ndarray
    gap_mm: float
    tangent_mismatch_deg: float | None
    logical_span_before: int
    logical_span_after: int
    sharp_corner_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "join_type": self.join_type,
            "point": [float(v) for v in self.point],
            "gap_mm": float(self.gap_mm),
            "tangent_mismatch_deg": self.tangent_mismatch_deg,
            "logical_span_before": int(self.logical_span_before),
            "logical_span_after": int(self.logical_span_after),
            "sharp_corner_review": self.sharp_corner_review,
        }


@dataclass
class PolyarcCurve:
    candidate_id: int
    is_closed: bool
    raw_points: np.ndarray
    reference_points: np.ndarray
    hard_corner_indices: list[int]
    hard_corner_points: np.ndarray
    primitives: list[PolyarcPrimitive]
    joins: list[PolyarcJoin]
    sampled_points: np.ndarray
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    status: str = "TEST_GEOMETRY"

    @property
    def primitive_count(self) -> int:
        return len(self.primitives)

    @property
    def line_count(self) -> int:
        return sum(item.kind == "LINE" for item in self.primitives)

    @property
    def arc_count(self) -> int:
        return sum(item.kind == "ARC" for item in self.primitives)

    @property
    def max_deviation_mm(self) -> float:
        return float(self.metrics.get("max_deviation_mm", float("inf")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": int(self.candidate_id),
            "is_closed": bool(self.is_closed),
            "raw_point_count": int(len(self.raw_points)),
            "reference_point_count": int(len(self.reference_points)),
            "hard_corner_indices": [int(v) for v in self.hard_corner_indices],
            "hard_corner_points": self.hard_corner_points.astype(float).tolist(),
            "primitive_count": self.primitive_count,
            "line_count": self.line_count,
            "arc_count": self.arc_count,
            "primitives": [item.to_dict() for item in self.primitives],
            "joins": [item.to_dict() for item in self.joins],
            "metrics": self.metrics,
            "warnings": list(self.warnings),
            "status": self.status,
        }


def _arc_from_start_tangent(
    start: np.ndarray,
    end: np.ndarray,
    tangent: np.ndarray,
    source_start_index: int,
    source_end_index: int,
    logical_span_index: int,
) -> PolyarcPrimitive | None:
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    tangent = _unit(tangent)
    delta = end - start
    chord = float(np.linalg.norm(delta))
    if chord <= 1e-9:
        return None
    normal = _left(tangent)
    denominator = 2.0 * float(np.dot(delta, normal))
    if abs(denominator) <= 1e-10 * max(chord, 1.0):
        if _angle_mismatch_deg(delta, tangent) <= 0.1:
            return PolyarcPrimitive(
                kind="LINE",
                start=start,
                end=end,
                source_start_index=source_start_index,
                source_end_index=source_end_index,
                logical_span_index=logical_span_index,
            )
        return None
    signed_radius = float(np.dot(delta, delta) / denominator)
    center = start + signed_radius * normal
    radius = abs(signed_radius)
    if not np.isfinite(radius) or radius > 1e10:
        return None
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    if signed_radius > 0.0:
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
    else:
        sweep = -((start_angle - end_angle) % (2.0 * math.pi))
    if abs(sweep) > math.pi + 1e-7:
        # Equal-distance biarcs should use the minor arc.  A major arc here is a
        # numerical or tangent-state degeneracy and should be split instead.
        return None
    return PolyarcPrimitive(
        kind="ARC",
        start=start,
        end=end,
        source_start_index=source_start_index,
        source_end_index=source_end_index,
        logical_span_index=logical_span_index,
        center=center,
        radius_mm=radius,
        signed_sweep_rad=float(sweep),
    )


def _parameterized_biarc(
    start: np.ndarray,
    end: np.ndarray,
    tangent_start: np.ndarray,
    tangent_end: np.ndarray,
    source_start_index: int,
    source_end_index: int,
    logical_span_index: int,
    distance_ratio: float = 1.0,
) -> list[PolyarcPrimitive] | None:
    """Construct a member of the standard one-parameter G1 biarc family.

    ``distance_ratio`` is the end/start tangent distance ratio.  A value of one
    gives the common equal-distance construction; nearby ratios robustly cover
    asymmetric and inflection-adjacent intervals without relaxing G1.
    """

    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    t0 = _unit(tangent_start)
    t1 = _unit(tangent_end)
    chord = end - start
    chord_length = float(np.linalg.norm(chord))
    if chord_length <= 1e-9:
        return None

    ratio = float(distance_ratio)
    if not np.isfinite(ratio) or ratio <= 0.0:
        return None
    one_minus_dot = 1.0 - float(np.clip(np.dot(t0, t1), -1.0, 1.0))
    if one_minus_dot <= 1e-12 and _angle_mismatch_deg(chord, t0) <= 0.1:
            return [
                PolyarcPrimitive(
                    kind="LINE",
                    start=start,
                    end=end,
                    source_start_index=source_start_index,
                    source_end_index=source_end_index,
                    logical_span_index=logical_span_index,
                )
            ]

    quadratic = 2.0 * ratio * one_minus_dot
    linear = 2.0 * float(np.dot(chord, t0 + ratio * t1))
    constant = -float(np.dot(chord, chord))
    if abs(quadratic) <= 1e-14:
        if abs(linear) <= 1e-14:
            return None
        distance_start = -constant / linear
    else:
        discriminant = linear * linear - 4.0 * quadratic * constant
        if discriminant < 0.0:
            return None
        distance_start = (-linear + math.sqrt(max(discriminant, 0.0))) / (2.0 * quadratic)
    distance_end = ratio * distance_start
    if (
        not np.isfinite(distance_start)
        or not np.isfinite(distance_end)
        or distance_start <= 1e-9
        or distance_end <= 1e-9
    ):
        return None

    endpoint_start = start + distance_start * t0
    endpoint_end = end - distance_end * t1
    joint = (
        distance_end * endpoint_start + distance_start * endpoint_end
    ) / (distance_start + distance_end)
    first = _arc_from_start_tangent(
        start,
        joint,
        t0,
        source_start_index,
        source_end_index,
        logical_span_index,
    )
    reverse_second = _arc_from_start_tangent(
        end,
        joint,
        -t1,
        source_start_index,
        source_end_index,
        logical_span_index,
    )
    if first is None or reverse_second is None:
        return None
    second = reverse_second.reversed()
    gap = float(np.linalg.norm(first.end - second.start))
    tangent_error = _angle_mismatch_deg(first.tangent_end(), second.tangent_start())
    if gap > 1e-7 or tangent_error > 1e-5:
        return None
    return [first, second]


def _equal_distance_biarc(
    start: np.ndarray,
    end: np.ndarray,
    tangent_start: np.ndarray,
    tangent_end: np.ndarray,
    source_start_index: int,
    source_end_index: int,
    logical_span_index: int,
) -> list[PolyarcPrimitive] | None:
    return _parameterized_biarc(
        start,
        end,
        tangent_start,
        tangent_end,
        source_start_index,
        source_end_index,
        logical_span_index,
        1.0,
    )


def _fit_endpoint_arc(
    points: np.ndarray,
    source_start_index: int,
    source_end_index: int,
    logical_span_index: int,
) -> PolyarcPrimitive | None:
    """Least-squares circle with both logical endpoints interpolated exactly."""

    values = np.asarray(points, dtype=float)
    if len(values) < 3:
        return None
    start = values[0]
    end = values[-1]
    chord = end - start
    length = float(np.linalg.norm(chord))
    if length <= 1e-9:
        return None
    midpoint = 0.5 * (start + end)
    normal = _left(chord / length)
    relative = values - midpoint
    along = relative @ (chord / length)
    across = relative @ normal
    # Centers satisfying equal endpoint radii lie on the chord bisector.
    denominator = 2.0 * across
    numerator = along * along + across * across - 0.25 * length * length
    valid = np.abs(denominator) > 1e-9
    if not np.any(valid):
        return None
    center_offset = float(np.median(numerator[valid] / denominator[valid]))
    center = midpoint + center_offset * normal
    radius = float(np.linalg.norm(start - center))
    if not np.isfinite(radius) or radius <= 1e-6 or radius > 1e10:
        return None
    angles = np.unwrap(np.arctan2(values[:, 1] - center[1], values[:, 0] - center[0]))
    sweep = float(angles[-1] - angles[0])
    if abs(sweep) <= 1e-7 or abs(sweep) > math.pi + 1e-7:
        return None
    return PolyarcPrimitive(
        kind="ARC",
        start=start.copy(),
        end=end.copy(),
        source_start_index=source_start_index,
        source_end_index=source_end_index,
        logical_span_index=logical_span_index,
        center=center,
        radius_mm=radius,
        signed_sweep_rad=sweep,
    )


def _sample_primitives(primitives: list[PolyarcPrimitive], spacing_mm: float = 0.5) -> np.ndarray:
    if not primitives:
        return np.empty((0, 2), dtype=float)
    chunks: list[np.ndarray] = []
    for index, primitive in enumerate(primitives):
        sampled = primitive.sample(spacing_mm)
        chunks.append(sampled if index == 0 else sampled[1:])
    return np.vstack(chunks)


def _tangent_field(points: np.ndarray, window_mm: float) -> np.ndarray:
    values = np.asarray(points, dtype=float)
    if len(values) < 2:
        return np.tile(np.array([[1.0, 0.0]]), (len(values), 1))
    segment_lengths = np.linalg.norm(np.diff(values, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
    tangents = np.empty_like(values)
    half_window = max(float(window_mm) * 0.5, 2.0)
    for index, distance in enumerate(cumulative):
        left_index = int(np.searchsorted(cumulative, distance - half_window, side="left"))
        right_index = int(np.searchsorted(cumulative, distance + half_window, side="right") - 1)
        left_index = max(0, min(left_index, len(values) - 2))
        right_index = max(left_index + 1, min(right_index, len(values) - 1))
        direction = values[right_index] - values[left_index]
        if np.linalg.norm(direction) <= 1e-9:
            direction = values[min(index + 1, len(values) - 1)] - values[max(index - 1, 0)]
        tangents[index] = _unit(direction)
    for index in range(1, len(tangents)):
        if float(np.dot(tangents[index - 1], tangents[index])) < 0.0:
            tangents[index] *= -1.0
    return tangents


def _limit_tangents_to_local_progression(
    points: np.ndarray,
    tangents: np.ndarray,
    maximum_offset_deg: float,
) -> np.ndarray:
    """Keep a smooth tangent field compatible with local path progression."""

    values = np.asarray(points, dtype=float)
    result = np.asarray(tangents, dtype=float).copy()
    limit = math.radians(max(float(maximum_offset_deg), 0.0))
    if len(values) < 2 or limit >= math.pi:
        return result
    for index in range(len(values)):
        if index == 0:
            local = _unit(values[1] - values[0])
        elif index == len(values) - 1:
            local = _unit(values[-1] - values[-2])
        else:
            local = _unit(values[index + 1] - values[index - 1])
        tangent = _unit(result[index])
        signed = math.atan2(
            float(local[0] * tangent[1] - local[1] * tangent[0]),
            float(np.clip(np.dot(local, tangent), -1.0, 1.0)),
        )
        signed = float(np.clip(signed, -limit, limit))
        cosine = math.cos(signed); sine = math.sin(signed)
        result[index] = np.array([
            cosine * local[0] - sine * local[1],
            sine * local[0] + cosine * local[1],
        ])
    return result


@dataclass
class _FitContext:
    raw_full: np.ndarray
    source: np.ndarray
    validation_source: np.ndarray
    guide_to_validation_index: np.ndarray
    targets: np.ndarray
    tangents: np.ndarray
    source_index_offset: int
    logical_span_index: int
    closed_reference: bool
    feature_side: str
    tolerance_mm: float
    tangent_max_deg: float
    sampling_mm: float
    max_depth: int
    allowed_region: Any | None = None
    forbidden_region: Any | None = None
    candidate_cache: dict[tuple[Any, ...], list["_Candidate"]] = field(default_factory=dict)
    diagnostics: dict[str, int] = field(default_factory=dict)


@dataclass
class _Candidate:
    primitives: list[PolyarcPrimitive]
    max_deviation_mm: float
    rms_deviation_mm: float
    forbidden_violations: int
    self_intersection: bool
    tangent_start_error_deg: float
    tangent_end_error_deg: float

    @property
    def valid(self) -> bool:
        return not self.self_intersection and self.forbidden_violations == 0


def _candidate_metrics(
    context: _FitContext,
    local_start: int,
    local_end: int,
    primitives: list[PolyarcPrimitive],
    constrain_start_tangent: bool,
    constrain_end_tangent: bool,
    start_tangent_reference: np.ndarray | None = None,
    end_tangent_reference: np.ndarray | None = None,
) -> _Candidate:
    sampled = _sample_primitives(primitives, context.sampling_mm)
    validation_start = int(context.guide_to_validation_index[local_start])
    validation_end = int(context.guide_to_validation_index[local_end])
    source = context.validation_source[validation_start : validation_end + 1]
    # Local fit decisions use a conservative cached-density check.  Re-running
    # the full 0.25 mm global polygon/deviation audit for every merge dominated
    # the first cockpit benchmark.  The completed curve still receives that
    # original full-density bidirectional audit below.
    dense_source = _sample_polyline(source, max(context.sampling_mm, 0.75))[:, :2]
    source_to_fit = cKDTree(sampled).query(dense_source, k=1)[0]
    fit_to_source = cKDTree(dense_source).query(sampled, k=1)[0]
    combined = np.concatenate((source_to_fit, fit_to_source))
    maximum = float(np.max(combined, initial=0.0))
    rms = float(np.sqrt(np.mean(combined * combined))) if len(combined) else 0.0
    if context.feature_side == "outer" and context.allowed_region is not None:
        forbidden = int(np.count_nonzero(
            ~intersects_xy(context.allowed_region, sampled[:, 0], sampled[:, 1])
        ))
    elif context.feature_side == "obstacle" and context.forbidden_region is not None:
        forbidden = int(np.count_nonzero(
            contains_xy(context.forbidden_region, sampled[:, 0], sampled[:, 1])
        ))
    else:
        forbidden = 0
    self_intersection = bool(len(sampled) >= 4 and not LineString(sampled).is_simple)
    start_reference = (
        context.tangents[local_start]
        if start_tangent_reference is None else np.asarray(start_tangent_reference, dtype=float)
    )
    end_reference = (
        context.tangents[local_end]
        if end_tangent_reference is None else np.asarray(end_tangent_reference, dtype=float)
    )
    start_error = _angle_mismatch_deg(primitives[0].tangent_start(), start_reference)
    end_error = _angle_mismatch_deg(primitives[-1].tangent_end(), end_reference)
    if not constrain_start_tangent:
        start_error = 0.0
    if not constrain_end_tangent:
        end_error = 0.0
    return _Candidate(
        primitives=primitives,
        max_deviation_mm=maximum,
        rms_deviation_mm=rms,
        forbidden_violations=forbidden,
        self_intersection=self_intersection,
        tangent_start_error_deg=start_error,
        tangent_end_error_deg=end_error,
    )


def _interval_candidates(
    context: _FitContext,
    local_start: int,
    local_end: int,
    constrain_start_tangent: bool,
    constrain_end_tangent: bool,
    start_tangent_override: np.ndarray | None = None,
    free_end_tangent: bool = False,
) -> list[_Candidate]:
    tangent_key = (
        None if start_tangent_override is None
        else tuple(np.round(_unit(start_tangent_override), 9).tolist())
    )
    cache_key = (
        local_start,
        local_end,
        constrain_start_tangent,
        constrain_end_tangent,
        tangent_key,
        free_end_tangent,
    )
    cached = context.candidate_cache.get(cache_key)
    if cached is not None:
        return cached
    source_start = context.source_index_offset + local_start
    source_end = context.source_index_offset + local_end
    start = context.targets[local_start]
    end = context.targets[local_end]
    candidates: list[list[PolyarcPrimitive]] = []
    candidates.append(
        [
            PolyarcPrimitive(
                kind="LINE",
                start=start.copy(),
                end=end.copy(),
                source_start_index=source_start,
                source_end_index=source_end,
                logical_span_index=context.logical_span_index,
            )
        ]
    )
    arc_points = context.source[local_start : local_end + 1].copy()
    arc_points[0] = start
    arc_points[-1] = end
    arc = _fit_endpoint_arc(
        arc_points,
        source_start,
        source_end,
        context.logical_span_index,
    )
    if arc is not None:
        candidates.append([arc])
    if free_end_tangent:
        tangent_arc = _arc_from_start_tangent(
            start,
            end,
            context.tangents[local_start]
                if start_tangent_override is None else start_tangent_override,
            source_start,
            source_end,
            context.logical_span_index,
        )
        if tangent_arc is not None:
            candidates.append([tangent_arc])
    measured: list[_Candidate] = []

    def evaluate(primitive_set: list[PolyarcPrimitive]) -> _Candidate | None:
        item = _candidate_metrics(
            context,
            local_start,
            local_end,
            primitive_set,
            constrain_start_tangent,
            constrain_end_tangent,
            start_tangent_override,
            None,
        )
        if (
            item.max_deviation_mm <= context.tolerance_mm + 1e-8
            and item.forbidden_violations == 0
            and not item.self_intersection
            and item.tangent_start_error_deg <= context.tangent_max_deg + 1e-8
            and item.tangent_end_error_deg <= context.tangent_max_deg + 1e-8
        ):
            return item
        return None

    for primitive_set in candidates:
        item = evaluate(primitive_set)
        if item is not None:
            measured.append(item)
    # A valid one-primitive model is lexicographically superior.  Only search
    # the asymmetric biarc family when LINE/ARC cannot satisfy the hard checks.
    if not measured:
        for ratio in (1.0, 0.5, 2.0, 0.25, 4.0, 0.125, 8.0, 0.0625, 16.0):
            biarc = _parameterized_biarc(
                start,
                end,
                context.tangents[local_start] if start_tangent_override is None else start_tangent_override,
                context.tangents[local_end],
                source_start,
                source_end,
                context.logical_span_index,
                ratio,
            )
            if biarc is None:
                continue
            item = evaluate(biarc)
            if item is not None:
                measured.append(item)
                break
    measured.sort(
        key=lambda item: (
            len(item.primitives),
            0 if all(primitive.kind == "LINE" for primitive in item.primitives) else 1,
            item.forbidden_violations,
            item.max_deviation_mm,
            item.rms_deviation_mm,
            -sum(primitive.length_mm for primitive in item.primitives),
        )
    )
    context.candidate_cache[cache_key] = measured
    return measured


def _provisional_error_split(context: _FitContext, start: int, end: int) -> int:
    biarc = _equal_distance_biarc(
        context.targets[start],
        context.targets[end],
        context.tangents[start],
        context.tangents[end],
        context.source_index_offset + start,
        context.source_index_offset + end,
        context.logical_span_index,
    )
    if biarc is not None and end - start > 2:
        sampled = _sample_primitives(biarc, max(context.sampling_mm, 0.5))
        distances, _ = cKDTree(sampled).query(context.source[start + 1 : end], k=1)
        if len(distances):
            split = start + 1 + int(np.argmax(distances))
            if start < split < end:
                return split
    return (start + end) // 2


def _fit_recursive(
    context: _FitContext,
    start: int,
    end: int,
    hard_start: bool,
    hard_end: bool,
    depth: int = 0,
) -> list[PolyarcPrimitive]:
    candidates = _interval_candidates(
        context,
        start,
        end,
        constrain_start_tangent=not hard_start,
        constrain_end_tangent=not hard_end,
    )
    if candidates:
        return candidates[0].primitives
    if end - start <= 1 or depth >= context.max_depth:
        if hard_start and hard_end:
            # No tangent continuity is required on either side of this
            # interval, so a bare chord between adjacent target samples is a
            # legitimate, harmless answer -- nothing to preserve here.
            key = "adjacent_fallback_count" if end - start <= 1 else "depth_fallback_count"
            context.diagnostics[key] = context.diagnostics.get(key, 0) + 1
            return [
                PolyarcPrimitive(
                    kind="LINE",
                    start=context.targets[start].copy(),
                    end=context.targets[end].copy(),
                    source_start_index=context.source_index_offset + start,
                    source_end_index=context.source_index_offset + end,
                    logical_span_index=context.logical_span_index,
                )
            ]
        # A smooth (G1) join is required on at least one side and no
        # LINE/ARC/biarc candidate -- including the biarc family tried
        # inside _interval_candidates -- satisfied it within tolerance at
        # even the finest interval. Emitting a bare chord here would
        # silently break tangent continuity. Raise instead so the caller
        # can flag this location SHARP_CORNER_REQUIRES_REVIEW and fall
        # back to preserved raw geometry there, rather than accept
        # ungoverned CAM geometry.
        context.diagnostics["sharp_corner_review_count"] = (
            context.diagnostics.get("sharp_corner_review_count", 0) + 1
        )
        raise SharpCornerRequiresReview(
            start, end,
            requires_start_tangent=not hard_start,
            requires_end_tangent=not hard_end,
        )
    split = _provisional_error_split(context, start, end)
    if split <= start or split >= end:
        split = (start + end) // 2
    left = _fit_recursive(context, start, split, hard_start, False, depth + 1)
    right = _fit_recursive(context, split, end, False, hard_end, depth + 1)
    return left + right


def _raw_review_fallback_primitives(context: _FitContext) -> list[PolyarcPrimitive]:
    """Preserve the untouched raw span as flagged LINE segments between every
    raw sample, rather than accepting a simplified primitive that could not
    be fit while honoring a required tangent constraint. Used only when
    SharpCornerRequiresReview propagates out of a span's fitting attempt.
    """

    points = context.source
    primitives: list[PolyarcPrimitive] = []
    for index in range(len(points) - 1):
        primitives.append(
            PolyarcPrimitive(
                kind="LINE",
                start=points[index].copy(),
                end=points[index + 1].copy(),
                source_start_index=context.source_index_offset + index,
                source_end_index=context.source_index_offset + index + 1,
                logical_span_index=context.logical_span_index,
                sharp_corner_review=True,
            )
        )
    return primitives


def _reduce_span(
    context: _FitContext,
    primitives: list[PolyarcPrimitive],
    maximum_window: int,
) -> list[PolyarcPrimitive]:
    """Replace the longest valid primitive windows until no count reduction remains."""

    result = list(primitives)
    changed = True
    while changed and len(result) > 1:
        changed = False
        window_cap = min(maximum_window, len(result))
        for width in range(window_cap, 1, -1):
            for first in range(0, len(result) - width + 1):
                last = first + width - 1
                local_start = result[first].source_start_index - context.source_index_offset
                local_end = result[last].source_end_index - context.source_index_offset
                candidates = _interval_candidates(
                    context,
                    local_start,
                    local_end,
                    constrain_start_tangent=first > 0,
                    constrain_end_tangent=last < len(result) - 1,
                )
                replacement = next(
                    (item.primitives for item in candidates if len(item.primitives) < width),
                    None,
                )
                if replacement is None:
                    continue
                result[first : first + width] = replacement
                changed = True
                break
            if changed:
                break
    return result


def _fit_longest_valid_chain(
    context: _FitContext,
    hard_start: bool,
    hard_end: bool,
    consecutive_failure_limit: int,
    stateful_tangents: bool = False,
    line_preference_ratio: float = 0.75,
) -> list[PolyarcPrimitive]:
    """Greedily select the longest locally valid edge on shared tangent states.

    Fit feasibility is normally monotone as the interval grows.  A small run of
    failed endpoints is allowed because raster contours can make isolated node
    states unfavorable even when the next endpoint is valid.
    """

    result: list[PolyarcPrimitive] = []
    cursor = 0
    final = len(context.source) - 1
    cumulative = np.concatenate((
        [0.0], np.cumsum(np.linalg.norm(np.diff(context.source, axis=0), axis=1)),
    ))
    probes = 0
    while cursor < final:
        best_end: int | None = None
        best_primitives: list[PolyarcPrimitive] | None = None
        incoming_tangent = (
            result[-1].tangent_end()
            if stateful_tangents and result else None
        )

        def probe(end: int) -> list[PolyarcPrimitive] | None:
            nonlocal probes
            probes += 1
            if stateful_tangents:
                candidates = _interval_candidates(
                    context,
                    cursor,
                    end,
                    constrain_start_tangent=incoming_tangent is not None,
                    constrain_end_tangent=bool(not hard_end and end == final),
                    start_tangent_override=incoming_tangent,
                    free_end_tangent=bool(hard_end or end < final),
                )
            else:
                candidates = _interval_candidates(
                    context,
                    cursor,
                    end,
                    constrain_start_tangent=not (hard_start and cursor == 0),
                    constrain_end_tangent=not (hard_end and end == final),
                )
            return candidates[0].primitives if candidates else None

        # Most simple spans fit end-to-end; test that decisive case first.
        direct = probe(final)
        if direct is not None:
            best_end = final
            best_primitives = direct
        else:
            # Establish a nearby feasible edge, tolerating a few unfavorable
            # raster nodes before falling back.
            for end in range(cursor + 1, min(final, cursor + consecutive_failure_limit) + 1):
                candidate = probe(end)
                if candidate is not None:
                    best_end = end
                    best_primitives = candidate
                    break
            if best_end is not None:
                low = best_end
                upper = final
                step = max(1, low - cursor)
                # Exponential growth locates the first infeasible scale.
                while low < final:
                    end = min(final, cursor + 2 * step)
                    if end <= low:
                        break
                    candidate = probe(end)
                    if candidate is None:
                        upper = end
                        break
                    low = end
                    best_end = end
                    best_primitives = candidate
                    step *= 2
                    if end == final:
                        break
                # Feasibility is locally monotone for a fixed endpoint tangent
                # field; binary refinement finds the longest passing endpoint.
                while best_end is not None and upper - best_end > 1:
                    end = (best_end + upper) // 2
                    candidate = probe(end)
                    if candidate is None:
                        upper = end
                    else:
                        best_end = end
                        best_primitives = candidate
        # Primitive count precedes raw interval length in the selection policy.
        # Locate the longest contiguous one-primitive run and prefer it when it
        # advances farther per entity than the longest general (often biarc)
        # candidate.
        one_end: int | None = None
        one_primitives: list[PolyarcPrimitive] | None = None
        one_failures = 0
        if not stateful_tangents:
            pass
        elif (
            best_end == final and best_primitives is not None
            and len(best_primitives) == 1 and best_primitives[0].kind == "LINE"
        ):
            one_end = best_end
            one_primitives = best_primitives
        else:
            for end in range(cursor + 1, final + 1):
                candidate = probe(end)
                if candidate is not None and len(candidate) == 1 and candidate[0].kind == "LINE":
                    one_end = end
                    one_primitives = candidate
                    one_failures = 0
                elif one_end is not None:
                    one_failures += 1
                    if one_failures >= max(2, consecutive_failure_limit // 4):
                        break
                elif end - cursor >= consecutive_failure_limit:
                    break
        if (
            one_end is not None
            and one_primitives is not None
            and (
                best_end is None
                or best_primitives is None
                or (cumulative[one_end] - cumulative[cursor])
                    >= float(line_preference_ratio)
                    * (cumulative[best_end] - cumulative[cursor]) / len(best_primitives)
            )
        ):
            best_end = one_end
            best_primitives = one_primitives
        if best_end is None or best_primitives is None:
            # At a rare concave node the pre-estimated outgoing tangent can make
            # every fixed-state biarc unsafe.  Search a bounded free-end escape
            # arc, then adopt its exact outgoing tangent as the shared state for
            # the following interval.  This preserves G1 without a chord
            # fallback or tolerance relaxation.
            repaired_end: int | None = None
            repaired: list[PolyarcPrimitive] | None = None
            for end in range(cursor + 1, min(final, cursor + consecutive_failure_limit) + 1):
                probes += 1
                candidates = _interval_candidates(
                    context,
                    cursor,
                    end,
                    constrain_start_tangent=not (hard_start and cursor == 0),
                    constrain_end_tangent=False,
                    start_tangent_override=(
                        None if hard_start and cursor == 0
                        else (
                            incoming_tangent
                            if incoming_tangent is not None
                            else context.tangents[cursor]
                        )
                    ),
                    free_end_tangent=True,
                )
                if candidates:
                    repaired_end = end
                    repaired = candidates[0].primitives
            if repaired_end is not None and repaired is not None:
                best_end = repaired_end
                best_primitives = repaired
                context.tangents[best_end] = best_primitives[-1].tangent_end()
                context.diagnostics["tangent_state_repair_count"] = (
                    context.diagnostics.get("tangent_state_repair_count", 0) + 1
                )
        if best_end is None or best_primitives is None:
            # Soft join locations are allowed to move inside the physical fit
            # corridor.  If an exact guide vertex makes the incoming G1 state
            # impossible, project that one soft vertex onto the incoming
            # tangent and validate the shifted LINE against the unchanged
            # robust reference and signed safety region.
            if incoming_tangent is not None and cursor < final:
                projected_end_index = cursor + 1
                start_point = context.targets[cursor]
                target_point = context.targets[projected_end_index]
                tangent = _unit(incoming_tangent)
                advance = float(np.dot(target_point - start_point, tangent))
                if advance > 1e-6:
                    projected_end = start_point + advance * tangent
                    projected_line = PolyarcPrimitive(
                        kind="LINE",
                        start=start_point.copy(),
                        end=projected_end,
                        source_start_index=context.source_index_offset + cursor,
                        source_end_index=context.source_index_offset + projected_end_index,
                        logical_span_index=context.logical_span_index,
                    )
                    projected_metrics = _candidate_metrics(
                        context,
                        cursor,
                        projected_end_index,
                        [projected_line],
                        True,
                        False,
                        tangent,
                        None,
                    )
                    if (
                        projected_metrics.max_deviation_mm <= context.tolerance_mm + 1e-8
                        and projected_metrics.forbidden_violations == 0
                        and not projected_metrics.self_intersection
                        and projected_metrics.tangent_start_error_deg <= context.tangent_max_deg + 1e-8
                    ):
                        context.targets[projected_end_index] = projected_end
                        context.tangents[projected_end_index] = tangent
                        best_end = projected_end_index
                        best_primitives = [projected_line]
                        context.diagnostics["soft_join_tangent_projection_count"] = (
                            context.diagnostics.get("soft_join_tangent_projection_count", 0) + 1
                        )
        if best_end is None or best_primitives is None:
            best_end = cursor + 1
            try:
                best_primitives = _fit_recursive(
                    context,
                    cursor,
                    best_end,
                    hard_start and cursor == 0,
                    hard_end and best_end == final,
                )
            except SharpCornerRequiresReview:
                # No tangent-preserving primitive exists across this one
                # adjacent-sample interval -- a genuine sharp feature the
                # corner detector did not protect.  Repair locally: keep the
                # chain gap-free with a single flagged chord across just this
                # interval and carry on fitting the rest of the span, rather
                # than surrendering the whole span to raw geometry.  The kink
                # is honest (sharp_corner_review=True) and surfaces as REVIEW.
                # Route through the physical-reference samples themselves (not
                # a chord between the safe-side targets): a chord can shortcut
                # an outward spike and cross the forbidden side, whereas the
                # reference path is safe by construction.
                start_point = result[-1].end.copy() if result else context.targets[cursor].copy()
                waypoints = [
                    start_point,
                    context.source[cursor][:2].copy(),
                    context.source[best_end][:2].copy(),
                    context.targets[best_end].copy(),
                ]
                best_primitives = []
                for a, b in zip(waypoints[:-1], waypoints[1:]):
                    if float(np.linalg.norm(b - a)) <= 1e-9:
                        continue
                    best_primitives.append(
                        PolyarcPrimitive(
                            kind="LINE",
                            start=np.asarray(a, dtype=float).copy(),
                            end=np.asarray(b, dtype=float).copy(),
                            source_start_index=context.source_index_offset + cursor,
                            source_end_index=context.source_index_offset + best_end,
                            logical_span_index=context.logical_span_index,
                            sharp_corner_review=True,
                        )
                    )
                context.diagnostics["sharp_corner_review_local_interval_count"] = (
                    context.diagnostics.get("sharp_corner_review_local_interval_count", 0) + 1
                )
        result.extend(best_primitives)
        cursor = best_end
    context.diagnostics["longest_chain_candidate_probes"] = probes
    return result


def _analytic_transition_chain(
    context: _FitContext,
    hard_start: bool,
    hard_end: bool,
    curvature_threshold_per_mm: float,
) -> list[PolyarcPrimitive] | None:
    """Recognize stable straight/curved run transitions before general fitting."""

    points = context.source
    if len(points) < 12:
        return None
    delta = np.diff(points, axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    unit = np.divide(delta, lengths[:, None], out=np.zeros_like(delta), where=lengths[:, None] > 1e-9)
    cross = unit[:-1, 0] * unit[1:, 1] - unit[:-1, 1] * unit[1:, 0]
    dot = np.sum(unit[:-1] * unit[1:], axis=1)
    turn = np.abs(np.arctan2(cross, np.clip(dot, -1.0, 1.0)))
    scale = np.maximum(0.5 * (lengths[:-1] + lengths[1:]), 1e-6)
    curvature = turn / scale
    if len(curvature) >= 5:
        padded = np.pad(curvature, (2, 2), mode="edge")
        curvature = np.asarray([
            float(np.median(padded[index : index + 5])) for index in range(len(curvature))
        ])
    curved = curvature >= float(curvature_threshold_per_mm)
    transitions: list[int] = []
    for change in np.flatnonzero(curved[1:] != curved[:-1]):
        # Curvature samples live at path vertices 1..n-2.  Entering a curved
        # run belongs to the following vertex; leaving belongs to the preceding
        # curved endpoint.
        transitions.append(int(change + (2 if curved[change + 1] else 1)))
    # Avoid reacting to short raster flicker and reserve this route for a small
    # number of physically stable analytic runs.
    filtered: list[int] = []
    for index in transitions:
        if index < 4 or index > len(points) - 5:
            continue
        if filtered and index - filtered[-1] < 5:
            continue
        filtered.append(int(index))
    if not filtered or len(filtered) > 8:
        return None
    boundaries = [0, *filtered, len(points) - 1]
    primitives: list[PolyarcPrimitive] = []
    for run_index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        candidates = _interval_candidates(
            context,
            start,
            end,
            constrain_start_tangent=False,
            constrain_end_tangent=False,
        )
        one = next((item.primitives for item in candidates if len(item.primitives) == 1), None)
        if one is None:
            return None
        primitives.extend(one)
    for before, after in zip(primitives[:-1], primitives[1:]):
        if _angle_mismatch_deg(before.tangent_end(), after.tangent_start()) > context.tangent_max_deg + 1e-8:
            return None
    return primitives


def _span_ranges(count: int, anchors: list[int], closed: bool) -> list[tuple[int, int]]:
    if count < 2:
        return []
    if not closed:
        values = sorted(set([0, count - 1] + [int(v) for v in anchors]))
        return [(values[index], values[index + 1]) for index in range(len(values) - 1)]
    values = sorted(set(int(v) % count for v in anchors))
    if not values:
        values = [0]
    ranges: list[tuple[int, int]] = []
    for index, start in enumerate(values):
        end = values[(index + 1) % len(values)]
        if end <= start:
            end += count
        ranges.append((start, end))
    return ranges


def _points_to_polyline_distance(points: np.ndarray, polyline: np.ndarray) -> np.ndarray:
    """Exact point-to-segment distances used by the broad manual fitter."""

    values = np.asarray(points, dtype=float)
    path = np.asarray(polyline, dtype=float)
    if not len(values):
        return np.empty(0, dtype=float)
    if len(path) < 2:
        return np.linalg.norm(values - path[0], axis=1) if len(path) else np.full(len(values), np.inf)
    starts = path[:-1]
    vectors = path[1:] - starts
    denominator = np.sum(vectors * vectors, axis=1)
    result = np.empty(len(values), dtype=float)
    for index, point in enumerate(values):
        relative = point - starts
        parameter = np.divide(
            np.sum(relative * vectors, axis=1),
            denominator,
            out=np.zeros_like(denominator),
            where=denominator > 1e-12,
        )
        parameter = np.clip(parameter, 0.0, 1.0)
        nearest = starts + parameter[:, None] * vectors
        result[index] = float(np.min(np.linalg.norm(point - nearest, axis=1)))
    return result


def _manual_free_line(
    source: np.ndarray,
    start_index: int,
    end_index: int,
    span_index: int,
) -> PolyarcPrimitive:
    center = np.mean(source, axis=0)
    _u, _s, vh = np.linalg.svd(source - center, full_matrices=False)
    direction = vh[0]
    if float(np.dot(direction, source[-1] - source[0])) < 0.0:
        direction = -direction
    parameters = (source - center) @ direction
    return PolyarcPrimitive(
        "LINE",
        center + float(parameters[0]) * direction,
        center + float(parameters[-1]) * direction,
        start_index,
        end_index,
        span_index,
    )


def _manual_free_arc(
    source: np.ndarray,
    start_index: int,
    end_index: int,
    span_index: int,
) -> PolyarcPrimitive | None:
    if len(source) < 3:
        return None
    matrix = np.column_stack(
        (2.0 * source[:, 0], 2.0 * source[:, 1], np.ones(len(source)))
    )
    right_hand_side = np.sum(source * source, axis=1)
    solution = np.linalg.lstsq(matrix, right_hand_side, rcond=None)[0]
    center = solution[:2]
    radii = np.linalg.norm(source - center, axis=1)
    radius = float(np.median(radii))
    if not np.isfinite(radius) or radius <= 1e-6 or radius > 1e7:
        return None
    angles = np.unwrap(
        np.arctan2(source[:, 1] - center[1], source[:, 0] - center[0])
    )
    sweep = float(angles[-1] - angles[0])
    if abs(sweep) <= 1e-7 or abs(sweep) > math.pi + 1e-7:
        return None
    start = center + radius * np.array([math.cos(angles[0]), math.sin(angles[0])])
    end = center + radius * np.array([math.cos(angles[-1]), math.sin(angles[-1])])
    return PolyarcPrimitive(
        "ARC",
        start,
        end,
        start_index,
        end_index,
        span_index,
        center=center,
        radius_mm=radius,
        signed_sweep_rad=sweep,
    )


def _manual_interval_candidate(
    points: np.ndarray,
    start: int,
    end: int,
    span_index: int,
    tolerance_mm: float,
    sampling_mm: float,
    safety: Callable[[np.ndarray], int] | None = None,
) -> tuple[PolyarcPrimitive, dict[str, float]] | None:
    source = points[start : end + 1]
    if len(source) < 2:
        return None
    options = [_manual_free_line(source, start, end, span_index)]
    arc = _manual_free_arc(source, start, end, span_index)
    if arc is not None:
        options.append(arc)
    valid: list[tuple[PolyarcPrimitive, dict[str, float]]] = []
    for primitive in options:
        if primitive.kind == "LINE":
            delta = primitive.end - primitive.start
            length = float(np.linalg.norm(delta))
            direction = delta / max(length, 1e-12)
            local = source - primitive.start
            projection = np.clip(local @ direction, 0.0, length)
            nearest = primitive.start + projection[:, None] * direction
            forward = np.linalg.norm(source - nearest, axis=1)
        else:
            forward = np.abs(
                np.linalg.norm(source - np.asarray(primitive.center), axis=1)
                - float(primitive.radius_mm)
            )
        forward_maximum = float(np.max(forward, initial=0.0))
        if forward_maximum > tolerance_mm:
            continue
        sampled = primitive.sample(sampling_mm)
        reverse = _points_to_polyline_distance(sampled, source)
        reverse_maximum = float(np.max(reverse, initial=0.0))
        maximum = max(forward_maximum, reverse_maximum)
        if maximum > tolerance_mm:
            continue
        # A broad primitive that sits on the wall side of the raw contour is
        # not a valid regime, however well it tracks the smoothed reference;
        # rejecting it here makes the DP add a primitive only where the raw
        # geometry demands one, instead of the whole proposal failing later.
        # Safety is sampled at the final-audit density (not the coarser
        # candidate-fit density) so nothing can dip out between samples.
        if safety is not None and safety(primitive.sample(min(sampling_mm, 0.5))) > 0:
            continue
        combined = np.concatenate((forward, reverse))
        valid.append((primitive, {
            "maximum_mm": maximum,
            "rms_mm": float(np.sqrt(np.mean(combined * combined))),
        }))
    if not valid:
        return None
    # A line wins an equal-complexity tie, matching manual long-straight-first
    # drafting.  Curves are selected only where the physical reference needs one.
    return min(
        valid,
        key=lambda item: (
            0 if item[0].kind == "LINE" else 1,
            item[1]["rms_mm"],
        ),
    )


def _manual_fit_span(
    points: np.ndarray,
    span_index: int,
    tolerance_mm: float,
    sampling_mm: float,
    node_stride: int,
    safety: Callable[[np.ndarray], int] | None = None,
) -> list[PolyarcPrimitive]:
    stride = max(int(node_stride), 1)
    while stride >= 1:
        nodes = list(range(0, len(points) - 1, stride)) + [len(points) - 1]
        costs = [math.inf] * len(nodes)
        choices: list[tuple[tuple[PolyarcPrimitive, dict[str, float]], int] | None] = [
            None
        ] * len(nodes)
        costs[-1] = 0.0
        cache: dict[tuple[int, int], tuple[PolyarcPrimitive, dict[str, float]] | None] = {}
        for start_node in range(len(nodes) - 2, -1, -1):
            for end_node in range(start_node + 1, len(nodes)):
                if not math.isfinite(costs[end_node]):
                    continue
                key = (nodes[start_node], nodes[end_node])
                if key not in cache:
                    cache[key] = _manual_interval_candidate(
                        points,
                        key[0],
                        key[1],
                        span_index,
                        tolerance_mm,
                        sampling_mm,
                        safety,
                    )
                selected = cache[key]
                if selected is None:
                    continue
                primitive, _metrics = selected
                cost = 1.0 + costs[end_node]
                current = choices[start_node]
                line_tie = (
                    cost == costs[start_node]
                    and current is not None
                    and primitive.kind == "LINE"
                    and current[0][0].kind != "LINE"
                )
                if cost < costs[start_node] or line_tie:
                    costs[start_node] = cost
                    choices[start_node] = (selected, end_node)
        if choices[0] is not None:
            result: list[PolyarcPrimitive] = []
            cursor = 0
            while cursor < len(nodes) - 1:
                choice = choices[cursor]
                if choice is None:
                    break
                selected, cursor = choice
                result.append(selected[0])
            if cursor == len(nodes) - 1:
                return result
        if stride == 1:
            break
        stride = max(1, stride // 2)
    raise ValueError(f"manual broad-regime fit found no path for logical span {span_index}")


def _manual_broad_regime_fit(
    reference: np.ndarray,
    anchors: list[int],
    closed: bool,
    tolerance_mm: float,
    configuration: dict[str, Any],
    safety: Callable[[np.ndarray], int] | None = None,
    safe_projection: Callable[[np.ndarray], np.ndarray] | None = None,
) -> tuple[np.ndarray, list[PolyarcPrimitive], dict[str, Any]]:
    """Return the manual-level broad reference and minimum primitive path.

    This stage deliberately solves global physical regimes instead of following
    raster vertices.  It is a reconstruction proposal, not an acceptance bypass:
    the ordinary corridor, signed-safety, gap, self-intersection, and join audits
    still decide whether the returned curve is valid.
    """

    from .robust_reference import _smooth_open_span

    values = np.asarray(reference, dtype=float)
    broad = values.copy()
    scales = [
        float(value)
        for value in configuration.get(
            "manual_reference_scales_mm", [20.0, 40.0, 80.0, 160.0]
        )
    ]
    ranges = _span_ranges(len(values), anchors, closed)
    for start, end in ranges:
        indices = np.arange(start, end + 1, dtype=int) % len(values)
        smoothed, _scale_outputs = _smooth_open_span(values[indices], scales)
        broad[indices] = smoothed
    # Multi-scale smoothing can carry the broad reference several mm onto
    # the wall side of the raw contour.  Pull those samples back to the raw
    # boundary so a safety-aware fit always has at least the trivial
    # adjacent-sample path available; everything else stays broad.
    safe_projected_count = 0
    if safe_projection is not None:
        projected = safe_projection(broad)
        moved = np.linalg.norm(projected - broad, axis=1) > 1e-9
        safe_projected_count = int(np.count_nonzero(moved))
        broad = projected
    if anchors:
        broad[anchors] = values[anchors]
    fit_tolerance = max(
        tolerance_mm - float(configuration.get("manual_fit_headroom_mm", 0.05)),
        0.1,
    )
    primitives: list[PolyarcPrimitive] = []
    sampling = float(configuration.get("manual_candidate_sampling_mm", 3.0))
    node_stride = int(configuration.get("manual_dp_node_stride", 2))
    for span_index, (start, end) in enumerate(ranges):
        indices = np.arange(start, end + 1, dtype=int) % len(broad)
        local = broad[indices]
        fitted = _manual_fit_span(
            local, span_index, fit_tolerance, sampling, node_stride, safety,
        )
        for primitive in fitted:
            local_start = int(primitive.source_start_index)
            local_end = int(primitive.source_end_index)
            primitive.source_start_index = start + local_start
            primitive.source_end_index = start + local_end
        primitives.extend(fitted)
    displacement = np.linalg.norm(broad - values, axis=1)
    smooth_mismatches = [
        _angle_mismatch_deg(before.tangent_end(), after.tangent_start())
        for before, after in zip(primitives, primitives[1:] + primitives[:1])
        if before.logical_span_index == after.logical_span_index
    ]
    gaps = [
        float(np.linalg.norm(before.end - after.start))
        for before, after in zip(primitives, primitives[1:] + primitives[:1])
    ]
    diagnostics = {
        "method": "multi-scale broad physical consensus plus whole-span minimum LINE/ARC path",
        "safety_aware": safety is not None,
        "safe_projected_sample_count": safe_projected_count,
        "manual_reference_scales_mm": scales,
        "fit_headroom_mm": float(configuration.get("manual_fit_headroom_mm", 0.05)),
        "dp_node_stride": node_stride,
        "reference_shift_rms_mm": float(np.sqrt(np.mean(displacement * displacement))),
        "reference_shift_p95_mm": float(np.percentile(displacement, 95)),
        "reference_shift_maximum_mm": float(np.max(displacement, initial=0.0)),
        "primitive_count": len(primitives),
        "line_count": sum(item.kind == "LINE" for item in primitives),
        "arc_count": sum(item.kind == "ARC" for item in primitives),
        "manual_benchmark_line_count": int(configuration.get("manual_benchmark_line_count", 14)),
        "manual_benchmark_arc_count": int(configuration.get("manual_benchmark_arc_count", 18)),
        "pre_join_maximum_gap_mm": max(gaps, default=0.0),
        "pre_join_maximum_tangent_mismatch_deg": max(smooth_mismatches, default=0.0),
        "join_refinement_status": "REQUIRES_EXACT_CONNECTION_AND_G1_REFINEMENT",
    }
    return broad, primitives, diagnostics


def _trim_primitive(
    primitive: PolyarcPrimitive,
    start_mm: float,
    end_mm: float,
    maximum_fraction: float = 0.40,
) -> PolyarcPrimitive:
    """Trim a LINE/ARC without changing its supporting line or circle."""

    length = primitive.length_mm
    start_fraction = float(np.clip(
        start_mm / max(length, 1e-12), 0.0, maximum_fraction,
    ))
    end_fraction = float(np.clip(
        1.0 - end_mm / max(length, 1e-12),
        1.0 - maximum_fraction,
        1.0,
    ))
    if primitive.kind == "LINE":
        delta = primitive.end - primitive.start
        return PolyarcPrimitive(
            "LINE",
            primitive.start + start_fraction * delta,
            primitive.start + end_fraction * delta,
            primitive.source_start_index,
            primitive.source_end_index,
            primitive.logical_span_index,
        )
    center = np.asarray(primitive.center, dtype=float)
    start_angle = math.atan2(
        primitive.start[1] - center[1], primitive.start[0] - center[0]
    )
    trimmed_start_angle = start_angle + start_fraction * primitive.signed_sweep_rad
    trimmed_sweep = (
        end_fraction - start_fraction
    ) * primitive.signed_sweep_rad
    trimmed_end_angle = trimmed_start_angle + trimmed_sweep
    radius = float(primitive.radius_mm or 0.0)
    return PolyarcPrimitive(
        "ARC",
        center + radius * np.array([
            math.cos(trimmed_start_angle), math.sin(trimmed_start_angle),
        ]),
        center + radius * np.array([
            math.cos(trimmed_end_angle), math.sin(trimmed_end_angle),
        ]),
        primitive.source_start_index,
        primitive.source_end_index,
        primitive.logical_span_index,
        center=center.copy(),
        radius_mm=radius,
        signed_sweep_rad=float(trimmed_sweep),
    )


def _repair_manual_joins(
    primitives: list[PolyarcPrimitive],
    physical_reference: np.ndarray,
    closed: bool,
    feature_side: str,
    allowed_region: Polygon | None,
    forbidden_region: Polygon | None,
    sampling_mm: float,
    configuration: dict[str, Any],
) -> tuple[list[PolyarcPrimitive], dict[str, Any]]:
    """Close a good low-count proposal by repairing only its joins.

    Smooth joins receive an exact tangent biarc after a short trim of the two
    neighboring primitives.  Protected hard corners receive a short LINE
    closure and intentionally retain their discontinuity.  The original broad
    spans are otherwise untouched.
    """

    if not primitives:
        return [], {"status": "NO_PRIMITIVES"}
    pair_count = len(primitives) if closed else len(primitives) - 1
    trim_mm = max(float(configuration.get("manual_join_trim_mm", 5.0)), 0.0)
    maximum_fraction = float(configuration.get("manual_join_maximum_trim_fraction", 0.40))
    count = len(primitives)
    # A join that cannot be bridged at the base trim gets progressively more
    # room: trimming the two neighbours further back gives the exact-tangent
    # biarc a longer chord to swing through safely.  One stubborn join must
    # not throw away an otherwise good low-count proposal.
    multipliers = [float(v) for v in configuration.get(
        "manual_join_trim_schedule_multipliers", [1.0, 2.0, 3.0, 4.0],
    )]
    if not multipliers or any(v <= 0.0 for v in multipliers):
        raise ValueError("manual_join_trim_schedule_multipliers must be positive")
    ratios = tuple(float(value) for value in configuration.get(
        "manual_join_biarc_ratios",
        [1.0, 0.5, 2.0, 0.25, 4.0, 0.125, 8.0, 0.0625, 16.0],
    ))
    tolerance_mm = float(configuration.get("absolute_fit_tolerance_mm", 3.0))
    tangent_max_deg = float(configuration.get("tangent_max_deg", 0.10))
    reference = np.asarray(physical_reference, dtype=float)
    if closed:
        reference = np.vstack([reference, reference[0]])

    trim_start = [0.0] * count
    trim_end = [0.0] * count
    smooth_joins: list[int] = []
    hard_join_count = 0
    for index in range(pair_count):
        before = primitives[index]
        after = primitives[(index + 1) % count]
        if before.logical_span_index == after.logical_span_index:
            smooth_joins.append(index)
        else:
            hard_join_count += 1

    def trimmed_primitive(index: int) -> PolyarcPrimitive:
        return _trim_primitive(
            primitives[index], trim_start[index], trim_end[index], maximum_fraction,
        )

    def safe_connector(index: int) -> list[PolyarcPrimitive] | None:
        before = trimmed_primitive(index)
        after = trimmed_primitive((index + 1) % count)
        connector_options: list[list[PolyarcPrimitive]] = []
        single = _arc_from_start_tangent(
            before.end,
            after.start,
            before.tangent_end(),
            before.source_end_index,
            after.source_start_index,
            before.logical_span_index,
        )
        if single is not None and _angle_mismatch_deg(
            single.tangent_end(), after.tangent_start()
        ) <= tangent_max_deg + 1e-8:
            connector_options.append([single])
        for ratio in ratios:
            connector = _parameterized_biarc(
                before.end,
                after.start,
                before.tangent_end(),
                after.tangent_start(),
                before.source_end_index,
                after.source_start_index,
                before.logical_span_index,
                ratio,
            )
            if connector is not None:
                connector_options.append(connector)
        candidates: list[tuple[int, float, list[PolyarcPrimitive]]] = []
        for connector in connector_options:
            sampled = _sample_primitives(connector, sampling_mm)
            fit_error = float(np.max(
                _points_to_polyline_distance(sampled, reference), initial=0.0,
            ))
            if fit_error > tolerance_mm + 1e-8:
                continue
            if feature_side == "outer" and allowed_region is not None:
                unsafe = int(np.count_nonzero(~intersects_xy(
                    allowed_region, sampled[:, 0], sampled[:, 1],
                )))
            elif feature_side == "obstacle" and forbidden_region is not None:
                unsafe = int(np.count_nonzero(contains_xy(
                    forbidden_region, sampled[:, 0], sampled[:, 1],
                )))
            else:
                unsafe = 0
            if unsafe:
                # A connector that crosses the forbidden side is not a worse
                # candidate to fall back on -- it is not a candidate at all.
                # Picking the "least unsafe" option here would let physical
                # interference through just to keep the join count low.
                continue
            candidates.append((len(connector), fit_error, connector))
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[2]

    connectors: dict[int, list[PolyarcPrimitive]] = {}
    failed_join_indices: list[int] = []
    escalated_join_trims_mm: dict[int, float] = {}
    for index in smooth_joins:
        following = (index + 1) % count
        for multiplier in multipliers:
            trim_end[index] = trim_mm * multiplier
            trim_start[following] = trim_mm * multiplier
            connector = safe_connector(index)
            if connector is not None:
                connectors[index] = connector
                if multiplier != multipliers[0]:
                    escalated_join_trims_mm[index] = trim_mm * multiplier
                break
        else:
            failed_join_indices.append(index)

    result: list[PolyarcPrimitive] = []
    connector_counts = {
        "hard_corner_lines": 0,
        "smooth_connector_primitives": 0,
        "single_primitive_connectors": 0,
        "biarc_connectors": 0,
    }
    special_span = max(item.logical_span_index for item in primitives) + 1
    for index in range(count):
        before = trimmed_primitive(index)
        result.append(before)
        if index >= pair_count:
            continue
        after = trimmed_primitive((index + 1) % count)
        if before.logical_span_index != after.logical_span_index:
            if np.linalg.norm(before.end - after.start) > 1e-9:
                result.append(PolyarcPrimitive(
                    "LINE",
                    before.end.copy(),
                    after.start.copy(),
                    before.source_end_index,
                    after.source_start_index,
                    special_span,
                ))
                connector_counts["hard_corner_lines"] += 1
                special_span += 1
            continue
        selected = connectors.get(index)
        if selected is None:
            continue
        result.extend(selected)
        connector_counts["smooth_connector_primitives"] += len(selected)
        connector_counts[
            "single_primitive_connectors" if len(selected) == 1 else "biarc_connectors"
        ] += 1
    diagnostics = {
        "status": "PASSED" if not failed_join_indices else "FAILED",
        "trim_mm": trim_mm,
        "trim_schedule_mm": [trim_mm * m for m in multipliers],
        "maximum_trim_fraction": maximum_fraction,
        "original_primitive_count": count,
        "repaired_primitive_count": len(result),
        "primitives_added": len(result) - count,
        "smooth_join_count": len(smooth_joins),
        "hard_corner_join_count": hard_join_count,
        "failed_join_indices": failed_join_indices,
        "escalated_join_trims_mm": {str(k): v for k, v in escalated_join_trims_mm.items()},
        **connector_counts,
    }
    return (result if not failed_join_indices else primitives), diagnostics


def _wall_side_penetration_stats(
    sampled: np.ndarray,
    raw_polygon: Polygon,
    feature_side: str,
    allowance_mm: float,
) -> dict[str, Any]:
    """Depth of accepted geometry beyond the raw contour on the forbidden
    side (outside the raw polygon for an outer boundary, inside it for an
    obstacle).  Reported regardless of the noise allowance so a VALID curve
    still says how far into the noise band it actually sits."""

    if feature_side not in {"outer", "obstacle"} or not len(sampled) or raw_polygon.is_empty:
        return {"sample_count": 0, "p50_mm": 0.0, "p95_mm": 0.0, "maximum_mm": 0.0,
                "allowance_mm": float(allowance_mm), "beyond_allowance_count": 0}
    xy = np.asarray(sampled, dtype=float)[:, :2]
    inside = contains_xy(raw_polygon, xy[:, 0], xy[:, 1])
    wrong_side = ~inside if feature_side == "outer" else inside
    if not np.any(wrong_side):
        return {"sample_count": 0, "p50_mm": 0.0, "p95_mm": 0.0, "maximum_mm": 0.0,
                "allowance_mm": float(allowance_mm), "beyond_allowance_count": 0}
    boundary = raw_polygon.exterior
    depths = shapely.distance(boundary, shapely.points(xy[wrong_side]))
    return {
        "sample_count": int(np.count_nonzero(wrong_side)),
        "p50_mm": float(np.percentile(depths, 50)),
        "p95_mm": float(np.percentile(depths, 95)),
        "maximum_mm": float(np.max(depths)),
        "allowance_mm": float(allowance_mm),
        "beyond_allowance_count": int(np.count_nonzero(depths > allowance_mm + 1e-9)),
    }


def _build_join_table(
    primitives: list[PolyarcPrimitive],
    closed: bool,
    hard_corner_points: np.ndarray,
) -> list[PolyarcJoin]:
    if not primitives:
        return []
    joins: list[PolyarcJoin] = []
    pair_count = len(primitives) if closed else len(primitives) - 1
    for index in range(pair_count):
        before = primitives[index]
        after = primitives[(index + 1) % len(primitives)]
        gap = float(np.linalg.norm(before.end - after.start))
        hard = before.logical_span_index != after.logical_span_index
        mismatch = None if hard else _angle_mismatch_deg(before.tangent_end(), after.tangent_start())
        joins.append(
            PolyarcJoin(
                index=index,
                join_type="HARD_CORNER" if hard else "SMOOTH_G1",
                point=0.5 * (before.end + after.start),
                gap_mm=gap,
                tangent_mismatch_deg=mismatch,
                logical_span_before=before.logical_span_index,
                logical_span_after=after.logical_span_index,
                sharp_corner_review=before.sharp_corner_review or after.sharp_corner_review,
            )
        )
    return joins


def fit_polyarc_curve(
    spline_reference: ManufacturingCurve,
    configuration: dict[str, Any] | None = None,
    robust_reference: Any | None = None,
) -> PolyarcCurve:
    """Fit one LINE/ARC representation.

    With ``robust_reference`` this is the V0.3.3 manual-style path: fit
    decisions and the 3 mm corridor use the persistent physical reference,
    while the untouched raw contour remains the signed safety boundary and is
    audited independently.  Without it the original V0.3.2 behavior is kept.
    """

    settings = dict(configuration or {})
    tolerance = float(settings.get("absolute_fit_tolerance_mm", 3.0))
    if tolerance > 3.0 + 1e-9:
        raise ValueError("V0.3.2 absolute_fit_tolerance_mm may not exceed 3.000 mm")
    tangent_target = float(settings.get("tangent_target_deg", 0.05))
    tangent_max = float(settings.get("tangent_max_deg", 0.10))
    sampling = float(settings.get("validation_sampling_mm", 0.5))
    tangent_window = float(settings.get("tangent_window_mm", 25.0))
    corridor_offset = float(settings.get("safe_corridor_offset_mm", 2.00))
    max_depth = int(settings.get("max_recursive_depth", 28))
    reduction_window = int(settings.get("maximum_reduction_window", 12))

    if robust_reference is None:
        raw, _ = _collapse_consecutive_samples(
            np.asarray(spline_reference.source.points_mm, dtype=float),
            bool(spline_reference.closed),
            float(settings.get("minimum_numerical_sample_separation_mm", 0.10)),
        )
        raw = raw[:, :2]
        physical_reference = raw.copy()
        # spline_reference.anchor_indices are indices into curve_fit.py's own
        # corner-detection array, not into `raw` above (an independent
        # collapse of the source points). Resolve by nearest coordinate
        # match rather than reusing the indices directly.
        anchors = map_points_to_indices(
            spline_reference.anchor_points_mm, raw,
            context=f"polyarc[{spline_reference.cam_curve_id}] spline anchor",
        )
    else:
        raw = np.asarray(robust_reference.raw_points, dtype=float)[:, :2].copy()
        physical_reference = np.asarray(robust_reference.points, dtype=float)[:, :2].copy()
        if len(raw) != len(physical_reference):
            raise ValueError("V0.3.3 robust reference must retain one point for each raw contour point")
        # robust_reference.hard_corner_indices were computed directly against
        # robust_reference.raw_points (== raw here), so they are already
        # valid indices into it -- no independent recollapse happened on this
        # branch. Still fail loudly instead of wrapping if that invariant
        # is ever violated upstream.
        anchors = sorted({int(v) for v in robust_reference.hard_corner_indices})
        if anchors and (anchors[0] < 0 or anchors[-1] >= len(raw)):
            raise ValueError(
                f"polyarc[{spline_reference.cam_curve_id}]: robust reference hard_corner_indices "
                f"out of range for raw_points (len={len(raw)}); refusing to wrap via modulo indexing."
            )
    closed = bool(spline_reference.closed)
    if not closed:
        anchors = sorted(set([0, len(raw) - 1] + anchors))
    hard_points = raw[anchors].copy() if anchors else np.empty((0, 2), dtype=float)
    _, feature_side = _feature_profile(spline_reference.source)
    source_layer = spline_reference.source.source.raw_curve.layer.upper()
    manual_diagnostics: dict[str, Any] | None = None
    manual_primitives: list[PolyarcPrimitive] | None = None
    primary_perimeter_mm = float(
        LineString(np.vstack([physical_reference, physical_reference[0]])).length
    ) if closed and len(physical_reference) >= 2 else 0.0
    reference_polygon = Polygon(raw)
    if not reference_polygon.is_valid:
        reference_polygon = reference_polygon.buffer(0.0)
    # Signed safety is judged against the RAW detected contour, but the raw
    # contour carries raster stair-steps and scanner spikes of ~1-2 mm.  A
    # curve drawn through the middle of that noise band -- the whole point of
    # the manufacturing fit -- necessarily sits on the wall side of every
    # inward noise notch.  Treating each notch as a physical wall (the
    # historical 0.35 mm allowance) made low-count geometry impossible on real
    # scans without catching a single real crossing.  The allowance below is
    # the penetration depth that counts as noise; anything deeper is a real
    # crossing and remains a hard failure.  Buffering by this allowance also
    # fills inward notches narrower than twice the allowance regardless of
    # depth, which is exactly the "isolated spike vs persistent feature"
    # distinction the robust reference makes.  The fit corridor still bounds
    # every sample to within absolute_fit_tolerance_mm of the physical
    # reference independently of this check.
    safety_allowance = float(settings.get("signed_safety_penetration_allowance_mm", 2.0))
    if safety_allowance < 0.0:
        raise ValueError("signed_safety_penetration_allowance_mm must be >= 0")
    allowed_region = reference_polygon.buffer(safety_allowance) if feature_side == "outer" else None
    forbidden_region = reference_polygon.buffer(-safety_allowance) if feature_side == "obstacle" else None

    def manual_safety(sampled: np.ndarray) -> int:
        if feature_side == "outer" and allowed_region is not None:
            return int(np.count_nonzero(~intersects_xy(allowed_region, sampled[:, 0], sampled[:, 1])))
        if feature_side == "obstacle" and forbidden_region is not None:
            return int(np.count_nonzero(contains_xy(forbidden_region, sampled[:, 0], sampled[:, 1])))
        return 0

    def manual_safe_projection(points: np.ndarray) -> np.ndarray:
        """Move any sample beyond the noise allowance back onto the raw
        contour boundary (zero penetration); leave every other sample alone."""
        result = np.asarray(points, dtype=float).copy()
        if feature_side == "outer" and allowed_region is not None:
            unsafe = ~intersects_xy(allowed_region, result[:, 0], result[:, 1])
        elif feature_side == "obstacle" and forbidden_region is not None:
            unsafe = contains_xy(forbidden_region, result[:, 0], result[:, 1])
        else:
            return result
        if np.any(unsafe):
            boundary = reference_polygon.exterior
            from shapely.ops import nearest_points
            from shapely.geometry import Point
            for index in np.flatnonzero(unsafe):
                nearest = nearest_points(Point(result[index, 0], result[index, 1]), boundary)[1]
                result[index, :2] = (nearest.x, nearest.y)
        return result

    if (
        robust_reference is not None
        and closed
        and "DECK_PRIMARY" in source_layer
        and bool(settings.get("use_manual_broad_regime_fit_for_primary", True))
        and len(physical_reference)
            >= int(settings.get("manual_primary_minimum_point_count", 500))
        and primary_perimeter_mm
            >= float(settings.get("manual_primary_minimum_perimeter_mm", 5000.0))
    ):
        try:
            physical_reference, manual_primitives, manual_diagnostics = (
                _manual_broad_regime_fit(
                    physical_reference,
                    anchors,
                    closed,
                    tolerance,
                    settings,
                    safety=manual_safety,
                    safe_projection=manual_safe_projection,
                )
            )
        except ValueError as error:
            # No safe LINE/ARC path exists for some span even at the finest
            # node spacing.  Fall through to the exact tangent-state chain
            # fitter for the whole curve rather than failing the run; the
            # reason is recorded so the report explains the higher count.
            manual_primitives = None
            manual_diagnostics = {
                "status": "NO_SAFE_BROAD_PATH",
                "reason": str(error),
                "adaptive_refinement_triggered": True,
                "adaptive_refinement_reasons": ["NO_SAFE_BROAD_PATH"],
            }
    ranges = _span_ranges(len(physical_reference), anchors, closed)

    if manual_primitives is not None and bool(
        settings.get("repair_manual_joins_locally", True)
    ):
        manual_primitives, local_repair = _repair_manual_joins(
            manual_primitives,
            physical_reference,
            closed,
            feature_side,
            allowed_region,
            forbidden_region,
            sampling,
            settings,
        )
        manual_diagnostics["local_join_repair"] = local_repair

    # The broad manual pass deliberately fits whole geometric regimes
    # independently.  That gives the desired low-complexity proposal, but it
    # does not by itself guarantee shared endpoints, G1 joins, or signed-side
    # safety.  Preserve it when it already passes those hard checks; otherwise
    # use the same broad physical reference as input to the exact tangent-state
    # chain below.  That chain adds primitives only where a longer connected
    # LINE/ARC interval cannot satisfy the fixed corridor and safety contract.
    if manual_primitives is not None and bool(
        settings.get("adaptive_refine_invalid_manual_primary", True)
    ):
        manual_sampled = _sample_primitives(manual_primitives, sampling)
        manual_pairs = list(zip(
            manual_primitives,
            manual_primitives[1:] + manual_primitives[:1],
        ))
        manual_gaps = [
            float(np.linalg.norm(before.end - after.start))
            for before, after in manual_pairs
        ]
        manual_tangencies = [
            _angle_mismatch_deg(before.tangent_end(), after.tangent_start())
            for before, after in manual_pairs
            if before.logical_span_index == after.logical_span_index
        ]
        if feature_side == "outer" and allowed_region is not None:
            manual_forbidden = int(np.count_nonzero(
                ~intersects_xy(
                    allowed_region,
                    manual_sampled[:, 0],
                    manual_sampled[:, 1],
                )
            ))
        elif feature_side == "obstacle" and forbidden_region is not None:
            manual_forbidden = int(np.count_nonzero(
                contains_xy(
                    forbidden_region,
                    manual_sampled[:, 0],
                    manual_sampled[:, 1],
                )
            ))
        else:
            manual_forbidden = 0
        manual_self_intersection = bool(
            closed
            and len(manual_sampled) >= 4
            and not LineString(manual_sampled).is_simple
        )
        refinement_reasons: list[str] = []
        if max(manual_gaps, default=0.0) > 1e-7:
            refinement_reasons.append("OPEN_JOIN_GAPS")
        if max(manual_tangencies, default=0.0) > tangent_max + 1e-8:
            refinement_reasons.append("SOFT_JOIN_TANGENCY")
        if manual_forbidden:
            refinement_reasons.append("SIGNED_SAFETY")
        if manual_self_intersection:
            refinement_reasons.append("SELF_INTERSECTION")
        manual_diagnostics.update({
            "adaptive_refinement_triggered": bool(refinement_reasons),
            "adaptive_refinement_reasons": refinement_reasons,
            "initial_forbidden_side_violations": manual_forbidden,
            "initial_self_intersection": manual_self_intersection,
        })
        if refinement_reasons:
            manual_primitives = None

    # The repaired manual proposal (broad regimes + biarc connectors) and the
    # exact tangent-state chain are two different valid constructions of the
    # same perimeter.  Neither is guaranteed to be the lower-count one, so
    # when both are available the one with fewer primitives wins -- validity
    # is decided by the hard audit below, count is the tie-breaker the
    # manufacturing philosophy asks for.
    compare_with_chain = manual_primitives is not None and bool(
        settings.get("compare_manual_with_tangent_chain", True)
    )
    chain_primitives: list[PolyarcPrimitive] = []
    fit_diagnostics: dict[str, int] = {}
    span_review_warnings: list[str] = []
    fitting_ranges = ranges if (manual_primitives is None or compare_with_chain) else []
    for span_index, (start, end) in enumerate(fitting_ranges):
        if end < len(physical_reference):
            source = physical_reference[start : end + 1].copy()
            raw_indices = np.arange(start, end + 1, dtype=int)
        else:
            source = np.vstack([
                physical_reference[start:],
                physical_reference[: end - len(physical_reference) + 1],
            ])
            raw_indices = np.concatenate(
                [
                    np.arange(start, len(physical_reference), dtype=int),
                    np.arange(0, end - len(physical_reference) + 1, dtype=int),
                ]
            )
        if len(source) < 2:
            continue
        validation_source = source.copy()
        guide_to_validation_index = np.arange(len(source), dtype=int)
        if robust_reference is not None:
            targets, _ = _corridor_targets(
                source,
                raw,
                feature_side,
                float(settings.get("manual_safe_corridor_offset_mm", 1.0)),
                float(settings.get("safe_normal_scale_mm", 8.0)),
                float(settings.get("manual_corridor_target_smoothing_mm", 20.0)),
            )
        else:
            targets, _ = _corridor_targets(
                source,
                raw,
                feature_side,
                corridor_offset,
                float(settings.get("safe_normal_scale_mm", 8.0)),
                float(settings.get("corridor_target_smoothing_mm", 6.0)),
            )
        # Smoothing a raster-derived offset boundary can move a target farther
        # than the manufacturing corridor even when it remains topologically on
        # the safe side.  Cap each guide displacement explicitly and reserve
        # numeric headroom for the circular fit between guide samples.
        target_cap = min(
            float(settings.get("maximum_guide_displacement_mm", 2.40)),
            max(tolerance - 0.25, 0.0),
        )
        displacement = targets[:, :2] - source[:, :2]
        distance = np.linalg.norm(displacement, axis=1)
        over_cap = distance > target_cap
        if np.any(over_cap):
            targets[over_cap, :2] = source[over_cap, :2] + (
                displacement[over_cap] * (target_cap / distance[over_cap])[:, None]
            )
        targets[0] = source[0]
        targets[-1] = source[-1]
        minimum_guide_step = float(settings.get("minimum_guide_sample_separation_mm", 1.0))
        if minimum_guide_step > 0.0 and len(targets) > 2:
            kept = [0]
            for guide_index in range(1, len(targets) - 1):
                if np.linalg.norm(targets[guide_index] - targets[kept[-1]]) >= minimum_guide_step:
                    kept.append(guide_index)
            kept.append(len(targets) - 1)
            kept_array = np.asarray(kept, dtype=int)
            source = source[kept_array]
            targets = targets[kept_array]
            raw_indices = raw_indices[kept_array]
            guide_to_validation_index = guide_to_validation_index[kept_array]
        tangents = _tangent_field(targets, tangent_window)
        tangents = _limit_tangents_to_local_progression(
            targets,
            tangents,
            float(settings.get("maximum_tangent_local_offset_deg", 20.0)),
        )
        smooth_closed_seam = bool(closed and not anchors and len(source) >= 3)
        if smooth_closed_seam:
            seam_tangent = _unit(targets[1] - targets[-2])
            tangents[0] = seam_tangent
            tangents[-1] = seam_tangent
        context = _FitContext(
            raw_full=raw,
            source=source,
            validation_source=validation_source,
            guide_to_validation_index=guide_to_validation_index,
            targets=targets,
            tangents=tangents,
            source_index_offset=0,
            logical_span_index=span_index,
            closed_reference=closed,
            feature_side=feature_side,
            tolerance_mm=tolerance,
            tangent_max_deg=tangent_max,
            sampling_mm=sampling,
            max_depth=max_depth,
            allowed_region=allowed_region,
            forbidden_region=forbidden_region,
        )
        try:
            if bool(settings.get("use_longest_valid_chain", True)):
                span_primitives = _fit_longest_valid_chain(
                    context,
                    not smooth_closed_seam,
                    not smooth_closed_seam,
                    int(settings.get("longest_chain_consecutive_failure_limit", 4)),
                    bool(
                        settings.get("use_stateful_tangent_chain_for_open_curves", True)
                        and (not closed or bool(anchors))
                    ),
                    float(settings.get("longest_line_preference_ratio", 0.75)),
                )
                analytic = _analytic_transition_chain(
                    context,
                    not smooth_closed_seam,
                    not smooth_closed_seam,
                    float(settings.get("analytic_transition_curvature_per_mm", 0.002)),
                )
                if analytic is not None and len(analytic) < len(span_primitives):
                    span_primitives = analytic
                    context.diagnostics["analytic_transition_chain_selected"] = 1
            else:
                span_primitives = _fit_recursive(
                    context,
                    0,
                    len(source) - 1,
                    not smooth_closed_seam,
                    not smooth_closed_seam,
                )
                span_primitives = _reduce_span(context, span_primitives, reduction_window)
        except SharpCornerRequiresReview as review:
            # No tangent-preserving LINE/ARC/biarc geometry could be found
            # somewhere in this span. Do not accept a kinked chord in its
            # place -- preserve the untouched raw span instead, flagged for
            # manual review (spec: SHARP_CORNER_REQUIRES_REVIEW).
            span_primitives = _raw_review_fallback_primitives(context)
            context.diagnostics["sharp_corner_review_span_count"] = (
                context.diagnostics.get("sharp_corner_review_span_count", 0) + 1
            )
            span_review_warnings.append(
                f"SHARP_CORNER_REQUIRES_REVIEW: span {span_index} could not find a "
                f"tangent-preserving fit near local interval [{review.local_start}, "
                f"{review.local_end}]; raw geometry preserved there for manual review."
            )
        # Convert local guide indices to original cyclic raw indices only after
        # all fitting/reduction operations have completed.
        for key, value in context.diagnostics.items():
            fit_diagnostics[key] = fit_diagnostics.get(key, 0) + int(value)
        for primitive in span_primitives:
            local_start = int(np.clip(primitive.source_start_index, 0, len(raw_indices) - 1))
            local_end = int(np.clip(primitive.source_end_index, 0, len(raw_indices) - 1))
            primitive.source_start_index = int(raw_indices[local_start])
            primitive.source_end_index = int(raw_indices[local_end])
        chain_primitives.extend(span_primitives)

    chain_has_review = any(item.sharp_corner_review for item in chain_primitives)
    if manual_primitives is None:
        primitives: list[PolyarcPrimitive] = chain_primitives
        selected_path = "TANGENT_CHAIN"
    elif compare_with_chain and chain_primitives and not chain_has_review \
            and len(chain_primitives) < len(manual_primitives):
        primitives = chain_primitives
        selected_path = "TANGENT_CHAIN_FEWER_PRIMITIVES"
    else:
        primitives = manual_primitives
        selected_path = "MANUAL_REPAIRED"
        # Review flags belong to the chain construction that was not chosen.
        span_review_warnings = []
    if manual_diagnostics is not None:
        manual_diagnostics.update({
            "selected_construction": selected_path,
            "manual_repaired_primitive_count": (
                None if manual_primitives is None else len(manual_primitives)
            ),
            "tangent_chain_primitive_count": len(chain_primitives) if chain_primitives else None,
            "tangent_chain_has_review_joins": chain_has_review,
        })

    sampled = _sample_primitives(primitives, sampling)
    if closed and len(sampled):
        if float(np.linalg.norm(sampled[0] - sampled[-1])) > 1e-9:
            sampled = np.vstack([sampled, sampled[0]])
        else:
            sampled[-1] = sampled[0]
    joins = _build_join_table(primitives, closed, hard_points)
    deviation_reference = (
        np.vstack([physical_reference, physical_reference[0]])
        if closed else physical_reference
    )
    deviation = _symmetric_deviation(deviation_reference, sampled)
    raw_deviation_reference = np.vstack([raw, raw[0]]) if closed else raw
    raw_deviation = _deviation_distribution(raw_deviation_reference, sampled)
    maximum = float(deviation["maximum_mm"])
    rms = float(deviation["rms_mm"])
    if feature_side == "outer" and allowed_region is not None:
        forbidden = int(np.count_nonzero(
            ~intersects_xy(allowed_region, sampled[:, 0], sampled[:, 1])
        ))
    elif feature_side == "obstacle" and forbidden_region is not None:
        forbidden = int(np.count_nonzero(
            contains_xy(forbidden_region, sampled[:, 0], sampled[:, 1])
        ))
    else:
        forbidden = 0
    # Honest wall-side penetration report: how far the accepted geometry sits
    # beyond the RAW contour on the forbidden side, independent of whether it
    # is inside the noise allowance.  A VALID result must still show this.
    wall_side_penetration = _wall_side_penetration_stats(
        sampled, reference_polygon, feature_side, safety_allowance,
    )
    closure_error = (
        float(np.linalg.norm(primitives[-1].end - primitives[0].start))
        if closed and primitives
        else 0.0
    )
    smooth_mismatches = [
        float(item.tangent_mismatch_deg)
        for item in joins
        if item.join_type == "SMOOTH_G1" and item.tangent_mismatch_deg is not None
        and not item.sharp_corner_review
    ]
    # Joins touching a SHARP_CORNER_REQUIRES_REVIEW raw-fallback primitive are
    # expected to be non-smooth by construction (that is the whole point of
    # flagging them) -- they are reported separately and never silently
    # smoothed over, but they do not count as a fitter-tolerance violation.
    review_mismatches = [
        float(item.tangent_mismatch_deg)
        for item in joins
        if item.sharp_corner_review and item.tangent_mismatch_deg is not None
    ]
    maximum_tangent = max(smooth_mismatches, default=0.0)
    maximum_review_tangent = max(review_mismatches, default=0.0)
    maximum_gap = max((item.gap_mm for item in joins), default=0.0)
    sharp_corner_review_join_count = sum(item.sharp_corner_review for item in joins)
    self_intersection = bool(closed and len(sampled) >= 4 and not LineString(sampled).is_simple)
    if manual_diagnostics is not None:
        adaptive = bool(manual_diagnostics.get("adaptive_refinement_triggered", False))
        adaptive_passed = bool(
            maximum_gap <= 1e-7
            and maximum_tangent <= tangent_max + 1e-8
            and forbidden == 0
            and not self_intersection
            and maximum <= tolerance + 1e-8
        )
        manual_diagnostics.update({
            "adaptive_final_primitive_count": len(primitives),
            "adaptive_primitives_added": (
                len(primitives) - int(manual_diagnostics.get("primitive_count", 0))
                if adaptive else 0
            ),
            "adaptive_final_maximum_gap_mm": maximum_gap,
            "adaptive_final_maximum_tangent_mismatch_deg": maximum_tangent,
            "adaptive_final_forbidden_side_violations": forbidden,
            "adaptive_refinement_passed": adaptive_passed,
            "adaptive_stop_reason": (
                "FIRST_LONGEST_VALID_TANGENT_CHAIN_WITHIN_FIXED_CORRIDOR"
                if adaptive_passed and adaptive
                else (
                    (
                        "LOCAL_JOIN_REPAIR_PASSED_ALL_HARD_VALIDATION"
                        if adaptive_passed
                        else "LOCAL_JOIN_REPAIR_COMPLETE_HARD_VALIDATION_STILL_FAILED"
                    )
                    if not adaptive else "HARD_VALIDATION_STILL_FAILED"
                )
            ),
            "join_refinement_status": (
                "PASSED" if adaptive_passed else "FAILED_HARD_VALIDATION"
            ),
        })
    lengths = [primitive.length_mm for primitive in primitives]
    length_array = np.asarray(lengths, dtype=float)
    warnings: list[str] = list(span_review_warnings)
    if sharp_corner_review_join_count:
        warnings.append(
            f"SHARP_CORNER_REQUIRES_REVIEW: {sharp_corner_review_join_count} join(s) adjacent to "
            f"preserved raw geometry (max tangent mismatch {maximum_review_tangent:.3f} deg); "
            "inspect the flagged region before manufacturing approval."
        )
    preferred_min = float(settings.get("preferred_minimum_primitive_length_mm", 25.0))
    warning_min = float(settings.get("warning_primitive_length_mm", 10.0))
    micro_limit = float(settings.get("micro_primitive_length_mm", 3.0))
    short_count = sum(length < warning_min for length in lengths)
    micro_count = sum(length < micro_limit for length in lengths)
    if short_count:
        warnings.append(f"{short_count} primitives are shorter than {warning_min:.3f} mm")
    if micro_count:
        warnings.append(f"{micro_count} micro-primitives are shorter than {micro_limit:.3f} mm")
    if maximum_tangent > tangent_target + 1e-8:
        warnings.append(
            f"maximum smooth-join tangent mismatch {maximum_tangent:.6f} deg exceeds "
            f"the {tangent_target:.6f} deg target"
        )
    if manual_diagnostics is not None and (
        maximum_gap > 1e-7 or maximum_tangent > tangent_max + 1e-8
    ):
        warnings.append(
            "manual-complexity reconstruction is a low-count review proposal; "
            "exact connection/G1 refinement has not yet passed"
        )

    area = float(abs(Polygon(sampled).area)) if closed and len(sampled) >= 4 else 0.0
    raw_closed = np.vstack([raw, raw[0]]) if closed else raw
    raw_area = float(abs(Polygon(raw_closed).area)) if closed and len(raw) >= 3 else 0.0
    reference_closed = (
        np.vstack([physical_reference, physical_reference[0]])
        if closed else physical_reference
    )
    reference_area = (
        float(abs(Polygon(reference_closed).area))
        if closed and len(physical_reference) >= 3 else 0.0
    )
    metrics: dict[str, Any] = {
        "fit_tolerance_mm": tolerance,
        "fit_reference": (
            "MANUAL_BROAD_PHYSICAL_REFERENCE"
            if manual_diagnostics is not None
            else (
                "ROBUST_PHYSICAL_REFERENCE"
                if robust_reference is not None
                else "RAW_DEVELOPED_CONTOUR"
            )
        ),
        "max_deviation_mm": float(maximum),
        "bidirectional_hausdorff_like_maximum_mm": float(maximum),
        "p95_deviation_mm": float(deviation["p95_mm"]),
        "rms_deviation_mm": float(rms),
        "raw_to_cam_deviation": raw_deviation,
        "forbidden_side_violations": int(forbidden),
        "signed_safety_penetration_allowance_mm": safety_allowance,
        "wall_side_penetration": wall_side_penetration,
        "self_intersection": self_intersection,
        "closure_error_mm": closure_error,
        "maximum_join_gap_mm": maximum_gap,
        "maximum_smooth_tangent_mismatch_deg": maximum_tangent,
        "tangent_target_deg": tangent_target,
        "tangent_max_deg": tangent_max,
        "sharp_corner_review_join_count": sharp_corner_review_join_count,
        "sharp_corner_review_maximum_tangent_mismatch_deg": maximum_review_tangent,
        "logical_span_count": len(ranges),
        "primitive_count": len(primitives),
        "line_count": sum(item.kind == "LINE" for item in primitives),
        "arc_count": sum(item.kind == "ARC" for item in primitives),
        "primitive_lengths_mm": lengths,
        "minimum_primitive_length_mm": min(lengths, default=0.0),
        "p10_primitive_length_mm": float(np.percentile(length_array, 10)) if len(length_array) else 0.0,
        "median_primitive_length_mm": float(np.median(length_array)) if len(length_array) else 0.0,
        "p90_primitive_length_mm": float(np.percentile(length_array, 90)) if len(length_array) else 0.0,
        "maximum_primitive_length_mm": max(lengths, default=0.0),
        "preferred_minimum_primitive_length_mm": preferred_min,
        "warning_primitive_length_mm": warning_min,
        "micro_primitive_length_mm": micro_limit,
        "primitives_below_preferred_count": sum(length < preferred_min for length in lengths),
        "short_primitive_count": short_count,
        "micro_primitive_count": micro_count,
        "primitives_below_3mm_count": sum(length < 3.0 for length in lengths),
        "primitives_below_5mm_count": sum(length < 5.0 for length in lengths),
        "primitives_below_10mm_count": sum(length < 10.0 for length in lengths),
        "primitives_below_25mm_count": sum(length < 25.0 for length in lengths),
        "micro_primitive_explanations": [
            {
                "primitive_index": index,
                "length_mm": primitive.length_mm,
                "logical_span_index": primitive.logical_span_index,
                "source_start_index": primitive.source_start_index,
                "source_end_index": primitive.source_end_index,
                "reason": (
                    (
                        "Local G1 join connector retained to close the broad manual spans without "
                        "altering their accepted long LINE/ARC geometry."
                    )
                    if manual_diagnostics is not None
                    and primitive.source_start_index == primitive.source_end_index
                    else (
                        "Retained by the longest-valid-chain search because no longer LINE/ARC interval at "
                        "this shared tangent state passed both the 3.000 mm bidirectional corridor and signed safety test."
                    )
                ),
            }
            for index, primitive in enumerate(primitives, 1)
            if primitive.length_mm < micro_limit
        ],
        "raw_perimeter_length_mm": float(LineString(raw_closed).length),
        "polyarc_perimeter_length_mm": float(sum(lengths)),
        "perimeter_length_change_percent": 100.0 * (
            float(sum(lengths)) - float(LineString(raw_closed).length)
        ) / max(float(LineString(raw_closed).length), 1e-12),
        "raw_enclosed_area_mm2": raw_area,
        "reference_enclosed_area_mm2": reference_area,
        "polyarc_enclosed_area_mm2": area,
        "enclosed_area_change_mm2": area - reference_area,
        "enclosed_area_change_percent": 100.0 * (area - reference_area) / max(reference_area, 1e-12),
        "raw_bbox_mm": (np.ptp(raw, axis=0).astype(float).tolist() if len(raw) else [0.0, 0.0]),
        "reference_bbox_mm": (
            np.ptp(physical_reference, axis=0).astype(float).tolist()
            if len(physical_reference) else [0.0, 0.0]
        ),
        "polyarc_bbox_mm": (
            np.ptp(sampled, axis=0).astype(float).tolist() if len(sampled) else [0.0, 0.0]
        ),
        "bbox_change_mm": (
            (
                np.ptp(sampled, axis=0) - np.ptp(raw, axis=0)
            ).astype(float).tolist()
            if len(sampled) and len(raw) else [0.0, 0.0]
        ),
        "hard_corner_count": len(anchors),
        "hard_corner_join_count": sum(item.join_type == "HARD_CORNER" for item in joins),
        "smooth_tangent_join_count": sum(item.join_type == "SMOOTH_G1" for item in joins),
        "line_arc_only": all(item.kind in {"LINE", "ARC"} for item in primitives),
        "recursive_fit_diagnostics": fit_diagnostics,
        "manual_broad_regime_fit": manual_diagnostics,
    }
    hard_failures = (
        maximum > tolerance + 1e-8
        or forbidden > 0
        or self_intersection
        or closure_error > 1e-7
        or maximum_gap > 1e-7
        or maximum_tangent > tangent_max + 1e-8
        or not primitives
    )
    # Status vocabulary (see docs/CAM_GEOMETRY.md):
    #   INVALID        -- a hard geometric check failed; never exported as final.
    #   REVIEW         -- hard checks pass but the curve contains preserved raw
    #                     geometry at a SHARP_CORNER_REQUIRES_REVIEW location, so a
    #                     human must look before it is treated as a perimeter.
    #   TEST_GEOMETRY  -- hard checks pass; any remaining warnings are advisory
    #                     (short connector primitives, tangent above the 0.05 deg
    #                     target but within the 0.10 deg maximum).  Continuity
    #                     outranks primitive count, so advisories do not block.
    if hard_failures:
        status = "INVALID"
    elif sharp_corner_review_join_count:
        status = "REVIEW"
    else:
        status = "TEST_GEOMETRY"
    return PolyarcCurve(
        candidate_id=int((re.search(r"cam-(\d+)", spline_reference.cam_curve_id) or [None, "0"])[1]),
        is_closed=closed,
        raw_points=raw,
        reference_points=physical_reference,
        hard_corner_indices=anchors,
        hard_corner_points=hard_points,
        primitives=primitives,
        joins=joins,
        sampled_points=sampled,
        metrics=metrics,
        warnings=warnings,
        status=status,
    )


def fit_polyarc_curves(
    spline_references: list[ManufacturingCurve],
    configuration: dict[str, Any] | None = None,
    robust_references: list[Any] | None = None,
) -> list[PolyarcCurve]:
    if robust_references is None:
        return [fit_polyarc_curve(item, configuration) for item in spline_references]
    if len(robust_references) != len(spline_references):
        raise ValueError("robust reference count must match manufacturing-curve count")
    return [
        fit_polyarc_curve(item, configuration, reference)
        for item, reference in zip(spline_references, robust_references)
    ]


def polyarc_curve_from_dict(payload: dict[str, Any]) -> PolyarcCurve:
    """Rehydrate cached CAM primitives for pattern-only reruns."""

    primitives = [
        PolyarcPrimitive(
            kind=item["kind"],
            start=np.asarray(item["start"], dtype=float),
            end=np.asarray(item["end"], dtype=float),
            source_start_index=int(item.get("source_start_index", 0)),
            source_end_index=int(item.get("source_end_index", 0)),
            logical_span_index=int(item.get("logical_span_index", 0)),
            center=None if item.get("center") is None else np.asarray(item["center"], dtype=float),
            radius_mm=None if item.get("radius_mm") is None else float(item["radius_mm"]),
            signed_sweep_rad=math.radians(float(item.get("signed_sweep_deg", 0.0))),
        )
        for item in payload.get("primitives", [])
    ]
    joins = [
        PolyarcJoin(
            index=int(item.get("index", index)),
            join_type=item["join_type"],
            point=np.asarray(item["point"], dtype=float),
            gap_mm=float(item.get("gap_mm", 0.0)),
            tangent_mismatch_deg=(
                None if item.get("tangent_mismatch_deg") is None
                else float(item["tangent_mismatch_deg"])
            ),
            logical_span_before=int(item.get("logical_span_before", 0)),
            logical_span_after=int(item.get("logical_span_after", 0)),
        )
        for index, item in enumerate(payload.get("joins", []))
    ]
    sampled = _sample_primitives(primitives, 0.5)
    closed = bool(payload.get("is_closed", False))
    if closed and len(sampled) and np.linalg.norm(sampled[0] - sampled[-1]) > 1e-9:
        sampled = np.vstack((sampled, sampled[0]))
    return PolyarcCurve(
        candidate_id=int(payload.get("candidate_id", 0)),
        is_closed=closed,
        raw_points=np.empty((0, 2), dtype=float),
        reference_points=np.empty((0, 2), dtype=float),
        hard_corner_indices=[int(value) for value in payload.get("hard_corner_indices", [])],
        hard_corner_points=np.asarray(payload.get("hard_corner_points", []), dtype=float).reshape((-1, 2)),
        primitives=primitives,
        joins=joins,
        sampled_points=sampled,
        metrics=dict(payload.get("metrics", {})),
        warnings=list(payload.get("warnings", [])),
        status=str(payload.get("status", "INVALID")),
    )
