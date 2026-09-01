from __future__ import annotations

"""Hard-feature preservation and manufacturing-oriented flat curve fitting."""

from dataclasses import dataclass, field
import math
from typing import Any

import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import LinearConstraint, minimize
from scipy.spatial import cKDTree

from .conditioning import curve_length, has_self_intersection, uniform_resample
from .models import FlattenedCurve


@dataclass(slots=True)
class FitSpan:
    kind: str
    source_start_index: int
    source_end_index: int
    sampled_points_mm: np.ndarray
    control_points_mm: np.ndarray = field(default_factory=lambda: np.empty((0, 3)))
    knots: np.ndarray = field(default_factory=lambda: np.empty(0))
    degree: int = 1
    center_mm: np.ndarray | None = None
    radius_mm: float | None = None
    start_angle_deg: float | None = None
    end_angle_deg: float | None = None
    clockwise: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ManufacturingCurve:
    source: FlattenedCurve
    cam_curve_id: str
    layer: str
    closed: bool
    anchor_indices: list[int]
    anchor_points_mm: np.ndarray
    spans: list[FitSpan]
    points_mm: np.ndarray
    status: str
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _open_points(points: np.ndarray, closed: bool) -> np.ndarray:
    result = np.asarray(points, dtype=np.float64)
    if closed and len(result) > 1 and np.linalg.norm(result[0, :2] - result[-1, :2]) <= 1e-8:
        return result[:-1]
    return result


def _collapse_consecutive_samples(points: np.ndarray, closed: bool, minimum_separation_mm: float) -> tuple[np.ndarray, int]:
    base = _open_points(points, closed)
    if len(base) <= 1 or minimum_separation_mm <= 0:
        return base, 0
    kept = [0]
    for index in range(1, len(base)):
        if np.linalg.norm(base[index, :2] - base[kept[-1], :2]) >= minimum_separation_mm:
            kept.append(index)
    if closed and len(kept) > 2 and np.linalg.norm(base[kept[-1], :2] - base[kept[0], :2]) < minimum_separation_mm:
        kept.pop()
    return base[kept], int(len(base) - len(kept))


def map_points_to_indices(
    points_2d: np.ndarray,
    target_points_2d: np.ndarray,
    *,
    tolerance_mm: float = 25.0,
    context: str = "index",
) -> list[int]:
    """Resolve landmark point coordinates to indices in an independently
    derived point array by nearest match, instead of reusing indices computed
    against a different array (which silently aliases under ``% len(...)``
    whenever the two arrays differ in length or ordering).

    ``tolerance_mm`` bounds how far a match may be from its source point.
    Corner-detection smoothing/resampling can shift a landmark by a few mm
    from the raw contour it was derived from (see ``diagnostic_spacing_mm``/
    ``diagnostic_smoothing_mm``, which top out at 5 mm in the default
    profiles); 25 mm is a generous margin for that while still catching a
    genuinely broken mapping, which on a real boat contour produces a miss
    far larger than any legitimate smoothing drift. A miss beyond tolerance
    fails loudly rather than silently indexing the wrong physical point.
    """

    points = np.asarray(points_2d, dtype=float)
    if not len(points):
        return []
    target = np.asarray(target_points_2d, dtype=float)
    if not len(target):
        raise ValueError(f"{context}: target point array is empty, cannot resolve {len(points)} point(s)")
    tree = cKDTree(target[:, :2])
    distances, nearest = tree.query(points[:, :2], k=1)
    bad = np.flatnonzero(distances > tolerance_mm)
    if len(bad):
        raise ValueError(
            f"{context}: {len(bad)} of {len(points)} point(s) could not be matched to the target "
            f"point array within {tolerance_mm:g} mm (worst miss {float(np.max(distances[bad])):.3f} mm); "
            "refusing to silently alias a hard-corner index via modulo indexing."
        )
    return sorted({int(v) for v in np.atleast_1d(nearest)})


def _smooth_diagnostic(points: np.ndarray, closed: bool, radius: int) -> np.ndarray:
    if radius <= 0:
        return points.copy()
    result = np.zeros_like(points)
    n = len(points)
    for offset in range(-radius, radius + 1):
        if closed:
            shifted = np.roll(points, offset, axis=0)
        else:
            indices = np.clip(np.arange(n) + offset, 0, n - 1)
            shifted = points[indices]
        result += shifted / (2 * radius + 1)
    return result


def _turn_angles(points: np.ndarray, step: int, closed: bool) -> np.ndarray:
    count = len(points); output = np.zeros(count, dtype=np.float64)
    for index in range(count):
        if not closed and (index < step or index + step >= count):
            continue
        a = points[(index - step) % count, :2] - points[index, :2]
        b = points[(index + step) % count, :2] - points[index, :2]
        na = np.linalg.norm(a); nb = np.linalg.norm(b)
        if na <= 1e-12 or nb <= 1e-12:
            continue
        interior = math.degrees(math.acos(float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))))
        output[index] = 180.0 - interior
    return output


def _cyclic_distance(a: int, b: int, count: int, closed: bool) -> int:
    direct = abs(a - b)
    return min(direct, count - direct) if closed else direct


def detect_manufacturing_corners(
    points_mm: np.ndarray,
    closed: bool,
    profile: str,
    config: dict[str, Any],
) -> tuple[np.ndarray, list[int], dict[str, Any]]:
    cfg = config["manufacturing_fit"]
    profile_cfg = cfg["corner_profiles"][profile]
    original = _open_points(points_mm, closed)
    diagnostic_spacing = float(profile_cfg["diagnostic_spacing_mm"])
    resampled = _open_points(uniform_resample(points_mm, closed, diagnostic_spacing), closed)
    radius = max(1, int(round(float(profile_cfg["diagnostic_smoothing_mm"]) / diagnostic_spacing)))
    diagnostic = _smooth_diagnostic(resampled, closed, radius)
    scales = [float(value) for value in profile_cfg["scales_mm"]]
    responses = np.vstack([
        _turn_angles(diagnostic, max(1, int(round(scale / diagnostic_spacing))), closed)
        for scale in scales
    ])
    votes = np.count_nonzero(responses >= float(profile_cfg["turn_threshold_deg"]), axis=0)
    persistence = int(profile_cfg["persistence_scales"])
    candidate_indices = np.flatnonzero(votes >= persistence)
    order = sorted(
        candidate_indices.tolist(),
        key=lambda index: (int(votes[index]), float(np.max(responses[:, index]))),
        reverse=True,
    )
    cluster_steps = max(1, int(round(float(profile_cfg["cluster_distance_mm"]) / diagnostic_spacing)))
    separation_steps = max(1, int(round(float(profile_cfg["minimum_separation_mm"]) / diagnostic_spacing)))
    selected: list[int] = []
    for index in order:
        if any(
            _cyclic_distance(index, existing, len(diagnostic), closed) < max(cluster_steps, separation_steps)
            or np.linalg.norm(diagnostic[index, :2] - diagnostic[existing, :2]) < float(profile_cfg["cluster_distance_mm"])
            for existing in selected
        ):
            continue
        selected.append(index)
    selected.sort()
    mapped = sorted({
        int(np.argmin(np.linalg.norm(original[:, :2] - diagnostic[index, :2], axis=1)))
        for index in selected
    })
    length = curve_length(np.vstack((original, original[:1]))) if closed else curve_length(original)
    density = 1000.0 * len(mapped) / max(length, 1e-12)
    metrics = {
        "profile": profile,
        "diagnostic_spacing_mm": diagnostic_spacing,
        "diagnostic_point_count": int(len(diagnostic)),
        "physical_scales_mm": scales,
        "persistence_requirement": persistence,
        "raw_persistent_response_count": int(len(candidate_indices)),
        "protected_corner_count": len(mapped),
        "anchor_density_per_m": density,
        "over_anchor_limit_per_m": float(profile_cfg["maximum_anchor_density_per_m"]),
        "over_anchored": density > float(profile_cfg["maximum_anchor_density_per_m"]),
    }
    # Return the original developed reference for manufacturing.  The diagnostic
    # copies above are evidence only and are never allowed to move an anchor.
    return original, mapped, metrics


