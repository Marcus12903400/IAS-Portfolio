from __future__ import annotations

"""Explicit, deterministic curve-conditioning stages for AutoDeck V0.2."""

from dataclasses import dataclass
import math
import re
from typing import Any

import numpy as np

from .models import AcceptedSurfaceGrid, ConditionedCurve, FeatureCurve


def curve_length(points: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()) if len(points) > 1 else 0.0


def _open_points(points: np.ndarray, closed: bool) -> np.ndarray:
    result = np.asarray(points, dtype=np.float64).copy()
    if closed and len(result) > 1 and np.linalg.norm(result[0] - result[-1]) <= 1e-9:
        result = result[:-1]
    return result


def _close_points(points: np.ndarray, closed: bool) -> np.ndarray:
    if closed and len(points) and np.linalg.norm(points[0] - points[-1]) > 1e-9:
        return np.vstack((points, points[0]))
    return points


def arc_length_parameterization(points: np.ndarray, closed: bool) -> tuple[np.ndarray, np.ndarray]:
    """Return a topology-preserving traversal and cumulative physical arc length."""
    traversal = _close_points(_open_points(points, closed), closed)
    if len(traversal) < 2:
        return traversal, np.zeros(len(traversal), dtype=np.float64)
    lengths = np.linalg.norm(np.diff(traversal, axis=0), axis=1)
    keep = np.r_[True, lengths > 1e-9]
    traversal = traversal[keep]
    if closed and len(traversal) > 1:
        traversal[-1] = traversal[0]
    cumulative = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(traversal, axis=0), axis=1))]
    return traversal, cumulative


def uniform_resample(points: np.ndarray, closed: bool, spacing_mm: float) -> np.ndarray:
    traversal, cumulative = arc_length_parameterization(points, closed)
    if len(traversal) < 2 or cumulative[-1] <= 1e-9:
        return traversal.copy()
    count = max(3 if closed else 2, int(math.ceil(cumulative[-1] / spacing_mm)))
    samples = np.linspace(0.0, cumulative[-1], count + (1 if closed else 0), endpoint=True)
    if not closed:
        samples = np.linspace(0.0, cumulative[-1], count, endpoint=True)
    output = np.column_stack([
        np.interp(samples, cumulative, traversal[:, axis]) for axis in range(3)
    ])
    if closed:
        output[-1] = output[0]
    return output


def _turn_angle(points_xy: np.ndarray, index: int, step: int, closed: bool) -> float:
    n = len(points_xy)
    if n < 3:
        return 0.0
    if not closed and (index - step < 0 or index + step >= n):
        return 0.0
    before = points_xy[(index - step) % n] - points_xy[index]
    after = points_xy[(index + step) % n] - points_xy[index]
    nb = np.linalg.norm(before); na = np.linalg.norm(after)
    if nb <= 1e-9 or na <= 1e-9:
        return 0.0
    interior = math.degrees(math.acos(float(np.clip(np.dot(before, after) / (nb * na), -1.0, 1.0))))
    return 180.0 - interior


