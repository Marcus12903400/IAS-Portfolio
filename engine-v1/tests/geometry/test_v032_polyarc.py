from __future__ import annotations

import numpy as np
import ezdxf
import pytest
from shapely.geometry import Polygon

from autodeck.config import load_config
from autodeck.curve_fit import fit_manufacturing_curve, map_points_to_indices
from autodeck.dxf_export import write_v032_dxfs
from autodeck.models import ConditionedCurve, FeatureCurve, FlattenedCurve
from autodeck.polyarc import (
    PolyarcPrimitive,
    SharpCornerRequiresReview,
    _equal_distance_biarc,
    _fit_recursive,
    _FitContext,
    fit_polyarc_curve,
)
from autodeck.v032_reports import make_landmark_dimension_audit


def _flat(
    points: np.ndarray,
    layer: str = "AUTODECK::SEAMS_HIGH_CONFIDENCE",
    closed: bool = False,
) -> FlattenedCurve:
    raw = FeatureCurve(layer, "polyarc-synthetic", points.copy(), closed, 1.0)
    smooth = FeatureCurve(layer, "polyarc-synthetic-conditioned", points.copy(), closed, 1.0)
    conditioned = ConditionedCurve(raw, "raw-polyarc", smooth, "smooth-polyarc", points.copy(), [], "GOOD")
    return FlattenedCurve(conditioned, "flat-polyarc", points.copy(), "FLAT_RAW_TEST", closed, "GOOD")


def _fit(points: np.ndarray, closed: bool = False, layer: str = "AUTODECK::SEAMS_HIGH_CONFIDENCE"):
    config = load_config()
    spline = fit_manufacturing_curve(_flat(points, layer, closed), 1, config)
    return fit_polyarc_curve(spline, config["polyarc_fit"])


def test_equal_distance_biarc_has_exact_g1_join():
    primitives = _equal_distance_biarc(
        np.array([1.0, 0.0]),
        np.array([0.0, 1.0]),
        np.array([0.0, 1.0]),
        np.array([-1.0, 0.0]),
        0,
        100,
        0,
    )
    assert primitives is not None
    assert len(primitives) == 2
    assert np.linalg.norm(primitives[0].end - primitives[1].start) < 1e-10
    assert np.dot(primitives[0].tangent_end(), primitives[1].tangent_start()) > 1.0 - 1e-12


def test_two_metre_noisy_straight_edge_is_one_line():
    random = np.random.default_rng(32)
    x = np.linspace(0.0, 2000.0, 801)
    y = random.uniform(-1.0, 1.0, len(x))
    y[[0, -1]] = 0.0
    result = _fit(np.column_stack([x, y, np.zeros(len(x))]))
    assert result.primitive_count == 1
    assert result.line_count == 1
    assert result.max_deviation_mm <= 3.0


def test_large_radius_circular_edge_is_one_true_arc():
    radius = 3000.0
    angles = np.linspace(-0.32, 0.32, 601)
    points = np.column_stack(
        [radius * np.sin(angles), radius * (1.0 - np.cos(angles)), np.zeros(len(angles))]
    )
    result = _fit(points)
    assert result.primitive_count == 1
    assert result.arc_count == 1
    assert abs(float(result.primitives[0].radius_mm) - radius) < 0.1
    assert result.max_deviation_mm <= 3.0


def test_closed_rectangle_preserves_four_hard_corners_and_closure():
    first = np.column_stack([np.linspace(0, 400, 81), np.zeros(81)])
    second = np.column_stack([np.full(41, 400), np.linspace(5, 200, 41)])
    third = np.column_stack([np.linspace(395, 0, 80), np.full(80, 200)])
    fourth = np.column_stack([np.zeros(40), np.linspace(195, 0, 40)])
    points_2d = np.vstack([first, second, third, fourth])
    points = np.column_stack([points_2d, np.zeros(len(points_2d))])
    result = _fit(points, True, "AUTODECK::DECK_PRIMARY_OUTER")
    assert len(result.hard_corner_indices) == 4
    assert result.primitive_count == 4
    assert result.line_count == 4
    assert result.metrics["closure_error_mm"] == 0.0
    assert result.metrics["forbidden_side_violations"] == 0
    assert not result.metrics["self_intersection"]


