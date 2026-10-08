"""Seams from the pointer to the cut file: placement, straightening, the API.

The properties here are the ones the fabricator would notice, and most of them
are about the same single fact: the boat's own axis is the master reference for
everything.  It oriented the teak lines, it orients the sheet, and it now
orients the seams, so a long seam is EXACTLY parallel to the planks running
through it and a short one EXACTLY square to them.  "Exactly" is meant
literally -- a seam a hundredth of a degree out is the pie shape the user is
complaining about -- so parallelism is asserted at 1e-12, not with a tolerance.

The three things this file guards that nothing else can:

  * `plan` and `preview` cut and draw the SAME pieces.  This file has been
    bitten once by those two resolving the grain axis separately, and a seam
    corrector split the same way would show a straight join and cut a crooked
    one.
  * Straightening is recomputed from the user's drawing every time, never from
    the last correction, so re-planning a job a hundred times does not walk a
    seam a hundred steps down the deck.
  * A seam is placed by choosing a direction and hovering, so the line the page
    previews under the pointer is geometrically the same line the router cuts.

Two cached runs are used, and the difference between them is load bearing:
AXIS_RUN was fitted with a pattern and so carries a real boat frame, while
NO_AXIS_RUN was fitted with pattern "none" and carries none.  A test that used
the second to check axis behaviour would pass while asserting nothing.
"""

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import LineString, Point

from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

RUNS = Path(__file__).resolve().parents[1] / "outputs" / "runs"
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"        # pattern "teak": has a boat frame
NO_AXIS_RUN = RUNS / "21kwcockpit-1-20260901-172519"     # pattern "none": has none

# The boat this fixture stores: 3.68 degrees off the placed frame's +X, bow
# towards +longitudinal_axis.  Nothing here may expect a seam at 0 or 90 -- that
# would be testing the coordinate system instead of the boat.
AXIS_DEG = 3.680395518430263


def options(**overrides):
    config = load_config()
    return sheets.settings({**config, "sheets": {**(config.get("sheets") or {}), **overrides}})


def config_with(**overrides):
    config = load_config()
    return {**config, "sheets": {**(config.get("sheets") or {}), **overrides}}


def rounded_rect(width, height, radius, x=0.0, y=0.0):
    """Four straight sides and four quarter-turn fillets -- a fitted panel."""

    b = math.tan(math.pi / 8)
    r = radius
    return sheets.Loop(np.array([
        [x + r,         y,              0.0],
        [x + width - r, y,              b],
        [x + width,     y + r,          0.0],
        [x + width,     y + height - r, b],
        [x + width - r, y + height,     0.0],
        [x + r,         y + height,     b],
        [x,             y + height - r, 0.0],
        [x,             y + r,          b],
    ], dtype=float))


def unit_at(angle_deg):
    turn = math.radians(angle_deg)
    return np.array([math.cos(turn), math.sin(turn)])


def seam_direction(seam):
    span = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1], dtype=float)
    return span / float(np.hypot(*span))


def parallelism(a, b):
    return abs(float(np.dot(a, b)))


def has_axis_run():
    return (AXIS_RUN / "run.json").is_file() and (AXIS_RUN / "final_auto.dxf").is_file()


# ---------------------------------------------------------------- settings

def test_one_config_block_controls_the_sheet_and_the_snapping():
    """The user sets both in the same place, so `sheets.settings` has to resolve
    both.  Without this, a snap option in config["sheets"] would be silently
    dropped and the corrector would quietly keep its defaults."""

    assert set(seamsnap.SNAP_DEFAULTS) <= set(sheets.DEFAULTS)
    resolved = options()
    for key, value in seamsnap.SNAP_DEFAULTS.items():
        assert resolved[key] == value

    overridden = options(seam_axis_snap_deg=8.0, seam_snap_enabled=False)
    assert overridden["seam_axis_snap_deg"] == 8.0
    assert overridden["seam_snap_enabled"] is False
    # The sheet keys are still there beside them.
    assert overridden["sheet_length_mm"] == pytest.approx(80 * 25.4)


def test_importing_either_module_first_still_merges_the_defaults():
    """`sheets` absorbs `seamsnap`'s defaults, so the two modules refer to each
    other.  A cycle closed on both sides would make the merge depend on which
    module a caller happened to import first, and the failure would be a missing
    config key rather than an ImportError."""

    import subprocess
    import sys

    for first, second in (("seamsnap", "sheets"), ("sheets", "seamsnap")):
        script = (f"from autodeck2 import {first}\n"
                  f"from autodeck2 import {second}\n"
                  "from autodeck2 import sheets, seamsnap\n"
                  "assert set(seamsnap.SNAP_DEFAULTS) <= set(sheets.DEFAULTS)\n"
                  "print('ok')\n")
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        assert done.returncode == 0, f"{first} first: {done.stderr}"
        assert done.stdout.strip() == "ok"


# ------------------------------------------------------------ the seam record

def test_a_seams_json_written_before_any_of_this_still_reads():
    """The user has runs on disk with seams in them.  An old seam has no snap,
    raw, mode or angle, and it has to come back as a normal snapping seam whose
    own geometry is the drawing."""

    seam = sheets.Seam.from_dict({"seam_id": "s1", "x1": 0.0, "y1": 10.0,
                                  "x2": 400.0, "y2": 12.0, "panel_id": None})

    assert seam.snap is True
    assert seam.raw is None
    assert seam.mode == ""
    assert seam.angle_deg is None
    assert seam.drawn == (0.0, 10.0, 400.0, 12.0)


