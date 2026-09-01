from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from autodeck.conditioning import condition_curves, curve_length, detect_persistent_corners, uniform_resample
from autodeck.config import load_config
from autodeck.flattening import PlanarFlattening, flatten_curves, write_dxf
from autodeck.models import AcceptedSurfaceGrid, FeatureCurve


def _surface(function, *, x0=-50.0, y0=-50.0, size=201, resolution=1.0, mask=None):
    cols = x0 + (np.arange(size) + 0.5) * resolution
    rows = y0 + (np.arange(size) + 0.5) * resolution
    x, y = np.meshgrid(cols, rows)
    accepted = np.ones((size, size), dtype=bool) if mask is None else mask(x, y)
    return AcceptedSurfaceGrid(x0, y0, resolution, accepted, function(x, y).astype(np.float64))


def _rectangle(z_function=lambda x, y: 0.0):
    xy = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 60.0], [0.0, 60.0], [0.0, 0.0]])
    z = np.array([z_function(x, y) for x, y in xy])
    return np.column_stack((xy, z))


def test_flat_rectangle_z_noise_is_reconstructed_from_surface():
    config = load_config(); surface = _surface(lambda x, y: np.zeros_like(x))
    points = _rectangle(); points[:, 2] = np.array([2.0, -2.0, 1.5, -1.5, 2.0])
    conditioned = condition_curves(
        [FeatureCurve("AUTODECK::DECK_PRIMARY_OUTER", "rectangle", points, True, 1.0)],
        surface, 1.0, config,
    )[0]
    assert conditioned.status == "GOOD"
    assert conditioned.metrics["raw_to_smooth_z_deviation_mm"]["rms"] > 1.0
    assert np.max(np.abs(conditioned.smoothed_curve.points_input[:, 2])) < 1e-6
    assert conditioned.metrics["xy_deviation_mm"]["max"] <= 1.5 + 1e-9


def test_sloped_plane_flattening_is_not_drop_z():
    slope = 0.25; surface = _surface(lambda x, y: slope * x)
    points = _rectangle(lambda x, y: slope * x)
    config = load_config()
    conditioned = condition_curves(
        [FeatureCurve("AUTODECK::DECK_PRIMARY_OUTER", "sloped_rectangle", points, True, 1.0)],
        surface, 1.0, config,
    )
    strategy = PlanarFlattening(); context = strategy.fit(surface, config)
    flat = flatten_curves(conditioned, strategy, context, 1.0, config)[0]
    true_long_edge = np.sqrt(100.0 ** 2 + 25.0 ** 2)
    smooth_3d_length = curve_length(conditioned[0].smoothed_curve.points_input)
    drop_z_length = curve_length(conditioned[0].smoothed_curve.points_input * np.array([1.0, 1.0, 0.0]))
    assert np.isclose(curve_length(flat.points_mm), smooth_3d_length, atol=0.05)
    assert curve_length(flat.points_mm) > drop_z_length + 4.0
    assert not np.isclose(true_long_edge, 100.0, atol=0.2)
    assert context.plane_metrics["rotation_from_world_xy_deg"] > 10.0


def test_noisy_circle_conditions_and_exports_circle(tmp_path: Path):
    config = load_config(); surface = _surface(lambda x, y: np.zeros_like(x))
    theta = np.linspace(0.0, 2.0 * np.pi, 121)
    radius = 30.0 + 0.20 * np.sin(17.0 * theta)
    points = np.column_stack((50.0 + radius * np.cos(theta), 50.0 + radius * np.sin(theta), 0.8 * np.sin(13.0 * theta)))
    conditioned = condition_curves(
        [FeatureCurve("AUTODECK::OBSTACLES_PRIMARY", "noisy_circle", points, True, 1.0)],
        surface, 1.0, config,
    )
    strategy = PlanarFlattening(); context = strategy.fit(surface, config)
    flat = flatten_curves(conditioned, strategy, context, 1.0, config)
    metrics = write_dxf(tmp_path / "circle.dxf", flat, 0.5)
    assert metrics["entity_counts"]["CIRCLE"] == 1
    assert metrics["insunits_code"] == 4


def test_persistent_rectangle_corners_are_anchors_and_preserved():
    points = uniform_resample(_rectangle(), True, 2.5)
    anchors, _metrics = detect_persistent_corners(points, True, 2.5, [5.0, 10.0, 20.0, 40.0], 35.0, 2)
    base = points[:-1]
    expected = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 60.0], [0.0, 60.0]])
    anchored = base[anchors, :2]
    for corner in expected:
        assert np.min(np.linalg.norm(anchored - corner, axis=1)) <= 2.5