def test_polyarc_tolerance_cannot_be_configured_above_three_mm():
    points = np.column_stack([np.linspace(0.0, 100.0, 21), np.zeros(21), np.zeros(21)])
    config = load_config()
    spline = fit_manufacturing_curve(_flat(points), 1, config)
    settings = dict(config["polyarc_fit"])
    settings["absolute_fit_tolerance_mm"] = 3.01
    try:
        fit_polyarc_curve(spline, settings)
    except ValueError as error:
        assert "may not exceed 3.000 mm" in str(error)
    else:
        raise AssertionError("V0.3.2 accepted a tolerance above the locked 3.000 mm limit")


def test_v032_dxf_writes_true_arcs_and_closed_bulged_polyline(tmp_path):
    radius = 350.0
    angles = np.linspace(0.0, 2.0 * np.pi, 721, endpoint=False)
    points = np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(len(angles))])
    config = load_config()
    spline = fit_manufacturing_curve(
        _flat(points, "AUTODECK::DECK_PRIMARY_OUTER", True), 1, config,
    )
    polyarc = fit_polyarc_curve(spline, config["polyarc_fit"])
    result = write_v032_dxfs(tmp_path, [polyarc], [spline], config)
    assert result["status"] == "GOOD"
    assert result["polyarc"]["one_entity_per_curve"]
    assert result["polyarc"]["all_closed_perimeters_closed"]
    assert result["linearc"]["line_arc_only"]
    document = ezdxf.readfile(tmp_path / "flattened_curves_polyarc.dxf")
    entities = list(document.modelspace())
    assert len(entities) == 1
    assert entities[0].dxftype() == "LWPOLYLINE"
    assert entities[0].closed
    assert any(abs(float(vertex[4])) > 1e-9 for vertex in entities[0].get_points("xyseb"))


def test_line_tangent_arc_line_returns_three_longest_primitives_not_ten():
    angles = np.linspace(-0.5 * np.pi, 0.0, 101)
    chain = np.vstack([
        np.column_stack([np.linspace(0.0, 300.0, 121), np.zeros(121)]),
        np.column_stack([300.0 + 100.0 * np.cos(angles[1:]), 100.0 + 100.0 * np.sin(angles[1:])]),
        np.column_stack([np.full(120, 400.0), np.linspace(102.5, 400.0, 120)]),
    ])
    result = _fit(np.column_stack([chain, np.zeros(len(chain))]))
    assert [item.kind for item in result.primitives] == ["LINE", "ARC", "LINE"]
    assert result.primitive_count == 3
    assert result.max_deviation_mm <= 3.0
    assert result.metrics["maximum_smooth_tangent_mismatch_deg"] <= 0.05


def test_organic_edge_and_s_curve_are_low_count_g1_polyarcs():
    x = np.linspace(0.0, 2000.0, 501)
    organic = np.column_stack([
        x,
        35.0 * np.sin(x / 300.0) + 8.0 * np.sin(x / 95.0),
        np.zeros(len(x)),
    ])
    organic_result = _fit(organic)
    assert organic_result.primitive_count <= 12
    assert organic_result.max_deviation_mm <= 3.0
    assert organic_result.metrics["maximum_smooth_tangent_mismatch_deg"] <= 0.1

    sx = np.linspace(0.0, 1600.0, 401)
    s_curve = np.column_stack([
        sx,
        80.0 * np.sin(2.0 * np.pi * sx / 1600.0),
        np.zeros(len(sx)),
    ])
    s_result = _fit(s_curve)
    signed_sweeps = [item.signed_sweep_rad for item in s_result.primitives if item.kind == "ARC"]
    assert any(value > 0.0 for value in signed_sweeps)
    assert any(value < 0.0 for value in signed_sweeps)
    assert s_result.primitive_count <= 10
    assert s_result.metrics["maximum_smooth_tangent_mismatch_deg"] <= 0.1