def test_a_real_old_seams_json_on_disk_reads_and_writes_back(tmp_path):
    if not (NO_AXIS_RUN / "seams.json").is_file():
        pytest.skip(f"cached run {NO_AXIS_RUN.name} is not present")
    # Pinned content (shared with test_nesting_speed) rather than the live
    # seams.json: the live file is the app's to rewrite, and this test wants
    # the OLD schema specifically -- the pre-5.3 four-field form.
    from test_nesting_speed import PINNED_SEAMS

    stored = {"seams": PINNED_SEAMS[NO_AXIS_RUN.name]}
    assert stored["seams"] and "raw" not in stored["seams"][0]

    source = tmp_path / "source"
    source.mkdir()
    (source / "seams.json").write_text(json.dumps(stored, indent=2), encoding="utf-8")
    seams = sheets.read_seams(source)
    sheets.write_seams(tmp_path, seams)
    again = sheets.read_seams(tmp_path)

    assert len(again) == len(seams)
    for before, after in zip(seams, again):
        assert (after.x1, after.y1, after.x2, after.y2) == (before.x1, before.y1, before.x2, before.y2)
        assert after.seam_id == before.seam_id and after.snap is True and after.mode == ""


def test_a_placed_seam_round_trips_through_json_bit_for_bit(tmp_path):
    """`raw` is what every future correction starts from, so a round trip
    through the file must not round it: a hundredth of a millimetre lost per
    save is a seam that creeps."""

    seam = sheets.Seam("s7", 1.0 / 3.0, -2.0 / 7.0, 900.1234567890123, 51.98765432109876,
                       panel_id=2, snap=False,
                       raw=(0.1, 0.2, 900.3, 51.4), mode="angle", angle_deg=37.5)

    sheets.write_seams(tmp_path, [seam])
    (back,) = sheets.read_seams(tmp_path)

    assert (back.x1, back.y1, back.x2, back.y2) == (seam.x1, seam.y1, seam.x2, seam.y2)
    assert back.raw == seam.raw
    assert (back.snap, back.mode, back.angle_deg, back.panel_id) == (False, "angle", 37.5, 2)
    assert back.to_dict() == seam.to_dict()


def test_a_seam_record_refuses_nonsense_rather_than_storing_it():
    with pytest.raises(ValueError):
        sheets.Seam.from_dict({"x1": 0, "y1": 0, "x2": 1, "y2": 1, "mode": "sideways"})
    with pytest.raises(ValueError):
        sheets.Seam.from_dict({"x1": 0, "y1": 0, "x2": 1, "y2": 1, "raw": [1.0, 2.0]})


def test_moving_a_seam_keeps_the_drawing_it_came_from():
    """The invariant the whole corrector rests on: correcting a corrected seam
    must correct the DRAWING again, not the correction."""

    drawn = sheets.Seam("s1", 0.0, 0.0, 400.0, 30.0, mode="across", angle_deg=None)
    once = drawn.moved_to(0.0, 1.0, 400.0, 1.0)
    twice = once.moved_to(0.0, 2.0, 400.0, 2.0)

    assert once.raw == (0.0, 0.0, 400.0, 30.0)
    assert twice.raw == (0.0, 0.0, 400.0, 30.0)
    assert twice.mode == "across"


# --------------------------------------------------------- placement modes

@pytest.mark.parametrize("angle", [0.0, AXIS_DEG, 45.0, 91.2, -33.0])
def test_vertical_and_horizontal_are_the_two_master_directions(angle):
    """The user's words for them: "vertical" is along the boat, "horizontal"
    across it, and the two are exactly ninety degrees apart whatever angle the
    boat happens to lie at on the page."""

    axis = unit_at(angle)
    along = seamplace.direction_for(axis, "along")
    across = seamplace.direction_for(axis, "across")

    assert parallelism(along, axis) == pytest.approx(1.0, abs=1e-15)
    assert float(np.dot(along, across)) == 0.0
    assert tuple(along) == tuple(seamsnap.master_directions(axis)[0])
    assert tuple(across) == tuple(seamsnap.master_directions(axis)[1])


def test_a_diagonal_at_zero_or_ninety_is_the_master_itself_not_a_rounding_of_it():
    """cos(radians(90)) is 6.1e-17, not zero.  A diagonal typed as 90 has to
    give the ACROSS direction bit for bit, or a seam the user thinks is square
    is a rounding error out and fans open against the one beside it."""

    axis = unit_at(AXIS_DEG)
    along, across = seamsnap.master_directions(axis)

    assert tuple(seamplace.direction_for(axis, "angle", 0.0)) == tuple(along)
    assert tuple(seamplace.direction_for(axis, "angle", 90.0)) == tuple(across)
    assert tuple(seamplace.direction_for(axis, "angle", 180.0)) == tuple(along)
    assert tuple(seamplace.direction_for(axis, "angle", 270.0)) == tuple(across)


def test_a_diagonal_is_measured_off_the_boat_and_turns_with_it():
    """One number, no ambiguity: 30 means thirty degrees off the centreline, on
    a boat lying at any angle."""

    for boat_deg in (0.0, AXIS_DEG, 61.0):
        axis = unit_at(boat_deg)
        diagonal = seamplace.direction_for(axis, "angle", 30.0)
        assert seamsnap._direction_angle_deg(diagonal, axis) == pytest.approx(30.0, abs=1e-9)


def test_no_boat_means_no_direction_is_invented():
    assert seamplace.direction_for(None, "along") is None
    assert seamplace.direction_for(None, "across") is None
    assert seamplace.direction_for([0.0, 0.0], "angle", 45.0) is None
    assert seamplace.direction_for(unit_at(10.0), "sideways") is None


# ---------------------------------------------------- trimming to the part

@pytest.fixture(scope="module")
def synthetic_panel():
    """A panel with a console in it, which is the shape the whole hover preview
    exists for: a line drawn across it has to break around the cut-out."""

    outer = rounded_rect(1600.0, 900.0, 120.0)
    console = rounded_rect(400.0, 300.0, 40.0, x=600.0, y=300.0)
    return {1: [outer, console]}


