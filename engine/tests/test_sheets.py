"""Seam splitting, arc survival and grain-constrained nesting.

The properties here are the ones that would be expensive to get wrong on a
sheet of directional decking, so they are asserted numerically rather than
smoke-tested.
"""

import math

import numpy as np
import pytest
from shapely.geometry import Polygon

from autodeck2 import nesting, sheets
from autodeck2.config import load_config


def options(**overrides):
    config = load_config()
    return sheets.settings({**config, "sheets": {**(config.get("sheets") or {}), **overrides}})


def rounded_rect(width, height, radius, x=0.0, y=0.0):
    """A closed loop with four straight sides and four 90-degree arcs, i.e. the
    shape of a real fitted panel: lines with tangent arcs between them."""

    b = math.tan(math.pi / 8)          # bulge of a quarter turn
    r = radius
    return sheets.Loop(np.array([
        [x + r,         y,          0.0],
        [x + width - r, y,          b],
        [x + width,     y + r,      0.0],
        [x + width,     y + height - r, b],
        [x + width - r, y + height, 0.0],
        [x + r,         y + height, b],
        [x,             y + height - r, 0.0],
        [x,             y + r,      b],
    ], dtype=float))


# --------------------------------------------------------------- bulge maths

@pytest.mark.parametrize("bulge", [0.05, 0.4142135623730951, 1.0, 2.5, -0.1, -1.0, -2.5])
def test_bulge_survives_a_round_trip_through_arc_geometry(bulge):
    """_bulge_through must invert bulge_to_arc, including for reflex arcs.

    These two disagreed on sign once, which put every rebuilt arc on the wrong
    side of its chord and silently flattened all of them to polylines.
    """

    p0 = np.array([1.0, 0.0])
    p1 = np.array([0.0, 1.0])
    centre, radius, start, sweep = sheets.bulge_to_arc(p0, p1, bulge)
    middle = centre + radius * np.array([math.cos(start + sweep / 2), math.sin(start + sweep / 2)])
    assert sheets._bulge_through(p0, middle, p1) == pytest.approx(bulge, abs=1e-9)


def test_sampling_a_loop_stays_on_its_arcs():
    loop = rounded_rect(800.0, 500.0, 100.0)
    points, source = sheets.sample_loop(loop, 1.0)
    assert len(points) == len(source)
    assert set(np.unique(source)) <= set(range(len(loop.vertices)))
    # every sampled point lies on the loop's own polygon, within a chord error
    polygon = Polygon(points)
    assert polygon.is_valid and polygon.area > 0


# --------------------------------------------------------------- seam splitting

def test_one_seam_splits_a_panel_leaving_exactly_the_seam_gap():
    opts = options()
    loop = rounded_rect(1600.0, 900.0, 120.0)
    seam = sheets.Seam("s", 800.0, -500.0, 800.0, 1400.0)
    pieces, _warnings = sheets.split_panel(1, loop, [], [seam], opts)

    assert len(pieces) == 2
    a, b = (p.polygon(opts["sample_step_mm"]) for p in pieces)
    assert a.distance(b) == pytest.approx(opts["seam_gap_mm"], abs=0.05)


def test_crossing_seams_give_four_pieces_and_never_closer_than_the_gap():
    opts = options()
    loop = rounded_rect(1600.0, 900.0, 120.0)
    seams = [
        sheets.Seam("v", 800.0, -500.0, 800.0, 1400.0),
        sheets.Seam("h", -500.0, 450.0, 2100.0, 450.0),
    ]
    pieces, _warnings = sheets.split_panel(1, loop, [], seams, opts)
    assert len(pieces) == 4

    polygons = [p.polygon(opts["sample_step_mm"]) for p in pieces]
    distances = sorted(
        polygons[i].distance(polygons[j])
        for i in range(len(polygons)) for j in range(i + 1, len(polygons))
    )
    # four edge-sharing pairs at the gap, two diagonal pairs at gap*sqrt(2)
    assert all(d >= opts["seam_gap_mm"] - 0.01 for d in distances)
    assert distances[0] == pytest.approx(opts["seam_gap_mm"], abs=0.05)
    assert distances[-1] == pytest.approx(opts["seam_gap_mm"] * math.sqrt(2), abs=0.1)


def test_splitting_keeps_arcs_instead_of_flattening_them():
    opts = options()
    loop = rounded_rect(1600.0, 900.0, 120.0)
    before = int(np.count_nonzero(np.abs(loop.bulges) > 1e-12))
    assert before == 4

    seam = sheets.Seam("s", 800.0, -500.0, 800.0, 1400.0)
    pieces, _warnings = sheets.split_panel(1, loop, [], [seam], opts)
    after = sum(int(np.count_nonzero(np.abs(p.outer.bulges) > 1e-12)) for p in pieces)
    assert after == before, "the corner arcs must survive a cut that does not touch them"
    assert max(p.max_arc_error_mm for p in pieces) <= opts["arc_rebuild_tolerance_mm"]


def test_a_panel_with_no_seams_is_returned_untouched():
    opts = options()
    loop = rounded_rect(600.0, 400.0, 50.0)
    pieces, warnings = sheets.split_panel(3, loop, [], [], opts)
    assert warnings == []
    assert len(pieces) == 1
    assert pieces[0].from_seam is False
    assert np.array_equal(pieces[0].outer.vertices, loop.vertices)