def test_notch_and_obstacle_keep_signed_forbidden_side_safety():
    notch_2d = np.asarray([
        [0, 0], [240, 0], [240, 140], [150, 140],
        [150, 80], [90, 80], [90, 140], [0, 140],
    ], dtype=float)
    notch = _fit(
        np.column_stack([notch_2d, np.zeros(len(notch_2d))]),
        True,
        "AUTODECK::DECK_PRIMARY_OUTER",
    )
    assert notch.metrics["forbidden_side_violations"] == 0
    assert notch.line_count == 8
    assert len(notch.hard_corner_indices) == 8

    angles = np.linspace(0.0, 2.0 * np.pi, 181, endpoint=False)
    obstacle_points = np.column_stack([
        100.0 * np.cos(angles),
        100.0 * np.sin(angles),
        np.zeros(len(angles)),
    ])
    obstacle = _fit(obstacle_points, True, "AUTODECK::OBSTACLES_PRIMARY")
    assert obstacle.metrics["forbidden_side_violations"] == 0
    assert not obstacle.metrics["self_intersection"]
    assert obstacle.metrics["closure_error_mm"] == 0.0


def test_2_9_mm_is_accepted_as_one_line_but_3_1_mm_forces_a_split():
    def alternating(amplitude: float) -> np.ndarray:
        x = np.linspace(0.0, 500.0, 101)
        y = np.zeros(101)
        y[5:-5:10] = amplitude
        y[10:-5:10] = -amplitude
        return np.column_stack([x, y, np.zeros(len(x))])

    accepted = _fit(alternating(2.9))
    split = _fit(alternating(3.1))
    assert accepted.primitive_count == 1
    assert accepted.line_count == 1
    assert accepted.max_deviation_mm <= 3.0
    assert split.primitive_count > 1
    assert split.max_deviation_mm <= 3.0


def test_landmark_dimension_audit_separates_development_from_cam_fit():
    corners_2d = np.asarray([[0, 0], [400, 0], [400, 200], [0, 200]], dtype=float)
    points = np.column_stack([corners_2d, np.zeros(len(corners_2d))])
    result = _fit(points, True, "AUTODECK::DECK_PRIMARY_OUTER")
    base_3d = points.copy()
    base_3d[:, 0] *= 1.01
    audit = make_landmark_dimension_audit(
        "rectangle",
        result.hard_corner_indices,
        base_3d,
        points,
        result,
    )
    assert audit["hard_corner_count"] == 4
    assert len(audit["pair_dimensions"]) == 6
    assert {item["relationship"] for item in audit["pair_dimensions"]} == {
        "neighboring", "non-neighboring",
    }
    assert max(abs(item["flat_to_polyarc_change_mm"]) for item in audit["pair_dimensions"]) < 1e-12
    assert any(abs(item["base_to_flat_change_mm"]) > 0.1 for item in audit["pair_dimensions"])


# --- Regression tests: tangent-losing fallback bug (see polyarc.py _fit_recursive) ---


def _blocked_context(hard_start: bool, hard_end: bool) -> _FitContext:
    """A minimal 2-point interval wrapped in a forbidden region that covers
    the whole local area, guaranteeing _interval_candidates finds zero valid
    LINE/ARC/biarc candidates regardless of geometry -- deterministically
    forcing _fit_recursive's base-case fallback.
    """

    points = np.array([[0.0, 0.0], [10.0, 0.0]])
    tangents = np.array([[1.0, 0.0], [1.0, 0.0]])
    return _FitContext(
        raw_full=points,
        source=points,
        validation_source=points,
        guide_to_validation_index=np.array([0, 1]),
        targets=points,
        tangents=tangents,
        source_index_offset=0,
        logical_span_index=0,
        closed_reference=False,
        feature_side="obstacle",
        tolerance_mm=3.0,
        tangent_max_deg=0.10,
        sampling_mm=0.5,
        max_depth=28,
        allowed_region=None,
        forbidden_region=Polygon([(-100, -100), (100, -100), (100, 100), (-100, 100)]),
    )