def _line_candidate(points: np.ndarray, sample_spacing_mm: float) -> FitSpan | None:
    """Return the exact-endpoint line so its rejection can be fully measured."""
    start = points[0, :2]; end = points[-1, :2]; delta = end - start; length = np.linalg.norm(delta)
    if length <= 1e-9:
        return None
    sample_count = max(2, int(math.ceil(length / max(sample_spacing_mm, 0.05))) + 1)
    sampled = np.column_stack((
        np.linspace(start[0], end[0], sample_count),
        np.linspace(start[1], end[1], sample_count),
        np.zeros(sample_count),
    ))
    return FitSpan("LINE", 0, len(points) - 1, sampled, np.vstack((points[0], points[-1])), degree=1)


def _fit_circle(points: np.ndarray) -> tuple[np.ndarray, float, np.ndarray, float] | None:
    xy = points[:, :2]
    if len(xy) < 5:
        return None
    matrix = np.column_stack((2.0 * xy[:, 0], 2.0 * xy[:, 1], np.ones(len(xy))))
    try:
        cx, cy, c = np.linalg.lstsq(matrix, np.sum(xy ** 2, axis=1), rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    radius_squared = c + cx * cx + cy * cy
    if radius_squared <= 0:
        return None
    center = np.asarray([cx, cy]); radius = float(math.sqrt(radius_squared))
    angles = np.unwrap(np.arctan2(xy[:, 1] - cy, xy[:, 0] - cx))
    maximum_error = float(np.max(np.abs(np.linalg.norm(xy - center, axis=1) - radius)))
    return center, radius, angles, maximum_error


def _fit_endpoint_circle(points: np.ndarray) -> tuple[np.ndarray, float, np.ndarray] | None:
    """Fit a circle whose center is constrained to the endpoint bisector.

    Both protected span endpoints therefore lie on the mathematical arc exactly;
    an accepted ARC can never create a join gap at a hard corner.
    """
    xy = np.asarray(points, dtype=np.float64)[:, :2]
    if len(xy) < 5:
        return None
    start = xy[0]; end = xy[-1]; chord = end - start; chord_length = float(np.linalg.norm(chord))
    if chord_length <= 1e-9:
        return None
    midpoint = 0.5 * (start + end)
    normal = np.asarray([-chord[1], chord[0]], dtype=np.float64) / chord_length
    q = xy - midpoint; a = start - midpoint
    design = 2.0 * ((q - a) @ normal)
    rhs = np.einsum("ij,ij->i", q, q) - float(np.dot(a, a))
    denominator = float(np.dot(design, design))
    if denominator <= 1e-12:
        return None
    offset = float(np.dot(design, rhs) / denominator)
    center = midpoint + offset * normal
    radius = float(np.linalg.norm(start - center))
    if not np.isfinite(radius) or radius <= 1e-9:
        return None
    angles = np.unwrap(np.arctan2(xy[:, 1] - center[1], xy[:, 0] - center[0]))
    return center, radius, angles


def _arc_candidate(
    points: np.ndarray,
    maximum_sweep_deg: float,
    sample_spacing_mm: float,
) -> tuple[FitSpan | None, dict[str, Any]]:
    fitted = _fit_endpoint_circle(points)
    if fitted is None:
        return None, {"geometrically_appropriate": False, "rejection_reason": "endpoint-constrained circle fit was degenerate"}
    center, radius, angles = fitted
    delta = np.diff(angles); total = float(angles[-1] - angles[0])
    direction = 1.0 if total >= 0 else -1.0
    monotonic = float(np.mean(delta * direction >= -math.radians(0.5))) if len(delta) else 1.0
    sweep = abs(math.degrees(total))
    if monotonic < 0.95 or sweep < 3.0 or sweep > maximum_sweep_deg:
        return None, {
            "geometrically_appropriate": False,
            "monotonic_fraction": monotonic,
            "sweep_deg": sweep,
            "rejection_reason": "arc traversal is non-monotonic or outside the configured sweep range",
        }
    sample_count = max(8, int(math.ceil(radius * abs(total) / max(sample_spacing_mm, 0.05))) + 1)
    dense_angles = np.linspace(angles[0], angles[-1], sample_count)
    sampled = np.column_stack((
        center[0] + radius * np.cos(dense_angles),
        center[1] + radius * np.sin(dense_angles),
        np.zeros(sample_count),
    ))
    return FitSpan(
        "ARC", 0, len(points) - 1, sampled,
        center_mm=np.asarray([center[0], center[1], 0.0]), radius_mm=radius,
        start_angle_deg=float(math.degrees(angles[0]) % 360.0),
        end_angle_deg=float(math.degrees(angles[-1]) % 360.0),
        clockwise=total < 0,
        metrics={"sweep_deg": sweep, "monotonic_fraction": monotonic, "exact_endpoints": True},
    ), {"geometrically_appropriate": True, "monotonic_fraction": monotonic, "sweep_deg": sweep}


def _rdp(points: np.ndarray, tolerance: float) -> np.ndarray:
    if len(points) <= 2:
        return points.copy()
    start = points[0, :2]; end = points[-1, :2]; delta = end - start; norm = np.linalg.norm(delta)
    if norm <= 1e-12:
        distances = np.linalg.norm(points[1:-1, :2] - start, axis=1)
    else:
        offsets = points[1:-1, :2] - start
        distances = np.abs(delta[0] * offsets[:, 1] - delta[1] * offsets[:, 0]) / norm
    if not len(distances) or float(np.max(distances)) <= tolerance:
        return points[[0, -1]].copy()
    split = int(np.argmax(distances)) + 1
    return np.vstack((_rdp(points[:split + 1], tolerance)[:-1], _rdp(points[split:], tolerance)))


def _sample_polyline(points: np.ndarray, spacing: float, closed: bool = False) -> np.ndarray:
    chain = np.asarray(points, dtype=np.float64)
    if closed and np.linalg.norm(chain[0, :2] - chain[-1, :2]) > 1e-9:
        chain = np.vstack((chain, chain[:1]))
    output: list[np.ndarray] = []
    for start, end in zip(chain[:-1], chain[1:]):
        count = max(2, int(math.ceil(np.linalg.norm(end[:2] - start[:2]) / spacing)) + 1)
        segment = np.linspace(start, end, count)
        output.extend(segment[:-1])
    output.append(chain[-1])
    return np.asarray(output)


def _symmetric_deviation(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    ref = _sample_polyline(np.asarray(reference), 0.25)[:, :2]
    cand = _sample_polyline(np.asarray(candidate), 0.25)[:, :2]
    ref_to_fit = cKDTree(cand).query(ref, workers=-1)[0]
    fit_to_ref = cKDTree(ref).query(cand, workers=-1)[0]
    combined = np.r_[ref_to_fit, fit_to_ref]
    return {
        "rms_mm": float(np.sqrt(np.mean(combined ** 2))),
        "p95_mm": float(np.percentile(combined, 95)),
        "maximum_mm": float(np.max(combined)),
    }


def _parameter_values(points: np.ndarray, method: str) -> np.ndarray:
    delta = np.linalg.norm(np.diff(np.asarray(points)[:, :2], axis=0), axis=1)
    if method == "centripetal":
        delta = np.sqrt(delta)
    elif method != "chord-length":
        raise ValueError("manufacturing_fit.spline_parameterization must be centripetal or chord-length")
    cumulative = np.r_[0.0, np.cumsum(delta)]
    if cumulative[-1] <= 1e-12:
        return np.linspace(0.0, 1.0, len(points))
    return cumulative / cumulative[-1]


def _clamped_knots(control_count: int, degree: int) -> np.ndarray:
    interior_count = control_count - degree - 1
    interior = np.linspace(0.0, 1.0, interior_count + 2)[1:-1] if interior_count > 0 else np.empty(0)
    return np.r_[np.zeros(degree + 1), interior, np.ones(degree + 1)]


def _fair_spline_controls(
    parameters: np.ndarray,
    target_points: np.ndarray,
    knots: np.ndarray,
    degree: int,
    fairness_weight: float,
    correction_parameters: np.ndarray | None = None,
    correction_targets: np.ndarray | None = None,
    correction_weight: float = 0.0,
) -> np.ndarray:
    """Fit clamped controls with exact endpoints and second-difference fairness.

    The objective uses mean squared data error plus a dimensionless fairness
    weight times mean squared control-polygon second difference.  This keeps the
    setting stable as input sampling density changes.
    """
    design = BSpline.design_matrix(parameters, knots, degree, extrapolate=False).toarray()
    control_count = design.shape[1]
    second = np.zeros((max(0, control_count - 2), control_count), dtype=np.float64)
    for row in range(len(second)):
        second[row, row:row + 3] = (1.0, -2.0, 1.0)
    regularization = fairness_weight * len(parameters) / max(len(second), 1)
    normal = design.T @ design
    if len(second):
        normal += regularization * (second.T @ second)
    rhs = design.T @ np.asarray(target_points)[:, :2]
    if correction_parameters is not None and correction_targets is not None and len(correction_parameters):
        correction_design = BSpline.design_matrix(
            correction_parameters, knots, degree, extrapolate=False,
        ).toarray()
        normal += correction_weight * (correction_design.T @ correction_design)
        rhs += correction_weight * (correction_design.T @ np.asarray(correction_targets)[:, :2])
    controls = np.zeros((control_count, 3), dtype=np.float64)
    controls[0, :2] = target_points[0, :2]
    controls[-1, :2] = target_points[-1, :2]
    if control_count > 2:
        interior = np.arange(1, control_count - 1)
        boundary = np.asarray([0, control_count - 1])
        local_rhs = rhs[interior] - normal[np.ix_(interior, boundary)] @ controls[boundary, :2]
        try:
            controls[interior, :2] = np.linalg.solve(normal[np.ix_(interior, interior)], local_rhs)
        except np.linalg.LinAlgError:
            controls[interior, :2] = np.linalg.lstsq(normal[np.ix_(interior, interior)], local_rhs, rcond=None)[0]
    return controls


def _constrained_fair_spline_controls(
    parameters: np.ndarray,
    source_points: np.ndarray,
    target_points: np.ndarray,
    knots: np.ndarray,
    degree: int,
    fairness_weight: float,
    safe_margin_mm: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Solve the fair fit with hard linearized safe-side constraints.

    The safe direction is supplied by the robust polygon offset target.  SLSQP
    solves the resulting convex quadratic objective with linear inequalities;
    the exact polygon test still independently validates the dense result.
    """
    design = BSpline.design_matrix(parameters, knots, degree, extrapolate=False).toarray()
    control_count = design.shape[1]
    second = np.zeros((max(0, control_count - 2), control_count), dtype=np.float64)
    for row in range(len(second)):
        second[row, row:row + 3] = (1.0, -2.0, 1.0)
    normal = design.T @ design
    if len(second):
        regularization = fairness_weight * len(parameters) / len(second)
        normal += regularization * (second.T @ second)
    rhs = design.T @ np.asarray(target_points)[:, :2]
    endpoints = np.asarray([0, control_count - 1], dtype=np.int64)
    interior = np.arange(1, control_count - 1, dtype=np.int64)
    fixed = np.asarray(source_points)[[0, -1], :2]
    local_normal = normal[np.ix_(interior, interior)]
    local_rhs = rhs[interior] - normal[np.ix_(interior, endpoints)] @ fixed
    system = np.block([
        [local_normal, np.zeros_like(local_normal)],
        [np.zeros_like(local_normal), local_normal],
    ])
    objective_rhs = np.r_[local_rhs[:, 0], local_rhs[:, 1]]
    try:
        initial = np.linalg.solve(system + 1e-10 * np.eye(len(system)), objective_rhs)
    except np.linalg.LinAlgError:
        initial = np.linalg.lstsq(system, objective_rhs, rcond=None)[0]

    directions = np.asarray(target_points)[:, :2] - np.asarray(source_points)[:, :2]
    direction_lengths = np.linalg.norm(directions, axis=1)
    directions /= np.maximum(direction_lengths[:, None], 1e-12)
    active_samples = np.flatnonzero(direction_lengths > 1e-8)
    active_samples = active_samples[(active_samples > 0) & (active_samples < len(parameters) - 1)]
    constraints: list[LinearConstraint] = []
    if len(active_samples):
        local_design = design[np.ix_(active_samples, interior)]
        matrix = np.zeros((len(active_samples), 2 * len(interior)), dtype=np.float64)
        matrix[:, :len(interior)] = local_design * directions[active_samples, 0, None]
        matrix[:, len(interior):] = local_design * directions[active_samples, 1, None]
        fixed_values = design[np.ix_(active_samples, endpoints)] @ fixed
        desired = np.asarray(source_points)[active_samples, :2] + safe_margin_mm * directions[active_samples]
        lower = (
            np.einsum("ij,ij->i", desired, directions[active_samples])
            - np.einsum("ij,ij->i", fixed_values, directions[active_samples])
        )
        constraints.append(LinearConstraint(matrix, lower, np.inf))

    result = minimize(
        lambda value: 0.5 * float(value @ system @ value) - float(objective_rhs @ value),
        initial,
        jac=lambda value: system @ value - objective_rhs,
        constraints=constraints,
        method="SLSQP",
        options={"maxiter": 500, "ftol": 1e-9, "disp": False},
    )
    controls = np.zeros((control_count, 3), dtype=np.float64)
    controls[0, :2] = fixed[0]; controls[-1, :2] = fixed[-1]
    controls[1:-1, 0] = result.x[:len(interior)]
    controls[1:-1, 1] = result.x[len(interior):]
    return controls, {
        "solver": "SLSQP-linear-safe-side-QP",
        "solver_success": bool(result.success),
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "solver_iteration_count": int(result.nit),
        "linear_constraint_count": int(len(active_samples)),
        "requested_safe_margin_mm": safe_margin_mm,
    }


def _one_sided_fair_spline_controls(
    parameters: np.ndarray,
    points: np.ndarray,
    knots: np.ndarray,
    degree: int,
    fairness_weight: float,
    safe_directions: np.ndarray,
    margin_mm: float,
    penalty_weight: float,
    iterations: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Solve a fair least-squares fit with iterative linear half-plane penalties.

    Each reference sample defines the local safe half-plane. Only violated
    constraints enter the active set, so the curve is not uniformly offset away
    from the scan merely to satisfy isolated raster stair steps.
    """
    design = BSpline.design_matrix(parameters, knots, degree, extrapolate=False).toarray()
    control_count = design.shape[1]
    second = np.zeros((max(0, control_count - 2), control_count), dtype=np.float64)
    for row in range(len(second)):
        second[row, row:row + 3] = (1.0, -2.0, 1.0)
    regularization = fairness_weight * len(parameters) / max(len(second), 1)
    normal = design.T @ design
    if len(second):
        normal += regularization * (second.T @ second)
    rhs_xy = design.T @ np.asarray(points)[:, :2]
    system = np.zeros((2 * control_count, 2 * control_count), dtype=np.float64)
    system[:control_count, :control_count] = normal
    system[control_count:, control_count:] = normal
    rhs = np.r_[rhs_xy[:, 0], rhs_xy[:, 1]]
    full = np.zeros(2 * control_count, dtype=np.float64)
    full[0] = points[0, 0]; full[control_count - 1] = points[-1, 0]
    full[control_count] = points[0, 1]; full[-1] = points[-1, 1]
    boundary = np.asarray([0, control_count - 1, control_count, 2 * control_count - 1], dtype=np.int64)
    interior = np.asarray([
        *range(1, control_count - 1),
        *range(control_count + 1, 2 * control_count - 1),
    ], dtype=np.int64)
    constraint = np.zeros((len(parameters), 2 * control_count), dtype=np.float64)
    constraint[:, :control_count] = design * safe_directions[:, 0, None]
    constraint[:, control_count:] = design * safe_directions[:, 1, None]
    desired = np.einsum("ij,ij->i", np.asarray(points)[:, :2], safe_directions) + margin_mm
    desired[[0, -1]] = np.einsum(
        "ij,ij->i", np.asarray(points)[[0, -1], :2], safe_directions[[0, -1]],
    )
    active_count = 0
    for iteration in range(max(1, iterations)):
        values = constraint @ full
        active = values < desired - 1e-7
        active[[0, -1]] = False
        active_count = int(np.count_nonzero(active))
        local_system = system.copy(); local_rhs = rhs.copy()
        if active_count:
            active_constraint = constraint[active]
            active_desired = desired[active]
            local_system += penalty_weight * (active_constraint.T @ active_constraint)
            local_rhs += penalty_weight * (active_constraint.T @ active_desired)
        reduced_rhs = local_rhs[interior] - local_system[np.ix_(interior, boundary)] @ full[boundary]
        try:
            full[interior] = np.linalg.solve(local_system[np.ix_(interior, interior)], reduced_rhs)
        except np.linalg.LinAlgError:
            full[interior] = np.linalg.lstsq(local_system[np.ix_(interior, interior)], reduced_rhs, rcond=None)[0]
        if not active_count:
            break
    controls = np.column_stack((full[:control_count], full[control_count:], np.zeros(control_count)))
    signed_margin = constraint @ full - desired
    return controls, {
        "constraint_iteration_count": iteration + 1,
        "active_constraint_count": active_count,
        "minimum_signed_constraint_margin_mm": float(np.min(signed_margin[1:-1])) if len(signed_margin) > 2 else 0.0,
        "constraint_penalty_weight": penalty_weight,
        "requested_safe_margin_mm": margin_mm,
    }


def _safe_side_targets(
    points: np.ndarray,
    reference_polygon: np.ndarray,
    feature_side: str,
    offset_mm: float | np.ndarray,
    normal_scale_mm: float,
) -> np.ndarray:
    targets = np.asarray(points, dtype=np.float64).copy()
    offsets = np.broadcast_to(np.asarray(offset_mm, dtype=np.float64), (len(targets),)).copy()
    if feature_side == "none" or not np.any(offsets > 0.0) or len(targets) < 3:
        return targets
    polygon = _open_points(np.asarray(reference_polygon), True)
    area_twice = float(np.sum(
        polygon[:, 0] * np.roll(polygon[:, 1], -1)
        - np.roll(polygon[:, 0], -1) * polygon[:, 1]
    ))
    distances = np.linalg.norm(np.diff(targets[:, :2], axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(distances)]
    tangent = np.zeros((len(targets), 2), dtype=np.float64)
    for index, position in enumerate(cumulative):
        left_index = int(np.searchsorted(cumulative, position - normal_scale_mm, side="left"))
        right_index = int(np.searchsorted(cumulative, position + normal_scale_mm, side="right") - 1)
        left_index = max(0, min(left_index, index))
        right_index = min(len(targets) - 1, max(right_index, index))
        if right_index == left_index:
            left_index = max(0, index - 1); right_index = min(len(targets) - 1, index + 1)
        tangent[index] = targets[right_index, :2] - targets[left_index, :2]
    length = np.linalg.norm(tangent, axis=1)
    tangent = tangent / np.maximum(length[:, None], 1e-12)
    left = np.column_stack((-tangent[:, 1], tangent[:, 0]))
    interior = left if area_twice >= 0.0 else -left
    direction = interior if feature_side == "outer" else -interior
    targets[1:-1, :2] += offsets[1:-1, None] * direction[1:-1]
    # Protected endpoints remain exact even though the least-squares target is
    # shifted to the safe side between them.
    targets[0] = points[0]; targets[-1] = points[-1]
    return targets


def _sample_bspline(knots: np.ndarray, controls: np.ndarray, degree: int, spacing_mm: float) -> np.ndarray:
    approximate_length = max(curve_length(controls), spacing_mm)
    count = max(64, int(math.ceil(approximate_length / max(spacing_mm, 0.05))) + 1)
    values = BSpline(knots, controls[:, :2], degree, extrapolate=False)(np.linspace(0.0, 1.0, count))
    return np.column_stack((values, np.zeros(len(values))))


def _unit_vector(vector: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64)[:2]
    length = float(np.linalg.norm(value))
    if length > 1e-12:
        return value / length
    if fallback is not None:
        return _unit_vector(fallback)
    return np.asarray([1.0, 0.0], dtype=np.float64)


def _bezier_values(controls: np.ndarray, parameters: np.ndarray) -> np.ndarray:
    """Evaluate one planar cubic Bezier without an interpolation dependency."""
    u = np.asarray(parameters, dtype=np.float64)[:, None]
    one_minus = 1.0 - u
    return (
        one_minus ** 3 * controls[0, :2]
        + 3.0 * one_minus ** 2 * u * controls[1, :2]
        + 3.0 * one_minus * u ** 2 * controls[2, :2]
        + u ** 3 * controls[3, :2]
    )


def _least_squares_bezier(
    points: np.ndarray,
    parameters: np.ndarray,
    left_tangent: np.ndarray,
    right_tangent: np.ndarray,
    fairness_weight: float,
) -> np.ndarray:
    """Fit a cubic with exact endpoints and a small tangent-length fairing term."""
    xy = np.asarray(points, dtype=np.float64)[:, :2]
    u = np.asarray(parameters, dtype=np.float64)
    one_minus = 1.0 - u
    b0 = one_minus ** 3; b1 = 3.0 * u * one_minus ** 2
    b2 = 3.0 * u ** 2 * one_minus; b3 = u ** 3
    a1 = b1[:, None] * left_tangent[None, :]
    a2 = b2[:, None] * right_tangent[None, :]
    base = (b0 + b1)[:, None] * xy[0] + (b2 + b3)[:, None] * xy[-1]
    residual = xy - base
    matrix = np.asarray([
        [float(np.einsum("ij,ij->", a1, a1)), float(np.einsum("ij,ij->", a1, a2))],
        [float(np.einsum("ij,ij->", a1, a2)), float(np.einsum("ij,ij->", a2, a2))],
    ])
    rhs = np.asarray([
        float(np.einsum("ij,ij->", a1, residual)),
        float(np.einsum("ij,ij->", a2, residual)),
    ])
    chord = float(np.linalg.norm(xy[-1] - xy[0]))
    preferred = chord / 3.0
    # The regularizer discourages extreme handles (a common source of loops)
    # while the recursive error constraint remains the acceptance authority.
    fair = max(0.0, fairness_weight) * max(len(xy), 1)
    matrix += fair * np.eye(2)
    rhs += fair * preferred
    try:
        alpha_left, alpha_right = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError:
        alpha_left = alpha_right = preferred
    minimum_handle = 1e-4 * max(chord, 1.0)
    if alpha_left < minimum_handle or alpha_right < minimum_handle:
        alpha_left = alpha_right = preferred
    controls = np.zeros((4, 3), dtype=np.float64)
    controls[0, :2] = xy[0]
    controls[1, :2] = xy[0] + float(alpha_left) * left_tangent
    controls[2, :2] = xy[-1] + float(alpha_right) * right_tangent
    controls[3, :2] = xy[-1]
    return controls


def _fit_cubic_bezier_pieces(
    points: np.ndarray,
    maximum_error_mm: float,
    fairness_weight: float,
    maximum_piece_count: int,
) -> list[np.ndarray]:
    """Recursively approximate an ordered chain with exact, G1 cubic pieces.

    This is the bounded-error cubic fitting construction described by Schneider
    (Graphics Gems).  The shared split tangent is used with opposite signs by
    the two children, so numerical subdivision cannot create a CNC hard corner.
    """
    source = np.asarray(points, dtype=np.float64)
    if len(source) < 2:
        return []
    left = _unit_vector(source[1, :2] - source[0, :2])
    right = _unit_vector(source[-2, :2] - source[-1, :2])
    pieces: list[np.ndarray] = []

    def recurse(first: int, last: int, left_tangent: np.ndarray, right_tangent: np.ndarray) -> None:
        count = last - first + 1
        subset = source[first:last + 1]
        if count == 2:
            distance = float(np.linalg.norm(subset[1, :2] - subset[0, :2])) / 3.0
            controls = np.zeros((4, 3), dtype=np.float64)
            controls[0] = subset[0]; controls[3] = subset[-1]
            controls[1, :2] = subset[0, :2] + distance * left_tangent
            controls[2, :2] = subset[-1, :2] + distance * right_tangent
            pieces.append(controls)
            return
        parameters = _parameter_values(subset, "chord-length")
        controls = _least_squares_bezier(
            subset, parameters, left_tangent, right_tangent, fairness_weight,
        )
        fitted = _bezier_values(controls, parameters)
        squared = np.sum((fitted - subset[:, :2]) ** 2, axis=1)
        split_local = int(np.argmax(squared[1:-1])) + 1
        maximum_error = float(math.sqrt(squared[split_local]))
        if maximum_error <= maximum_error_mm or len(pieces) + 1 >= maximum_piece_count:
            pieces.append(controls)
            return
        split = first + split_local
        center = _unit_vector(
            source[split - 1, :2] - source[split + 1, :2],
            source[split - 1, :2] - source[split, :2],
        )
        recurse(first, split, left_tangent, center)
        recurse(split, last, -center, right_tangent)

    recurse(0, len(source) - 1, left, right)
    return pieces


def _combine_bezier_pieces(pieces: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Represent a G1 Bezier chain as one standards-compliant cubic B-spline."""
    if not pieces:
        return np.empty((0, 3)), np.empty(0)
    controls = np.vstack([pieces[0], *[piece[1:] for piece in pieces[1:]]])
    if len(pieces) == 1:
        knots = np.r_[np.zeros(4), np.ones(4)]
    else:
        interior = np.repeat(np.arange(1, len(pieces), dtype=np.float64) / len(pieces), 3)
        knots = np.r_[np.zeros(4), interior, np.ones(4)]
    return controls, knots


def _next_error_knot(
    parameters: np.ndarray,
    residuals: np.ndarray,
    knots: np.ndarray,
    degree: int,
) -> float | None:
    existing = knots[degree + 1:-(degree + 1)]
    for index in np.argsort(residuals)[::-1]:
        value = float(parameters[int(index)])
        if 1e-4 < value < 1.0 - 1e-4 and (not len(existing) or float(np.min(np.abs(existing - value))) > 1e-3):
            return value
    unique = np.unique(knots)
    if len(unique) < 2:
        return None
    gaps = np.diff(unique); index = int(np.argmax(gaps))
    return float(0.5 * (unique[index] + unique[index + 1])) if gaps[index] > 1e-4 else None


def _corridor_targets(
    points: np.ndarray,
    reference_polygon: np.ndarray,
    feature_side: str,
    offset_mm: float,
    normal_scale_mm: float,
    smoothing_scale_mm: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Map reference samples to a robust geometric safe-side offset corridor."""
    if feature_side == "none" or offset_mm <= 0.0:
        return np.asarray(points, dtype=np.float64).copy(), {
            "method": "none", "offset_mm": 0.0, "valid": True,
        }
    try:
        from shapely import make_valid
        from shapely.geometry import Point, Polygon
        from shapely.ops import nearest_points

        polygon = make_valid(Polygon(_open_points(reference_polygon, True)[:, :2]))
        if polygon.geom_type == "MultiPolygon":
            polygon = max(polygon.geoms, key=lambda item: item.area)
        corridor = polygon.buffer(-offset_mm if feature_side == "outer" else offset_mm, join_style="round")
        if corridor.is_empty:
            raise ValueError("offset corridor collapsed")
        if corridor.geom_type == "MultiPolygon":
            corridor = max(corridor.geoms, key=lambda item: item.area)
        boundary = corridor.exterior
        targets = np.asarray(points, dtype=np.float64).copy()
        for index, point in enumerate(targets):
            nearest = nearest_points(Point(float(point[0]), float(point[1])), boundary)[1]
            targets[index, :2] = (nearest.x, nearest.y)
        sample_steps = np.linalg.norm(np.diff(targets[:, :2], axis=0), axis=1)
        positive_steps = sample_steps[sample_steps > 1e-9]
        representative_spacing = float(np.median(positive_steps)) if len(positive_steps) else max(smoothing_scale_mm, 1.0)
        smoothing_radius = (
            max(1, int(round(0.5 * smoothing_scale_mm / max(representative_spacing, 1e-6))))
            if smoothing_scale_mm > 0.0 else 0
        )
        smoothed = _smooth_diagnostic(targets, False, smoothing_radius)
        for index in range(1, len(smoothed) - 1):
            query = Point(float(smoothed[index, 0]), float(smoothed[index, 1]))
            # Safe region: INSIDE the shrunk polygon for an outer deck edge,
            # OUTSIDE the grown polygon for an obstacle.  A smoothed target
            # that drifted to the forbidden side is pulled back to the
            # corridor boundary.  (Using `not covers` for both sides let
            # obstacle targets drift into the obstacle unchecked.)
            on_forbidden_side = (
                not corridor.covers(query) if feature_side == "outer" else corridor.covers(query)
            )
            if on_forbidden_side:
                nearest = nearest_points(query, boundary)[1]
                smoothed[index, :2] = (nearest.x, nearest.y)
        targets = smoothed
        targets[0] = points[0]; targets[-1] = points[-1]
        return targets, {
            "method": "shapely-polygon-buffer",
            "offset_mm": offset_mm,
            "valid": True,
            "reference_polygon_valid": bool(polygon.is_valid),
            "corridor_area_mm2": float(corridor.area),
            "target_smoothing_scale_mm": smoothing_scale_mm,
            "target_smoothing_radius_samples": smoothing_radius,
        }
    except (ImportError, ValueError, TypeError) as exc:
        fallback = _safe_side_targets(points, reference_polygon, feature_side, offset_mm, normal_scale_mm)
        return fallback, {
            "method": "local-normal-fallback",
            "offset_mm": offset_mm,
            "valid": False,
            "warning": str(exc),
        }


def _adaptive_spline_candidate(
    points: np.ndarray,
    reference_polygon: np.ndarray,
    feature_side: str,
    config: dict[str, Any],
) -> tuple[FitSpan | None, dict[str, Any]]:
    cfg = config["manufacturing_fit"]
    degree = 3
    target_tolerance = float(cfg["target_fit_deviation_mm"])
    absolute_tolerance = float(cfg["absolute_max_fit_deviation_mm"])
    initial_controls = max(degree + 1, int(cfg["initial_spline_control_points"]))
    maximum_controls = min(int(cfg["maximum_spline_control_points"]), max(degree + 1, len(points)))
    parameterization = str(cfg.get("spline_parameterization", "centripetal"))
    fairness = float(cfg["spline_fairness_weight"])
    safe_normal_scale = float(cfg.get("spline_safe_normal_scale_mm", 20.0))
    corridor_offset = float(cfg.get("spline_safe_corridor_offset_mm", 0.60))
    corridor_smoothing = float(cfg.get("spline_corridor_smoothing_mm", 30.0))
    safe_margin = float(cfg.get("spline_safe_constraint_margin_mm", 0.10))
    knots_per_iteration = max(1, int(cfg.get("spline_knots_per_iteration", 1)))
    spacing = float(cfg["validation_sample_spacing_mm"])
    complexity_ratio = float(cfg.get("absolute_tolerance_complexity_ratio", 0.60))
    attempt: dict[str, Any] = {
        "attempted": True,
        "degree": degree,
        "parameterization": parameterization,
        "fairness_weight": fairness,
        "safe_side_normal_scale_mm": safe_normal_scale,
        "safe_corridor_offset_mm": corridor_offset if feature_side != "none" else 0.0,
        "safe_corridor_smoothing_mm": corridor_smoothing,
        "safe_constraint_margin_mm": safe_margin if feature_side != "none" else 0.0,
        "constraint_method": "hard linearized safe-side inequalities plus exact polygon validation",
        "target_fit_deviation_mm": target_tolerance,
        "absolute_max_fit_deviation_mm": absolute_tolerance,
        "initial_control_point_count": initial_controls,
        "maximum_control_point_count": maximum_controls,
        "adaptive_control_growth_minimum": knots_per_iteration,
        "iterations": [],
    }
    if len(points) < degree + 1 or maximum_controls < degree + 1:
        attempt.update({"accepted": False, "rejection_reason": "fewer than four usable samples"})
        return None, attempt

    parameters = _parameter_values(points, parameterization)
    fit_targets, corridor_metrics = _corridor_targets(
        points, reference_polygon, feature_side, corridor_offset, safe_normal_scale, corridor_smoothing,
    )
    attempt["safe_corridor"] = corridor_metrics
    first_absolute: FitSpan | None = None
    first_absolute_iteration: dict[str, Any] | None = None
    target_candidate: FitSpan | None = None
    target_iteration: dict[str, Any] | None = None
    control_count = min(initial_controls, maximum_controls)
    while control_count <= maximum_controls:
        knots = _clamped_knots(control_count, degree)
        if feature_side == "none":
            controls = _fair_spline_controls(
                parameters, fit_targets, knots, degree, fairness,
            )
            constraint_metrics = {
                "solver": "unconstrained-fair-least-squares",
                "solver_success": True,
                "linear_constraint_count": 0,
            }
        else:
            controls, constraint_metrics = _constrained_fair_spline_controls(
                parameters, points, fit_targets, knots, degree, fairness, safe_margin,
            )
        sampled = _sample_bspline(knots, controls, degree, spacing)
        deviation = _symmetric_deviation(points, sampled)
        violations = _forbidden_violations(
            sampled, reference_polygon, feature_side, float(cfg["forbidden_side_epsilon_mm"]),
        )
        try:
            from shapely.geometry import LineString
            span_self_intersection = not bool(LineString(sampled[:, :2]).is_simple)
        except ImportError:
            span_self_intersection = has_self_intersection(sampled, False)
        fitted_at_reference = BSpline(knots, controls[:, :2], degree, extrapolate=False)(parameters)
        residuals = np.linalg.norm(fitted_at_reference - fit_targets[:, :2], axis=1)
        iteration = {
            "control_point_count": control_count,
            "knot_count": int(len(knots)),
            "deviation": deviation,
            "forbidden_side_violation_count": violations,
            "self_intersection": span_self_intersection,
            "maximum_target_residual_mm": float(np.max(residuals)),
            "constraint": constraint_metrics,
        }
        attempt["iterations"].append(iteration)
        span = FitSpan(
            "SPLINE", 0, len(points) - 1, sampled, controls, knots.copy(), degree,
            metrics={
                "deviation": deviation,
                "control_point_count": control_count,
                "knot_count": int(len(knots)),
                "fairness_weight": fairness,
                "parameterization": parameterization,
                "safe_corridor": corridor_metrics,
                "constraint": constraint_metrics,
                "forbidden_side_violation_count": violations,
                "self_intersection": span_self_intersection,
                "exact_endpoints": True,
            },
        )
        if (
            violations == 0 and not span_self_intersection
            and deviation["maximum_mm"] <= absolute_tolerance and first_absolute is None
        ):
            first_absolute = span; first_absolute_iteration = iteration
        if violations == 0 and not span_self_intersection and deviation["maximum_mm"] <= target_tolerance:
            target_candidate = span; target_iteration = iteration
            break
        if control_count >= maximum_controls:
            break
        # Once the target candidate would be too complex to beat the first safe
        # absolute-corridor candidate, further optimization cannot change the
        # documented selection rule.
        if (
            feature_side != "none" and first_absolute is not None
            and control_count >= math.ceil(len(first_absolute.control_points_mm) / max(complexity_ratio, 1e-6))
        ):
            break
        growth = max(knots_per_iteration, int(math.ceil(0.25 * control_count)))
        next_count = min(maximum_controls, control_count + growth)
        if next_count == control_count:
            break
        control_count = next_count

    chosen: FitSpan | None = target_candidate
    chosen_iteration = target_iteration
    used_absolute = False
    if feature_side == "none" and target_candidate is not None:
        chosen = target_candidate; chosen_iteration = target_iteration
    elif first_absolute is not None and target_candidate is not None:
        if len(first_absolute.control_points_mm) <= complexity_ratio * len(target_candidate.control_points_mm):
            chosen = first_absolute; chosen_iteration = first_absolute_iteration; used_absolute = True
    elif first_absolute is not None:
        chosen = first_absolute; chosen_iteration = first_absolute_iteration; used_absolute = True
    if chosen is None:
        last = attempt["iterations"][-1] if attempt["iterations"] else {}
        reason = "adaptive spline exhausted its documented complexity limit"
        if last.get("forbidden_side_violation_count", 0):
            reason += "; forbidden-side violations remained"
        if last.get("self_intersection", False):
            reason += "; spline self-intersection remained"
        if float(last.get("deviation", {}).get("maximum_mm", math.inf)) > absolute_tolerance:
            reason += "; absolute deviation corridor was not met"
        attempt.update({
            "accepted": False,
            "control_point_count": int(last.get("control_point_count", 0)),
            "knot_count": int(last.get("knot_count", 0)),
            "max_error_mm": last.get("deviation", {}).get("maximum_mm"),
            "rms_error_mm": last.get("deviation", {}).get("rms_mm"),
            "forbidden_side_violation_count": int(last.get("forbidden_side_violation_count", 0)),
            "rejection_reason": reason,
        })
        return None, attempt
    chosen.metrics["used_absolute_tolerance"] = used_absolute
    attempt.update({
        "accepted": True,
        "control_point_count": int(len(chosen.control_points_mm)),
        "knot_count": int(len(chosen.knots)),
        "max_error_mm": chosen.metrics["deviation"]["maximum_mm"],
        "p95_error_mm": chosen.metrics["deviation"]["p95_mm"],
        "rms_error_mm": chosen.metrics["deviation"]["rms_mm"],
        "forbidden_side_violation_count": int(chosen.metrics["forbidden_side_violation_count"]),
        "used_absolute_tolerance": used_absolute,
        "acceptance_reason": (
            "lowest-complexity safe spline inside the absolute corridor"
            if used_absolute else "lowest-complexity safe spline meeting the target corridor"
        ),
        "selected_iteration": chosen_iteration,
    })
    return chosen, attempt


def _point_in_polygon(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    query = np.asarray(points)[:, :2]; poly = _open_points(np.asarray(polygon), True)[:, :2]
    inside = np.zeros(len(query), dtype=bool)
    for start in range(0, len(query), 256):
        chunk = query[start:start + 256]; local = np.zeros(len(chunk), dtype=bool)
        x = chunk[:, 0, None]; y = chunk[:, 1, None]
        for edge_start in range(0, len(poly), 512):
            index = np.arange(edge_start, min(edge_start + 512, len(poly)))
            a = poly[index]; b = poly[(index + 1) % len(poly)]
            crossing = ((a[:, 1] > y) != (b[:, 1] > y)) & (
                x < (b[:, 0] - a[:, 0]) * (y - a[:, 1]) / (b[:, 1] - a[:, 1] + 1e-30) + a[:, 0]
            )
            local ^= np.logical_xor.reduce(crossing, axis=1)
        inside[start:start + len(chunk)] = local
    return inside


def _forbidden_mask(candidate: np.ndarray, reference: np.ndarray, feature_side: str, epsilon: float) -> np.ndarray:
    if feature_side == "none" or not len(candidate):
        return np.zeros(len(candidate), dtype=bool)
    dense_reference = _sample_polyline(reference, max(epsilon, 0.25), closed=True)
    distance = cKDTree(dense_reference[:, :2]).query(candidate[:, :2], workers=-1)[0]
    inside = _point_in_polygon(candidate, reference)
    if feature_side == "outer":
        return (~inside) & (distance > epsilon)
    return inside & (distance > epsilon)


def _forbidden_violations(candidate: np.ndarray, reference: np.ndarray, feature_side: str, epsilon: float) -> int:
    return int(np.count_nonzero(_forbidden_mask(candidate, reference, feature_side, epsilon)))


def _span_model(
    points: np.ndarray,
    closed: bool,
    reference_polygon: np.ndarray,
    feature_side: str,
    config: dict[str, Any],
) -> FitSpan:
    cfg = config["manufacturing_fit"]
    target = float(cfg["target_fit_deviation_mm"])
    absolute = float(cfg["absolute_max_fit_deviation_mm"])
    spacing = float(cfg["validation_sample_spacing_mm"])
    epsilon = float(cfg["forbidden_side_epsilon_mm"])
    attempts: dict[str, Any] = {}

    def evaluate(kind: str, candidate: FitSpan | None, diagnostic: dict[str, Any] | None = None) -> FitSpan | None:
        record: dict[str, Any] = {"attempted": True, **(diagnostic or {})}
        if candidate is None:
            record.setdefault("accepted", False)
            record.setdefault("rejection_reason", f"{kind.lower()} construction was geometrically degenerate")
            attempts[kind] = record
            return None
        deviation = _symmetric_deviation(points, candidate.sampled_points_mm)
        violations = _forbidden_violations(candidate.sampled_points_mm, reference_polygon, feature_side, epsilon)
        candidate.metrics.update({"deviation": deviation, "forbidden_side_violation_count": violations})
        record.update({
            "max_error_mm": deviation["maximum_mm"],
            "p95_error_mm": deviation["p95_mm"],
            "rms_error_mm": deviation["rms_mm"],
            "forbidden_side_violation_count": violations,
        })
        model_absolute = min(
            absolute,
            float(cfg.get("analytic_primitive_absolute_max_deviation_mm", absolute)),
        ) if kind in {"LINE", "ARC", "CIRCLE"} else absolute
        if deviation["maximum_mm"] > model_absolute:
            record.update({"accepted": False, "rejection_reason": f"maximum error exceeds absolute limit {model_absolute:.3f} mm"})
        elif violations:
            record.update({"accepted": False, "rejection_reason": "hard forbidden-side rule was violated"})
        else:
            used_absolute = bool(deviation["maximum_mm"] > target)
            record.update({
                "accepted": True,
                "used_absolute_tolerance": used_absolute,
                "acceptance_reason": (
                    f"minimal native {kind} is inside absolute limit {model_absolute:.3f} mm"
                    if used_absolute else f"meets target {target:.3f} mm and safety corridor"
                ),
            })
            candidate.metrics["used_absolute_tolerance"] = used_absolute
        attempts[kind] = record
        if record["accepted"]:
            candidate.metrics["attempts"] = attempts
            return candidate
        return None

    if not closed:
        accepted = evaluate("LINE", _line_candidate(points, spacing))
        if accepted is not None:
            attempts["ARC"] = {"attempted": False, "rejection_reason": "higher-priority LINE was accepted"}
            attempts["SPLINE"] = {"attempted": False, "rejection_reason": "higher-priority LINE was accepted"}
            attempts["POLYLINE"] = {"required": False, "reason": "native LINE accepted"}
            accepted.metrics["attempts"] = attempts
            return accepted
        arc, arc_diagnostic = _arc_candidate(points, float(cfg["maximum_arc_sweep_deg"]), spacing)
        accepted = evaluate("ARC", arc, arc_diagnostic)
        if accepted is not None:
            attempts["SPLINE"] = {"attempted": False, "rejection_reason": "higher-priority ARC was accepted"}
            attempts["POLYLINE"] = {"required": False, "reason": "native ARC accepted"}
            accepted.metrics["attempts"] = attempts
            return accepted
    else:
        attempts["LINE"] = {"attempted": False, "rejection_reason": "logical span is a complete closed curve"}
        attempts["ARC"] = {"attempted": False, "rejection_reason": "logical span is a complete closed curve"}
        fitted = _fit_circle(_open_points(points, True))
        if fitted is not None:
            center, radius, _angles, error = fitted
            count = max(32, int(math.ceil(2 * math.pi * radius / max(spacing, 0.05))))
            theta = np.linspace(0.0, 2 * math.pi, count + 1)
            sampled = np.column_stack((center[0] + radius * np.cos(theta), center[1] + radius * np.sin(theta), np.zeros(len(theta))))
            circle = FitSpan(
                "CIRCLE", 0, len(points) - 1, sampled,
                center_mm=np.r_[center, 0.0], radius_mm=radius,
                metrics={"radial_fit_maximum_mm": error},
            )
            accepted = evaluate("CIRCLE", circle)
            if accepted is not None:
                attempts["SPLINE"] = {"attempted": False, "rejection_reason": "higher-priority CIRCLE was accepted"}
                attempts["POLYLINE"] = {"required": False, "reason": "native CIRCLE accepted"}
                accepted.metrics["attempts"] = attempts
                return accepted
        else:
            attempts["CIRCLE"] = {"attempted": True, "accepted": False, "rejection_reason": "circle fit was degenerate"}

    spline, spline_attempt = _adaptive_spline_candidate(points, reference_polygon, feature_side, config)
    attempts["SPLINE"] = spline_attempt
    if spline is not None:
        attempts["POLYLINE"] = {"required": False, "reason": "native cubic B-spline accepted"}
        spline.metrics["attempts"] = attempts
        return spline

    simplified = _rdp(points, target * 0.45)
    if closed and np.linalg.norm(simplified[0, :2] - simplified[-1, :2]) > 1e-9:
        simplified = np.vstack((simplified, simplified[:1]))
    sampled = _sample_polyline(simplified, spacing, closed)
    deviation = _symmetric_deviation(points, sampled)
    violations = _forbidden_violations(sampled, reference_polygon, feature_side, epsilon)
    fallback_reasons = [
        str(attempts.get(model, {}).get("rejection_reason", f"{model.lower()} was not accepted"))
        for model in ("LINE", "ARC", "SPLINE")
    ]
    if violations > 0 or deviation["maximum_mm"] > absolute:
        raw_controls = points.copy()
        raw_sampled = _sample_polyline(raw_controls, spacing, closed)
        raw_violations = _forbidden_violations(raw_sampled, reference_polygon, feature_side, epsilon)
        attempts["POLYLINE"] = {
            "required": True,
            "reason": "; ".join(fallback_reasons),
            "simplified_rejected_because": (
                f"forbidden violations={violations}, max error={deviation['maximum_mm']:.4f} mm"
            ),
            "raw_reference_fallback": True,
        }
        span = FitSpan(
            "POLYLINE", 0, len(points) - 1, raw_sampled, raw_controls, degree=1,
            metrics={
                "deviation": _symmetric_deviation(points, raw_sampled),
                "forbidden_side_violation_count": raw_violations,
                "fallback": True,
                "raw_reference_fallback": True,
                "rejected_simplified_violation_count": violations,
                "needs_review": True,
            },
        )
        span.metrics["attempts"] = attempts
        return span
    attempts["POLYLINE"] = {
        "required": True,
        "reason": "; ".join(fallback_reasons),
        "raw_reference_fallback": False,
    }
    span = FitSpan(
        "POLYLINE", 0, len(points) - 1, sampled, simplified, degree=1,
        metrics={"deviation": deviation, "forbidden_side_violation_count": violations,
                 "fallback": True, "needs_review": False},
    )
    span.metrics["attempts"] = attempts
    return span


def _feature_profile(curve: FlattenedCurve) -> tuple[str, str]:
    layer = curve.source.raw_curve.layer.upper()
    if "OBSTACLE" in layer:
        return "small_obstacle", "obstacle"
    if "DECK_PRIMARY" in layer or "DECK_SECONDARY" in layer:
        return "deck", "outer"
    return "deck", "none"


def fit_manufacturing_curve(curve: FlattenedCurve, index: int, config: dict[str, Any]) -> ManufacturingCurve:
    raw, numerical_samples_removed = _collapse_consecutive_samples(
        curve.points_mm, curve.closed,
        float(config["manufacturing_fit"].get("minimum_numerical_sample_separation_mm", 0.10)),
    )
    profile, side = _feature_profile(curve)
    diagnostic, anchors, corner_metrics = detect_manufacturing_corners(raw, curve.closed, profile, config)
    warnings: list[str] = []
    if corner_metrics["over_anchored"]:
        warnings.append("OVER_ANCHORED_CURVE: protected-corner density exceeds the physical profile limit.")

    spans_with_ranges: list[tuple[np.ndarray, int, int, bool]] = []
    if curve.closed and len(anchors) >= 2:
        for offset, start in enumerate(anchors):
            end = anchors[(offset + 1) % len(anchors)]
            if end > start:
                points = diagnostic[start:end + 1]
            else:
                points = np.vstack((diagnostic[start:], diagnostic[:end + 1]))
            spans_with_ranges.append((points, start, end, False))
    elif curve.closed:
        chain = np.vstack((diagnostic, diagnostic[:1]))
        spans_with_ranges.append((chain, 0, 0, True))
    else:
        cuts = sorted(set([0, *anchors, len(diagnostic) - 1]))
        spans_with_ranges.extend((diagnostic[start:end + 1], start, end, False) for start, end in zip(cuts[:-1], cuts[1:]))

    spans: list[FitSpan] = []
    for points, start, end, span_closed in spans_with_ranges:
        span = _span_model(points, span_closed, diagnostic, side, config)
        span.source_start_index = start; span.source_end_index = end
        spans.append(span)
    chains: list[np.ndarray] = []
    for span in spans:
        chains.append(span.sampled_points_mm if not chains else span.sampled_points_mm[1:])
    cam = np.vstack(chains) if chains else np.empty((0, 3))
    if curve.closed and len(cam) and np.linalg.norm(cam[0, :2] - cam[-1, :2]) > 1e-8:
        cam = np.vstack((cam, cam[:1]))
    deviation = _symmetric_deviation(np.vstack((diagnostic, diagnostic[:1])) if curve.closed else diagnostic, cam) if len(cam) else {
        "rms_mm": math.inf, "p95_mm": math.inf, "maximum_mm": math.inf,
    }
    forbidden = sum(int(span.metrics.get("forbidden_side_violation_count", 0)) for span in spans)
    self_intersections = bool(len(cam) and has_self_intersection(cam, curve.closed))
    minimum_span = min((curve_length(span.sampled_points_mm) for span in spans), default=0.0)
    short_limit = float(config["manufacturing_fit"]["minimum_span_length_mm"])
    fallback_count = sum(span.kind == "POLYLINE" for span in spans)
    spline_count = sum(span.kind == "SPLINE" for span in spans)
    span_lengths = [curve_length(span.sampled_points_mm) for span in spans]
    total_span_length = float(sum(span_lengths))
    fallback_length = float(sum(length for length, span in zip(span_lengths, spans) if span.kind == "POLYLINE"))
    join_gaps: list[float] = []
    join_count = len(spans) if curve.closed else max(0, len(spans) - 1)
    for span_index in range(join_count):
        current = spans[span_index]
        following = spans[(span_index + 1) % len(spans)]
        join_gaps.append(float(np.linalg.norm(current.sampled_points_mm[-1, :2] - following.sampled_points_mm[0, :2])))
    maximum_join_gap = max(join_gaps, default=0.0)
    join_tolerance = float(config["manufacturing_fit"]["maximum_join_gap_mm"])
    target_tolerance = float(config["manufacturing_fit"]["target_fit_deviation_mm"])
    absolute_tolerance = float(config["manufacturing_fit"]["absolute_max_fit_deviation_mm"])
    used_absolute = any(bool(span.metrics.get("used_absolute_tolerance", False)) for span in spans)
    primary_outline = "DECK_PRIMARY" in curve.source.raw_curve.layer.upper()
    organic_spline_regression = bool(primary_outline and spline_count == 0 and fallback_count > 0)
    if organic_spline_regression:
        warnings.append("ORGANIC_SPLINE_REGRESSION: primary outline contains fallback geometry but no native spline.")
    invalid = (
        forbidden > 0 or self_intersections or not len(cam)
        or maximum_join_gap > join_tolerance
        or deviation["maximum_mm"] > absolute_tolerance
    )
    review = (
        invalid or corner_metrics["over_anchored"] or fallback_count > 0
        or deviation["maximum_mm"] > target_tolerance or used_absolute
        or organic_spline_regression
    )
    status = "INVALID" if invalid else ("NEEDS_REVIEW" if review else "GOOD")
    metrics = {
        "corner_detection": corner_metrics,
        "source_point_count": int(len(diagnostic)),
        "numerical_samples_removed": numerical_samples_removed,
        "cam_sample_point_count": int(len(cam)),
        "protected_corner_count": len(anchors),
        "span_count": len(spans),
        "logical_physical_span_count": len(spans),
        "internal_spline_piece_count": spline_count,
        "span_type_counts": {kind: sum(span.kind == kind for span in spans) for kind in ("LINE", "ARC", "CIRCLE", "SPLINE", "POLYLINE")},
        "control_point_count": int(sum(len(span.control_points_mm) for span in spans)),
        "nurbs_control_point_count": int(sum(len(span.control_points_mm) for span in spans if span.kind == "SPLINE")),
        "polyline_fallback_count": fallback_count,
        "polyline_fallback_fraction": fallback_length / max(total_span_length, 1e-12),
        "polyline_fallback_span_fraction": fallback_count / max(len(spans), 1),
        "organic_spline_regression": organic_spline_regression,
        "deviation": deviation,
        "forbidden_side_violation_count": forbidden,
        "self_intersection": self_intersections,
        "join_gaps_mm": join_gaps,
        "maximum_join_gap_mm": maximum_join_gap,
        "join_gap_tolerance_mm": join_tolerance,
        "logical_path_closed": bool(curve.closed and maximum_join_gap <= join_tolerance),
        "used_absolute_tolerance": used_absolute,
        "target_fit_deviation_mm": target_tolerance,
        "absolute_max_fit_deviation_mm": absolute_tolerance,
        "minimum_span_length_mm": minimum_span,
        "short_span_count": sum(curve_length(span.sampled_points_mm) < short_limit for span in spans),
        "raw_length_mm": curve_length(np.vstack((diagnostic, diagnostic[:1])) if curve.closed else diagnostic),
        "cam_length_mm": curve_length(cam),
        "length_change_percent": 100.0 * (
            curve_length(cam) - curve_length(np.vstack((diagnostic, diagnostic[:1])) if curve.closed else diagnostic)
        ) / max(curve_length(np.vstack((diagnostic, diagnostic[:1])) if curve.closed else diagnostic), 1e-12),
        "primitive_reduction": max(0, int(len(diagnostic) - len(spans))),
    }
    return ManufacturingCurve(
        curve, f"cam-{index:04d}-{curve.flattened_curve_id}", curve.layer, curve.closed,
        anchors, diagnostic[anchors] if anchors else np.empty((0, 3)), spans, cam, status, metrics, warnings,
    )


def fit_manufacturing_curves(curves: list[FlattenedCurve], config: dict[str, Any]) -> list[ManufacturingCurve]:
    return [fit_manufacturing_curve(curve, index, config) for index, curve in enumerate(curves, 1) if curve.status != "INVALID"]