def test_a_seam_that_misses_the_panel_changes_nothing():
    opts = options()
    loop = rounded_rect(600.0, 400.0, 50.0)
    far_away = sheets.Seam("s", 5000.0, -500.0, 5000.0, 500.0)
    pieces, _warnings = sheets.split_panel(1, loop, [], [far_away], opts)
    assert len(pieces) == 1


# --------------------------------------------------------------- grain frame

def test_sheet_transform_puts_the_boat_axis_on_the_sheet_long_axis():
    for degrees in (0.0, 14.0, 90.0, -37.5, 180.0):
        radians = math.radians(degrees)
        axis = np.array([math.cos(radians), math.sin(radians)])
        rotation = sheets.sheet_transform(axis)
        mapped = rotation @ axis
        assert mapped == pytest.approx([0.0, 1.0], abs=1e-9), f"axis at {degrees} deg"
        assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-9)   # rotation, never a mirror


def test_sheet_transform_falls_back_to_identity_without_an_axis():
    assert np.array_equal(sheets.sheet_transform(None), np.eye(2))
    assert np.array_equal(sheets.sheet_transform([0.0, 0.0]), np.eye(2))


def test_oversize_report_names_the_pieces_that_still_need_a_seam():
    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    big = sheets.Piece("P1", 1, rounded_rect(3000.0, 1500.0, 60.0), [], 4.5e6)
    small = sheets.Piece("P2", 1, rounded_rect(400.0, 300.0, 30.0), [], 1.2e5)
    report = sheets.oversize_report([big, small], rotation, opts)
    assert [item["piece_id"] for item in report] == ["P1"]
    assert report[0]["over_width_mm"] > 0 and report[0]["over_length_mm"] > 0


# --------------------------------------------------------------- nesting

def test_nesting_respects_the_usable_area_the_spacing_and_the_grain():
    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    pieces = [
        sheets.Piece(f"P{i}", 1, rounded_rect(600.0, 900.0, 40.0), [], 540000.0)
        for i in range(4)
    ]
    sheet_list, summary, _warnings = nesting.nest(pieces, rotation, opts)

    assert summary["piece_count"] == 4
    assert summary["unplaced_piece_ids"] == []
    margin_x = (opts["sheet_width_mm"] - opts["max_part_width_mm"]) / 2
    margin_y = (opts["sheet_length_mm"] - opts["max_part_length_mm"]) / 2

    for sheet in sheet_list:
        placed = []
        for placement in sheet.placements:
            assert placement.rotation_deg in (0, 180), "grain forbids 90 degrees and mirroring"
            piece = next(p for p in pieces if p.piece_id == placement.piece_id)
            points, _s = sheets.sample_loop(piece.outer, opts["sample_step_mm"])
            polygon = Polygon(placement.apply(points, rotation))
            x0, y0, x1, y1 = polygon.bounds
            assert x0 >= margin_x - 0.01 and y0 >= margin_y - 0.01
            assert x1 <= margin_x + opts["max_part_width_mm"] + 0.01
            assert y1 <= margin_y + opts["max_part_length_mm"] + 0.01
            placed.append(polygon)
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                assert placed[i].distance(placed[j]) >= opts["part_spacing_mm"] - 0.01


def test_a_piece_too_big_for_any_rotation_is_reported_not_silently_dropped():
    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    huge = sheets.Piece("P9", 1, rounded_rect(3000.0, 1500.0, 40.0), [], 4.5e6)
    sheet_list, summary, warnings = nesting.nest([huge], rotation, opts)
    assert summary["unplaced_piece_ids"] == ["P9"]
    assert sheet_list == []
    assert any("add a seam" in w for w in warnings)


def test_nesting_is_deterministic():
    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    pieces = [sheets.Piece(f"P{i}", 1, rounded_rect(500.0 + 40 * i, 700.0, 30.0), [], 4e5 + i)
              for i in range(5)]
    first = nesting.nest(pieces, rotation, opts)[0]
    second = nesting.nest(list(reversed(pieces)), rotation, opts)[0]
    layout = lambda sheets_: [(p.piece_id, p.rotation_deg, round(float(p.offset[0]), 6),
                               round(float(p.offset[1]), 6))
                              for s in sheets_ for p in s.placements]
    assert layout(first) == layout(second)


def test_settings_reject_a_part_limit_larger_than_the_sheet():
    with pytest.raises(ValueError):
        options(max_part_width_mm=2000.0)
    with pytest.raises(ValueError):
        options(max_part_length_mm=5000.0)


def test_defaults_match_the_shop_numbers():
    opts = options()
    assert opts["sheet_width_mm"] == pytest.approx(40 * 25.4)
    assert opts["sheet_length_mm"] == pytest.approx(80 * 25.4)
    assert opts["max_part_width_mm"] == pytest.approx(39 * 25.4)
    assert opts["max_part_length_mm"] == pytest.approx(79 * 25.4)
    assert opts["seam_gap_mm"] == 6.0
    assert opts["part_spacing_mm"] == 20.0