def test_gradual_z_trend_is_preserved():
    config = load_config(); surface = _surface(lambda x, y: 0.04 * x + 0.01 * y)
    points = _rectangle(lambda x, y: 0.04 * x + 0.01 * y)
    points[:, 2] += np.array([0.5, -0.5, 0.4, -0.4, 0.5])
    item = condition_curves(
        [FeatureCurve("AUTODECK::DECK_PRIMARY_OUTER", "trend", points, True, 1.0)],
        surface, 1.0, config,
    )[0]
    smooth = item.smoothed_curve.points_input
    expected = 0.04 * smooth[:, 0] + 0.01 * smooth[:, 1]
    assert np.sqrt(np.mean((smooth[:, 2] - expected) ** 2)) < 0.15
    assert np.ptp(smooth[:, 2]) > 3.5


def test_floor_side_fit_rejects_adjacent_wall_height():
    def height(x, y):
        return np.where(x >= 0.0, 0.0, 50.0)
    surface = _surface(height, x0=-60.0, y0=-60.0, size=121, mask=lambda x, y: x >= 0.0)
    y = np.linspace(-30.0, 30.0, 41)
    points = np.column_stack((np.zeros_like(y), y, np.full_like(y, 25.0)))
    config = load_config()
    item = condition_curves(
        [FeatureCurve("AUTODECK::SEAMS_HIGH_CONFIDENCE", "floor_wall", points, False, 1.0)],
        surface, 1.0, config,
    )[0]
    assert item.metrics["surface_projection_success_fraction"] > 0.9
    assert np.max(np.abs(item.smoothed_curve.points_input[:, 2])) < 1e-6


def test_hole_nesting_and_winding_survive_condition_and_flatten():
    config = load_config(); surface = _surface(lambda x, y: np.zeros_like(x))
    outer = _rectangle()
    hole = np.array([[30.0, 20.0, 0.0], [30.0, 40.0, 0.0], [70.0, 40.0, 0.0], [70.0, 20.0, 0.0], [30.0, 20.0, 0.0]])
    conditioned = condition_curves([
        FeatureCurve("AUTODECK::DECK_PRIMARY_OUTER", "outer", outer, True, 1.0),
        FeatureCurve("AUTODECK::OBSTACLES_PRIMARY", "hole", hole, True, 1.0),
    ], surface, 1.0, config)
    strategy = PlanarFlattening(); context = strategy.fit(surface, config)
    flattened = flatten_curves(conditioned, strategy, context, 1.0, config)
    assert all(item.metrics["orientation_preserved"] for item in flattened)
    assert all(not item.metrics["validation"]["self_intersection"] for item in flattened)
    assert abs(flattened[0].metrics["flat_signed_area_mm2"]) > abs(flattened[1].metrics["flat_signed_area_mm2"])


def test_coordinate_frame_is_right_handed_and_not_mirrored():
    surface = _surface(lambda x, y: 0.1 * x + 0.05 * y)
    config = load_config(); context = PlanarFlattening().fit(surface, config)
    plane = context.plane_metrics
    assert plane["right_handed_determinant"] > 0.999999
    assert plane["u_dot_world_x"] > 0.0
    assert plane["v_dot_world_y"] > 0.0
    starboard = np.array([[20.0, 0.0, 2.0]])
    port = np.array([[-20.0, 0.0, -2.0]])
    strategy = PlanarFlattening()
    assert strategy.flatten_points(starboard, context)[0, 0] > strategy.flatten_points(port, context)[0, 0]


def test_distortion_outlier_cannot_receive_unqualified_pass():
    surface = _surface(lambda x, y: np.zeros_like(x), size=61)
    surface.floor_height_mm[30, 30] = 20.0
    context = PlanarFlattening().fit(surface, load_config())
    assert context.status in {"WARN", "FAIL"}
    assert context.distortion_metrics["maximum_absolute_strain_percent"] > 10.0
    assert context.warnings


class ConditioningFlatteningTests(unittest.TestCase):
    def test_flat_rectangle_z_noise(self):
        test_flat_rectangle_z_noise_is_reconstructed_from_surface()

    def test_sloped_plane_is_not_drop_z(self):
        test_sloped_plane_flattening_is_not_drop_z()

    def test_noisy_circle_and_primitive_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            test_noisy_circle_conditions_and_exports_circle(Path(directory))

    def test_persistent_corners(self):
        test_persistent_rectangle_corners_are_anchors_and_preserved()

    def test_gradual_z_trend(self):
        test_gradual_z_trend_is_preserved()

    def test_floor_side_wall_rejection(self):
        test_floor_side_fit_rejects_adjacent_wall_height()

    def test_hole_nesting(self):
        test_hole_nesting_and_winding_survive_condition_and_flatten()

    def test_no_mirroring(self):
        test_coordinate_frame_is_right_handed_and_not_mirrored()

    def test_distortion_outlier_warning(self):
        test_distortion_outlier_cannot_receive_unqualified_pass()
