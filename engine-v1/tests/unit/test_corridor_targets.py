from __future__ import annotations

import numpy as np
from shapely import contains_xy
from shapely.geometry import Point, Polygon

from autodeck.curve_fit import _corridor_targets


def _noisy_circle(radius: float, noise: float, count: int = 720, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    angles = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
    r = radius + rng.uniform(-noise, noise, count)
    return np.column_stack([r * np.cos(angles), r * np.sin(angles), np.zeros(count)])


def test_obstacle_corridor_targets_never_drift_into_the_obstacle():
    # Regression: after smoothing, targets on an obstacle were only
    # re-projected when *outside* the grown polygon, so a target dragged
    # inside the obstacle stayed there and the fitter aimed into the
    # forbidden side.
    raw = _noisy_circle(150.0, 1.5)
    reference = _noisy_circle(150.0, 0.2, seed=3)
    targets, info = _corridor_targets(reference, raw, "obstacle", 2.0, 8.0, 6.0)
    assert info["valid"]
    polygon = Polygon(raw[:, :2])
    interior = targets[1:-1]  # endpoints are pinned to the reference by design
    inside = contains_xy(polygon, interior[:, 0], interior[:, 1])
    assert not inside.any(), f"{int(inside.sum())} obstacle targets sit inside the obstacle"
    depths = np.array([polygon.exterior.distance(Point(p[:2])) for p in interior])
    # every interior target sits roughly one offset outside the obstacle
    assert np.percentile(depths, 5) > 1.0


def test_outer_corridor_targets_stay_inside_the_deck():
    raw = _noisy_circle(1500.0, 1.5)
    reference = _noisy_circle(1500.0, 0.2, seed=3)
    targets, info = _corridor_targets(reference, raw, "outer", 2.0, 8.0, 6.0)
    assert info["valid"]
    polygon = Polygon(raw[:, :2])
    interior = targets[1:-1]
    inside = contains_xy(polygon, interior[:, 0], interior[:, 1])
    assert inside.all()
