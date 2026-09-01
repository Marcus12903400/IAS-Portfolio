import numpy as np

from autodeck2 import v1compat
from autodeck2.config import load_config
from autodeck2.engine import MappedCurve
from autodeck2.layout import compute_layout
from autodeck2.pipeline import panel_sources
from autodeck2.teak import _domain_polygon, generate_teak
from helpers import make_result


def test_domain_polygon_keeps_largest_piece_of_a_self_touching_outline():
    # A figure-eight outline: v1's clean_polygon returns None (MultiPolygon);
    # the teak domain must fall back to the largest lobe instead of giving up.
    figure_eight = np.array([[0, 0], [200, 0], [200, 100], [0, 100], [0, 0.0001], [-150, 100], [-150, 0]], float)
    assert v1compat.clean_polygon(figure_eight) is None or _domain_polygon(figure_eight) is not None
    polygon = _domain_polygon(figure_eight)
    assert polygon is not None and polygon.area > 10000


def test_small_noisy_secondary_panel_still_gets_teak():
    result = make_result([(1, "primary", (0.0, 0.0), 2000.0, 1000.0, 0.0), (2, "secondary", (1400.0, 0.0), 190.0, 190.0, 0.0)], with_obstacle=False)
    # replace panel 2's outline with a self-touching noisy version of the square
    outer = next(c for c in result.curves if c.panel_id == 2 and c.family == "OUTER")
    noisy = np.array([[-95, -95], [95, -95], [95, 95], [-95, 95], [-95, -95 + 1e-4], [-120, 20], [-120, 0]], float)
    result.curves[result.curves.index(outer)] = MappedCurve(outer.curve_id, "OUTER", outer.source_layer, outer.name, True, 1.0,
                                                          np.column_stack([noisy + [1400.0, 0.0], np.zeros(len(noisy))]), noisy, 2, {})
    placements, _ = compute_layout(panel_sources(result), load_config(), "boat-plan")
    lines, info = generate_teak(result, placements, v1compat.load_v1_config()["pattern"])
    assert info["spacing_mm"] == 63.5
    assert len(lines[1]) > 10
    assert len(lines[2]) >= 2  # 190 mm across at 63.5 mm pitch


def test_diamond_and_hex_patterns_generate_and_follow_the_panel():
    from autodeck2.teak import generate_pattern

    result = make_result([(1, "primary", (0.0, 0.0), 2000.0, 1000.0, 0.0), (2, "secondary", (1400.0, 0.0), 190.0, 190.0, 0.0)], with_obstacle=False)
    placements, _ = compute_layout(panel_sources(result), load_config(), "boat-plan")
    settings = v1compat.load_v1_config()["pattern"]
    for pattern, dimension in (("diamond", "diamond_long_diagonal_mm"), ("hex", "hex_across_flats_mm")):
        lines, info = generate_pattern(result, placements, settings, pattern)
        assert info["pattern"] == pattern
        assert dimension in info["dimensions_mm"]
        assert len(lines[1]) > 20            # the 2 m primary is full of lattice
        assert len(lines.get(2, [])) >= 1    # the small far-away panel is reached too
        for p0, p1 in lines[1]:
            assert np.isfinite(p0).all() and np.isfinite(p1).all()


def test_pattern_for_domains_clips_to_given_loops_and_honours_offsets():
    from shapely.geometry import Polygon
    from autodeck2.teak import pattern_for_domains

    settings = v1compat.load_v1_config()["pattern"]
    boat_plan = np.array([[0, 0], [2000, 0], [2000, 1000], [0, 1000]], float)
    offset = np.array([0.0, -1500.0])
    domain_placed = Polygon(np.array([[300, 300], [1200, 300], [1200, 800], [300, 800]], float) + offset)
    lines = pattern_for_domains(boat_plan, {7: offset}, settings, "teak", {7: domain_placed})
    assert len(lines[7]) >= 5   # a 500 mm tall window at 63.5 mm pitch
    for p0, p1 in lines[7]:
        assert domain_placed.buffer(0.5).contains(Polygon([p0, p1, p1 + 0.01]).centroid)  # every groove stays inside
        assert 300 - 1 <= p0[0] <= 1200 + 1 and 300 - 1501 <= p0[1] <= 800 - 1499


def test_final_dxf_carries_machinable_pattern_lines():
    import ezdxf
    from pathlib import Path
    import tempfile
    from autodeck2.calibration import Loop, Segment
    from autodeck2.ingest import PanelIngest, write_final_dxf

    square = [np.array(p, float) for p in [(0, 0), (1000, 0), (1000, 600), (0, 600)]]
    segments = [Segment("LINE", square[i].copy(), square[(i + 1) % 4].copy()) for i in range(4)]
    panel = PanelIngest(1, Loop(segments, [0, 1, 2, 3], closed=True), [])
    grooves = {1: [(np.array([100.0, y]), np.array([900.0, y])) for y in (150.0, 300.0, 450.0)]}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final_test.dxf"
        info = write_final_dxf(path, {1: panel}, pattern=("teak", grooves))
        assert info["polyline_count"] == 1 and info["all_closed"]
        assert info["pattern"] == "teak" and info["pattern_line_count"] == 3
        doc = ezdxf.readfile(str(path))
        layers = {e.dxf.layer for e in doc.modelspace()}
        assert layers == {"CAM_USER__PANEL_1", "PATTERN_TEAK__PANEL_1"}