def detect_persistent_corners(
    points: np.ndarray,
    closed: bool,
    spacing_mm: float,
    scales_mm: list[float],
    threshold_deg: float,
    minimum_scales: int,
) -> tuple[list[int], dict[str, Any]]:
    base = _open_points(points, closed)
    if len(base) < 3:
        return ([0, len(base) - 1] if len(base) else []), {"turning_angles_deg": {}}
    all_angles: dict[str, list[float]] = {}
    votes = np.zeros(len(base), dtype=np.int32)
    for scale in scales_mm:
        step = max(1, int(round(scale / max(spacing_mm, 1e-9))))
        step = min(step, max(1, (len(base) - 1) // 3))
        angles = np.asarray([_turn_angle(base[:, :2], i, step, closed) for i in range(len(base))])
        all_angles[f"{scale:g}"] = angles.tolist()
        votes += angles >= threshold_deg
    candidate = np.flatnonzero(votes >= minimum_scales)
    # Keep one strongest anchor per physical neighborhood so pixel-scale zigzags
    # do not masquerade as a run of persistent corners.
    strength = np.asarray([sum(all_angles[f"{s:g}"][i] for s in scales_mm) for i in range(len(base))])
    suppression_scale = scales_mm[1] if len(scales_mm) > 1 else scales_mm[0]
    suppression = max(1, int(round(suppression_scale / max(spacing_mm, 1e-9))))
    anchors: list[int] = []
    for index in sorted(candidate.tolist(), key=lambda i: (-strength[i], i)):
        if all(min((index - kept) % len(base), (kept - index) % len(base)) > suppression for kept in anchors):
            anchors.append(index)
    if not closed:
        anchors.extend([0, len(base) - 1])
    anchors = sorted(set(anchors))
    return anchors, {
        "scales_mm": scales_mm,
        "threshold_deg": threshold_deg,
        "minimum_scales": minimum_scales,
        "persistent_anchor_count": len(anchors),
        "anchor_indices": anchors,
    }


def _gaussian_kernel(radius: int) -> np.ndarray:
    if radius <= 0:
        return np.ones(1)
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    sigma = max(radius / 2.5, 0.75)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    return kernel / kernel.sum()


def _smooth_series(values: np.ndarray, radius: int, closed: bool) -> np.ndarray:
    if radius <= 0 or len(values) < 3:
        return values.copy()
    kernel = _gaussian_kernel(radius)
    if closed:
        padded = np.pad(values, (radius, radius), mode="wrap")
    else:
        padded = np.pad(values, (radius, radius), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _smooth_piecewise(values: np.ndarray, radius: int, closed: bool, anchors: list[int]) -> np.ndarray:
    if not anchors:
        return _smooth_series(values, radius, closed)
    result = values.copy(); n = len(values)
    ordered = sorted(set(index for index in anchors if 0 <= index < n))
    if not closed:
        ordered = sorted(set([0, *ordered, n - 1]))
        spans = [(np.arange(start, end + 1), False) for start, end in zip(ordered[:-1], ordered[1:])]
    else:
        spans = []
        for position, start in enumerate(ordered):
            end = ordered[(position + 1) % len(ordered)]
            if end <= start:
                indices = np.r_[np.arange(start, n), np.arange(0, end + 1)]
            else:
                indices = np.arange(start, end + 1)
            spans.append((indices, False))
    for indices, _ in spans:
        if len(indices) >= 3:
            filtered = _smooth_series(values[indices], min(radius, max(1, (len(indices) - 1) // 2)), False)
            filtered[0] = values[indices[0]]; filtered[-1] = values[indices[-1]]
            result[indices] = filtered
    return result


def smooth_xy_piecewise(
    points: np.ndarray,
    closed: bool,
    anchors: list[int],
    spacing_mm: float,
    window_mm: float,
    strength: float,
    max_deviation_mm: float,
) -> np.ndarray:
    base = _open_points(points, closed)
    result = base.copy()
    radius = max(1, int(round(0.5 * window_mm / max(spacing_mm, 1e-9))))
    for axis in (0, 1):
        filtered = _smooth_piecewise(base[:, axis], radius, closed, anchors)
        result[:, axis] = base[:, axis] + strength * (filtered - base[:, axis])
    if anchors:
        result[anchors, :2] = base[anchors, :2]
    delta = result[:, :2] - base[:, :2]
    distance = np.linalg.norm(delta, axis=1)
    scale = np.minimum(1.0, max_deviation_mm / np.maximum(distance, 1e-12))
    result[:, :2] = base[:, :2] + delta * scale[:, None]
    return _close_points(result, closed)


@dataclass(slots=True)
class SurfaceReconstruction:
    z_mm: np.ndarray
    floor_side_sign: np.ndarray
    fit_sample_count: np.ndarray
    fit_residual_rms_mm: np.ndarray
    success: np.ndarray


def _grid_sample(mask: np.ndarray, grid: AcceptedSurfaceGrid, xy: np.ndarray) -> bool:
    col = int(math.floor((xy[0] - grid.x_min_mm) / grid.resolution_mm))
    row = int(math.floor((xy[1] - grid.y_min_mm) / grid.resolution_mm))
    return bool(0 <= row < mask.shape[0] and 0 <= col < mask.shape[1] and mask[row, col])


def reconstruct_surface_height(
    points_mm: np.ndarray,
    closed: bool,
    grid: AcceptedSurfaceGrid,
    offsets_mm: list[float],
    fit_radius_mm: float,
    minimum_samples: int,
) -> SurfaceReconstruction:
    base = _open_points(points_mm, closed)
    count = len(base)
    output = np.full(count, np.nan); signs = np.zeros(count, dtype=np.int8)
    sample_counts = np.zeros(count, dtype=np.int32); residuals = np.full(count, np.nan)
    radius_px = max(1, int(math.ceil(fit_radius_mm / grid.resolution_mm)))
    for i, point in enumerate(base):
        if count < 2:
            continue
        if closed:
            tangent = base[(i + 1) % count, :2] - base[(i - 1) % count, :2]
        elif i == 0:
            tangent = base[1, :2] - base[0, :2]
        elif i == count - 1:
            tangent = base[-1, :2] - base[-2, :2]
        else:
            tangent = base[i + 1, :2] - base[i - 1, :2]
        length = np.linalg.norm(tangent)
        if length <= 1e-9:
            continue
        normal = np.array([-tangent[1], tangent[0]]) / length
        plus = sum(_grid_sample(grid.accepted_mask, grid, point[:2] + distance * normal) for distance in offsets_mm)
        minus = sum(_grid_sample(grid.accepted_mask, grid, point[:2] - distance * normal) for distance in offsets_mm)
        sign = 1 if plus >= minus else -1
        signs[i] = sign
        inward = point[:2] + sign * float(np.median(offsets_mm)) * normal
        center_col = int(math.floor((inward[0] - grid.x_min_mm) / grid.resolution_mm))
        center_row = int(math.floor((inward[1] - grid.y_min_mm) / grid.resolution_mm))
        r0 = max(0, center_row - radius_px); r1 = min(grid.accepted_mask.shape[0], center_row + radius_px + 1)
        c0 = max(0, center_col - radius_px); c1 = min(grid.accepted_mask.shape[1], center_col + radius_px + 1)
        if r1 <= r0 or c1 <= c0:
            continue
        rows, cols = np.mgrid[r0:r1, c0:c1]
        x = grid.x_min_mm + (cols + 0.5) * grid.resolution_mm
        y = grid.y_min_mm + (rows + 0.5) * grid.resolution_mm
        radial = np.hypot(x - inward[0], y - inward[1])
        valid = grid.accepted_mask[r0:r1, c0:c1] & np.isfinite(grid.floor_height_mm[r0:r1, c0:c1]) & (radial <= fit_radius_mm)
        if np.count_nonzero(valid) < minimum_samples:
            continue
        dx = x[valid] - point[0]; dy = y[valid] - point[1]
        z = grid.floor_height_mm[r0:r1, c0:c1][valid].astype(np.float64)
        a = np.column_stack((dx, dy, np.ones(len(dx))))
        weight = np.exp(-0.5 * (radial[valid] / max(0.6 * fit_radius_mm, 1e-9)) ** 2)
        coef = np.zeros(3)
        for _ in range(2):
            root = np.sqrt(weight)
            coef, *_ = np.linalg.lstsq(a * root[:, None], z * root, rcond=None)
            fit_residual = z - a @ coef
            scale = 1.4826 * np.median(np.abs(fit_residual - np.median(fit_residual))) + 1e-6
            weight *= np.minimum(1.0, 2.5 * scale / np.maximum(np.abs(fit_residual), 1e-9))
        output[i] = coef[2]
        sample_counts[i] = len(z)
        residuals[i] = float(np.sqrt(np.mean((z - a @ coef) ** 2)))
    success = np.isfinite(output)
    return SurfaceReconstruction(output, signs, sample_counts, residuals, success)


def smooth_z_physical(
    z_mm: np.ndarray,
    spacing_mm: float,
    window_mm: float,
    closed: bool,
    anchors: list[int] | None = None,
) -> np.ndarray:
    if len(z_mm) == 0:
        return z_mm.copy()
    result = z_mm.copy()
    valid = np.isfinite(result)
    if not np.any(valid):
        return result
    indices = np.arange(len(result))
    if np.count_nonzero(valid) == 1:
        result[:] = result[valid][0]
    else:
        result[~valid] = np.interp(indices[~valid], indices[valid], result[valid])
    radius = max(1, int(round(0.5 * window_mm / max(spacing_mm, 1e-9))))
    return _smooth_piecewise(result, radius, closed, anchors or [])


def signed_area_xy(points: np.ndarray) -> float:
    base = _open_points(points, True)
    if len(base) < 3:
        return 0.0
    return 0.5 * float(np.sum(base[:, 0] * np.roll(base[:, 1], -1) - np.roll(base[:, 0], -1) * base[:, 1]))


def _segments_intersect(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> bool:
    def cross(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> float:
        u = q - p; v = r - p
        return float(u[0] * v[1] - u[1] * v[0])
    ab_c = cross(a, b, c); ab_d = cross(a, b, d); cd_a = cross(c, d, a); cd_b = cross(c, d, b)
    return ab_c * ab_d < -1e-12 and cd_a * cd_b < -1e-12


def has_self_intersection(points: np.ndarray, closed: bool) -> bool:
    traversal = _close_points(_open_points(points, closed), closed)
    segment_count = len(traversal) - 1
    if segment_count < 3:
        return False
    lengths = np.linalg.norm(np.diff(traversal[:, :2], axis=0), axis=1)
    cell = max(float(np.median(lengths) * 4.0), 1.0)
    buckets: dict[tuple[int, int], list[int]] = {}
    checked: set[tuple[int, int]] = set()
    for i in range(segment_count):
        p = traversal[i, :2]; q = traversal[i + 1, :2]
        low = np.floor(np.minimum(p, q) / cell).astype(int); high = np.floor(np.maximum(p, q) / cell).astype(int)
        for cx in range(low[0], high[0] + 1):
            for cy in range(low[1], high[1] + 1):
                key = (cx, cy)
                for j in buckets.get(key, []):
                    pair = (j, i)
                    if pair in checked:
                        continue
                    checked.add(pair)
                    if abs(i - j) <= 1 or (closed and {i, j} == {0, segment_count - 1}):
                        continue
                    if _segments_intersect(traversal[j, :2], traversal[j + 1, :2], p, q):
                        return True
                buckets.setdefault(key, []).append(i)
    return False


def validate_topology(points: np.ndarray, closed: bool, expected_winding: float | None = None) -> dict[str, Any]:
    finite = bool(np.all(np.isfinite(points)))
    closure_error = float(np.linalg.norm(points[0] - points[-1])) if closed and len(points) else 0.0
    segments = np.linalg.norm(np.diff(points[:, :2], axis=0), axis=1) if len(points) > 1 else np.empty(0)
    micro = int(np.count_nonzero(segments <= 1e-6))
    winding = signed_area_xy(points) if closed else 0.0
    winding_preserved = bool(expected_winding is None or expected_winding == 0.0 or winding == 0.0 or np.sign(winding) == np.sign(expected_winding))
    self_intersection = has_self_intersection(points, closed) if finite else True
    valid = bool(finite and len(points) >= (4 if closed else 2) and closure_error <= 1e-6 and micro == 0 and not self_intersection and winding_preserved)
    return {
        "valid": valid,
        "finite": finite,
        "closure_error_mm": closure_error,
        "micro_segment_count": micro,
        "self_intersection": self_intersection,
        "signed_area_mm2": winding,
        "winding_preserved": winding_preserved,
    }


def _point_line_distance(points: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    delta = end - start
    denominator = float(np.dot(delta, delta))
    if denominator <= 1e-18:
        return np.linalg.norm(points - start, axis=1)
    t = np.clip(((points - start) @ delta) / denominator, 0.0, 1.0)
    return np.linalg.norm(points - (start + t[:, None] * delta), axis=1)


def _rdp_indices(points: np.ndarray, tolerance: float) -> list[int]:
    if len(points) <= 2:
        return list(range(len(points)))
    keep = {0, len(points) - 1}; stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        distance = _point_line_distance(points[start + 1:end, :2], points[start, :2], points[end, :2])
        if len(distance):
            local = int(np.argmax(distance)); maximum = float(distance[local])
            if maximum > tolerance:
                index = start + 1 + local; keep.add(index); stack.extend(((start, index), (index, end)))
    return sorted(keep)


def simplify_with_anchors(points: np.ndarray, closed: bool, anchors: list[int], tolerance_mm: float) -> np.ndarray:
    base = _open_points(points, closed)
    if len(base) < 3:
        return _close_points(base, closed)
    if closed:
        breaks = sorted(set([0, *anchors, len(base)]))
        extended = np.vstack((base, base[0]))
    else:
        breaks = sorted(set([0, *anchors, len(base) - 1])); extended = base
    keep: set[int] = set()
    for start, end in zip(breaks[:-1], breaks[1:]):
        if end <= start:
            continue
        for local in _rdp_indices(extended[start:end + 1], tolerance_mm):
            keep.add((start + local) % len(base))
    result = base[sorted(keep)]
    return _close_points(result, closed)


def _distribution(values: np.ndarray) -> dict[str, float]:
    finite = np.asarray(values)[np.isfinite(values)]
    if not len(finite):
        return {"mean": 0.0, "rms": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "mean": float(np.mean(finite)),
        "rms": float(np.sqrt(np.mean(finite ** 2))),
        "p95": float(np.percentile(finite, 95)),
        "max": float(np.max(finite)),
    }


def _profile_name(layer: str) -> str:
    upper = layer.upper()
    if "SECONDARY" in upper:
        return "secondary"
    if "NONSKID" in upper:
        return "nonskid"
    if "OBSTACLE" in upper:
        return "obstacle"
    if "SEAM" in upper or "HATCH" in upper:
        return "seam"
    return "deck"


def _identifier(prefix: str, index: int, curve: FeatureCurve) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", curve.name).strip("_").lower()
    return f"{prefix}-{index:04d}-{name}"


def condition_curve(
    curve: FeatureCurve,
    raw_curve_id: str,
    smoothed_curve_id: str,
    surface: AcceptedSurfaceGrid,
    mm_per_input_unit: float,
    config: dict[str, Any],
) -> ConditionedCurve:
    cfg = config["curve_conditioning"]
    profile_name = _profile_name(curve.layer); profile = cfg["profiles"][profile_name]
    spacing = float(cfg["resample_spacing_mm"])
    raw_mm = np.asarray(curve.points_input, dtype=np.float64) * mm_per_input_unit
    raw_validation = validate_topology(raw_mm, curve.closed)
    resampled = uniform_resample(raw_mm, curve.closed, spacing)
    anchors, corner_metrics = detect_persistent_corners(
        resampled, curve.closed, spacing,
        [float(v) for v in cfg["corner_scales_mm"]],
        float(cfg["corner_turn_threshold_deg"]), int(cfg["corner_persistence_scales"]),
    )
    xy_smoothed = smooth_xy_piecewise(
        resampled, curve.closed, anchors, spacing,
        float(profile["xy_window_mm"]), float(profile["xy_strength"]),
        float(profile["max_xy_deviation_mm"]),
    )
    xy_base = _open_points(resampled, curve.closed); xy_stage = _open_points(xy_smoothed, curve.closed)
    reconstruction = reconstruct_surface_height(
        xy_smoothed, curve.closed, surface,
        [float(v) for v in cfg["floor_side_offsets_mm"]],
        float(cfg["floor_fit_radius_mm"]), int(cfg["minimum_floor_fit_samples"]),
    )
    reconstructed_z = reconstruction.z_mm
    fallback_z = xy_base[:, 2]
    reconstructed_z = np.where(np.isfinite(reconstructed_z), reconstructed_z, fallback_z)
    conditioned_z = smooth_z_physical(
        reconstructed_z, spacing, float(profile["z_window_mm"]), curve.closed, anchors
    )
    conditioned = xy_stage.copy(); conditioned[:, 2] = conditioned_z
    conditioned = _close_points(conditioned, curve.closed)
    expected_winding = raw_validation["signed_area_mm2"] if curve.closed else None
    stage_validation = validate_topology(conditioned, curve.closed, expected_winding)
    warnings: list[str] = []
    if not stage_validation["valid"]:
        warnings.append("Conditioned stage was topologically invalid; retained the last valid resampled geometry.")
        conditioned = resampled.copy()
    simplified = simplify_with_anchors(
        conditioned, curve.closed, anchors, float(profile["simplify_tolerance_mm"])
    )
    final_validation = validate_topology(simplified, curve.closed, expected_winding)
    if not final_validation["valid"]:
        warnings.append("Simplification was topologically invalid; retained the valid pre-simplification curve.")
        simplified = conditioned.copy(); final_validation = validate_topology(simplified, curve.closed, expected_winding)
    xy_delta = np.linalg.norm(xy_stage[:, :2] - xy_base[:, :2], axis=1)
    raw_z = xy_base[:, 2]
    raw_surface_delta = raw_z - reconstructed_z
    smooth_surface_delta = conditioned_z - reconstructed_z
    raw_to_smooth_z = conditioned_z - raw_z
    projection_fraction = float(np.count_nonzero(reconstruction.success) / max(len(reconstruction.success), 1))
    if not final_validation["valid"] or not raw_validation["finite"]:
        status = "INVALID"
    elif projection_fraction < 0.80 or warnings:
        status = "NEEDS_REVIEW"
    else:
        status = "GOOD"
    smooth_layer = curve.layer.replace("AUTODECK::", "AUTODECK::SMOOTH_3D::", 1)
    smooth_curve = FeatureCurve(
        smooth_layer, f"{curve.name}_conditioned", simplified / mm_per_input_unit,
        curve.closed, curve.confidence, {"source_raw_curve_id": raw_curve_id}, smoothed_curve_id,
    )
    raw_length = curve_length(raw_mm); resampled_length = curve_length(resampled); smooth_length = curve_length(simplified)
    metrics: dict[str, Any] = {
        "profile": profile_name,
        "raw_point_count": int(len(raw_mm)),
        "resampled_point_count": int(len(resampled)),
        "smoothed_point_count": int(len(simplified)),
        "raw_length_mm": raw_length,
        "resampled_length_mm": resampled_length,
        "smoothed_length_mm": smooth_length,
        "resampling_length_change_percent": 100.0 * (resampled_length - raw_length) / max(raw_length, 1e-12),
        "smoothing_length_change_percent": 100.0 * (smooth_length - raw_length) / max(raw_length, 1e-12),
        "point_reduction_percent_vs_resampled": 100.0 * (len(resampled) - len(simplified)) / max(len(resampled), 1),
        "xy_deviation_mm": _distribution(xy_delta),
        "raw_adjacent_surface_z_deviation_mm": _distribution(np.abs(raw_surface_delta)),
        "smooth_adjacent_surface_z_deviation_mm": _distribution(np.abs(smooth_surface_delta)),
        "raw_to_smooth_z_deviation_mm": _distribution(np.abs(raw_to_smooth_z)),
        "surface_fit_residual_mm": _distribution(reconstruction.fit_residual_rms_mm),
        "surface_projection_success_fraction": projection_fraction,
        "floor_side_plus_count": int(np.count_nonzero(reconstruction.floor_side_sign > 0)),
        "floor_side_minus_count": int(np.count_nonzero(reconstruction.floor_side_sign < 0)),
        "corner_detection": corner_metrics,
        "raw_validation": raw_validation,
        "conditioned_validation": stage_validation,
        "final_validation": final_validation,
        "configured_max_xy_deviation_mm": float(profile["max_xy_deviation_mm"]),
        "simplify_tolerance_mm": float(profile["simplify_tolerance_mm"]),
    }
    return ConditionedCurve(
        curve, raw_curve_id, smooth_curve, smoothed_curve_id,
        resampled / mm_per_input_unit, anchors, status, metrics, warnings,
    )


def condition_curves(
    curves: list[FeatureCurve],
    surface: AcceptedSurfaceGrid,
    mm_per_input_unit: float,
    config: dict[str, Any],
) -> list[ConditionedCurve]:
    output: list[ConditionedCurve] = []
    for index, curve in enumerate(curves, 1):
        raw_id = curve.curve_id or _identifier("raw", index, curve)
        smooth_id = _identifier("smooth", index, curve)
        # Do not mutate the detector's raw curve object or point array.
        raw_copy = FeatureCurve(
            curve.layer, curve.name, np.asarray(curve.points_input).copy(), curve.closed,
            curve.confidence, dict(curve.metrics), raw_id,
        )
        output.append(condition_curve(raw_copy, raw_id, smooth_id, surface, mm_per_input_unit, config))
    return output


def useful_raw_curves(
    primary: FeatureCurve | None,
    secondaries: dict[int, FeatureCurve],
    feature_curves: list[FeatureCurve],
) -> list[FeatureCurve]:
    curves: list[FeatureCurve] = []
    if primary is not None:
        curves.append(primary)
    curves.extend(secondaries[key] for key in sorted(secondaries))
    curves.extend(curve for curve in feature_curves if curve.layer.startswith("AUTODECK::"))
    return curves