def test_fit_recursive_raises_review_instead_of_untangented_chord_when_smooth_join_required():
    context = _blocked_context(hard_start=False, hard_end=False)
    with pytest.raises(SharpCornerRequiresReview):
        _fit_recursive(context, 0, 1, hard_start=False, hard_end=False)


def test_fit_recursive_still_returns_plain_chord_when_no_tangent_required():
    context = _blocked_context(hard_start=True, hard_end=True)
    primitives = _fit_recursive(context, 0, 1, hard_start=True, hard_end=True)
    assert len(primitives) == 1
    assert primitives[0].kind == "LINE"
    assert not primitives[0].sharp_corner_review


def test_sharp_corner_fallback_flags_review_instead_of_silent_kink(monkeypatch):
    # Force every interval candidate search to fail, as if no LINE/ARC/biarc
    # could satisfy the required tangent state anywhere in the span -- the
    # exact condition that used to fall through to an untangented chord.
    monkeypatch.setattr("autodeck.polyarc._interval_candidates", lambda *args, **kwargs: [])

    x = np.linspace(0.0, 200.0, 21)
    points = np.column_stack([x, np.zeros(21), np.zeros(21)])
    config = load_config()
    spline = fit_manufacturing_curve(_flat(points), 1, config)
    settings = dict(config["polyarc_fit"])
    settings["use_longest_valid_chain"] = False
    result = fit_polyarc_curve(spline, settings)

    assert result.primitive_count > 0
    assert all(primitive.sharp_corner_review for primitive in result.primitives)
    assert any("SHARP_CORNER_REQUIRES_REVIEW" in warning for warning in result.warnings)
    assert result.status != "TEST_GEOMETRY"
    assert result.metrics["sharp_corner_review_join_count"] > 0
    # The core invariant: nothing exceeding the hard tangent limit may pass
    # through unflagged.
    tangent_max = float(settings["tangent_max_deg"])
    for join in result.joins:
        if join.tangent_mismatch_deg is not None and join.tangent_mismatch_deg > tangent_max:
            assert join.sharp_corner_review


# --- Regression tests: hard-corner index-aliasing bug (see curve_fit.map_points_to_indices) ---


def test_map_points_to_indices_resolves_by_coordinate_not_modulo():
    # A target array shorter than any naive "source index" would assume --
    # modulo indexing would silently wrap onto an unrelated point instead of
    # matching by actual location.
    target = np.array([[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]])
    points = np.array([[10.0, 0.0], [0.0, 0.0]])  # deliberately out of index order
    indices = map_points_to_indices(points, target, context="test")
    assert indices == [0, 2]


def test_map_points_to_indices_fails_loudly_when_no_match_within_tolerance():
    target = np.array([[0.0, 0.0], [5.0, 0.0]])
    points = np.array([[500.0, 500.0]])  # nowhere near the target array
    with pytest.raises(ValueError):
        map_points_to_indices(points, target, tolerance_mm=1.0, context="test")


def test_landmark_dimension_audit_rejects_mismatched_length_arrays():
    corners_2d = np.asarray([[0, 0], [400, 0], [400, 200], [0, 200]], dtype=float)
    points = np.column_stack([corners_2d, np.zeros(len(corners_2d))])
    result = _fit(points, True, "AUTODECK::DECK_PRIMARY_OUTER")
    mismatched_base_3d = points[:-1]  # one point short of flat_raw_points_mm
    with pytest.raises(ValueError):
        make_landmark_dimension_audit(
            "rectangle", result.hard_corner_indices, mismatched_base_3d, points, result,
        )