def test_a_hover_returns_the_chord_from_one_edge_of_the_part_to_the_other(synthetic_panel):
    opts = options()
    polygons = seamplace.panel_polygons(synthetic_panel, opts)

    segments = seamplace.seam_through((300.0, 450.0), unit_at(90.0), polygons, opts)

    assert len(segments) == 1
    (chord,) = segments
    assert chord["panel_id"] == 1
    # Edge to edge of a 900 mm panel, at x=300 where nothing is filleted away.
    assert chord["length_mm"] == pytest.approx(900.0, abs=0.5)
    assert chord["y1"] == pytest.approx(0.0, abs=0.5)
    assert chord["y2"] == pytest.approx(900.0, abs=0.5)
    # Reported pointing the way the user chose, so two seams placed the same way
    # are stored the same way round.
    assert chord["y2"] > chord["y1"]


def test_a_hover_breaks_around_the_console_instead_of_drawing_across_it(synthetic_panel):
    """The user is placing a join, not a line on a screen: a seam that ran
    across the console would be cutting a piece that is not there."""

    opts = options()
    polygons = seamplace.panel_polygons(synthetic_panel, opts)

    segments = seamplace.seam_through((800.0, 450.0), unit_at(90.0), polygons, opts)

    assert len(segments) == 2
    below, above = segments
    assert below["y2"] == pytest.approx(300.0, abs=1.0)      # stops at the cut-out
    assert above["y1"] == pytest.approx(600.0, abs=1.0)      # picks up the other side
    assert below["length_mm"] == pytest.approx(300.0, abs=1.0)
    assert above["length_mm"] == pytest.approx(300.0, abs=1.0)
    # Ordered along the direction, so the page can draw them as one broken line.
    assert below["along_mm"] < above["along_mm"]


def test_a_hover_off_the_part_returns_nothing_at_all(synthetic_panel):
    opts = options()
    polygons = seamplace.panel_polygons(synthetic_panel, opts)

    assert seamplace.seam_through((-5000.0, -5000.0), unit_at(90.0), polygons, opts) == []
    # Parallel to the panel and well past it: the line exists, the part does not.
    assert seamplace.seam_through((800.0, 5000.0), unit_at(0.0), polygons, opts) == []
    # Inside the console is inside a hole, which is not part of the part.
    assert seamplace.seam_through((800.0, 450.0), unit_at(0.0), polygons, opts) != []
    inside = seamplace.seam_through((800.0, 450.0), unit_at(0.0), polygons, opts)
    assert all(not (s["x1"] < 800.0 < s["x2"]) for s in inside)


def test_the_hover_endpoints_are_really_on_the_fitted_outline(synthetic_panel):
    """Every end of every chord is a point of the CAM outline itself -- the
    outer boundary or a cut-out edge -- because that is what "trimmed to the
    part" has to mean if the seam is going to cut where the preview showed it."""

    opts = options()
    polygons = seamplace.panel_polygons(synthetic_panel, opts)
    boundary = polygons[1].boundary

    for angle in (0.0, 31.0, 90.0, 117.0):
        for y in (150.0, 450.0, 700.0):
            for segment in seamplace.seam_through((700.0, y), unit_at(angle), polygons, opts):
                for x, y_end in ((segment["x1"], segment["y1"]), (segment["x2"], segment["y2"])):
                    assert boundary.distance(Point(x, y_end)) < 1e-6


def test_grazing_a_corner_is_not_a_seam(synthetic_panel):
    """A line that only touches the outline hands back a Point, or a chord a
    fraction of a millimetre long.  Neither is a join, and offering one would
    let the user click and get nothing."""

    opts = options()
    polygons = seamplace.panel_polygons(synthetic_panel, opts)
    minimum = seamplace.MIN_CHORD_MM

    # Along the very bottom edge of the panel, where the line is tangent.
    for offset in (0.0, -0.05, 0.05):
        for segment in seamplace.seam_through((800.0, offset), unit_at(0.0), polygons, opts):
            assert segment["length_mm"] >= minimum


def test_only_the_panels_a_seam_really_crosses_are_offered(synthetic_panel):
    """The same rule the cut uses: a free seam cuts the panels its DRAWN segment
    touches, not every panel its extension would reach."""

    opts = options()
    panels = {1: synthetic_panel[1], 2: [rounded_rect(600.0, 400.0, 50.0, x=4000.0, y=0.0)]}
    polygons = seamplace.panel_polygons(panels, opts)

    assert seamplace.panels_crossed(300.0, 100.0, 300.0, 800.0, polygons, opts) == [1]
    assert seamplace.panels_crossed(4300.0, 100.0, 4300.0, 300.0, polygons, opts) == [2]
    assert seamplace.panels_crossed(300.0, 100.0, 4300.0, 100.0, polygons, opts) == [1, 2]
    # A bound seam is only ever its own panel's.
    assert seamplace.panels_crossed(300.0, 100.0, 4300.0, 100.0, polygons, opts, panel_id=2) == [2]

    narrowed = seamplace.seam_through((300.0, 450.0), unit_at(90.0), polygons, opts, panel_ids=[2])
    assert narrowed == []


# --------------------------------------------------- straightening a job

