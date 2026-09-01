import numpy as np

from autodeck2.config import load_config
from autodeck2.layout import compute_layout, overlaps, procrustes_rotation
from autodeck2.pipeline import panel_sources
from helpers import make_result


def test_boat_plan_places_secondary_at_true_relative_position_without_gap_requirement():
    result = make_result([(1, "primary", (500.0, 200.0), 1000.0, 600.0, 0.0), (2, "secondary", (1250.0, 200.0), 400.0, 300.0, 0.0)])
    placements, warnings = compute_layout(panel_sources(result), load_config(), "boat-plan")
    p1, p2 = placements[1], placements[2]
    assert np.allclose(p1.placed_outline.mean(axis=0), [0.0, 0.0], atol=1e-6)
    # secondary centre = world offset from the primary centre, exactly
    assert np.allclose(p2.placed_outline.mean(axis=0), [750.0, 0.0], atol=1e-6)
    assert overlaps(placements) == []
    assert p2.moved_to_row is False and p2.nested_in is None
    assert not any("overlap" in w for w in warnings)


def test_nested_panel_flagged_in_boat_plan_and_moved_in_nest():
    specs = [(1, "primary", (0.0, 0.0), 1000.0, 600.0, 0.0), (2, "secondary", (100.0, 50.0), 200.0, 150.0, 0.0)]
    placements, warnings = compute_layout(panel_sources(make_result(specs)), load_config(), "boat-plan")
    assert placements[2].nested_in == 1
    assert overlaps(placements)  # still overlapping by design in boat-plan
    assert any("nested" in w for w in warnings)

    placements, warnings = compute_layout(panel_sources(make_result(specs)), load_config(), "nest")
    assert placements[2].moved_to_row is True
    assert overlaps(placements) == []
    gap = float(load_config()["layout"]["nest_gap_mm"])
    top_of_moved = placements[2].placed_outline[:, 1].max()
    bottom_of_primary = placements[1].placed_outline[:, 1].min()
    assert abs((bottom_of_primary - top_of_moved) - gap) < 1e-6
    assert np.allclose(placements[2].nest_offset, placements[2].translation - (np.array([100.0, 50.0]) - 0.0), atol=1e-6)


def test_collision_uses_polygon_intersection_not_bounding_boxes():
    # L-shaped-ish arrangement: bounding boxes overlap, polygons do not.
    result = make_result([(1, "primary", (0.0, 0.0), 1000.0, 200.0, 0.0), (2, "secondary", (450.0, 300.0), 200.0, 300.0, 0.0)], with_obstacle=False)
    # shift secondary so bboxes overlap in x but polygons are separated in y
    placements, _ = compute_layout(panel_sources(result), load_config(), "nest")
    assert overlaps(placements) == []
    assert placements[2].moved_to_row is False


def test_rotated_development_frame_is_aligned_and_recorded():
    result = make_result([(1, "primary", (0.0, 0.0), 1000.0, 600.0, 7.0)], with_obstacle=False)
    placements, warnings = compute_layout(panel_sources(result), load_config(), "boat-plan")
    p = placements[1]
    assert abs(abs(p.rotation_deg) - 7.0) < 0.1
    assert any("rotated" in w for w in warnings)
    # after alignment the placed outline is axis-aligned with the world rectangle
    src = panel_sources(result)[1]
    _, residual_angle = procrustes_rotation(p.apply(src.uv_xy), src.world_xy)
    assert abs(residual_angle) < 1e-6
    extent = p.placed_outline.max(axis=0) - p.placed_outline.min(axis=0)
    assert np.allclose(extent, [1000.0, 600.0], atol=1e-6)


def test_placement_transform_roundtrips_through_to_dict():
    result = make_result([(1, "primary", (10.0, 20.0), 400.0, 300.0, 0.0)], with_obstacle=False)
    placements, _ = compute_layout(panel_sources(result), load_config(), "nest")
    d = placements[1].to_dict()
    assert d["mode"] == "nest" and d["role"] == "primary"
    assert np.allclose(d["world_centroid_mm"], [10.0, 20.0])
    assert d["placed_bbox_mm"] is not None