# --- Signed safety: raster noise is not a wall, a real feature still is ---


def _raster_noisy_rectangle(notch_depth_mm: float) -> np.ndarray:
    """A 1200 x 400 closed deck outline whose bottom edge carries inward
    raster stair-step notches (3 mm wide, notch_depth_mm deep) every 10 mm --
    the shape a scanned straight wall actually produces."""

    bottom: list[list[float]] = []
    x = 0.0
    while x < 1200.0:
        bottom.append([x, 0.0])
        bottom.append([min(x + 7.0, 1200.0), 0.0])
        bottom.append([min(x + 7.0, 1200.0), notch_depth_mm])
        bottom.append([min(x + 10.0, 1200.0), notch_depth_mm])
        x += 10.0
    bottom.append([1200.0, 0.0])
    right = [[1200.0, y] for y in np.linspace(5.0, 400.0, 80)]
    top = [[x, 400.0] for x in np.linspace(1195.0, 0.0, 240)]
    left = [[0.0, y] for y in np.linspace(395.0, 5.0, 79)]
    points_2d = np.asarray(bottom + right + top + left, dtype=float)
    return np.column_stack([points_2d, np.zeros(len(points_2d))])


def _fit_with(points: np.ndarray, layer: str, closed: bool, **overrides):
    config = load_config()
    spline = fit_manufacturing_curve(_flat(points, layer, closed), 1, config)
    settings = dict(config["polyarc_fit"])
    settings.update(overrides)
    return fit_polyarc_curve(spline, settings)


def test_raster_noise_notches_are_not_treated_as_a_wall():
    # 1.5 mm raster notches on a straight wall: a line through the middle of
    # the noise band is the intended answer and must carry zero violations.
    result = _fit_with(_raster_noisy_rectangle(1.5), "AUTODECK::DECK_PRIMARY_OUTER", True)
    assert result.metrics["forbidden_side_violations"] == 0
    assert result.status != "INVALID"
    penetration = result.metrics["wall_side_penetration"]
    assert penetration["maximum_mm"] <= result.metrics["signed_safety_penetration_allowance_mm"]
    assert penetration["beyond_allowance_count"] == 0
    # The bottom edge must not have shattered into per-notch fragments.
    assert result.primitive_count <= 12


def test_penetration_allowance_separates_noise_from_crossing():
    # Documents the semantics this change establishes: a sample 1.0 mm beyond
    # the raw contour is a violation under the historical 0.35 mm allowance
    # (which is why real scans could never validate) and is noise under the
    # 2.0 mm default; a sample 10 mm beyond is a crossing under both.
    from shapely.geometry import Polygon
    from autodeck.polyarc import _wall_side_penetration_stats
    square = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    noise_sample = np.array([[50.0, -1.0]])
    crossing_sample = np.array([[50.0, -10.0]])
    assert _wall_side_penetration_stats(noise_sample, square, "outer", 0.35)["beyond_allowance_count"] == 1
    assert _wall_side_penetration_stats(noise_sample, square, "outer", 2.0)["beyond_allowance_count"] == 0
    assert _wall_side_penetration_stats(crossing_sample, square, "outer", 0.35)["beyond_allowance_count"] == 1
    assert _wall_side_penetration_stats(crossing_sample, square, "outer", 2.0)["beyond_allowance_count"] == 1
    # Obstacle semantics are the mirror image: inside the obstacle is forbidden.
    inside_obstacle = np.array([[50.0, 1.0]])
    assert _wall_side_penetration_stats(inside_obstacle, square, "obstacle", 2.0)["sample_count"] == 1
    assert _wall_side_penetration_stats(inside_obstacle, square, "outer", 2.0)["sample_count"] == 0