def test_a_seam_marked_not_to_snap_is_left_exactly_where_it_was_placed():
    """The per-seam opt out, for the one join the fabricator wants by eye.  Not
    "nearly where it was placed" -- exactly, so it does not drift either."""

    loops = {1: [rounded_rect(1600.0, 900.0, 120.0)]}
    axis = unit_at(AXIS_DEG)
    crooked = sheets.Seam("s1", 100.0, 40.0, 1500.0, 60.0, snap=False)
    same_but_snapping = sheets.Seam("s2", 100.0, 40.0, 1500.0, 60.0)

    (left, moved), (untouched, corrected), _w = sheetjob.apply_snap(
        [crooked, same_but_snapping], loops, axis, options())

    assert (left.x1, left.y1, left.x2, left.y2) == (100.0, 40.0, 1500.0, 60.0)
    assert not untouched.applied and untouched.note == ""
    # ...while the identical seam beside it was squared, so the opt out is what
    # made the difference and not the geometry.
    assert corrected.applied and corrected.kind == "along-boat"
    assert (moved.x1, moved.y1) != (100.0, 40.0)


def test_turning_snapping_off_leaves_every_seam_as_drawn():
    loops = {1: [rounded_rect(1600.0, 900.0, 120.0)]}
    drawn = sheets.Seam("s1", 100.0, 40.0, 1500.0, 60.0)

    (seam,), (result,), _w = sheetjob.apply_snap([drawn], loops, unit_at(AXIS_DEG),
                                                 options(seam_snap_enabled=False))

    assert (seam.x1, seam.y1, seam.x2, seam.y2) == (100.0, 40.0, 1500.0, 60.0)
    assert not result.applied


def test_a_seam_placed_across_the_boat_stays_across_it_when_the_grain_moves():
    """This is why the MODE is stored and not only the coordinates.  The user
    places a short seam side to side, then sets a manual grain angle because the
    detection was unsure -- and the seam has to follow the boat, not stay frozen
    at the angle it was first given."""

    loops = {1: [rounded_rect(1600.0, 900.0, 120.0)]}
    placed_on = unit_at(AXIS_DEG)
    seam = sheets.Seam("s1", 700.0, 100.0, 700.0, 800.0, mode="across")
    # Place it exactly across the boat it was drawn on, the way the hover tool
    # would have.
    (seam,), _r, _w = sheetjob.apply_snap([seam], loops, placed_on, options())
    assert parallelism(seam_direction(seam), seamsnap.master_directions(placed_on)[1]) \
        == pytest.approx(1.0, abs=1e-12)

    moved_to = unit_at(AXIS_DEG + 25.0)
    (after,), (result,), _w = sheetjob.apply_snap([seam], loops, moved_to, options())

    across_now = seamsnap.master_directions(moved_to)[1]
    assert parallelism(seam_direction(after), across_now) == pytest.approx(1.0, abs=1e-12)
    assert result.kind == "across-boat"
    # It followed the boat about its own middle, so it is still where it was put.
    assert (after.x1 + after.x2) / 2.0 == pytest.approx(700.0, abs=1e-6)
    assert (after.y1 + after.y2) / 2.0 == pytest.approx(450.0, abs=1e-6)
    # And the drawing is still the drawing.
    assert after.raw == (700.0, 100.0, 700.0, 800.0)


def test_a_seam_with_a_mode_but_no_boat_says_so_instead_of_guessing():
    loops = {1: [rounded_rect(1600.0, 900.0, 120.0)]}
    seam = sheets.Seam("s1", 700.0, 100.0, 700.0, 800.0, mode="across")

    (after,), _results, warnings = sheetjob.apply_snap([seam], loops, None, options())

    assert (after.x1, after.y1, after.x2, after.y2) == (700.0, 100.0, 700.0, 800.0)
    assert any("no boat direction" in w for w in warnings)
    assert any(seamsnap.NO_AXIS_WARNING == w for w in warnings)


def test_every_long_seam_in_a_job_comes_out_exactly_parallel():
    """Three joins down a deck, dragged by hand at three different angles.  If
    any two of them are a rounding error apart the piece between them is a
    wedge, which is the "pie shape thing" the user is describing."""

    loops = {1: [rounded_rect(3000.0, 1600.0, 120.0)]}
    axis = unit_at(AXIS_DEG)
    drawn = [sheets.Seam(f"s{i}", 200.0, y, 2800.0, y + 2800.0 * math.tan(math.radians(off)))
             for i, (y, off) in enumerate(((300.0, 6.0), (700.0, -4.0), (1200.0, 11.0)))]

    seams, results, _w = sheetjob.apply_snap(drawn, loops, axis, options())

    assert [r.kind for r in results] == ["along-boat"] * 3
    first = seam_direction(seams[0])
    for seam in seams:
        assert parallelism(seam_direction(seam), axis) == pytest.approx(1.0, abs=1e-12)
        assert parallelism(seam_direction(seam), first) == pytest.approx(1.0, abs=1e-12)


def test_straightening_the_straightened_seams_changes_nothing():
    loops = {1: [rounded_rect(1600.0, 900.0, 120.0)]}
    axis = unit_at(AXIS_DEG)
    drawn = [sheets.Seam("s1", 100.0, 40.0, 1500.0, 130.0),
             sheets.Seam("s2", 700.0, 20.0, 760.0, 880.0)]

    once, first, _w = sheetjob.apply_snap(drawn, loops, axis, options())
    twice, again, _w = sheetjob.apply_snap(once, loops, axis, options())

    for before, after in zip(once, twice):
        assert (after.x1, after.y1, after.x2, after.y2) == (before.x1, before.y1, before.x2, before.y2)
    # The report does not go quiet on the second pass, and it should not: the
    # correction is always measured from the DRAWING, so the user keeps being
    # told their line was squared 3 degrees rather than the note vanishing once
    # the job has been saved.
    assert [r.to_dict() for r in again] == [r.to_dict() for r in first]
    assert all(r.applied for r in first)


# -------------------------------------------------- against the real deck

@pytest.fixture(scope="module")
def axis_run():
    if not has_axis_run():
        pytest.skip(f"cached run {AXIS_RUN.name} is not present")
    return AXIS_RUN


