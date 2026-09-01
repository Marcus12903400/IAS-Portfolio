"""Phase 2 fitter: corners first, then lines, then tangent arcs between them.

Built from the user's own Key West drawing (docs/calibration/KEYWEST_1_FINDINGS.md):

0. CORNERS FIRST.  A sharp corner is a direction change of at least
   `corner_angle_deg` concentrated in a short length of the contour (a fillet
   spreads its turn over its arc length; a corner does not).  Corners are
   explicit anchors and are reported as intentional corners.
1. LINES.  Between corners, every maximal straight run that stays inside the
   band (default 3.0 mm) AND is genuinely straight -- a run that a shallow
   circle fits much better than a line is a curved regime, not a line -- is
   one long LINE through the middle of the band.
2. ARCS.  Every gap between consecutive anchors (line or corner) is bridged
   tangentially with ONE arc if it fits the band, else TWO (biarc), else
   THREE.  Never more than three.  Where a line meets a corner on its own
   extension, the line is simply extended to the corner.  If three arcs
   cannot stay in the band, a straight-ish piece of the gap is promoted to a
   LINE and the halves are bridged separately; failing that, the best
   solution is kept and flagged -- never silently.
3. A loop with neither corners nor lines becomes a circle if one fits.

Joins are exactly tangent by construction (shared point, shared tangent), so
the 0.10 deg validation is met without post-repair.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

from .calibration import Loop, Segment

TWO_PI = 2.0 * math.pi


@dataclass
class FitSettings:
    band_mm: float = 3.5
    band_quantile: float = 0.95      # this share of the points must be inside band_mm ...
    spike_mm: float = 6.5            # ... and no point beyond this (isolated raw spikes are tolerated)
    resample_mm: float = 5.0
    min_line_length_mm: float = 120.0
    small_loop_line_fraction: float = 0.12
    corner_angle_deg: float = 25.0
    corner_window_short_mm: float = 15.0
    corner_window_long_mm: float = 40.0
    corner_concentration: float = 0.6
    corner_max_gap_mm: float = 60.0
    corner_snap_mm: float = 12.0
    max_arcs_per_connection: int = 3
    min_arc_radius_mm: float = 8.0
    max_line_curvature_radius_mm: float = 30000.0
    trim_candidates_mm: tuple[float, ...] = (0.0, 15.0, 30.0, 60.0, 100.0, 150.0, 250.0)
    intermediate_line_min_mm: float = 40.0
    max_line_trim_fraction: float = 0.45
    max_line_extend_mm: float = 400.0
    arc_anchor_min_mm: float = 120.0   # an arc piece at least this long is drawn as its own primitive
    join_tangent_max_deg: float = 0.10  # joins kinked more than this become review corners
    # A cut panel that is slightly small still fits; slightly big does not.
    edge_bias_mm: float = 1.5          # whole contour shifted this far to the safe side before fitting
    inward_extra_mm: float = 3.0       # extra allowance for a primitive that cuts INTO the panel
    safe_left: float = 0.0             # runtime: +1 = safe side is left of travel, -1 = right, 0 = symmetric

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "FitSettings":
        s = dict(config.get("autofit", {}))
        s.pop("fit_reference", None)
        if "trim_candidates_mm" in s:
            s["trim_candidates_mm"] = tuple(float(v) for v in s["trim_candidates_mm"])
        known = {k: v for k, v in s.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------------------------------------------------------------------------
# geometry helpers

def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.array([1.0, 0.0])


def _left(v: np.ndarray) -> np.ndarray:
    return np.array([-v[1], v[0]])


def _cross2(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(np.dot(_unit(a), _unit(b)), -1.0, 1.0))))


def resample_closed(points: np.ndarray, spacing: float) -> np.ndarray:
    pts = np.asarray(points, dtype=float)[:, :2]
    if len(pts) and np.linalg.norm(pts[0] - pts[-1]) < 1e-9:
        pts = pts[:-1]
    ring = np.vstack([pts, pts[:1]])
    seg = np.linalg.norm(np.diff(ring, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    count = max(8, int(round(total / spacing)))
    targets = np.linspace(0.0, total, count, endpoint=False)
    return np.column_stack([np.interp(targets, cum, ring[:, 0]), np.interp(targets, cum, ring[:, 1])])


def arc_tangent_through(start: np.ndarray, tangent: np.ndarray, end: np.ndarray, source: str = "") -> Segment:
    """The unique circle tangent to `tangent` at `start` through `end` (a LINE when collinear)."""

    t = _unit(np.asarray(tangent, float)); n = _left(t)
    start = np.asarray(start, float); end = np.asarray(end, float)
    w = end - start
    denominator = 2.0 * float(np.dot(w, n))
    if np.linalg.norm(w) < 1e-9 or abs(denominator) < 1e-9 * max(float(np.linalg.norm(w)), 1.0):
        return Segment("LINE", start.copy(), end.copy(), source=source)
    r_signed = float(np.dot(w, w)) / denominator
    center = start + n * r_signed
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = (a1 - a0) % TWO_PI if r_signed > 0 else -((a0 - a1) % TWO_PI)
    return Segment("ARC", start.copy(), end.copy(), center, abs(r_signed), float(sweep), source)


def arc_through_three(start: np.ndarray, mid: np.ndarray, end: np.ndarray, source: str = "") -> Segment:
    from .calibration import arc_from_three_points
    seg = arc_from_three_points(np.asarray(start, float), np.asarray(mid, float), np.asarray(end, float), source)
    return seg if seg is not None else Segment("LINE", np.asarray(start, float).copy(), np.asarray(end, float).copy(), source=source)


def _kasa_circle(pts: np.ndarray) -> tuple[np.ndarray, float] | None:
    x, y = pts[:, 0], pts[:, 1]
    a = np.column_stack([x, y, np.ones(len(pts))])
    try:
        sol, *_ = np.linalg.lstsq(a, x * x + y * y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r2 = sol[2] + cx * cx + cy * cy
    return (np.array([cx, cy]), math.sqrt(r2)) if r2 > 0 and np.isfinite(r2) else None


def _circumcircle(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> tuple[np.ndarray, float] | None:
    ax, ay = p1; bx, by = p2; cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    center = np.array([ux, uy])
    return center, float(np.linalg.norm(p1 - center))


def _parabola_circle(pts: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Circle from the sagitta parabola in the chord frame -- well conditioned
    for shallow arcs (metres of radius over a short chord) where the
    algebraic fit is not."""

    centroid, direction, _ = _tls_line(pts)
    rel = pts - centroid
    along = rel @ direction
    perp = rel[:, 0] * direction[1] - rel[:, 1] * direction[0]
    try:
        co = np.polyfit(along, perp, 2)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if abs(co[0]) < 1e-15:
        return None
    normal = _left(direction)
    samples = []
    for s in (float(along.min()), float(along.mean()), float(along.max())):
        # perp is measured as cross(direction, rel) = -dot(rel, normal)
        samples.append(centroid + direction * s - normal * float(np.polyval(co, s)))
    return _circumcircle(*samples)