def test_wide_deep_feature_is_still_a_hard_forbidden_crossing():
    # A 60 mm deep, 60 mm wide inward notch is a real feature (a wall step),
    # not noise: the fitter must keep its corners rather than shortcut across.
    notch_2d = np.asarray([
        [0, 0], [240, 0], [240, 140], [150, 140],
        [150, 80], [90, 80], [90, 140], [0, 140],
    ], dtype=float)
    points = np.column_stack([notch_2d, np.zeros(len(notch_2d))])
    result = _fit_with(points, "AUTODECK::DECK_PRIMARY_OUTER", True)
    assert result.metrics["forbidden_side_violations"] == 0
    assert result.line_count == 8
    # And a shortcut chord across the notch would penetrate 60 mm: prove the
    # allowance does not admit it by auditing that chord directly.
    from shapely.geometry import Polygon
    from autodeck.polyarc import _wall_side_penetration_stats
    shortcut = np.array([[90.0, 140.0], [120.0, 140.0], [150.0, 140.0]])
    stats = _wall_side_penetration_stats(shortcut, Polygon(notch_2d), "outer", 2.0)
    assert stats["beyond_allowance_count"] > 0
    # the chord midpoint is 30 mm from the nearest notch wall
    assert stats["maximum_mm"] > 20.0


def test_chain_fitter_localizes_an_unfittable_interval_instead_of_dropping_the_span(monkeypatch):
    # Make exactly one adjacent-sample interval unfittable; the rest of the
    # span must still be fitted, and the kink must be confined to that spot.
    import autodeck.polyarc as polyarc_module
    original = polyarc_module._interval_candidates
    poisoned: dict[str, tuple[int, int] | None] = {"interval": None}

    def failing(context, local_start, local_end, *args, **kwargs):
        if poisoned["interval"] is None:
            # poison an interior interval of the first span we see
            poisoned["interval"] = (max(1, len(context.source) // 2), max(1, len(context.source) // 2) + 1)
        if (local_start, local_end) == poisoned["interval"] or (
            local_start <= poisoned["interval"][0] and local_end >= poisoned["interval"][1]
        ):
            return []
        return original(context, local_start, local_end, *args, **kwargs)

    original_metrics = polyarc_module._candidate_metrics

    def failing_metrics(context, local_start, local_end, *args, **kwargs):
        # The soft-join projection repair scores through _candidate_metrics
        # directly; poison the same interval there so the fallback is reached.
        item = original_metrics(context, local_start, local_end, *args, **kwargs)
        if poisoned["interval"] is not None and (local_start, local_end) == poisoned["interval"]:
            item.forbidden_violations = 1
        return item

    monkeypatch.setattr(polyarc_module, "_interval_candidates", failing)
    monkeypatch.setattr(polyarc_module, "_candidate_metrics", failing_metrics)
    x = np.linspace(0.0, 400.0, 81)
    points = np.column_stack([x, np.zeros(81), np.zeros(81)])
    result = _fit_with(points, "AUTODECK::SEAMS_HIGH_CONFIDENCE", False)
    review = [p for p in result.primitives if p.sharp_corner_review]
    # the local repair routes through the two reference samples: at most
    # target->ref, ref->ref, ref->target
    assert 1 <= len(review) <= 3
    assert result.status == "REVIEW"
    assert result.metrics["maximum_join_gap_mm"] <= 1e-7
    # Everything outside the poisoned interval is still real fitted geometry.
    assert result.primitive_count < 10
    assert result.metrics["recursive_fit_diagnostics"].get("sharp_corner_review_local_interval_count") == 1


def test_negative_safety_allowance_is_rejected():
    with pytest.raises(ValueError):
        _fit_with(
            _raster_noisy_rectangle(1.5), "AUTODECK::DECK_PRIMARY_OUTER", True,
            signed_safety_penetration_allowance_mm=-1.0,
        )