def test_the_stored_boat_frame_says_which_end_is_the_bow(axis_run):
    """The seam tab draws itself bow up, so the bow has to come out of the run
    rather than being guessed from which way the panels happen to lie."""

    frame = sheetjob.boat_axis(axis_run)

    assert frame.axis == pytest.approx([0.9979376404082442, 0.06419085492829811])
    assert frame.confidence == pytest.approx(0.6494771714508549)
    assert frame.bow_sign == 1
    assert frame.bow_confidence == pytest.approx(0.824070661710407)
    assert frame.bow_direction == pytest.approx(frame.axis)
    assert math.degrees(math.atan2(frame.axis[1], frame.axis[0])) == pytest.approx(AXIS_DEG)

    # And a run with no pattern frame reports no bow rather than a default one.
    if (NO_AXIS_RUN / "run.json").is_file():
        blank = sheetjob.boat_axis(NO_AXIS_RUN)
        assert blank.axis is None and blank.bow_sign == 0 and blank.bow_confidence == 0.0
        assert blank.bow_direction is None


def test_a_manual_grain_angle_carries_the_bow_with_it(axis_run):
    """The override gives a line, not a heading.  Which end of it is the bow has
    to come from the detection, and reversing the axis has to reverse the bow --
    otherwise the seam view flips end for end when the grain is nudged."""

    forward, _w = sheetjob.resolve_frame(axis_run, options(grain_angle_deg=AXIS_DEG))
    backward, _w = sheetjob.resolve_frame(axis_run, options(grain_angle_deg=AXIS_DEG + 180.0))

    assert forward.bow_sign == 1
    assert backward.bow_sign == -1
    assert forward.bow_direction == pytest.approx(backward.bow_direction, abs=1e-9)


def test_plan_and_preview_cut_and_draw_the_same_pieces(axis_run):
    """The one bug this module has already had: the two used to resolve the
    grain axis separately, so the pieces were placed in one frame and drawn in
    another.  With the seam corrector in the same path there is a second way to
    diverge -- straightening once for the cut and once for the picture -- so
    both are asserted here, on the run's own seams."""

    config = config_with()
    planned = sheetjob.plan(axis_run, config, write_files=False)
    previewed = sheetjob.preview(axis_run, config)

    assert planned["seams"] == previewed["seams"]
    assert planned["seam_snaps"] == previewed["seam_snaps"]
    assert planned["pieces"] == previewed["pieces"]
    assert planned["sheets"] == previewed["sheets"]
    assert planned["boat_frame"] == previewed["boat_frame"]
    assert previewed["preview"]["sheets"], "the preview drew no sheets to compare"


def test_replanning_a_job_never_moves_a_seam_by_a_micron(axis_run):
    """Every re-plan straightens the DRAWING again, not the last straightening.
    If it did not, a job opened and saved ten times would have its seams ten
    steps further down the deck, and the tenth cut would not be the first."""

    config = config_with()
    first = sheetjob.plan(axis_run, config, write_files=False)
    seams = [sheets.Seam.from_dict(item) for item in first["seams"]]
    assert seams, "the cached run has no seams to re-plan"

    for _round in range(4):
        again = sheetjob.plan(axis_run, config, seams=seams, write_files=False)
        for before, after in zip(first["seams"], again["seams"]):
            for key in ("x1", "y1", "x2", "y2"):
                assert after[key] == before[key], f"{after['seam_id']} moved in {key}"
            assert after["raw"] == before["raw"]
        assert again["seam_snaps"] == first["seam_snaps"]
        seams = [sheets.Seam.from_dict(item) for item in again["seams"]]


def test_the_grain_override_re_squares_every_seam(axis_run):
    """Overriding the grain is the user saying "the boat runs THIS way".  It has
    to re-square the seams as well as re-orienting the sheets, or the joins
    would be cut square to a boat the job has stopped believing in."""

    turned = AXIS_DEG + 12.0
    seams, results, warnings = sheetjob.snapped_seams(axis_run, config_with(grain_angle_deg=turned))
    if not seams:
        pytest.skip("the cached run has no seams")

    along, across = seamsnap.master_directions(unit_at(turned))
    for seam, result in zip(seams, results):
        assert result.kind in ("along-boat", "across-boat"), result.note
        master = along if result.kind == "along-boat" else across
        assert parallelism(seam_direction(seam), master) == pytest.approx(1.0, abs=1e-12)
    assert any("grain direction set manually" in w for w in warnings)


def test_a_run_with_no_boat_axis_still_plans_and_says_why(axis_run):
    """The fallback fixture doing its one job: without a pattern frame there is
    no direction to square to, the seams are only lined up with fitted edges,
    and the user is told rather than being left to wonder."""

    if not (NO_AXIS_RUN / "final_auto.dxf").is_file():
        pytest.skip(f"cached run {NO_AXIS_RUN.name} is not present")

    seams, results, warnings = sheetjob.snapped_seams(NO_AXIS_RUN, config_with())

    assert seams
    assert all(r.kind not in ("along-boat", "across-boat") for r in results)
    assert any(seamsnap.NO_AXIS_WARNING == w for w in warnings)
    assert any("no boat axis stored for this run" in w for w in warnings)


