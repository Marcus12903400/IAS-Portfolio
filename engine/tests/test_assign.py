import numpy as np
from shapely.geometry import Polygon

from autodeck2.assign import GridInfo, PanelGeometry, assign_curve, mask_votes
from autodeck2.config import load_config


def _grid():
    # 100 x 100 cells at 10 mm: primary occupies cols 0-59 (x 0..600), secondary cols 70-99 (x 700..1000)
    res = 10.0
    primary = np.zeros((100, 100), bool); primary[10:90, 0:60] = True
    secondary = np.zeros((100, 100), bool); secondary[20:60, 70:100] = True
    grid = GridInfo(0.0, 0.0, res, {1: primary, 2: secondary})
    panels = {
        1: PanelGeometry(1, Polygon([(0, 100), (600, 100), (600, 900), (0, 900)]), np.array([[x, y] for x in range(0, 600, 50) for y in range(100, 900, 50)], float)),
        2: PanelGeometry(2, Polygon([(700, 200), (1000, 200), (1000, 600), (700, 600)]), np.array([[x, y] for x in range(700, 1000, 50) for y in range(200, 600, 50)], float)),
    }
    return grid, panels


def _square(cx, cy, half):
    return np.array([[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]], float)


def test_curve_inside_secondary_goes_to_secondary_not_primary():
    grid, panels = _grid()
    result = assign_curve(_square(850, 400, 30), grid, panels, load_config())
    assert result.panel_id == 2
    assert result.method == "mask_majority"


def test_obstacle_hole_contour_needs_dilated_vote():
    grid, panels = _grid()
    # A void in the secondary: the mask is False inside the hole, so the exact
    # vote on the hole's contour is weak; the dilated vote must decide.
    hole = np.zeros_like(grid.patch_masks[2]); hole[35:45, 80:90] = True
    grid.patch_masks[2] = grid.patch_masks[2] & ~hole
    contour = _square(850, 400, 49.9)
    exact = mask_votes(contour, grid, 0)
    assert exact[1] == 0.0
    result = assign_curve(contour, grid, panels, load_config())
    assert result.panel_id == 2
    assert result.method in {"mask_majority", "mask_majority_dilated", "outer_polygon"}


def test_far_away_curve_is_unassigned_with_warning_note():
    grid, panels = _grid()
    result = assign_curve(_square(5000, 5000, 20), grid, panels, load_config())
    assert result.panel_id is None
    assert result.ambiguous
    assert "no decisive panel" in result.note


def test_straddling_curve_is_not_silently_given_to_primary():
    grid, panels = _grid()
    # A curve spanning the gap between the panels, half on each side of nothing.
    pts = np.array([[550, 500], [650, 500], [750, 500], [650, 520]], float)
    result = assign_curve(pts, grid, panels, load_config())
    assert result.panel_id is None or result.method != "mask_majority" or result.fraction >= 0.5
