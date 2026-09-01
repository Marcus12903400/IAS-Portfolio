import math

import numpy as np

from autodeck2.calibration import loop_statistics
from autodeck2.config import load_config
from autodeck2.fitter import FitSettings, arc_tangent_through, fit_closed_loop
from autodeck2.ingest import validate_panel


def _settings():
    return FitSettings.from_config(load_config())


def _noisy(points: np.ndarray, amplitude: float, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return points + rng.uniform(-amplitude, amplitude, points.shape)


def _dense_polygon(vertices: np.ndarray, spacing: float = 2.0) -> np.ndarray:
    chunks = []
    n = len(vertices)
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        count = max(2, int(np.linalg.norm(b - a) / spacing))
        chunks.append(np.linspace(a, b, count, endpoint=False))
    return np.vstack(chunks)


def _arc_points(center, radius, a0, a1, spacing=2.0):
    count = max(3, int(abs(a1 - a0) * radius / spacing))
    angles = np.linspace(a0, a1, count, endpoint=False)
    return np.column_stack([center[0] + radius * np.cos(angles), center[1] + radius * np.sin(angles)])


def _fillet_rect(w, h, r):
    parts = [
        np.linspace([r, 0], [w - r, 0], 60, endpoint=False),
        _arc_points((w - r, r), r, -math.pi / 2, 0),
        np.linspace([w, r], [w, h - r], 40, endpoint=False),
        _arc_points((w - r, h - r), r, 0, math.pi / 2),
        np.linspace([w - r, h], [r, h], 60, endpoint=False),
        _arc_points((r, h - r), r, math.pi / 2, math.pi),
        np.linspace([0, h - r], [0, r], 40, endpoint=False),
        _arc_points((r, r), r, math.pi, 1.5 * math.pi),
    ]
    return np.vstack(parts)


def test_arc_tangent_through_is_exact():
    seg = arc_tangent_through(np.array([0.0, 0.0]), np.array([1.0, 0.0]), np.array([100.0, 100.0]))
    assert seg.kind == "ARC"
    assert abs(seg.radius_mm - 100.0) < 1e-9
    assert np.allclose(seg.center, [0.0, 100.0])
    assert abs(math.degrees(seg.sweep_rad) - 90.0) < 1e-9
    assert np.allclose(seg.tangent_start(), [1.0, 0.0])


def test_sharp_rectangle_becomes_four_lines_with_corners():
    raw = _noisy(_dense_polygon(np.array([[0, 0], [1200, 0], [1200, 600], [0, 600]], float)), 0.8)
    fit = fit_closed_loop(raw, _settings())
    assert fit.line_count == 4 and fit.arc_count == 0
    assert fit.corner_count == 4
    assert fit.max_deviation_mm <= 3.0
    stats = loop_statistics(fit.loop, None, "outer", load_config())
    assert stats["tangent_failure_count"] == 0  # all four joins are declared corners
    assert stats["max_gap_mm"] < 1e-9


def test_fillet_rectangle_becomes_four_lines_and_four_single_arcs():
    raw = _noisy(_fillet_rect(1200.0, 600.0, 80.0), 0.8)
    fit = fit_closed_loop(raw, _settings())
    assert fit.line_count == 4
    assert fit.arc_count == 4
    assert fit.corner_count == 0
    assert all(c.arc_count == 1 for c in fit.connections)
    assert fit.max_deviation_mm <= 3.0
    stats = loop_statistics(fit.loop, None, "outer", load_config())
    assert stats["tangent_failure_count"] == 0
    assert stats["max_tangent_mismatch_non_corner_deg"] < 0.05
    radii = [s.radius_mm for s in fit.loop.segments if s.kind == "ARC"]
    assert all(abs(r - 80.0) < 6.0 for r in radii)


def test_gentle_bow_side_is_one_big_arc_not_a_chain():
    # a 2 m side that is a true 25 m-radius arc bulging 20 mm, between two straight sides
    w, h = 2000.0, 800.0
    sag = 20.0
    radius = (w * w / 4.0 + sag * sag) / (2.0 * sag)  # 25010 mm
    center = np.array([w / 2.0, h + sag - radius])
    half_angle = math.asin((w / 2.0) / radius)
    angles = np.linspace(math.pi / 2 - half_angle, math.pi / 2 + half_angle, 400)  # x = w .. 0: top edge, right->left
    bow = center + radius * np.column_stack([np.cos(angles), np.sin(angles)])
    parts = [np.linspace([0, 0], [w, 0], 400, endpoint=False), np.linspace([w, 0], [w, h], 160, endpoint=False), bow[:-1],
             np.linspace([0, h], [0, 0], 160, endpoint=False)]
    raw = _noisy(np.vstack(parts), 0.8)
    fit = fit_closed_loop(raw, _settings())
    kinds = [s.kind for s in fit.loop.segments]
    assert kinds.count("LINE") == 3
    assert 1 <= kinds.count("ARC") <= 3
    assert fit.max_deviation_mm <= 3.0
    stats = loop_statistics(fit.loop, None, "outer", load_config())
    assert stats["tangent_failure_count"] == 0
    big = max(s.radius_mm for s in fit.loop.segments if s.kind == "ARC")
    assert big > 10000.0


def test_small_foot_rectangle_is_fitted_faithfully():
    raw = _noisy(_dense_polygon(np.array([[0, 0], [106, 0], [106, 63], [0, 63]], float), 1.0), 0.5)
    fit = fit_closed_loop(raw, _settings())
    assert fit.line_count == 4 and fit.corner_count == 4
    extent = np.ptp(fit.loop.points(1.0), axis=0)
    assert np.allclose(extent, [106.0, 63.0], atol=3.0)


def test_round_drain_becomes_a_circle():
    raw = _noisy(_arc_points((0, 0), 45.0, 0, 2 * math.pi, 1.0), 0.5)
    fit = fit_closed_loop(raw, _settings())
    assert fit.method == "circle"
    assert fit.arc_count == 2 and fit.line_count == 0


def test_fitted_loop_passes_strict_ingest_validation():
    raw = _noisy(_fillet_rect(1200.0, 600.0, 80.0), 0.8)
    fit = fit_closed_loop(raw, _settings())
    joins = fit.loop.joins()
    corners = np.array([joins[j]["point_mm"] for j in fit.loop.intentional_corner_joins]) if fit.loop.intentional_corner_joins else np.empty((0, 2))
    panel = validate_panel(1, fit.loop.segments, corners, {"outer": raw, "obstacles": []}, load_config())
    assert panel.valid, panel.problems
    dev = panel.loops_report[0]["stats"]["deviation"]
    assert dev["absolute"]["max_mm"] <= 4.0  # raw points carry +-0.8 mm noise on top of the fit


# ---------------------------------------------------------------------------
# safe-side fitting (loop_kind): slightly small fits the boat, slightly big does not


def _segment_points(fit, spacing=2.0):
    return np.vstack([seg.sample(spacing) for seg in fit.loop.segments])


def test_outer_loop_is_biased_inward_by_edge_bias():
    raw = _noisy(_dense_polygon(np.array([[0, 0], [1200, 0], [1200, 600], [0, 600]], float)), 0.8)
    fit = fit_closed_loop(raw, _settings(), "bias", loop_kind="outer")
    assert fit.line_count == 4 and fit.arc_count == 0
    pts = _segment_points(fit)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    bias = _settings().edge_bias_mm
    assert abs(lo[0] - bias) < 1.2 and abs(lo[1] - bias) < 1.2
    assert abs(hi[0] - (1200 - bias)) < 1.2 and abs(hi[1] - (600 - bias)) < 1.2


def test_hole_loop_is_biased_outward():
    theta = np.linspace(0, 2 * math.pi, 240, endpoint=False)
    hole = np.column_stack([300 + 80 * np.cos(theta), 300 + 80 * np.sin(theta)])
    fit = fit_closed_loop(_noisy(hole, 0.3), _settings(), "hole", loop_kind="hole")
    radii = np.linalg.norm(_segment_points(fit) - [300, 300], axis=1)
    assert radii.mean() > 80.5  # grown by ~edge_bias so the cut-out clears the obstacle


def test_wall_recess_is_bridged_straight_but_intrusion_is_respected():
    """A pocket in the wall (contour dips AWAY from the deck) is safely skipped by
    one straight line; a wall bulge INTO the deck must not be cut through."""

    def square_with_bottom_bump(depth):
        vertices = np.array([[0, 0], [1000, 0], [1000, 1000], [0, 1000]], float)
        pts = _dense_polygon(vertices, spacing=4.0)
        on_bottom = (pts[:, 1] < 0.5) & (pts[:, 0] > 400) & (pts[:, 0] < 550)
        pts = pts.copy()
        pts[on_bottom, 1] += depth  # +y = into the CCW square's interior
        return pts

    settings = _settings()
    recess = fit_closed_loop(square_with_bottom_bump(-4.5), settings, "recess", loop_kind="outer")
    assert recess.line_count == 4 and recess.arc_count == 0 and not recess.flagged
    pts = _segment_points(recess)
    assert pts[:, 1].min() > -0.6  # never outside the detected border

    intrusion = fit_closed_loop(square_with_bottom_bump(4.5), settings, "intrusion", loop_kind="outer")
    inside = _segment_points(intrusion)
    bump = inside[(inside[:, 0] > 405) & (inside[:, 0] < 545)]
    # the fitted edge must go around the bulge (stay on the deck side of it), not through its base
    assert bump[:, 1].max() > 3.0