def test_a_hover_on_the_real_deck_lands_on_the_real_fitted_outline(axis_run):
    """The synthetic panel above proves the clipping; this proves it against the
    scan's own fitted geometry, where the outline is a few hundred arcs and
    lines rather than a rectangle."""

    opts = options()
    loops = sheets.read_fitted_dxf(sheetjob.source_dxf(axis_run))[0]
    polygons = seamplace.panel_polygons(loops, opts)
    frame = sheetjob.boat_axis(axis_run)
    along = seamplace.direction_for(frame.axis, "along")
    across = seamplace.direction_for(frame.axis, "across")

    biggest = max(polygons, key=lambda pid: polygons[pid].area)
    inside = polygons[biggest].representative_point()
    found = 0
    for unit in (along, across, seamplace.direction_for(frame.axis, "angle", 45.0)):
        for segment in seamplace.seam_through((inside.x, inside.y), unit, polygons, opts):
            boundary = polygons[segment["panel_id"]].boundary
            for point in (Point(segment["x1"], segment["y1"]), Point(segment["x2"], segment["y2"])):
                assert boundary.distance(point) < 1e-6
            assert segment["length_mm"] >= seamplace.MIN_CHORD_MM
            # And the chord really is inside the part, not across a cut-out.
            middle = LineString([(segment["x1"], segment["y1"]), (segment["x2"], segment["y2"])])
            assert polygons[segment["panel_id"]].buffer(1e-6).contains(middle)
            found += 1
    assert found >= 3, "the real deck produced almost no chords"


def test_a_seam_placed_by_hovering_needs_no_correction_afterwards(axis_run):
    """The point of choosing the direction first: the seam IS the master
    direction when it is placed, so the corrector has nothing to straighten and
    the line the user saw under the pointer is the line that gets cut."""

    opts = options()
    loops = sheets.read_fitted_dxf(sheetjob.source_dxf(axis_run))[0]
    polygons = seamplace.panel_polygons(loops, opts)
    frame = sheetjob.boat_axis(axis_run)
    biggest = max(polygons, key=lambda pid: polygons[pid].area)
    inside = polygons[biggest].representative_point()

    for mode in ("along", "across"):
        unit = seamplace.direction_for(frame.axis, mode)
        chords = seamplace.seam_through((inside.x, inside.y), unit, polygons, opts)
        assert chords
        longest = max(chords, key=lambda s: s["length_mm"])
        placed = sheets.Seam("hover", longest["x1"], longest["y1"], longest["x2"], longest["y2"],
                             panel_id=longest["panel_id"], mode=mode)

        (cut,), (result,), _w = sheetjob.apply_snap([placed], loops, frame.axis, opts)

        assert result.angle_change_deg == pytest.approx(0.0, abs=1e-9)
        master = seamsnap.master_directions(frame.axis)[0 if mode == "along" else 1]
        assert parallelism(seam_direction(cut), master) == pytest.approx(1.0, abs=1e-12)
        # The hover's settle is the SAME correction now (it runs through
        # apply_snap against the same reference pool), so a seam placed by
        # hovering needs no further movement at all -- it used to tolerate the
        # full 25 mm slide here, which is exactly the click-time jump the
        # fabricator saw between the preview and the cut.
        assert result.moved_mm <= 1e-6
        assert cut.mode == mode and cut.raw is not None


# ----------------------------------------------- the app: picking and hover

def app_bridge():
    return pytest.importorskip(
        "autodeck_app.bridge",
        reason="the app package is not on the path; run pytest with app/ in PYTHONPATH")


@pytest.fixture(scope="module")
def loaded_run():
    """The run open in the app: the scan has to still be on disk, because the
    lift and the picking both work off the development mesh."""

    if not has_axis_run():
        pytest.skip(f"cached run {AXIS_RUN.name} is not present")
    meta = json.loads((AXIS_RUN / "run.json").read_text(encoding="utf-8"))
    if not Path(str(meta.get("input_path", ""))).is_file():
        pytest.skip("the scan this run was made from is no longer on disk")
    bridge = app_bridge()
    return bridge, bridge.load_run(AXIS_RUN, lambda message: None)


def test_a_world_point_picked_off_the_model_comes_back_in_the_flat_frame(loaded_run):
    """The 3D half of the seam tool.  A point on the deck has to survive the
    round trip world -> flat -> world, and a point nowhere near the deck has to
    be refused rather than snapped to the nearest panel."""

    bridge, view = loaded_run
    pid = max(view.result.panels, key=lambda p: len(view.result.panels[p].development.uv_mm))
    xyz = np.asarray(view.result.panels[pid].development.mesh.base_vertices_mm, dtype=float)
    sample = xyz[np.linspace(0, len(xyz) - 1, 20, dtype=int)]

    picks = bridge.pick_flat(view, sample)

    assert len(picks) == 20
    assert all(p["panel_id"] is not None for p in picks)
    assert max(p["distance_mm"] for p in picks) < 0.5
    # Back the other way through the lift: the flat point is the same place.
    for pick, original in zip(picks, sample):
        placed = np.array([[pick["x"], pick["y"]]])
        uv = bridge._unplace(view.placements[pick["panel_id"]], placed)
        back = view.lifters[pick["panel_id"]](uv)[0]
        assert float(np.linalg.norm(back - original)) < 5.0

    (miss,) = bridge.pick_flat(view, np.array([[0.0, 0.0, 50000.0]]))
    assert miss["panel_id"] is None
    assert miss["distance_mm"] > bridge.PICK_MAX_MM


def test_a_hover_answers_fast_enough_to_follow_the_pointer(loaded_run):
    """This runs on every pointer move.  Slower than about ten milliseconds and
    the preview lags the mouse, which reads as the tool being broken."""

    bridge, view = loaded_run
    bridge.seam_hover(view, {}, "across", point_flat=[0.0, 0.0])      # warm the caches
    rng = np.random.default_rng(7)
    points = rng.uniform([-1900.0, -1100.0], [1900.0, 1100.0], size=(60, 2))

    timings = []
    for point in points:
        start = time.perf_counter()
        bridge.seam_hover(view, {}, "across", point_flat=point)
        timings.append((time.perf_counter() - start) * 1000.0)

    assert float(np.median(timings)) < 10.0, f"median hover {np.median(timings):.1f} ms"
    assert float(np.percentile(timings, 95)) < 25.0