def _refine_circle(pts: np.ndarray, center: np.ndarray, radius: float, iterations: int = 4) -> tuple[np.ndarray, float]:
    """A few Gauss-Newton steps on the geometric distance."""

    c = center.astype(float).copy(); r = float(radius)
    for _ in range(iterations):
        diff = pts - c
        dist = np.linalg.norm(diff, axis=1)
        if np.any(dist < 1e-12):
            break
        residual = dist - r
        jac = np.column_stack([-diff[:, 0] / dist, -diff[:, 1] / dist, -np.ones(len(pts))])
        try:
            step, *_ = np.linalg.lstsq(jac, -residual, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        c = c + step[:2]; r = r + float(step[2])
        if r <= 0:
            return center, radius
    return c, r


def circle_fit(points: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Least-squares circle: best of an algebraic and a sagitta-parabola start, refined geometrically."""

    pts = np.asarray(points, float)
    if len(pts) < 3:
        return None
    best: tuple[np.ndarray, float, float] | None = None
    for start in (_kasa_circle(pts), _parabola_circle(pts)):
        if start is None:
            continue
        center, radius = _refine_circle(pts, *start)
        if not (np.all(np.isfinite(center)) and np.isfinite(radius)) or radius <= 0:
            continue
        worst = float(np.abs(np.linalg.norm(pts - center, axis=1) - radius).max())
        if best is None or worst < best[2]:
            best = (center, radius, worst)
    return (best[0], best[1]) if best is not None else None


def _samples(segments: list[Segment], spacing: float = 2.0) -> np.ndarray:
    chunks = [s.sample(spacing) for s in segments if s.length_mm > 1e-9]
    return np.vstack(chunks) if chunks else np.empty((0, 2))


def deviation_profile(candidate: list[Segment], reference: np.ndarray) -> np.ndarray:
    """Distances reference->candidate and candidate->reference, concatenated."""

    cand = _samples(candidate); ref = np.asarray(reference, float)
    if len(cand) < 2 or len(ref) < 1:
        return np.array([float("inf")])
    if len(ref) == 1:
        d = np.linalg.norm(cand - ref[0], axis=1)
        return np.concatenate([[float(d.min())], d])
    d1 = shapely.distance(LineString(cand), shapely.points(ref))
    d2 = shapely.distance(LineString(ref), shapely.points(cand))
    return np.concatenate([np.asarray(d1, float), np.asarray(d2, float)])


def symmetric_deviation(candidate: list[Segment], reference: np.ndarray) -> float:
    return float(deviation_profile(candidate, reference).max())


def band_deviation(deviations: np.ndarray, settings: FitSettings) -> float:
    """What a band test sees: the band_quantile of the deviations, or the worst
    point scaled so that spike_mm maps onto band_mm -- whichever is larger.
    <= band_mm therefore means 'inside the band, isolated spikes tolerated',
    which is how the hand drawing treated the raw contour."""

    dev = np.asarray(deviations, float)
    if dev.size == 0:
        return 0.0
    if not np.all(np.isfinite(dev)):
        return float("inf")
    quantile = float(np.quantile(dev, settings.band_quantile))
    return max(quantile, float(dev.max()) * settings.band_mm / settings.spike_mm)


def _polyline_length(points: np.ndarray) -> float:
    return float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1))) if len(points) > 1 else 0.0


# ---------------------------------------------------------------------------
# safe-side (asymmetric) deviations: a primitive may cut deeper INTO the panel
# (band + inward_extra) than it may stick out toward the wall (band).  The
# signed deviation is mapped to a symmetric equivalent so every existing
# band test keeps working: outward counts in full, inward is scaled so that
# band + inward_extra lands exactly on the band.

def _effective_deviation(outward: np.ndarray, settings: FitSettings) -> np.ndarray:
    out = np.asarray(outward, float)
    if settings.safe_left == 0.0 or settings.inward_extra_mm <= 0.0:
        return np.abs(out)
    shrink = settings.band_mm / (settings.band_mm + settings.inward_extra_mm)
    return np.where(out >= 0.0, out, -out * shrink)


def _line_effective(pts: np.ndarray, point_on_line: np.ndarray, direction: np.ndarray, settings: FitSettings) -> np.ndarray:
    """Effective deviations of contour points (in travel order) from a fitted
    line.  A contour point on the SAFE side of the line means the line pokes
    outward there (strict); a point on the unsafe side means the line cuts
    inward (generous)."""

    rel = pts - point_on_line
    cross = direction[0] * rel[:, 1] - direction[1] * rel[:, 0]   # > 0: point left of the line
    return _effective_deviation(settings.safe_left * cross if settings.safe_left != 0.0 else cross, settings)


def _arc_effective(pts: np.ndarray, center: np.ndarray, radius: float, settings: FitSettings) -> np.ndarray:
    """Effective deviations of contour points (in travel order) from a fitted
    circle, with the same safe-side asymmetry as `_line_effective`."""

    residual = np.linalg.norm(pts - center, axis=1) - radius
    if settings.safe_left == 0.0 or len(pts) < 3:
        return _effective_deviation(residual, settings)
    k = len(pts) // 2
    travel = pts[min(len(pts) - 1, k + 1)] - pts[max(0, k - 1)]
    center_left = _cross2(travel, center - pts[k]) > 0.0
    sign = -1.0 if center_left == (settings.safe_left > 0.0) else 1.0
    return _effective_deviation(sign * residual, settings)


def _polyline_tangents(pts: np.ndarray) -> np.ndarray:
    if len(pts) < 2:
        return np.tile(np.array([1.0, 0.0]), (len(pts), 1))
    tangent = np.vstack([pts[1:] - pts[:-1], pts[-1:] - pts[-2:-1]])
    return tangent / np.maximum(np.linalg.norm(tangent, axis=1), 1e-12)[:, None]


def deviation_profile_effective(candidate: list[Segment], reference: np.ndarray, settings: FitSettings) -> np.ndarray:
    """`deviation_profile` with the safe-side asymmetry: a candidate that runs
    on the panel side of the reference (cutting the panel slightly smaller)
    is judged generously; sticking out toward the wall is judged in full."""

    if settings.safe_left == 0.0:
        return deviation_profile(candidate, reference)
    cand = _samples(candidate)
    ref = np.asarray(reference, float)
    if len(cand) < 2 or len(ref) < 2:
        return deviation_profile(candidate, reference)
    cand_tan = _polyline_tangents(cand)
    ref_tan = _polyline_tangents(ref)

    def nearest(sources: np.ndarray, queries: np.ndarray) -> np.ndarray:
        # chunked brute force keeps memory bounded without a scipy dependency
        out = np.empty(len(queries), dtype=np.int64)
        for i in range(0, len(queries), 512):
            block = queries[i:i + 512]
            d2 = ((block[:, None, :] - sources[None, :, :]) ** 2).sum(axis=2)
            out[i:i + 512] = np.argmin(d2, axis=1)
        return out

    # exact distances (as the symmetric profile uses); the nearest sample only
    # supplies WHICH SIDE the point sits on
    dist1 = np.asarray(shapely.distance(LineString(cand), shapely.points(ref)), float)
    dist2 = np.asarray(shapely.distance(LineString(ref), shapely.points(cand)), float)
    # reference -> candidate: a ref point on the SAFE side of the candidate
    # means the candidate pokes toward the wall there (strict).
    j = nearest(cand, ref)
    delta = ref - cand[j]
    side1 = cand_tan[j, 0] * delta[:, 1] - cand_tan[j, 1] * delta[:, 0]
    out1 = np.where(settings.safe_left * side1 >= 0.0, dist1, -dist1)
    # candidate -> reference: a candidate point on the SAFE side of the
    # reference is cutting into the panel (generous).
    k = nearest(ref, cand)
    delta2 = cand - ref[k]
    side2 = ref_tan[k, 0] * delta2[:, 1] - ref_tan[k, 1] * delta2[:, 0]
    out2 = np.where(settings.safe_left * side2 >= 0.0, -dist2, dist2)
    return _effective_deviation(np.concatenate([out1, out2]), settings)


def _loop_ccw(pts: np.ndarray) -> bool:
    x = pts[:, 0]; y = pts[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0.0


def _offset_closed(points: np.ndarray, left_shift: float) -> np.ndarray:
    """Every resampled point moved `left_shift` along the local left normal of
    a lightly smoothed tangent -- a small, smooth parallel shift (no polygon
    buffering, whose mitred joins spray artificial corners into a noisy
    contour).  Positive = left of travel."""

    if abs(left_shift) < 1e-12 or len(points) < 4:
        return points
    smooth = _gaussian_smooth_closed(points, 2.0)
    tangent = np.roll(smooth, -1, axis=0) - np.roll(smooth, 1, axis=0)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1), 1e-12)[:, None]
    normals = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    return points + left_shift * normals


# ---------------------------------------------------------------------------
# corners first

def detect_corners(points: np.ndarray, settings: FitSettings) -> list[int]:
    return [corner[0] for corner in detect_corner_points(points, settings)]


def _gaussian_smooth_closed(points: np.ndarray, sigma_samples: float) -> np.ndarray:
    radius = max(1, int(math.ceil(3.0 * sigma_samples)))
    offsets = np.arange(-radius, radius + 1)
    weights = np.exp(-0.5 * (offsets / sigma_samples) ** 2)
    weights /= weights.sum()
    out = np.zeros_like(points)
    for offset, weight in zip(offsets, weights):
        out += weight * np.roll(points, -int(offset), axis=0)
    return out


def corner_turning(points: np.ndarray, settings: FitSettings) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-sample turning angles (deg) on a lightly smoothed copy of the contour:

    short  -- between the tangents +-corner_window_short_mm/2 away: a corner
              turns by (almost) its full angle here, an arc only by 2k/R;
    long   -- twice the larger one-sided turning from the tangent at the
              sample to the tangent corner_window_long_mm away.  At a true
              corner the local tangent is the bisector, so this recovers the
              full angle even when the other side belongs to a small feature
              (a 25 mm jog, a 63 mm foot side); inside a round it keeps
              growing with the window and the short/long ratio collapses;
    sym    -- the plain symmetric long-window turning, for diagnostics.
    """

    smooth = _gaussian_smooth_closed(points, 1.0)
    spacing = settings.resample_mm
    tangent = np.roll(smooth, -1, axis=0) - np.roll(smooth, 1, axis=0)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1), 1e-12)[:, None]

    def angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", a, b), -1.0, 1.0)))

    k_short = max(1, int(round(0.5 * settings.corner_window_short_mm / spacing)))
    k_long = max(k_short + 2, int(round(settings.corner_window_long_mm / spacing)))
    short = angle(np.roll(tangent, k_short, axis=0), np.roll(tangent, -k_short, axis=0))
    one_sided = np.maximum(angle(np.roll(tangent, k_long, axis=0), tangent), angle(tangent, np.roll(tangent, -k_long, axis=0)))
    long_ = np.minimum(2.0 * one_sided, 180.0)
    sym = angle(np.roll(tangent, k_long, axis=0), np.roll(tangent, -k_long, axis=0))
    return short, long_, sym