def test_a_hover_over_the_boat_previews_the_seam_it_would_place(loaded_run):
    bridge, view = loaded_run
    opts = options()
    loops = sheets.read_fitted_dxf(sheetjob.source_dxf(AXIS_RUN))[0]
    polygons = seamplace.panel_polygons(loops, opts)
    biggest = max(polygons, key=lambda pid: polygons[pid].area)
    inside = polygons[biggest].representative_point()

    result = bridge.seam_hover(view, {}, "across", point_flat=[inside.x, inside.y])

    assert result["segments"]
    assert result["direction_deg"] == pytest.approx((AXIS_DEG + 90.0) % 180.0, abs=1e-3)
    assert result["boat"]["bow_sign"] == 1
    assert all(s["length_mm"] > 0 for s in result["segments"])
    # Drawn on the deck as well as in the layout, one world polyline per chord.
    assert len(result["world"]) == len(result["segments"])
    assert all(len(polyline) >= 2 and len(polyline[0]) == 3 for polyline in result["world"])

    # A diagonal is measured off the boat, and reads as such.  The sweep runs
    # from along-boat towards across-boat, and "across" is the ANTI-clockwise
    # quarter turn -- the very arrow run.json stores as transverse_axis -- so 30
    # typed into the angle box is 30 degrees anti-clockwise of the centreline,
    # which is what the seam list and the bow-up view then say it is.  It used to
    # come back at 150, the mirror image, leaning the wrong way over the planks.
    diagonal = bridge.seam_hover(view, {}, "angle", 30.0, point_flat=[inside.x, inside.y])
    assert diagonal["direction_deg"] == pytest.approx((AXIS_DEG + 30.0) % 180.0, abs=1e-3)

    off = bridge.seam_hover(view, {}, "across", point_flat=[500000.0, 500000.0])
    assert off["segments"] == [] and off["world"] == []


def test_a_seam_drawn_on_the_deck_really_lies_on_the_deck(loaded_run):
    """`seams_world` is what the 3D view draws to let the user check a join
    before cutting it.  If those points were not on the surface the check would
    be worthless, so they are measured against the development mesh itself."""

    igl = pytest.importorskip("igl", reason="the surface check needs libigl's exact distance")
    bridge, view = loaded_run
    result = bridge.sheet_preview(AXIS_RUN, {}, view=view)
    assert result["available"]
    lifted = result["seams_world"]
    assert lifted and any(entry["world"] for entry in lifted)

    points = np.vstack([np.asarray(piece, dtype=float)
                        for entry in lifted for piece in entry["world"] if len(piece) >= 2])
    nearest = np.full(len(points), np.inf)
    for pid in sorted(view.result.panels):
        development = view.result.panels[pid].development
        xyz = np.ascontiguousarray(np.asarray(development.mesh.base_vertices_mm, dtype=float))
        faces = np.ascontiguousarray(np.asarray(development.mesh.faces, dtype=np.int32))
        squared, _face, _closest = igl.point_mesh_squared_distance(
            np.ascontiguousarray(points), xyz, faces)
        nearest = np.minimum(nearest, np.sqrt(np.maximum(squared, 0.0)))

    # They are drawn OVER the deck, not in it -- see bridge.OVERLAY_PROUD_MM --
    # so what is checked is that they hug the surface at that offset.  Measured
    # on this run: median 1.50 mm, p95 1.56 mm, worst 7.2 mm.
    #
    # The worst case is not a bad lift.  A seam runs edge to edge of the part,
    # and the part's edge is the FITTED outline -- a fair curve -- while the
    # scan's meshed border is ragged, so the last stretch of a seam genuinely
    # overhangs the mesh by a few millimetres.  The plane fit extrapolates
    # correctly out there, but there is no triangle underneath to measure
    # against, so the honest bound is the raggedness itself: 8.3 mm was the
    # worst gap measured over every fitted loop of every cached run.
    assert float(np.median(nearest)) == pytest.approx(bridge.OVERLAY_PROUD_MM, abs=0.2)
    assert float(np.percentile(nearest, 95)) < bridge.OVERLAY_PROUD_MM + 1.0
    assert float(nearest.max()) < bridge.OVERLAY_PROUD_MM + 9.0
    # ...and the overhang is the rare case, not the normal one.
    assert float((nearest > bridge.OVERLAY_PROUD_MM + 1.0).mean()) < 0.05


def test_no_overlay_point_ends_up_below_the_deck(loaded_run):
    """The fix for the green CAM line dipping in and out of the model near the
    panel edges.  A fitted outline is a fair curve while the scan's meshed
    border is ragged, so a quarter to two thirds of every loop lands just off
    the meshed footprint; drawn exactly on the surface and depth-tested against
    a decimated preview mesh, it reads as sinking into the deck.  Every lifted
    overlay point now sits on the OUTWARD side of the surface, and the signed
    clearance says so -- an unsigned distance would pass just as happily with
    the line buried."""

    igl = pytest.importorskip("igl", reason="the surface check needs libigl's exact distance")
    bridge, view = loaded_run
    layers = {layer["id"]: layer for layer in bridge.overlays(view, lambda message: None)["layers"]}
    lifted_layers = [key for key in ("final_auto_file", "auto_cam", "pattern") if key in layers]
    assert lifted_layers, "the cached run has no lifted overlay layers to check"

    for layer_id in lifted_layers:
        points = np.vstack([np.asarray(piece, dtype=float)
                            for piece in layers[layer_id]["world"] if len(piece)])
        best = np.full(len(points), np.inf)
        signed = np.zeros(len(points))
        for pid in sorted(view.result.panels):
            development = view.result.panels[pid].development
            xyz = np.ascontiguousarray(np.asarray(development.mesh.base_vertices_mm, dtype=float))
            faces = np.ascontiguousarray(np.asarray(development.mesh.faces, dtype=np.int32))
            squared, face, closest = igl.point_mesh_squared_distance(
                np.ascontiguousarray(points), xyz, faces)
            corners = xyz[faces[np.asarray(face, dtype=int)]]
            normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
            normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-12)[:, None]
            gap = np.einsum("ij,ij->i", points - np.asarray(closest), normal)
            closer = np.sqrt(np.maximum(squared, 0.0)) < best
            best[closer] = np.sqrt(np.maximum(squared, 0.0))[closer]
            signed[closer] = gap[closer]

        assert float(signed.min()) > 0.0, f"{layer_id}: {int((signed <= 0).sum())} points below the deck"
        assert float(np.median(signed)) == pytest.approx(bridge.OVERLAY_PROUD_MM, abs=0.1)


def test_the_flat_layer_is_not_moved_by_the_3d_offset(loaded_run):
    """Floating the overlay is a rendering decision and must not touch anything
    that gets cut.  The flat layer is drawn from the DXF's own coordinates, so
    it has to match the DXF exactly whatever the 3D view does."""

    bridge, view = loaded_run
    layers = {layer["id"]: layer for layer in bridge.overlays(view, lambda message: None)["layers"]}
    if "final_auto_file" not in layers:
        pytest.skip("the cached run has no final_auto.dxf overlay")
    loops, _pattern = bridge.read_final_dxf(AXIS_RUN / "final_auto.dxf")

    drawn = [np.asarray(piece, dtype=float) for piece in layers["final_auto_file"]["flat"]]
    for pid, pieces in loops.items():
        for piece in pieces:
            match = min(drawn, key=lambda d: 0.0 if len(d) != len(piece)
                        else float(np.abs(d - piece[:, :2]).max()) - 1e12)
            if len(match) != len(piece):
                continue
            assert float(np.abs(match - piece[:, :2]).max()) <= 0.005 + 1e-9


# ------------------------------------------------- the HTTP contracts

def flask_app():
    server = pytest.importorskip("autodeck_app.server",
                                 reason="the app package is not on the path")
    return server, server.create_app()


@pytest.mark.parametrize("payload", [
    {"seam_gap_mm": "a bit"},
    {"seam_gap_mm": -1.0},
    {"seam_gap_mm": 1e9},
    {"part_spacing_mm": float("nan")},
    {"part_spacing_mm": float("inf")},
    {"nest_step_mm": 0.0},
    {"nest_step_mm": True},
    {"grain_angle_deg": "north"},
    {"grain_angle_deg": 5000.0},
    {"seam_axis_snap_deg": 400.0},
    {"seam_axis_snap_deg": [20.0]},
    {"seam_snap_angle_deg": -3.0},
    {"seam_snap_offset_mm": "25 mm"},
    {"seam_snap_max_move_mm": 100000.0},
    {"seam_snap_enabled": "maybe"},
    {"seam_axis_priority": 7},
    {"allow_180_rotation": {}},
])
def test_a_nonsense_sheet_option_is_a_400_naming_the_field(payload):
    """Never a 500.  The user is typing into a box on a page, and every wrong
    thing they can type has to come back as a sentence about the box."""

    from werkzeug.exceptions import BadRequest

    server, _app = flask_app()
    with pytest.raises(BadRequest) as raised:
        server.sheet_options(payload)
    assert next(iter(payload)) in raised.value.description


def test_the_sheet_options_that_are_meant_to_work_do():
    server, _app = flask_app()

    assert server.sheet_options({}) == {}
    # null and "" mean "leave it alone", which is how the page clears the manual
    # grain angle rather than pinning it to zero degrees.
    assert server.sheet_options({"grain_angle_deg": None, "seam_gap_mm": ""}) == {}
    assert server.sheet_options({
        "seam_gap_mm": 6, "grain_angle_deg": "-12.5", "seam_axis_snap_deg": 20,
        "seam_snap_enabled": "false", "seam_axis_priority": True,
        "seam_snap_use_axis": 0, "allow_180_rotation": "yes",
    }) == {"seam_gap_mm": 6.0, "grain_angle_deg": -12.5, "seam_axis_snap_deg": 20.0,
           "seam_snap_enabled": False, "seam_axis_priority": True,
           "seam_snap_use_axis": False, "allow_180_rotation": True}


@pytest.mark.parametrize("payload,field", [
    ({"points": []}, "points"),
    ({"points": "0,0,0"}, "points"),
    ({"points": [[0.0, 0.0]]}, "points"),
    ({"points": [[0.0, 0.0, "up"]]}, "points"),
    ({"points": [[0.0, 0.0, 0.0]] * 65}, "points"),
])
def test_a_malformed_pick_payload_is_a_400(payload, field):
    from werkzeug.exceptions import BadRequest

    server, _app = flask_app()
    with pytest.raises(BadRequest) as raised:
        server._as_points(payload["points"], 3, "points")
    assert field in raised.value.description


def test_the_pick_and_hover_endpoints_refuse_politely_with_no_run_open():
    """Both are pointer-driven, so the page can call them before a run is ready.
    That has to be a 400 with a sentence, not a 500 out of a None."""

    _server, app = flask_app()
    client = app.test_client()

    for path, payload in (("/api/seam/hover", {"mode": "across", "point_flat": [0.0, 0.0]}),
                          ("/api/sheets/seams", {"seams": []})):
        response = client.post(path, json=payload)
        assert response.status_code == 400, path
        assert "no run is open" in response.get_json()["error"]

    assert client.get("/api/sheets").status_code == 400