def detect_corner_points(points: np.ndarray, settings: FitSettings) -> list[tuple[int, np.ndarray, np.ndarray, np.ndarray]]:
    """Sharp corners on a closed resampled contour:
    (index, apex, incoming flank direction, outgoing flank direction).

    Directions are measured on a Gaussian-smoothed copy (sigma = 2 samples)
    so the polyline kinks of a conditioned scan curve do not read as corners.
    A candidate turns by at least `corner_angle_deg` at both the short and
    the long window and the turn is *persistent*: short/long turning ratio
    >= corner_concentration (a corner turns fully at every scale, an arc of
    radius R turns only scale/R at the short one).  The corner point is the
    intersection of total-least-squares lines through the two flanks when
    the contour reaches it within the band (a fillet cuts that corner by
    r(sec(theta/2) - 1) and is rejected).
    """

    n = len(points)
    if n < 8:
        return []
    spacing = settings.resample_mm
    short, long_, _sym = corner_turning(points, settings)
    # persistence: a corner turns by its full angle already across the short
    # window; an arc of radius R turns by only (window / R), so the
    # short/long ratio separates a corner (~1) from a fillet (~k_short/k_long)
    ratio = short / np.maximum(long_, 1e-9)
    # a turn of 60 deg or more inside +-10 mm is a corner (or a fillet under
    # ~R20, which the approach test sorts out) whatever the long window says:
    # next to a U-turn the long window saturates and the ratio is meaningless
    sharp = short >= 60.0
    persistent = np.where(((ratio >= settings.corner_concentration) | sharp) & (np.minimum(short, long_) >= settings.corner_angle_deg),
                          np.minimum(short, long_), 0.0)
    candidates = np.flatnonzero(persistent > 0)
    if not len(candidates):
        return []
    kl = max(3, int(round(settings.corner_window_long_mm / spacing)))
    dead = max(1, int(round(settings.corner_window_short_mm / spacing)) // 2)
    doubled = np.vstack([points, points, points])  # cyclic windows without modulo bookkeeping
    corners: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    taken = np.zeros(n, dtype=bool)
    for i in sorted(candidates, key=lambda k: -persistent[k]):
        if taken[i]:
            continue
        if persistent[i] < 2.0 * settings.corner_angle_deg:
            # a weak corner needs at least one genuinely straight flank; a
            # kink inside a round is the arc fitter's business
            long_before = doubled[n + i - 2 * kl: n + i - dead + 1]
            long_after = doubled[n + i + dead: n + i + 2 * kl + 1]
            if (band_deviation(_tls_line(long_before)[2], settings) > 0.5 * settings.band_mm
                    and band_deviation(_tls_line(long_after)[2], settings) > 0.5 * settings.band_mm):
                continue
        # a fillet of radius r cuts the corner by r(sec(theta/2) - 1); a real
        # corner reaches (within noise) the intersection of its flanks.  The
        # flanks are tried at two lengths: the inner corner of a 25 mm step
        # has a flank shorter than the long window.
        found: tuple[int, np.ndarray, np.ndarray, np.ndarray] | None = None
        for reach in (kl, max(dead + 2, kl // 2)):
            flank_before = doubled[n + i - reach: n + i - dead + 1]
            flank_after = doubled[n + i + dead: n + i + reach + 1]
            if len(flank_before) < 3 or len(flank_after) < 3:
                continue
            c_b, d_b, r_b = _tls_line(flank_before)
            c_a, d_a, r_a = _tls_line(flank_after)
            if band_deviation(r_b, settings) > settings.band_mm or band_deviation(r_a, settings) > settings.band_mm:
                continue
            x = _line_intersection(c_b, d_b, c_a, d_a)
            if x is None:
                continue
            window = doubled[n + i - reach: n + i + reach + 1]
            distances = np.linalg.norm(window - x, axis=1)
            # a small fillet still counts as a corner: up to ~R12 at a gentle
            # corner (cut 1.5 x band), up to ~R15 at a sharp one (2.5 x band)
            allowance = (2.5 if short[i] >= 60.0 else 1.5) * settings.band_mm
            if float(distances.min()) > allowance:
                continue
            found = ((int(np.argmin(distances)) + i - reach) % n, x.copy(), d_b.copy(), d_a.copy())
            break
        if found is None:
            continue
        index, x, d_in, d_out = found
        if index not in corners:
            corners[index] = (x, d_in, d_out)
        # suppress only the short window around it: the two corners of a
        # 25 mm jog or a 63 mm foot side must both survive
        suppress = max(2, int(round(settings.corner_window_short_mm / spacing)))
        for d in range(-suppress, suppress + 1):
            taken[(index + d) % n] = True
            taken[(i + d) % n] = True
    return [(index, *corners[index]) for index in sorted(corners)]


# ---------------------------------------------------------------------------
# lines

@dataclass
class LineRegime:
    i0: int
    i1: int
    start: np.ndarray
    end: np.ndarray

    @property
    def direction(self) -> np.ndarray:
        return _unit(self.end - self.start)

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.end - self.start))


def _chord_deviations(pts: np.ndarray) -> np.ndarray:
    a, b = pts[0], pts[-1]
    d = b - a; length = float(np.linalg.norm(d))
    if length < 1e-9:
        return np.linalg.norm(pts - a, axis=1)
    u = d / length; rel = pts - a
    return np.abs(rel[:, 0] * u[1] - rel[:, 1] * u[0])


def _chord_effective(pts: np.ndarray, settings: FitSettings) -> np.ndarray:
    """Safe-side effective deviations from the chord pts[0] -> pts[-1]."""

    a, b = pts[0], pts[-1]
    d = b - a; length = float(np.linalg.norm(d))
    if length < 1e-9:
        return np.linalg.norm(pts - a, axis=1)
    return _line_effective(pts, a, d / length, settings)


def _tls_line(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Total-least-squares line: centroid, unit direction (following the run), |residual| per point."""

    centroid = pts.mean(axis=0)
    _u, _s, vt = np.linalg.svd(pts - centroid)
    direction = vt[0]
    if np.dot(direction, pts[-1] - pts[0]) < 0:
        direction = -direction
    rel = pts - centroid
    residual = np.abs(rel[:, 0] * direction[1] - rel[:, 1] * direction[0])
    return centroid, direction, residual


def _genuinely_straight(pts: np.ndarray, settings: FitSettings) -> bool:
    """A run is a curved regime, not a line, when a shallow parabola (sagitta
    profile) explains most of its residual and the implied radius is below
    max_line_curvature_radius_mm.  A quadratic fit of the perpendicular
    offsets is well conditioned even for short noisy chords, unlike an
    algebraic circle fit."""

    if len(pts) < 8:
        return True
    if len(pts) >= 12:
        pts = pts[2:-2]   # the samples at a run's ends may sit on a corner apex or a fillet
    centroid, direction, _ = _tls_line(pts)
    rel = pts - centroid
    along = rel @ direction
    perp = rel[:, 0] * direction[1] - rel[:, 1] * direction[0]
    line_residual = float(np.abs(perp).max())
    try:
        coefficients = np.polyfit(along, perp, 2)
    except (np.linalg.LinAlgError, ValueError):
        return True
    curvature = 2.0 * abs(float(coefficients[0]))
    if curvature < 1e-12 or 1.0 / curvature > settings.max_line_curvature_radius_mm:
        return True
    quad_residual = float(np.abs(perp - np.polyval(coefficients, along)).max())
    return not (quad_residual < 0.6 * line_residual and (line_residual - quad_residual) > 0.5)


def find_line_regimes_open(points: np.ndarray, settings: FitSettings, min_length: float, offset: int = 0) -> list[LineRegime]:
    """Maximal straight runs on an open polyline; indices are offset into the loop."""

    n = len(points)
    band = settings.band_mm
    runs: list[tuple[float, int, int]] = []
    step = 4
    for i in range(n - 2):
        j = i + 2
        while j + step < n and band_deviation(_chord_effective(points[i:j + step + 1], settings), settings) <= band:
            j += step
        while j + 1 < n and band_deviation(_chord_effective(points[i:j + 2], settings), settings) <= band:
            j += 1
        length = float(np.linalg.norm(points[j] - points[i]))
        if length >= min_length:
            runs.append((length, i, j))
    runs.sort(key=lambda r: -r[0])
    used = np.zeros(n, dtype=bool)
    curved = np.zeros(n, dtype=bool)   # points of runs rejected as curved regimes
    regimes: list[LineRegime] = []
    for _length, i, j in runs:
        idx = np.arange(i, j + 1)
        # a sub-chord of a curved regime is not a line, however straight it looks
        if curved[idx].mean() >= 0.5:
            continue
        free = ~used[idx]
        if not free.any():
            continue
        best_start = best_len = cur_start = cur_len = 0
        for k, f in enumerate(free):
            if f:
                if cur_len == 0:
                    cur_start = k
                cur_len += 1
                if cur_len > best_len:
                    best_start, best_len = cur_start, cur_len
            else:
                cur_len = 0
        sub = idx[best_start:best_start + best_len]
        if len(sub) < 3:
            continue
        pts = points[sub]
        if float(np.linalg.norm(pts[-1] - pts[0])) < min_length:
            continue
        centroid, direction, _residual = _tls_line(pts)
        if band_deviation(_line_effective(pts, centroid, direction, settings), settings) > band:
            continue
        if not _genuinely_straight(pts, settings):
            curved[sub] = True
            continue
        s0 = float(np.dot(pts[0] - centroid, direction)); s1 = float(np.dot(pts[-1] - centroid, direction))
        regimes.append(LineRegime(int(sub[0]) + offset, int(sub[-1]) + offset, centroid + direction * s0, centroid + direction * s1))
        used[sub] = True
    regimes.sort(key=lambda r: r.i0)
    return regimes


def _line_ok(pts: np.ndarray, settings: FitSettings) -> bool:
    if len(pts) < 3:
        return True
    centroid, direction, _residual = _tls_line(pts)
    return band_deviation(_line_effective(pts, centroid, direction, settings), settings) <= settings.band_mm


def _arc_ok(pts: np.ndarray, settings: FitSettings) -> bool:
    if len(pts) < 5:
        return True
    fit = circle_fit(pts)
    if fit is None:
        return False
    center, radius = fit
    if radius < settings.min_arc_radius_mm:
        return False
    return band_deviation(_arc_effective(pts, center, radius, settings), settings) <= settings.band_mm


def _reach(points: np.ndarray, i: int, ok) -> int:
    """Furthest j with ok(points[i..j]) -- exponential growth then binary
    search (feasibility is treated as prefix-closed)."""

    m = len(points)
    if i >= m - 1:
        return i
    lo = i + 1
    step = 4
    hi = min(m - 1, i + step)
    while ok(points[i:hi + 1]):
        lo = hi
        if hi == m - 1:
            return hi
        step *= 2
        hi = min(m - 1, i + step)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(points[i:mid + 1]):
            lo = mid
        else:
            hi = mid
    return lo


def segment_open_chain(points: np.ndarray, settings: FitSettings, min_line: float) -> list[tuple[str, int, int]]:
    """Cover an open chain with the fewest LINE / ARC pieces that stay inside
    the band (lines win ties).  This is the hand's global judgement: one
    8 m arc along a gently curved side, not a chain of 300 mm chords that
    each fit locally.  Only the LINE pieces become anchors; the arcs are
    re-solved with exact tangency by connect()."""

    m = len(points)
    if m < 3:
        return [("GAP", 0, m - 1)] if m == 2 else []
    reach_line = np.arange(m)
    reach_arc = np.arange(m)
    for i in range(m - 1):
        r = _reach(points, i, lambda p: _line_ok(p, settings))
        # a run that is a shallow curve is not a line; keep its longest straight prefix
        lo, hi = i, r
        if r > i and not _genuinely_straight(points[i:r + 1], settings):
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if _genuinely_straight(points[i:mid + 1], settings):
                    lo = mid
                else:
                    hi = mid
            r = lo
        if r > i and float(np.linalg.norm(points[r] - points[i])) < min_line:
            r = i
        reach_line[i] = r
        reach_arc[i] = _reach(points, i, lambda p: _arc_ok(p, settings))
    INF = (10 ** 9, 0.0)
    best: list[tuple[int, float]] = [INF] * m
    choice: list[tuple[int, str] | None] = [None] * m
    best[0] = (0, 0.0)
    for i in range(m - 1):
        if best[i] == INF:
            continue
        count, neg_line = best[i]
        rl, ra = int(reach_line[i]), int(reach_arc[i])
        top = max(rl, ra)
        if top == i:
            top = i + 1  # nothing fits: step one sample as a flagged gap
        # lines first: an arc costs two so that "line + arc + line" is not
        # beaten by "arc + arc" (a notch lip, a straight run beside a round)
        for j in range(i + 1, top + 1):
            if j <= rl and float(np.linalg.norm(points[j] - points[i])) >= min_line:
                cand = (count + 1, neg_line - float(np.linalg.norm(points[j] - points[i]))); kind = "LINE"
            elif j <= ra and j - i >= 3:
                cand = (count + 2, neg_line); kind = "ARC"
            else:
                cand = (count + 3, neg_line); kind = "GAP"
            if cand < best[j]:
                best[j] = cand
                choice[j] = (i, kind)
    pieces: list[tuple[str, int, int]] = []
    j = m - 1
    while j > 0 and choice[j] is not None:
        i, kind = choice[j]
        pieces.append((kind, i, j))
        j = i
    pieces.reverse()
    return pieces


def find_line_regimes_closed(points: np.ndarray, settings: FitSettings, min_length: float) -> list[LineRegime]:
    """Closed-loop version (no corners): scan the doubled contour, keep runs cyclic."""

    n = len(points)
    doubled = np.vstack([points, points])
    regimes = find_line_regimes_open(doubled, settings, min_length)
    kept: list[LineRegime] = []
    used = np.zeros(n, dtype=bool)
    for r in sorted(regimes, key=lambda r: -r.length):
        if r.i1 - r.i0 + 1 > n:
            continue
        idx = np.arange(r.i0, r.i1 + 1) % n
        if used[idx].any():
            continue
        used[idx] = True
        kept.append(LineRegime(int(r.i0 % n), int(r.i1 % n), r.start, r.end))
    kept.sort(key=lambda r: r.i0)
    return kept


# ---------------------------------------------------------------------------
# anchors and connections

@dataclass
class Anchor:
    kind: str                 # "line" | "corner"
    i0: int                   # first loop index owned by this anchor
    i1: int                   # last loop index owned by this anchor
    point_in: np.ndarray      # where an incoming connection must arrive
    point_out: np.ndarray     # where an outgoing connection starts
    tangent_in: np.ndarray | None   # fixed arrival tangent (lines) or None (corner: free)
    tangent_out: np.ndarray | None
    line: LineRegime | None = None
    ref_tangent_in: np.ndarray | None = None    # reference direction into a corner (soft guide)
    ref_tangent_out: np.ndarray | None = None
    points: np.ndarray | None = None            # contour points this anchor covers, in chain order
    center: np.ndarray | None = None            # arc anchors: circle centre ...
    radius: float = 0.0                         # ... radius ...
    ccw: bool = True                            # ... and travel direction around it


@dataclass
class Connection:
    kind: str                 # "extend" | "arcs" | "flagged"
    segments: list[Segment]
    p1: np.ndarray            # where the previous anchor now ends
    p2: np.ndarray            # where the next anchor now starts
    arc_count: int
    max_deviation_mm: float
    note: str = ""
    band_deviation_mm: float = 0.0   # spike-tolerant residual the band test uses
    trim_total_mm: float = 0.0       # how much line the bridge consumed or added


def _biarc(p1: np.ndarray, d1: np.ndarray, p2: np.ndarray, d2: np.ndarray, gap: np.ndarray, source: str) -> list[Segment] | None:
    """Exact G1 biarc whose joint lies on the reference gap (root search of the
    tangent mismatch at the joint)."""

    if len(gap) < 3:
        return None
    # joints everywhere along the gap, denser near the ends so a long arc can
    # finish with a short fillet into the next line
    fractions = np.concatenate([np.linspace(0.05, 0.95, 37), [0.005, 0.01, 0.02, 0.03, 0.97, 0.98, 0.99, 0.995]])
    idx = np.unique(np.clip((np.sort(fractions) * (len(gap) - 1)).round().astype(int), 1, len(gap) - 2))

    def mismatch(k: int) -> tuple[float, Segment, Segment] | None:
        joint = gap[k]
        a1 = arc_tangent_through(p1, d1, joint, source + "-1")
        a2 = arc_tangent_through(p2, -d2, joint, source + "-2").reversed()
        if a1.length_mm < 1e-6 or a2.length_mm < 1e-6:
            return None
        t1 = a1.tangent_end(); t2 = a2.tangent_start()
        return math.degrees(math.atan2(t1[0] * t2[1] - t1[1] * t2[0], float(np.dot(t1, t2)))), a1, a2

    values = [(int(k), mismatch(int(k))) for k in idx]
    best_pair: tuple[Segment, Segment] | None = None
    best_abs = float("inf")
    for (ka, a), (kb, b) in zip(values[:-1], values[1:]):
        if a is None or b is None:
            continue
        if abs(a[0]) < best_abs:
            best_abs, best_pair = abs(a[0]), (a[1], a[2])
        if a[0] * b[0] < 0:
            lo, hi, va = ka, kb, a
            while hi - lo > 1:
                mid = (lo + hi) // 2
                m = mismatch(mid)
                if m is None:
                    break
                if m[0] * va[0] <= 0:
                    hi = mid
                else:
                    lo, va = mid, m
            for cand in (lo, hi):
                m = mismatch(cand)
                if m is not None and abs(m[0]) < best_abs:
                    best_abs, best_pair = abs(m[0]), (m[1], m[2])
    if best_pair is None:
        return None
    a1 = best_pair[0]
    # rebuild the second arc from the joint with arc1's exact end tangent, then
    # check it still arrives tangent to d2 (it does when the joint is a true root)
    a2 = arc_tangent_through(a1.end, a1.tangent_end(), p2, source + "-2")
    arrival = _angle_deg(a2.tangent_end(), d2)
    if arrival <= 0.05:
        return [a1, a2]
    return None


def _triarc(p1: np.ndarray, d1: np.ndarray, p2: np.ndarray, d2: np.ndarray, gap: np.ndarray, source: str) -> list[Segment] | None:
    if len(gap) < 6:
        return None
    best: list[Segment] | None = None
    best_dev = float("inf")
    # the first or last arc may be a short fillet or a tip round, so the
    # split point is tried close to either end as well as across the middle
    for fraction in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.98):
        k = min(len(gap) - 2, max(1, int(round(fraction * (len(gap) - 1)))))
        mid = gap[k]
        a1 = arc_tangent_through(p1, d1, mid, source + "-a")
        if a1.length_mm > 1e-6:
            rest = _biarc(mid, a1.tangent_end(), p2, d2, gap[k:], source + "-b")
            if rest is not None:
                cand = [a1, *rest]
                dev = symmetric_deviation(cand, gap)
                if dev < best_dev:
                    best_dev, best = dev, cand
        a3 = arc_tangent_through(p2, -d2, mid, source + "-c").reversed()
        if a3.length_mm > 1e-6:
            head = _biarc(p1, d1, mid, a3.tangent_start(), gap[: k + 1], source + "-d")
            if head is not None:
                cand = [*head, a3]
                dev = symmetric_deviation(cand, gap)
                if dev < best_dev:
                    best_dev, best = dev, cand
    return best


def _line_intersection(p1: np.ndarray, d1: np.ndarray, p2: np.ndarray, d2: np.ndarray) -> np.ndarray | None:
    denominator = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denominator) < 1e-9:
        return None
    w = p2 - p1
    t = (w[0] * d2[1] - w[1] * d2[0]) / denominator
    return p1 + d1 * t


def _reference_tangent(points: np.ndarray, index: int, forward: bool, settings: FitSettings) -> np.ndarray:
    """Direction of the contour leaving (forward) or arriving at (backward) a
    corner, from a least-squares line over the long corner window."""

    n = len(points)
    k = max(3, int(round(settings.corner_window_long_mm / settings.resample_mm)))
    if forward:
        idx = [(index + s) % n for s in range(0, k + 1)]
    else:
        idx = [(index - s) % n for s in range(k, -1, -1)]
    return _tls_line(points[idx])[1]


def _tangent_options(direction: np.ndarray | None, degrees: tuple[float, ...] = (0.0, -8.0, 8.0, -16.0, 16.0, -25.0, 25.0)) -> list[np.ndarray]:
    if direction is None:
        return [np.array([1.0, 0.0])]
    out = []
    for deg in degrees:
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        out.append(np.array([c * direction[0] - s * direction[1], s * direction[0] + c * direction[1]]))
    return out


def _smallest_radius(conn: Connection) -> float:
    radii = [s.radius_mm for s in conn.segments if s.kind == "ARC"]
    return min(radii) if radii else 0.0


def _arcs_through(p1: np.ndarray, p2: np.ndarray, gap: np.ndarray, source: str) -> list[Segment]:
    """Arcs through both endpoints over a range of bulges around the gap's own sagitta."""

    chord = p2 - p1
    length = float(np.linalg.norm(chord))
    if length < 1e-9 or len(gap) < 3:
        return []
    u = chord / length; nrm = _left(u); mid = (p1 + p2) / 2.0
    offsets = (gap - mid) @ nrm
    apex = float(offsets[np.argmax(np.abs(offsets))])
    arcs = [Segment("LINE", p1.copy(), p2.copy(), source=source)]   # the straight chord first
    if abs(apex) > 1e-6:
        for f in (0.8, 0.86, 0.92, 0.96, 1.0, 1.04, 1.08, 1.14, 1.2):
            arcs.append(arc_through_three(p1, mid + nrm * (apex * f), p2, source))
    for k in (len(gap) // 4, len(gap) // 2, (3 * len(gap)) // 4):
        arcs.append(arc_through_three(p1, gap[k], p2, source))
    return arcs


# ---------------------------------------------------------------------------
# anchor geometry (lines and arcs)

def _rotate(v: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def _circle_tangent(anchor: Anchor, q: np.ndarray) -> np.ndarray:
    """Travel direction of an arc anchor's circle at q."""

    assert anchor.center is not None
    t = _left(_unit(q - anchor.center))
    return t if anchor.ccw else -t


def _arc_length_along(anchor: Anchor, q: np.ndarray) -> float:
    """Travel distance from the arc anchor's start to q along its circle (0 .. 2*pi*R)."""

    assert anchor.center is not None
    a0 = math.atan2(anchor.point_in[1] - anchor.center[1], anchor.point_in[0] - anchor.center[0])
    aq = math.atan2(q[1] - anchor.center[1], q[0] - anchor.center[0])
    d = (aq - a0) if anchor.ccw else (a0 - aq)
    return (d % TWO_PI) * anchor.radius


def _anchor_length(anchor: Anchor) -> float:
    if anchor.kind == "line":
        return float(np.linalg.norm(anchor.point_out - anchor.point_in))
    if anchor.kind == "arc":
        return max(_arc_length_along(anchor, anchor.point_out), 1e-6)
    return 0.0


def _travel_tangent(anchor: Anchor, q: np.ndarray) -> np.ndarray | None:
    if anchor.kind == "line":
        return anchor.tangent_out
    if anchor.kind == "arc":
        return _circle_tangent(anchor, q)
    return None


def _anchor_end(anchor: Anchor, trim: float, outgoing: bool) -> tuple[np.ndarray, np.ndarray | None]:
    """The anchor's end point and travel tangent after moving it by `trim`
    (positive = shorten, negative = extend)."""

    if anchor.kind == "line":
        if outgoing:
            return anchor.point_out - anchor.tangent_out * trim, anchor.tangent_out
        return anchor.point_in + anchor.tangent_in * trim, anchor.tangent_in
    if anchor.kind == "arc":
        assert anchor.center is not None
        ang = trim / max(anchor.radius, 1e-9)
        sign = 1.0 if anchor.ccw else -1.0
        if outgoing:
            q = anchor.center + _rotate(anchor.point_out - anchor.center, -sign * ang)
        else:
            q = anchor.center + _rotate(anchor.point_in - anchor.center, sign * ang)
        return q, _circle_tangent(anchor, q)
    return (anchor.point_out if outgoing else anchor.point_in), None


def _trim_amount(anchor: Anchor, q: np.ndarray, outgoing: bool) -> float:
    """How far a tangent point q moves the anchor's end (positive = shortened)."""

    if anchor.kind == "line":
        if outgoing:
            return float(np.dot(anchor.point_out - q, anchor.tangent_out))
        return float(np.dot(q - anchor.point_in, anchor.tangent_in))
    length = _anchor_length(anchor)
    circumference = TWO_PI * anchor.radius
    s = _arc_length_along(anchor, q)
    if outgoing:
        if s > length + 0.5 * (circumference - length):
            s -= circumference
        return length - s
    if s > 0.5 * (circumference + length):
        s -= circumference
    return s


def _extension_piece(anchor: Anchor, base: np.ndarray, p: np.ndarray, outgoing: bool, source: str) -> Segment:
    """The bit of anchor added when its end is extended from `base` to `p`."""

    if anchor.kind == "arc":
        if outgoing:
            return arc_tangent_through(base, anchor.tangent_out, p, source)
        return arc_tangent_through(p, _circle_tangent(anchor, p), base, source)
    return Segment("LINE", base.copy(), p.copy(), source=source) if outgoing else Segment("LINE", p.copy(), base.copy(), source=source)


def _tangent_point(anchor: Anchor, x: np.ndarray) -> np.ndarray:
    """Foot of a fillet centre x on the anchor (line: perpendicular foot; arc: radial point)."""

    if anchor.kind == "line":
        d = anchor.tangent_out
        return anchor.point_in + d * float(np.dot(x - anchor.point_in, d))
    assert anchor.center is not None
    return anchor.center + _unit(x - anchor.center) * anchor.radius


def _circle_circle(c1: np.ndarray, r1: float, c2: np.ndarray, r2: float) -> list[np.ndarray]:
    d = float(np.linalg.norm(c2 - c1))
    if d < 1e-9 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2.0 * d)
    h2 = r1 * r1 - a * a
    h = math.sqrt(max(h2, 0.0))
    u = (c2 - c1) / d
    m = c1 + u * a
    return [m + _left(u) * h, m - _left(u) * h] if h > 1e-9 else [m]


def _line_circle_offsets(p: np.ndarray, d: np.ndarray, r: float, c: np.ndarray, rho: float) -> list[np.ndarray]:
    """Centres at distance r from line (p, d) and distance rho from c."""

    out = []
    n = _left(d)
    for sigma in (1.0, -1.0):
        w = p + n * (sigma * r) - c
        b = float(np.dot(w, d)); cc = float(np.dot(w, w)) - rho * rho
        disc = b * b - cc
        if disc < 0:
            continue
        for s in (-b + math.sqrt(disc), -b - math.sqrt(disc)):
            out.append(p + d * s + n * (sigma * r))
    return out


def _fillet_centers(a: Anchor, b: Anchor, r: float) -> list[np.ndarray]:
    if a.kind == "line" and b.kind == "arc":
        assert b.center is not None
        return [x for rho in (b.radius + r, abs(b.radius - r)) for x in _line_circle_offsets(a.point_in, a.tangent_out, r, b.center, rho)]
    if a.kind == "arc" and b.kind == "line":
        assert a.center is not None
        return [x for rho in (a.radius + r, abs(a.radius - r)) for x in _line_circle_offsets(b.point_in, b.tangent_in, r, a.center, rho)]
    if a.kind == "arc" and b.kind == "arc":
        assert a.center is not None and b.center is not None
        out = []
        for rho1 in (a.radius + r, abs(a.radius - r)):
            for rho2 in (b.radius + r, abs(b.radius - r)):
                out.extend(_circle_circle(a.center, rho1, b.center, rho2))
        return out
    return []


def _fillets_general(a: Anchor, b: Anchor, gap_length: float, settings: FitSettings, source: str) -> list[tuple[list[Segment], np.ndarray, np.ndarray, float, float]]:
    """Single arcs tangent to a line and an arc, or two arcs (closed-form
    centres for each radius).  Returns (segments, p1, p2, trim_a, trim_b)."""

    results = []
    len_a = _anchor_length(a); len_b = _anchor_length(b)
    r_max = max(gap_length, 300.0)
    for r in np.geomspace(settings.min_arc_radius_mm, r_max, 40):
        for x in _fillet_centers(a, b, float(r)):
            t1 = _tangent_point(a, x); t2 = _tangent_point(b, x)
            tan1 = _travel_tangent(a, t1); tan2 = _travel_tangent(b, t2)
            if tan1 is None or tan2 is None:
                continue
            arc = arc_tangent_through(t1, tan1, t2, source + "-fillet")
            # a fillet may sweep past a half turn (a notch cap, the tip of a
            # sliver) but never nearly all the way round
            if arc.kind != "ARC" or _angle_deg(arc.tangent_end(), tan2) > 0.05 or arc.length_mm > 1.9 * math.pi * r:
                continue
            ta = _trim_amount(a, t1, True); tb = _trim_amount(b, t2, False)
            if ta > settings.max_line_trim_fraction * len_a or tb > settings.max_line_trim_fraction * len_b:
                continue
            if ta < -settings.max_line_extend_mm or tb < -settings.max_line_extend_mm:
                continue
            results.append(([arc], t1, t2, ta, tb))
    return results


def _arc_to_apex(arc: Anchor, apex: np.ndarray, gap: np.ndarray, settings: FitSettings, source: str, outgoing: bool) -> Connection | None:
    """End an arc anchor exactly on a corner apex when its circle nearly
    passes through it: the arc keeps its far end and tangent, the near end
    moves to the apex."""

    if outgoing:
        new = arc_tangent_through(arc.point_in, arc.tangent_in, apex, source + "-to-apex")
    else:
        new = arc_tangent_through(arc.point_out, -arc.tangent_out, apex, source + "-to-apex").reversed()
    if new.kind != "ARC" or new.radius_mm < settings.min_arc_radius_mm:
        return None
    reference = np.vstack([arc.points, gap]) if arc.points is not None else gap
    profile = deviation_profile([new], reference)
    if band_deviation(profile, settings) > settings.band_mm or new.length_mm > 1.5 * (_anchor_length(arc) + _polyline_length(gap)) + 50.0:
        return None
    arc.center = new.center.copy(); arc.radius = new.radius_mm; arc.ccw = new.sweep_rad > 0
    if outgoing:
        arc.point_out = apex.copy(); arc.tangent_out = new.tangent_end()
    else:
        arc.point_in = apex.copy(); arc.tangent_in = new.tangent_start()
    return Connection("extend", [], apex.copy(), apex.copy(), 0, float(profile.max()), "arc extended to corner")


def connect(a: Anchor, b: Anchor, gap: np.ndarray, settings: FitSettings, source: str, depth: int = 0) -> Connection:
    """Bridge anchor A -> anchor B with the fewest tangent arcs inside the band."""

    band = settings.band_mm
    gap_length = _polyline_length(gap)
    p1_base, p2_base = a.point_out, b.point_in
    t1 = a.tangent_out; t2 = b.tangent_in
    trims_a = _trims(a, settings); trims_b = _trims(b, settings)

    # Anchors already joined exactly (an arc refitted tangent to its line or through an apex).
    if float(np.linalg.norm(p2_base - p1_base)) < 0.5 and (t1 is None or t2 is None or _angle_deg(t1, t2) <= 0.1):
        return Connection("extend", [], p2_base.copy(), p2_base.copy(), 0, 0.0, "anchors meet")

    # Lines meeting directly at a sharp corner (undetected by the corner pass).
    if a.kind == "line" and b.kind == "line" and t1 is not None and t2 is not None:
        turning = _angle_deg(t1, t2)
        x = _line_intersection(p1_base, t1, p2_base, t2)
        if x is not None and gap_length <= settings.corner_max_gap_mm and turning >= settings.corner_angle_deg:
            dist = float(shapely.distance(LineString(gap) if len(gap) > 1 else shapely.points(gap[0]), shapely.points(x[None, :]))[0])
            if dist <= settings.corner_snap_mm:
                return Connection("corner", [], x, x, 0, dist, "sharp corner between lines")

    # A line whose extension runs straight into a corner: just extend (or
    # shorten) it to the apex, provided the contour in between stays in band.
    if a.kind == "line" and b.kind == "corner" and t1 is not None:
        along = float(np.dot(p2_base - p1_base, t1))
        off = abs(float(_cross2(t1, p2_base - p1_base)))
        if off <= band and along >= -settings.corner_snap_mm:
            piece = Segment("LINE", p1_base - t1 * max(0.0, -along + 1.0), p2_base.copy(), source=source + "-extend")
            profile = deviation_profile([piece], gap) if len(gap) > 1 else np.array([off])
            if band_deviation(profile, settings) <= band:
                return Connection("extend", [], p2_base.copy(), p2_base.copy(), 0, float(profile.max()), "line extended to corner")
    if a.kind == "corner" and b.kind == "line" and t2 is not None:
        along = float(np.dot(p1_base - p2_base, -t2))
        off = abs(float(_cross2(t2, p1_base - p2_base)))
        if off <= band and along >= -settings.corner_snap_mm:
            piece = Segment("LINE", p1_base.copy(), p2_base + t2 * max(0.0, -along + 1.0), source=source + "-extend")
            profile = deviation_profile([piece], gap) if len(gap) > 1 else np.array([off])
            if band_deviation(profile, settings) <= band:
                return Connection("extend", [], p1_base.copy(), p1_base.copy(), 0, float(profile.max()), "line extended back to corner")
    # An arc whose circle (nearly) passes through a corner apex ends on it.
    if a.kind == "arc" and b.kind == "corner":
        conn = _arc_to_apex(a, p2_base, gap, settings, source, outgoing=True)
        if conn is not None:
            return conn
    if a.kind == "corner" and b.kind == "arc":
        conn = _arc_to_apex(b, p1_base, gap, settings, source, outgoing=False)
        if conn is not None:
            return conn

    candidates: list[Connection] = []
    best_any: Connection | None = None

    def evaluate(segs: list[Segment], p1: np.ndarray, p2: np.ndarray, ta: float, tb: float) -> Connection | None:
        """Score a bridge.  A trimmed anchor leaves its piece in the
        reference; an extended anchor adds its extension to the candidate."""
        nonlocal best_any
        if not segs or any(s.kind == "ARC" and s.radius_mm < settings.min_arc_radius_mm for s in segs):
            return None
        # a tangent arc can legally go the long way round; that is never the bridge we want
        if sum(s.length_mm for s in segs) > 2.5 * gap_length + 100.0:
            return None
        reference = _gap_reference(a, b, gap, p1, p2, ta, tb)
        check = list(segs)
        if ta < -1e-9:
            check.insert(0, _extension_piece(a, p1_base, p1, True, source + "-ext-a"))
        if tb < -1e-9:
            check.append(_extension_piece(b, p2_base, p2, False, source + "-ext-b"))
        profile = deviation_profile_effective(check, reference, settings)
        conn = Connection("arcs", segs, p1, p2, sum(s.kind == "ARC" for s in segs), float(profile.max()),
                          band_deviation_mm=band_deviation(profile, settings), trim_total_mm=abs(ta) + abs(tb))
        if best_any is None or conn.band_deviation_mm < best_any.band_deviation_mm:
            best_any = conn
        return conn if conn.band_deviation_mm <= band else None

    def endpoints(ta: float, tb: float) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray | None]:
        p1, u1 = _anchor_end(a, ta, True)
        p2, u2 = _anchor_end(b, tb, False)
        return p1, u1, p2, u2

    # ONE ARC
    if a.kind == "line" and b.kind == "line":
        # fillet family between two lines: the tangent points follow the radius, not the trims
        for segs, p1, p2, ta, tb in _fillets(a, b, settings, source):
            conn = evaluate(segs, p1, p2, ta, tb)
            if conn is not None:
                candidates.append(conn)
    elif a.kind in ("line", "arc") and b.kind in ("line", "arc"):
        for segs, p1, p2, ta, tb in _fillets_general(a, b, gap_length, settings, source):
            conn = evaluate(segs, p1, p2, ta, tb)
            if conn is not None:
                candidates.append(conn)
    else:
        for ta in trims_a:
            for tb in trims_b:
                p1, u1, p2, u2 = endpoints(ta, tb)
                if u1 is not None:
                    arcs = [arc_tangent_through(p1, u1, p2, source + "-1")]
                elif u2 is not None:
                    arcs = [arc_tangent_through(p2, -u2, p1, source + "-1").reversed()]
                else:
                    arcs = _arcs_through(p1, p2, gap, source + "-1")
                for arc in arcs:
                    if arc.kind == "LINE":
                        if float(np.linalg.norm(p2 - p1)) < 0.5:
                            candidates.append(Connection("extend", [], p2.copy(), p2.copy(), 0, 0.0, "anchors meet"))
                            continue
                        arc = Segment("LINE", p1.copy(), p2.copy(), source=source + "-1")
                    conn = evaluate([arc], p1, p2, ta, tb)
                    if conn is not None:
                        candidates.append(conn)
    good = [c for c in candidates if c.band_deviation_mm <= band]
    if good:
        # fewest arcs; a straight bridge with the least line eaten; else the
        # largest arc that fits (a hand draws the biggest arc it can), then fit
        return min(good, key=lambda c: (c.arc_count, c.trim_total_mm if c.arc_count == 0 else 0.0,
                                        -_smallest_radius(c), c.band_deviation_mm))

    # TWO then THREE ARCS.  Free tangents (corners) start from the reference
    # direction and may rotate a little: the reference is a scan, not a drawing.
    for arc_count in (2, 3):
        if arc_count > settings.max_arcs_per_connection:
            break
        for ta in trims_a:
            for tb in trims_b:
                p1, u1, p2, u2 = endpoints(ta, tb)
                reference = _search_reference(_gap_reference(a, b, gap, p1, p2, ta, tb), p1, u1, p2, u2)
                d1_options = [u1] if u1 is not None else _tangent_options(a.ref_tangent_out)
                d2_options = [u2] if u2 is not None else _tangent_options(b.ref_tangent_in)
                for d1 in d1_options:
                    for d2 in d2_options:
                        segs = _biarc(p1, d1, p2, d2, reference, source) if arc_count == 2 else _triarc(p1, d1, p2, d2, reference, source)
                        if segs is None:
                            continue
                        conn = evaluate(segs, p1, p2, ta, tb)
                        if conn is not None:
                            return conn

    # Promote a straight-ish piece of the gap to an intermediate line, once.
    if depth == 0 and gap_length > 3 * settings.intermediate_line_min_mm:
        inner = find_line_regimes_open(gap, settings, settings.intermediate_line_min_mm)
        if inner:
            mid = max(inner, key=lambda r: r.length)
            mid_anchor = Anchor("line", mid.i0, mid.i1, mid.start, mid.end, mid.direction, mid.direction, mid)
            left = connect(a, mid_anchor, gap[: mid.i0 + 1], settings, source + "-L", depth + 1)
            right = connect(mid_anchor, b, gap[mid.i1:], settings, source + "-R", depth + 1)
            if left.kind != "flagged" and right.kind != "flagged":
                mid_line = Segment("LINE", left.p2.copy(), right.p1.copy(), source=source + "-mid")
                segs = [*left.segments, mid_line, *right.segments]
                return Connection("arcs", segs, left.p1, right.p2, left.arc_count + right.arc_count,
                                  max(left.max_deviation_mm, right.max_deviation_mm), "intermediate line inserted",
                                  band_deviation_mm=max(left.band_deviation_mm, right.band_deviation_mm))
    # Last resort before flagging: the gap's own fewest-primitives cover
    # (lines and short arcs) becomes a chain of sub-anchors joined one by one.
    if depth == 0:
        via = _connect_via_pieces(a, b, gap, settings, source)
        if via is not None:
            return via
    if best_any is not None:
        best_any.kind = "flagged"
        best_any.note = (f"best {best_any.arc_count}-arc solution exceeds the {band:.1f} mm band "
                         f"({best_any.band_deviation_mm:.2f} mm effective, worst point {best_any.max_deviation_mm:.2f} mm)")
        return best_any
    chord = Segment("LINE", p1_base.copy(), p2_base.copy(), source=source + "-chord")
    profile = deviation_profile([chord], gap)
    return Connection("flagged", [chord], p1_base, p2_base, 0, float(profile.max()), "no tangent solution; chord kept for review",
                      band_deviation_mm=band_deviation(profile, settings))


def _connect_via_pieces(a: Anchor, b: Anchor, gap: np.ndarray, settings: FitSettings, source: str) -> Connection | None:
    """Bridge a gap that no 1-3 arc solution fits by making every piece of
    its fewest-primitives cover (a lobe, a step with rounded corners) a
    sub-anchor and joining the chain piece by piece.  More primitives than
    a hand would draw, but a faithful, tangent, in-band result instead of a
    flagged chord."""

    if len(gap) < 8:
        return None
    pieces = segment_open_chain(gap, settings, 40.0)
    if len(pieces) < 2:
        return None
    subs: list[Anchor] = []
    for kind, i, j in pieces:
        pts = gap[i:j + 1]
        if kind == "LINE":
            centroid, direction, _ = _tls_line(pts)
            s0 = float(np.dot(pts[0] - centroid, direction)); s1 = float(np.dot(pts[-1] - centroid, direction))
            regime = LineRegime(i, j, centroid + direction * s0, centroid + direction * s1)
            subs.append(Anchor("line", i, j, regime.start, regime.end, regime.direction, regime.direction, regime, points=pts.copy()))
        elif kind == "ARC" and j - i >= 3:
            anchor = _arc_anchor_from_points(pts, i, j, settings)
            if anchor is not None:
                subs.append(anchor)
    if not subs:
        return None
    chain = [a, *subs, b]
    segments: list[Segment] = []
    max_dev = 0.0; band_dev = 0.0; arcs = 0
    joins: list[Connection] = []
    for k in range(len(chain) - 1):
        left, right = chain[k], chain[k + 1]
        i0 = 0 if k == 0 else subs[k - 1].i1
        i1 = len(gap) - 1 if k == len(chain) - 2 else subs[k].i0
        piece_gap = gap[i0:i1 + 1] if i1 >= i0 else gap[i0:i0 + 1]
        conn = connect(left, right, piece_gap, settings, f"{source}-via{k}", depth=1)
        if conn.kind == "flagged":
            return None
        joins.append(conn)
        max_dev = max(max_dev, conn.max_deviation_mm); band_dev = max(band_dev, conn.band_deviation_mm); arcs += conn.arc_count
    for k, sub in enumerate(subs):
        incoming, outgoing = joins[k], joins[k + 1]
        segments.extend(incoming.segments)
        start, end = incoming.p2, outgoing.p1
        if float(np.linalg.norm(end - start)) > 1e-6:
            if sub.kind == "line":
                segments.append(Segment("LINE", start.copy(), end.copy(), source=f"{source}-via-line{k}"))
            else:
                seg = arc_tangent_through(start, _circle_tangent(sub, start), end, f"{source}-via-arc{k}")
                segments.append(seg)
                arcs += 1 if seg.kind == "ARC" else 0
    segments.extend(joins[-1].segments)
    return Connection("arcs", segments, joins[0].p1, joins[-1].p2, arcs, max_dev, "bridged through the gap's own pieces",
                      band_deviation_mm=band_dev)


def _trims(anchor: Anchor, settings: FitSettings) -> list[float]:
    """Candidate end moves for a line anchor: positive = trim the line back,
    negative = extend it (an extension is checked against the reference)."""

    if anchor.kind not in ("line", "arc"):
        return [0.0]
    limit = settings.max_line_trim_fraction * _anchor_length(anchor)
    trims = [t for t in settings.trim_candidates_mm if t <= limit] or [0.0]
    extends = [-t for t in settings.trim_candidates_mm if 0.0 < t <= settings.max_line_extend_mm]
    return sorted(set(trims + extends), key=abs)


def _gap_reference(a: Anchor, b: Anchor, gap: np.ndarray, p1: np.ndarray, p2: np.ndarray, ta: float, tb: float) -> np.ndarray:
    """The gap plus the contour a trimmed line gives up (it must stay inside the band too)."""

    head: list[np.ndarray] = []
    tail: list[np.ndarray] = []
    if ta > 1e-9 and a.tangent_out is not None:
        if a.points is not None and len(a.points):
            if a.kind == "arc":
                s1 = _arc_length_along(a, p1)
                head = [a.points[np.array([_arc_length_along(a, q) > s1 for q in a.points])]]
            else:
                head = [a.points[(a.points - p1) @ a.tangent_out > 0.0]]
        else:
            head = [np.linspace(p1, a.point_out, 8)]
    if tb > 1e-9 and b.tangent_in is not None:
        if b.points is not None and len(b.points):
            if b.kind == "arc":
                s2 = _arc_length_along(b, p2)
                tail = [b.points[np.array([_arc_length_along(b, q) < s2 for q in b.points])]]
            else:
                tail = [b.points[(b.points - p2) @ b.tangent_in < 0.0]]
        else:
            tail = [np.linspace(b.point_in, p2, 8)]
    return np.vstack([*head, gap, *tail])


def _search_reference(reference: np.ndarray, p1: np.ndarray, d1: np.ndarray | None, p2: np.ndarray, d2: np.ndarray | None) -> np.ndarray:
    """For joint searches: drop reference points behind an extended endpoint."""

    keep = np.ones(len(reference), dtype=bool)
    if d1 is not None:
        keep &= (reference - p1) @ d1 >= -1e-9
    if d2 is not None:
        keep &= (reference - p2) @ d2 <= 1e-9
    out = reference[keep]
    return out if len(out) >= 3 else reference


def _fillets(a: Anchor, b: Anchor, settings: FitSettings, source: str) -> list[tuple[list[Segment], np.ndarray, np.ndarray, float, float]]:
    """Single arcs tangent to both lines.  The tangent points follow the
    radius (t = r tan(theta/2) from the intersection), so either line may be
    trimmed back (up to max_line_trim_fraction) or extended (up to
    max_line_extend_mm) to meet the arc -- exactly what a tangent-tangent arc
    drawn between two lines does.  Returns (segments, p1, p2, trim_a, trim_b)."""

    assert a.line is not None and b.line is not None
    d1 = a.tangent_out; d2 = b.tangent_in
    turning = _angle_deg(d1, d2)
    if turning < 0.5 or turning > 175.0:
        return []
    x = _line_intersection(a.point_out, d1, b.point_in, d2)
    if x is None:
        return []
    tan_half = math.tan(math.radians(turning) / 2.0)
    x_a = float(np.dot(x - a.point_out, d1))    # intersection measured from A's end (+ = ahead)
    x_b = float(np.dot(x - b.point_in, d2))     # intersection measured from B's start (- = before it)
    t_lo = max(x_a - settings.max_line_extend_mm, -x_b - settings.max_line_extend_mm, settings.min_arc_radius_mm * tan_half, 1e-6)
    t_hi = min(x_a + settings.max_line_trim_fraction * a.line.length, -x_b + settings.max_line_trim_fraction * b.line.length)
    if t_hi <= t_lo:
        return []
    result = []
    for t in np.geomspace(t_lo, t_hi, 48):
        t1 = x - d1 * t; t2 = x + d2 * t
        arc = arc_tangent_through(t1, d1, t2, source + "-fillet")
        if arc.kind != "ARC" or _angle_deg(arc.tangent_end(), d2) > 0.05:
            continue
        result.append(([arc], t1, t2, t - x_a, x_b + t))
    return result


# ---------------------------------------------------------------------------
# whole loop

@dataclass
class LoopFit:
    loop: Loop
    connections: list[Connection]
    line_count: int
    arc_count: int
    corner_count: int
    flagged: list[str] = field(default_factory=list)
    max_deviation_mm: float = 0.0
    method: str = "corners-lines-arcs"
    detected_corners: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method, "line_count": self.line_count, "arc_count": self.arc_count,
            "corner_count": self.corner_count, "detected_corners": self.detected_corners,
            "primitive_count": len(self.loop.segments), "max_deviation_mm": self.max_deviation_mm, "flagged": list(self.flagged),
            "connections": [{"kind": c.kind, "arcs": c.arc_count, "max_deviation_mm": c.max_deviation_mm, "note": c.note}
                            for c in self.connections],
        }


def _cyclic_slice(points: np.ndarray, i0: int, i1: int) -> np.ndarray:
    n = len(points); i0 %= n; i1 %= n
    return points[i0:i1 + 1] if i1 >= i0 else np.vstack([points[i0:], points[:i1 + 1]])


def build_anchors(points: np.ndarray, settings: FitSettings, min_line: float) -> tuple[list[Anchor], int]:
    n = len(points)
    corner_points = detect_corner_points(points, settings)
    corners = [corner[0] for corner in corner_points]
    anchors: list[Anchor] = []
    if corners:
        for k, (c, apex, d_in, d_out) in enumerate(corner_points):
            next_c = corners[(k + 1) % len(corners)]
            # the flank lines that proved the corner are its reference tangents
            anchors.append(Anchor("corner", c, c, apex.copy(), apex.copy(), None, None, None, d_in.copy(), d_out.copy()))
            span = _cyclic_slice(points, c, next_c) if next_c != c else np.vstack([points[c:], points[:c + 1]])
            if len(span) < 6:
                continue
            # the corner samples themselves stay out of the pieces: an apex
            # sample sits off both flanks and would bend a short lip
            anchors.extend(_span_anchors(span[1:-1], settings, min_line, offset=c + 1, n=n))
        # keep cyclic order by first index starting at the first corner
        first = corners[0]
        anchors.sort(key=lambda an: (an.i0 - first) % n)
    else:
        # no corners: open the loop at the start of its longest straight run
        regimes = find_line_regimes_closed(points, settings, min_line)
        if regimes:
            start = max(regimes, key=lambda r: r.length).i0
            chain = np.vstack([points[start:], points[:start], points[start:start + 1]])
            anchors.extend(_span_anchors(chain, settings, min_line, offset=start, n=n))
            anchors.sort(key=lambda an: (an.i0 - start) % n)
    anchors = _merge_collinear(anchors, points, settings)
    _refine_corner_apexes(anchors, points, settings)
    _snap_arc_anchors(anchors, points, settings)
    return anchors, len(corners)


def _span_anchors(span: np.ndarray, settings: FitSettings, min_line: float, offset: int, n: int) -> list[Anchor]:
    """Anchors from the fewest-primitives cover of a span: every LINE piece,
    and every ARC piece long enough to be drawn as its own primitive (the
    bow side, a notch cap, a transom round).  Short arc pieces are left to
    the connections."""

    anchors: list[Anchor] = []
    # between close corners (notch lips, small cut-outs) a hand draws short
    # connector lines, so the minimum line length scales with the span
    span_length = _polyline_length(span)
    min_line = max(40.0, min(min_line, 0.15 * span_length))
    # likewise a small feature (a cleat ear, a jog) is drawn with its own short arcs
    arc_min = max(40.0, min(settings.arc_anchor_min_mm, 0.3 * span_length))
    for kind, i, j in segment_open_chain(span, settings, min_line):
        pts = span[i:j + 1]
        if kind == "LINE":
            centroid, direction, _ = _tls_line(pts)
            s0 = float(np.dot(pts[0] - centroid, direction)); s1 = float(np.dot(pts[-1] - centroid, direction))
            regime = LineRegime((offset + i) % n, (offset + j) % n, centroid + direction * s0, centroid + direction * s1)
            anchors.append(Anchor("line", regime.i0, regime.i1, regime.start, regime.end, regime.direction, regime.direction, regime,
                                  points=pts.copy()))
        elif kind == "ARC" and _polyline_length(pts) >= arc_min:
            anchor = _arc_anchor_from_points(pts, (offset + i) % n, (offset + j) % n, settings)
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def _arc_anchor_from_points(pts: np.ndarray, i0: int, i1: int, settings: FitSettings, circle: tuple[np.ndarray, float] | None = None) -> Anchor | None:
    fit = circle if circle is not None else circle_fit(pts)
    if fit is None:
        return None
    center, radius = fit
    if radius < settings.min_arc_radius_mm:
        return None
    start = center + _unit(pts[0] - center) * radius
    end = center + _unit(pts[-1] - center) * radius
    k = len(pts) // 2
    travel = pts[min(len(pts) - 1, k + 1)] - pts[max(0, k - 1)]
    ccw = _cross2(pts[k] - center, travel) > 0
    anchor = Anchor("arc", i0, i1, start, end, None, None, None, points=pts.copy(), center=center.copy(), radius=float(radius), ccw=ccw)
    anchor.tangent_in = _circle_tangent(anchor, start)
    anchor.tangent_out = _circle_tangent(anchor, end)
    return anchor


def _gauss_newton(residual, x0: np.ndarray, iterations: int = 12) -> np.ndarray:
    x = np.array(x0, dtype=float)
    for _ in range(iterations):
        r = residual(x)
        if r is None or not np.all(np.isfinite(r)):
            break
        jac = np.empty((len(r), len(x)))
        for j in range(len(x)):
            h = 1e-4 * max(1.0, abs(float(x[j])))
            xp = x.copy(); xp[j] += h
            rp = residual(xp)
            if rp is None or not np.all(np.isfinite(rp)):
                return x
            jac[:, j] = (rp - r) / h
        try:
            step, *_ = np.linalg.lstsq(jac, -r, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        x = x + step
        if float(np.linalg.norm(step)) < 1e-6:
            break
    return x


def _circle_residual(pts: np.ndarray, center: np.ndarray, radius: float) -> np.ndarray:
    return np.linalg.norm(pts - center, axis=1) - radius


def _refit_arc(anchor: Anchor, line_in: Anchor | None, line_out: Anchor | None, apex_in: np.ndarray | None,
               apex_out: np.ndarray | None, settings: FitSettings) -> tuple[np.ndarray, float] | None:
    """Least-squares circle for the anchor's points under the join constraints
    (tangent to an adjacent line, through an adjacent corner apex)."""

    pts = anchor.points
    assert pts is not None and anchor.center is not None
    c0, r0 = anchor.center, anchor.radius

    def line_frame(line: Anchor) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        p0 = line.point_in; d = line.tangent_out; nrm = _left(d)
        side = 1.0 if float(np.dot(c0 - p0, nrm)) >= 0 else -1.0
        return p0, d, nrm, side

    if line_in is not None and line_out is not None:
        p1, d1, _n1, _s1 = line_frame(line_in); p2, d2, _n2, _s2 = line_frame(line_out)
        x = _line_intersection(p1, d1, p2, d2)
        u = _unit(-d1 + d2)
        if x is None or float(np.linalg.norm(-d1 + d2)) < 1e-6:
            return None
        k = abs(_cross2(d1, u))

        def res(v):
            c = x + u * v[0]
            return _circle_residual(pts, c, abs(v[0]) * k)
        v = _gauss_newton(res, np.array([float(np.dot(c0 - x, u))]))
        return x + u * v[0], abs(float(v[0])) * k
    if line_in is not None or line_out is not None:
        line = line_in if line_in is not None else line_out
        apex = apex_out if line_in is not None else apex_in
        p0, d, nrm, side = line_frame(line)
        if apex is not None:
            def res(v):
                m = apex - (p0 + d * v[0])
                denom = 2.0 * side * float(np.dot(m, nrm))
                if abs(denom) < 1e-9:
                    return None
                radius = float(np.dot(m, m)) / denom
                if radius <= 0:
                    return None
                return _circle_residual(pts, p0 + d * v[0] + nrm * (side * radius), radius)
            v = _gauss_newton(res, np.array([float(np.dot(c0 - p0, d))]))
            m = apex - (p0 + d * v[0]); denom = 2.0 * side * float(np.dot(m, nrm))
            if abs(denom) < 1e-9:
                return None
            radius = float(np.dot(m, m)) / denom
            return (p0 + d * v[0] + nrm * (side * radius), radius) if radius > 0 else None

        def res2(v):
            return _circle_residual(pts, p0 + d * v[0] + nrm * (side * v[1]), v[1])
        v = _gauss_newton(res2, np.array([float(np.dot(c0 - p0, d)), r0]))
        return (p0 + d * v[0] + nrm * (side * v[1]), float(v[1])) if v[1] > 0 else None
    if apex_in is not None and apex_out is not None:
        mid = (apex_in + apex_out) / 2.0; nrm = _left(_unit(apex_out - apex_in))

        def res3(v):
            c = mid + nrm * v[0]
            return _circle_residual(pts, c, float(np.linalg.norm(apex_in - c)))
        v = _gauss_newton(res3, np.array([float(np.dot(c0 - mid, nrm))]))
        c = mid + nrm * v[0]
        return c, float(np.linalg.norm(apex_in - c))
    apex = apex_in if apex_in is not None else apex_out
    if apex is not None:
        def res4(v):
            c = np.array([v[0], v[1]])
            return _circle_residual(pts, c, float(np.linalg.norm(apex - c)))
        v = _gauss_newton(res4, c0.copy())
        c = np.array([v[0], v[1]])
        return c, float(np.linalg.norm(apex - c))
    return None


def _snap_arc_anchors(anchors: list[Anchor], points: np.ndarray, settings: FitSettings) -> None:
    """Refit each arc anchor so it joins its neighbours exactly: tangent to an
    adjacent line (the line ends at the tangent foot) and/or through an
    adjacent corner apex -- the way a tangent arc is drawn and then trimmed.
    Falls back to the free circle when the constrained one leaves the band."""

    n = len(points); m = len(anchors)
    if m < 2:
        return
    reach = max(2, int(round(settings.corner_window_long_mm / settings.resample_mm)))
    for k, anchor in enumerate(anchors):
        if anchor.kind != "arc" or anchor.points is None:
            continue
        prev = anchors[(k - 1) % m]; nxt = anchors[(k + 1) % m]
        line_in = prev if (prev is not anchor and prev.kind == "line" and (anchor.i0 - prev.i1) % n <= reach) else None
        line_out = nxt if (nxt is not anchor and nxt.kind == "line" and (nxt.i0 - anchor.i1) % n <= reach) else None
        apex_in = prev.point_out if (prev is not anchor and prev.kind == "corner" and (anchor.i0 - prev.i1) % n <= reach) else None
        apex_out = nxt.point_in if (nxt is not anchor and nxt.kind == "corner" and (nxt.i0 - anchor.i1) % n <= reach) else None
        if line_in is None and line_out is None and apex_in is None and apex_out is None:
            continue
        # both joins first; if that circle leaves the band, one join at a time
        # (the other side then gets a fillet from connect())
        attempts = [(line_in, line_out, apex_in, apex_out)]
        if (line_in is not None or apex_in is not None) and (line_out is not None or apex_out is not None):
            attempts.append((None, line_out, None, apex_out))
            attempts.append((line_in, None, apex_in, None))
        accepted = None
        for li, lo, ai, ao in attempts:
            fit = _refit_arc(anchor, li, lo, ai, ao, settings)
            if fit is None:
                continue
            center, radius = fit
            if not (np.all(np.isfinite(center)) and np.isfinite(radius)) or radius < settings.min_arc_radius_mm:
                continue
            if band_deviation(_arc_effective(anchor.points, center, radius, settings), settings) <= settings.band_mm:
                accepted = (li, lo, ai, ao, center, radius)
                break
        if accepted is None:
            continue
        line_in, line_out, apex_in, apex_out, center, radius = accepted
        anchor.center = center.copy(); anchor.radius = float(radius)
        # new end points: tangent foot on a line, the apex on a corner, else the projected end
        if line_in is not None:
            d = line_in.tangent_out
            foot = line_in.point_in + d * float(np.dot(center - line_in.point_in, d))
            anchor.point_in = foot.copy(); line_in.point_out = foot.copy()
        elif apex_in is not None:
            anchor.point_in = apex_in.copy()
        else:
            anchor.point_in = center + _unit(anchor.point_in - center) * radius
        if line_out is not None:
            d = line_out.tangent_in
            foot = line_out.point_in + d * float(np.dot(center - line_out.point_in, d))
            anchor.point_out = foot.copy(); line_out.point_in = foot.copy()
        elif apex_out is not None:
            anchor.point_out = apex_out.copy()
        else:
            anchor.point_out = center + _unit(anchor.point_out - center) * radius
        anchor.tangent_in = _circle_tangent(anchor, anchor.point_in)
        anchor.tangent_out = _circle_tangent(anchor, anchor.point_out)


def _refine_corner_apexes(anchors: list[Anchor], points: np.ndarray, settings: FitSettings) -> None:
    """A corner that a fitted line reaches sits on that line: intersect the
    adjacent lines (or the flank directions where no line reaches) and move
    the apex there when that is within corner_snap_mm."""

    n = len(points); m = len(anchors)
    if m < 2:
        return
    reach = max(2, int(round(settings.corner_window_long_mm / settings.resample_mm)))
    for k, anchor in enumerate(anchors):
        if anchor.kind != "corner":
            continue
        prev = anchors[(k - 1) % m]; nxt = anchors[(k + 1) % m]
        if prev.kind == "line" and prev.tangent_out is not None and (anchor.i0 - prev.i1) % n <= reach:
            p_b, d_b = prev.point_out, prev.tangent_out
        else:
            p_b, d_b = anchor.point_in, anchor.ref_tangent_in
        if nxt.kind == "line" and nxt.tangent_in is not None and (nxt.i0 - anchor.i0) % n <= reach:
            p_a, d_a = nxt.point_in, nxt.tangent_in
        else:
            p_a, d_a = anchor.point_out, anchor.ref_tangent_out
        if d_b is None or d_a is None:
            continue
        x = _line_intersection(p_b, d_b, p_a, d_a)
        if x is None or float(np.linalg.norm(x - anchor.point_in)) > settings.corner_snap_mm:
            continue
        anchor.point_in = x.copy(); anchor.point_out = x.copy()


def _merge_collinear(anchors: list[Anchor], points: np.ndarray, settings: FitSettings) -> list[Anchor]:
    """Neighbouring line anchors (no corner between them) that one line covers
    inside the band become one line -- a hand draws one long line there, not
    two short ones with a wiggle in between."""

    n = len(points)
    changed = True
    while changed and len(anchors) > 1:
        changed = False
        for k in range(len(anchors)):
            j = (k + 1) % len(anchors)
            a, b = anchors[k], anchors[j]
            if a is b or a.kind != "line" or b.kind != "line":
                continue
            span = _cyclic_slice(points, a.i0, b.i1)
            if len(span) >= n or len(span) < 4:
                continue
            centroid, direction, _residual = _tls_line(span)
            if band_deviation(_line_effective(span, centroid, direction, settings), settings) > settings.band_mm or not _genuinely_straight(span, settings):
                continue
            s0 = float(np.dot(span[0] - centroid, direction)); s1 = float(np.dot(span[-1] - centroid, direction))
            regime = LineRegime(a.i0, b.i1, centroid + direction * s0, centroid + direction * s1)
            anchors[k] = Anchor("line", a.i0, b.i1, regime.start, regime.end, regime.direction, regime.direction, regime)
            del anchors[j]
            changed = True
            break
    return anchors


def _loop_self_intersects(loop: Loop) -> bool:
    try:
        pts = np.vstack([seg.sample(2.0) for seg in loop.segments if seg.length_mm > 1e-9])
        return len(pts) >= 4 and not Polygon(pts).is_valid
    except Exception:  # noqa: BLE001
        return False


def fit_closed_loop(reference: np.ndarray, settings: FitSettings, source: str = "loop",
                    loop_kind: str | None = None) -> LoopFit:
    """Fit one closed loop.  With loop_kind 'outer' or 'hole' the fit is
    biased to the safe side: the contour is shifted edge_bias_mm into the
    panel (outer shrinks, hole grows) and primitives may cut inward_extra_mm
    deeper into the panel than they may stick out toward the wall -- a panel
    slightly too small still fits the boat, slightly too big does not.
    With loop_kind None (default) the fit is symmetric, as before.  A biased
    fit that comes out self-intersecting retries symmetrically -- the safe
    side is an improvement, never a way to lose a panel."""

    fit = _fit_loop_once(reference, settings, source, loop_kind)
    if loop_kind in ("outer", "hole") and _loop_self_intersects(fit.loop):
        fallback = _fit_loop_once(reference, settings, source + "-sym", None)
        if not _loop_self_intersects(fallback.loop):
            fallback.method += " (symmetric fallback: biased fit self-intersected)"
            return fallback
    return fit


def _fit_loop_once(reference: np.ndarray, settings: FitSettings, source: str,
                   loop_kind: str | None) -> LoopFit:
    points = resample_closed(reference, settings.resample_mm)
    if loop_kind in ("outer", "hole") and len(points) >= 4 and Polygon(points).is_valid:
        # a self-intersecting contour has no consistent inside; it falls back
        # to the symmetric fit (and fails validation later, as it should)
        ccw = _loop_ccw(points)
        safe = (1.0 if ccw else -1.0) if loop_kind == "outer" else (-1.0 if ccw else 1.0)
        settings = replace(settings, safe_left=safe)
        points = _offset_closed(points, settings.edge_bias_mm * safe)
    perimeter = _polyline_length(np.vstack([points, points[:1]]))
    min_line = min(settings.min_line_length_mm, max(20.0, settings.small_loop_line_fraction * perimeter))
    anchors, corner_total = build_anchors(points, settings, min_line)
    if not anchors or (len(anchors) == 1 and anchors[0].kind == "arc"):
        return _fit_round_loop(points, settings, source)

    connections: list[Connection] = []
    for k, a in enumerate(anchors):
        b = anchors[(k + 1) % len(anchors)]
        gap = _cyclic_slice(points, a.i1, b.i0) if len(anchors) > 1 else np.vstack([points[a.i1:], points[:a.i0 + 1]])
        connections.append(connect(a, b, gap, settings, f"{source}-c{k}"))

    segments: list[Segment] = []
    corner_joins: list[int] = []
    flagged: list[str] = []
    for k, anchor in enumerate(anchors):
        incoming = connections[(k - 1) % len(anchors)]
        outgoing = connections[k]
        if anchor.kind == "line":
            start, end = incoming.p2, outgoing.p1
            if np.linalg.norm(end - start) > 1e-6:
                segments.append(Segment("LINE", start.copy(), end.copy(), source=f"{source}-line{k}"))
        elif anchor.kind == "arc":
            start, end = incoming.p2, outgoing.p1
            if np.linalg.norm(end - start) > 1e-6:
                segments.append(arc_tangent_through(start, _circle_tangent(anchor, start), end, f"{source}-arc{k}"))
        else:
            # a corner contributes no segment; the join between the segment
            # before and after it is an intentional corner
            if segments:
                corner_joins.append(len(segments) - 1)
            else:
                corner_joins.append(-1)  # resolved after assembly (join before segment 0 == last join)
        if outgoing.kind == "corner":
            corner_joins.append(len(segments) - 1)
        elif outgoing.segments:
            segments.extend(outgoing.segments)
            if outgoing.kind == "flagged":
                flagged.append(f"connection {k}: {outgoing.note}")
    n_seg = len(segments)
    joins = sorted({(j % n_seg) for j in corner_joins if n_seg}) if n_seg else []
    # a join that could not be made tangent (a chord kept for review) is
    # declared a corner so the loop still validates; it stays flagged
    for j in range(n_seg):
        s1, s2 = segments[j], segments[(j + 1) % n_seg]
        if j in joins or s1.length_mm < 1e-9 or s2.length_mm < 1e-9:
            continue
        mismatch = _angle_deg(s1.tangent_end(), s2.tangent_start())
        if mismatch > settings.join_tangent_max_deg:
            joins.append(j)
            flagged.append(f"join after segment {j}: {mismatch:.1f} deg kink declared a corner for review")
    joins = sorted(set(joins))
    loop = Loop(segments, joins, closed=True)
    if not loop.is_ccw:
        loop = loop.reverse()
        loop.intentional_corner_joins = sorted({(n_seg - 2 - j) % n_seg for j in joins})
    max_dev = symmetric_deviation(loop.segments, np.vstack([points, points[:1]]))
    return LoopFit(loop, connections, sum(s.kind == "LINE" for s in loop.segments), sum(s.kind == "ARC" for s in loop.segments),
                   len(loop.intentional_corner_joins), flagged, max_dev, detected_corners=corner_total)


def _fit_round_loop(points: np.ndarray, settings: FitSettings, source: str) -> LoopFit:
    fit = circle_fit(points)
    if fit is not None:
        center, radius = fit
        residual = _arc_effective(points, center, radius, settings)
        if band_deviation(residual, settings) <= settings.band_mm and radius >= settings.min_arc_radius_mm:
            top = center + np.array([radius, 0.0]); bottom = center - np.array([radius, 0.0])
            a1 = Segment("ARC", top.copy(), bottom.copy(), center.copy(), radius, math.pi, source + "-circle-1")
            a2 = Segment("ARC", bottom.copy(), top.copy(), center.copy(), radius, math.pi, source + "-circle-2")
            return LoopFit(Loop([a1, a2], [], closed=True), [], 0, 2, 0, [], float(residual.max()), "circle")
    n = len(points); half = n // 2
    anchors = [
        Anchor("corner", 0, 0, points[0].copy(), points[0].copy(), None, None, None,
               _reference_tangent(points, 0, False, settings), _reference_tangent(points, 0, True, settings)),
        Anchor("corner", half, half, points[half].copy(), points[half].copy(), None, None, None,
               _reference_tangent(points, half, False, settings), _reference_tangent(points, half, True, settings)),
    ]
    c1 = connect(anchors[0], anchors[1], _cyclic_slice(points, 0, half), settings, source + "-r1")
    c2 = connect(anchors[1], anchors[0], _cyclic_slice(points, half, 0), settings, source + "-r2")
    segments = [*c1.segments, *c2.segments]
    loop = Loop(segments, [], closed=True)
    if not loop.is_ccw:
        loop = loop.reverse()
    flagged = [f"round loop: {c.note}" for c in (c1, c2) if c.kind == "flagged"]
    return LoopFit(loop, [c1, c2], sum(s.kind == "LINE" for s in segments), sum(s.kind == "ARC" for s in segments), 0, flagged,
                   symmetric_deviation(loop.segments, np.vstack([points, points[:1]])), "arcs-only")
