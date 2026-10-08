"""The seam corrector: a roughly dragged line becomes a deliberate cut.

The properties asserted here are the ones a fabricator would see on the
finished deck.  Most of them are about the boat's own axis, because that is
what the corrector is built around: the long seams have to come out EXACTLY
parallel to the centreline and the teak lines, the short ones EXACTLY square to
it, and "exactly" is meant literally -- two seams a hundredth of a degree apart
are the pie shape the user is complaining about.  So the tests check dot
products against 1e-12, not angles against a tolerance.

The rest are the old promises the module still has to keep: exact collinearity
and exact tangency where the boat has no opinion, a seam that is never
teleported somewhere it was not drawn, and snapping the same seam twice doing
nothing the second time.
"""

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from autodeck2 import seamsnap, sheets

RUNS = Path(__file__).resolve().parents[1] / "outputs" / "runs"


def options(**overrides):
    """The shipped defaults, plus the sampling step the sheet job would merge in."""

    return {**seamsnap.SNAP_DEFAULTS, "sample_step_mm": 1.0, **overrides}


def boat(angle_deg):
    """A boat axis pointing `angle_deg` off +X in the placed frame."""

    turn = math.radians(angle_deg)
    return np.array([math.cos(turn), math.sin(turn)])


# Bow to stern straight up the page. Used wherever a test asserts an exact
# coordinate, because this axis is free of the 1e-17 that cos(90 deg) carries.
UP_THE_BOAT = np.array([0.0, 1.0])


def seam_at(angle_deg, through, length_mm=300.0):
    """Endpoints of a seam `length_mm` long, centred on `through`, at `angle_deg`."""

    turn = math.radians(angle_deg)
    unit = np.array([math.cos(turn), math.sin(turn)])
    middle = np.asarray(through, dtype=float)
    start = middle - unit * (length_mm / 2.0)
    end = middle + unit * (length_mm / 2.0)
    return float(start[0]), float(start[1]), float(end[0]), float(end[1])


def line_ref(x1, y1, x2, y2, label="panel 1 cut-out edge", panel_id=1, kind="line"):
    return seamsnap.Reference(kind, label, panel_id,
                              np.array([x1, y1], dtype=float), np.array([x2, y2], dtype=float))


def edge_at(angle_deg, through, length_mm=400.0, **kwargs):
    """A fitted straight edge at a chosen angle -- a console side, say."""

    return line_ref(*seam_at(angle_deg, through, length_mm), **kwargs)


def arc_ref(radius, start_deg, sweep_deg, centre=(0.0, 0.0), label="panel 1 cut-out edge"):
    """One fitted fillet, as `references_from_loops` builds it from a bulge."""

    middle = np.asarray(centre, dtype=float)
    start = math.radians(start_deg)
    end = start + math.radians(sweep_deg)
    p0 = middle + radius * np.array([math.cos(start), math.sin(start)])
    p1 = middle + radius * np.array([math.cos(end), math.sin(end)])
    return seamsnap.Reference("arc", label, 1, p0, p1,
                              centre=middle, radius=float(radius), angles=(start, end))


def rounded_rect(width, height, radius, x=0.0, y=0.0):
    """Four straight sides and four quarter-turn fillets -- the shape a fitted
    panel actually has."""

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


def drawn_off(reference, angle_deg, offset_mm, length_mm=300.0):
    """A seam dragged `angle_deg` off `reference` and `offset_mm` to one side of
    it, centred on the middle of the edge -- i.e. what a mouse produces when
    the user meant to continue that edge."""

    unit = reference.unit
    normal = np.array([-unit[1], unit[0]])
    turn = math.radians(angle_deg)
    swung = unit * math.cos(turn) + normal * math.sin(turn)
    middle = reference.midpoint + normal * offset_mm
    start = middle - swung * (length_mm / 2.0)
    end = middle + swung * (length_mm / 2.0)
    return float(start[0]), float(start[1]), float(end[0]), float(end[1])


def endpoints(result):
    return np.array([[result.x1, result.y1], [result.x2, result.y2]], dtype=float)


def direction(result):
    span = np.array([result.x2 - result.x1, result.y2 - result.y1], dtype=float)
    return span / float(np.hypot(*span))


def middle_of(result):
    return np.array([(result.x1 + result.x2) / 2.0, (result.y1 + result.y2) / 2.0])


def parallelism(a, b):
    """|cos| between two directions: exactly 1.0 when they are exactly parallel,
    whichever way round each one points."""

    return abs(float(np.dot(a, b)))


def distance_from_line(point, result):
    """How far `point` is from the infinite line the corrected seam lies on."""

    p = endpoints(result)[0]
    unit = direction(result)
    return abs(float(unit[0] * (point[1] - p[1]) - unit[1] * (point[0] - p[0])))


# ------------------------------------------------------- the two directions

def test_the_defaults_are_the_numbers_the_user_asked_for():
    """The capture is deliberately four times the feature tolerance: the user
    says these seams are ALWAYS square, so five degrees would leave a sloppily
    dragged line crooked."""

    assert seamsnap.SNAP_DEFAULTS["seam_axis_snap_deg"] == 20.0
    assert seamsnap.SNAP_DEFAULTS["seam_axis_priority"] is True
    assert seamsnap.SNAP_DEFAULTS["seam_snap_angle_deg"] == 5.0
    assert seamsnap.SNAP_DEFAULTS["seam_snap_enabled"] is True


@pytest.mark.parametrize("angle", [0.0, 17.3, 45.0, 63.0, 89.9, 90.0, 137.5, -44.0, 180.0])
def test_the_two_master_directions_are_exactly_ninety_degrees_apart(angle):
    """Not nearly ninety. The short seams are cut against the long ones, and a
    rounding error between them is how a piece ends up wedge shaped."""

    along, across = seamsnap.master_directions(boat(angle))

    assert float(np.dot(along, across)) == 0.0
    assert seamsnap._direction_angle_deg(along, across) == 90.0
    assert float(np.hypot(*along)) == pytest.approx(1.0, abs=1e-15)
    assert float(np.hypot(*across)) == pytest.approx(1.0, abs=1e-15)


def test_a_run_with_no_detected_axis_gets_no_directions_invented_for_it():
    assert seamsnap.master_directions(None) is None
    assert seamsnap.master_directions([0.0, 0.0]) is None
    assert seamsnap.axis_references(None) == []


# ------------------------------------------------------------ axis capture

def test_a_seam_twelve_degrees_off_square_is_squared_across_the_boat():
    axis = boat(63.0)
    drawn = seam_at(63.0 - 90.0 + 12.0, (400.0, 250.0))

    result = seamsnap.snap_seam(*drawn, [], options(), axis=axis)

    assert result.applied and result.kind == "across-boat"
    assert result.angle_change_deg == pytest.approx(12.0, abs=1e-9)
    # Dead square to the boat: the dot product with the axis is zero, not small.
    assert abs(float(np.dot(direction(result), axis))) < 1e-12
    assert result.note == "squared across the boat (was 12.0 deg off)"
    assert result.reference_label == "across the boat"
    # It was squared where it was drawn: the middle of the stroke did not move.
    assert middle_of(result) == pytest.approx([400.0, 250.0], abs=1e-9)


def test_a_seam_three_degrees_off_is_lined_up_with_the_boat_and_the_planks():
    axis = boat(63.0)
    result = seamsnap.snap_seam(*seam_at(66.1, (0.0, 0.0), 400.0), [], options(), axis=axis)

    assert result.applied and result.kind == "along-boat"
    assert result.note == "lined up with the boat and the teak lines (was 3.1 deg off)"
    assert parallelism(direction(result), axis) == pytest.approx(1.0, abs=1e-12)


def test_a_seam_twenty_five_degrees_out_is_left_where_it_was_drawn():
    """Past the capture the user meant the angle they drew -- a deliberately
    diagonal seam is a real thing on a deck -- so it is kept."""

    axis = boat(63.0)
    drawn = seam_at(63.0 - 90.0 + 25.0, (400.0, 250.0))

    result = seamsnap.snap_seam(*drawn, [], options(), axis=axis)
    assert not result.applied and result.kind == "" and result.note == ""
    assert (result.x1, result.y1, result.x2, result.y2) == drawn

    # ... and it is the capture angle doing it, not something else.
    wider = seamsnap.snap_seam(*drawn, [], options(seam_axis_snap_deg=30.0), axis=axis)
    assert wider.applied and wider.kind == "across-boat"


def test_a_seam_fifteen_degrees_out_is_still_taken_as_meant_to_be_square():
    """Three times the feature tolerance, and still square: this is the whole
    point of the wide capture."""

    result = seamsnap.snap_seam(*seam_at(15.0, (0.0, 0.0), 600.0), [], options(),
                                axis=UP_THE_BOAT)
    assert result.applied and result.kind == "across-boat"
    assert result.y1 == result.y2


def test_squaring_a_long_seam_is_not_stopped_by_the_move_guard():
    """A two metre seam eight degrees out has to swing its ends 140 mm to come
    square, far past `seam_snap_max_move_mm`.  It must still come square: the
    guard exists to stop a seam being dragged onto geometry the user cannot
    see, and the boat direction is not hidden geometry -- it is the planks the
    user is looking at.  The middle of the stroke, where the seam was actually
    put, does not move at all."""

    result = seamsnap.snap_seam(*seam_at(8.0, (500.0, 500.0), 2000.0), [], options(),
                                axis=UP_THE_BOAT)

    assert result.applied and result.kind == "across-boat"
    assert result.moved_mm > options()["seam_snap_max_move_mm"]
    assert result.y1 == result.y2
    assert middle_of(result) == pytest.approx([500.0, 500.0], abs=1e-9)


# ------------------------------------------- the axis overrules the geometry

def test_the_boat_overrules_a_console_edge_the_seam_was_nearly_on():
    """The correction the user asked for, in one test.

    The seam is drawn 2 degrees off the console's own edge and 3 degrees off the
    boat.  The old corrector put it on the edge, because the edge was nearer in
    angle.  That is what produced a long seam fanning open against the planks --
    "pie shape things" -- so the boat now wins, and the edge is not even
    considered.  The companion test below proves the edge really was a live
    candidate and not just too far away to matter.
    """

    axis = boat(63.0)
    edge = edge_at(68.0, (400.0, 250.0))            # a scanned edge, 5 deg off the boat
    drawn = drawn_off(edge, -2.0, 8.0)              # 2 deg off the edge, 3 off the boat

    result = seamsnap.snap_seam(*drawn, [edge], options(), axis=axis)

    assert result.applied and result.kind == "along-boat"
    assert result.reference_label == "the boat and the teak lines"
    assert result.note == "lined up with the boat and the teak lines (was 3.0 deg off)"
    assert parallelism(direction(result), axis) == pytest.approx(1.0, abs=1e-12)
    # It did NOT end up on the console edge, and it is not parallel to it either.
    assert edge.offset_of(endpoints(result)[0]) > 1.0
    assert seamsnap._direction_angle_deg(direction(result), edge.unit) == pytest.approx(5.0, abs=1e-9)


def test_with_the_priority_switched_off_the_same_seam_goes_onto_the_edge():
    """`seam_axis_priority` False restores the old order, for a run whose
    detected axis is not trusted."""

    axis = boat(63.0)
    edge = edge_at(68.0, (400.0, 250.0))
    drawn = drawn_off(edge, -2.0, 8.0)

    result = seamsnap.snap_seam(*drawn, [edge], options(seam_axis_priority=False), axis=axis)

    assert result.applied and result.kind == "collinear"
    assert result.reference_label == "panel 1 cut-out edge"
    for point in endpoints(result):
        assert edge.offset_of(point) < 1e-9


def test_the_boat_can_be_switched_off_altogether():
    axis = boat(63.0)
    drawn = seam_at(63.0 - 90.0 + 12.0, (400.0, 250.0))
    result = seamsnap.snap_seam(*drawn, [], options(seam_snap_use_axis=False), axis=axis)

    assert not result.applied
    assert result.warnings == []            # switched off on purpose is not a problem


# ---------------------------------------------------------- exact parallels

def test_two_seams_squared_to_the_same_master_are_exactly_parallel():
    """Not "parallel to within a tolerance". Two joins that are a hundredth of
    a degree apart open a wedge along a two metre piece, which is the defect
    this whole rule exists to remove."""

    axis = boat(63.0)
    first = seamsnap.snap_seam(*seam_at(63.0 - 90.0 + 11.0, (0.0, 0.0), 400.0),
                               [], options(), axis=axis)
    # Drawn the other way round, and from the other side, on purpose.
    second = seamsnap.snap_seam(*seam_at(63.0 + 90.0 - 17.0, (900.0, -500.0), 250.0),
                                [], options(), axis=axis)

    assert first.applied and second.applied
    assert parallelism(direction(first), direction(second)) == pytest.approx(1.0, abs=1e-12)


def test_a_long_seam_is_exactly_parallel_to_the_teak_lines_of_the_same_run():
    """The teak pattern was drawn along this same axis, so "parallel to the
    boat" and "parallel to the planks" have to be the same statement."""

    teak_direction = boat(63.0)             # run.json -> teak.frame.longitudinal_axis
    result = seamsnap.snap_seam(*seam_at(66.5, (200.0, 300.0), 1500.0), [], options(),
                                axis=teak_direction)

    assert result.kind == "along-boat"
    assert parallelism(direction(result), teak_direction) == pytest.approx(1.0, abs=1e-12)


def test_a_short_seam_is_exactly_square_to_a_long_one():
    axis = boat(21.7)
    long_seam = seamsnap.snap_seam(*seam_at(25.0, (0.0, 0.0), 900.0), [], options(), axis=axis)
    short_seam = seamsnap.snap_seam(*seam_at(21.7 - 90.0 - 9.0, (300.0, 100.0), 400.0),
                                    [], options(), axis=axis)

    assert float(np.dot(direction(long_seam), direction(short_seam))) == pytest.approx(0.0, abs=1e-12)


# ------------------------------------------------------ position refinement

def test_a_squared_seam_slides_onto_a_console_edge_that_is_square_too():
    """The best of both worlds: exactly square to the boat AND exactly on the
    edge the user was aiming at -- but only because that edge turned out to be
    square itself."""

    edge = line_ref(0.0, 104.0, 400.0, 104.0)
    result = seamsnap.snap_seam(*seam_at(12.0, (200.0, 100.0)), [edge], options(),
                                axis=UP_THE_BOAT)

    assert result.applied and result.kind == "across-boat"
    assert result.reference_label == "panel 1 cut-out edge"
    assert result.note == "squared across the boat, then moved 4 mm onto panel 1 cut-out edge"
    assert (result.y1, result.y2) == (104.0, 104.0)
    for point in endpoints(result):
        assert edge.offset_of(point) < 1e-9


def test_refinement_translates_and_never_rotates():
    """Asserted bit for bit on the helper itself: the corrected direction is the
    same pair of floats going out as it was going in."""

    a = np.array([100.0, 0.0])
    b = np.array([100.0, 300.0])
    unit = np.array([0.0, 1.0])
    edge = line_ref(104.0, -50.0, 104.0, 500.0)

    moved_a, moved_b, reference, distance = seamsnap._refine_position(
        a, b, unit, [edge], options())

    assert reference is edge
    assert distance == pytest.approx(4.0, abs=1e-12)
    assert tuple(moved_b - moved_a) == tuple(b - a)         # bit identical direction
    assert tuple(moved_a) == (104.0, 0.0)
    assert tuple(moved_b) == (104.0, 300.0)                 # and exactly on the line


def test_an_edge_that_is_not_square_does_not_get_the_seam():
    """Three degrees off is not "square too". Sliding the seam onto that edge
    would leave a wedge between them, which is the thing being designed out."""

    edge = line_ref(0.0, 104.0, 400.0, 104.0 + 400.0 * math.tan(math.radians(3.0)))
    result = seamsnap.snap_seam(*seam_at(12.0, (200.0, 100.0)), [edge], options(),
                                axis=UP_THE_BOAT)

    assert result.applied and result.kind == "across-boat"
    assert result.reference_label == "across the boat"
    assert result.note == "squared across the boat (was 12.0 deg off)"
    assert result.y1 == pytest.approx(100.0, abs=1e-9)      # stayed where the boat put it


def test_refinement_respects_the_move_guard():
    """The rotation is exempt from the guard; the slide is not."""

    edge = line_ref(0.0, 140.0, 400.0, 140.0)               # square, but 40 mm away
    drawn = seam_at(12.0, (200.0, 100.0))

    reachable = seamsnap.snap_seam(*drawn, [edge], options(seam_snap_offset_mm=200.0),
                                   axis=UP_THE_BOAT)
    assert (reachable.y1, reachable.y2) == (140.0, 140.0)

    guarded = seamsnap.snap_seam(*drawn, [edge],
                                 options(seam_snap_offset_mm=200.0, seam_snap_max_move_mm=10.0),
                                 axis=UP_THE_BOAT)
    assert guarded.applied and guarded.kind == "across-boat"
    assert guarded.reference_label == "across the boat"
    assert guarded.y1 == pytest.approx(100.0, abs=1e-9)


def test_a_square_edge_out_of_reach_cannot_pull_the_seam_across_the_boat():
    edge = line_ref(3000.0, 104.0, 3400.0, 104.0)
    result = seamsnap.snap_seam(*seam_at(12.0, (200.0, 100.0)), [edge], options(),
                                axis=UP_THE_BOAT)

    assert result.kind == "across-boat"
    assert result.y1 == pytest.approx(100.0, abs=1e-9)


# ---------------------------------------------- diagonals: feature snapping

def test_a_diagonal_seam_still_lines_up_with_the_edge_it_was_continuing():
    """Forty-five degrees to the boat is a real seam, not a mistake, so the old
    corrector still has it: exactly collinear with the edge it continues."""

    edge = edge_at(45.0, (500.0, 500.0), 600.0)
    drawn = drawn_off(edge, 3.0, 8.0)
    drawn_unit = seamsnap._unit(np.array([drawn[2] - drawn[0], drawn[3] - drawn[1]]))
    for master in seamsnap.master_directions(UP_THE_BOAT):
        assert seamsnap._direction_angle_deg(drawn_unit, master) > 20.0

    result = seamsnap.snap_seam(*drawn, [edge], options(), axis=UP_THE_BOAT)

    assert result.applied and result.kind == "collinear"
    assert result.note == "straightened 3.0 deg onto panel 1 cut-out edge"
    for point in endpoints(result):
        assert edge.offset_of(point) < 1e-9


def test_a_diagonal_seam_is_made_exactly_tangent_to_a_fillet():
    """The corrected line touches the circle: it does not cut into it and does
    not miss it."""

    fillet = arc_ref(100.0, start_deg=0.0, sweep_deg=90.0)
    touch = 104.0 * np.array([math.cos(math.radians(45.0)), math.sin(math.radians(45.0))])
    result = seamsnap.snap_seam(*seam_at(135.0, touch, 200.0), [fillet], options(),
                                axis=UP_THE_BOAT)

    assert result.applied and result.kind == "tangent"
    assert result.note == "made tangent to panel 1 cut-out edge (r 100 mm)"
    assert distance_from_line(np.zeros(2), result) == pytest.approx(100.0, abs=1e-9)


def test_a_tangency_past_the_end_of_the_arc_is_refused():
    """The same fillet and the same grazing line, on the side of the circle the
    arc does not cover.  A quarter fillet in one corner must not straighten a
    seam in the opposite corner just because they share a circle."""

    fillet = arc_ref(100.0, start_deg=0.0, sweep_deg=90.0)
    touch = -104.0 * np.array([math.cos(math.radians(45.0)), math.sin(math.radians(45.0))])
    drawn = seam_at(135.0, touch, 200.0)

    result = seamsnap.snap_seam(*drawn, [fillet], options(), axis=UP_THE_BOAT)

    assert not result.applied
    assert (result.x1, result.y1, result.x2, result.y2) == drawn


def fillet_references(radius=100.0):
    """A full circle as two bulged segments, exactly how a fitted round cut-out
    is stored in the DXF."""

    loop = sheets.Loop(np.array([[radius, 0.0, 1.0], [-radius, 0.0, 1.0]]))
    return seamsnap.references_from_loops({1: [loop]}, options(), hole_ids={1: [0]})


def test_a_fitted_round_cut_out_gives_two_arc_references():
    references = fillet_references(100.0)
    assert [r.kind for r in references] == ["arc", "arc"]
    assert {round(float(r.radius), 9) for r in references} == {100.0}


def test_a_tangent_snap_keeps_the_seam_the_length_it_was_drawn():
    drawn = (-60.0, 108.0, 60.0, 104.0)
    before = math.hypot(drawn[2] - drawn[0], drawn[3] - drawn[1])
    result = seamsnap.snap_seam(*drawn, fillet_references(100.0), options())
    after = math.hypot(result.x2 - result.x1, result.y2 - result.y1)
    assert result.applied and result.kind == "tangent"
    assert after == pytest.approx(before, abs=1e-9)


def test_a_seam_nowhere_near_the_fillet_is_not_dragged_onto_it():
    # 40 mm clear of the circle: past seam_snap_offset_mm, so it stays put.
    result = seamsnap.snap_seam(-60.0, 140.0, 60.0, 140.0, fillet_references(100.0), options())
    assert not result.applied


def test_squaring_onto_an_edge_leaves_the_meeting_point_exactly_where_it_was():
    """Rule 3 for a diagonal: the user placed the corner of the join, so only
    the far end may swing."""

    edge = edge_at(45.0, (0.0, 0.0), 600.0, label="panel 2 outer edge", panel_id=2)
    drawn = (0.0, 0.0, *seam_at(-45.0 + 2.0, np.zeros(2), 600.0)[2:])

    result = seamsnap.snap_seam(*drawn, [edge], options(), axis=UP_THE_BOAT)

    assert result.applied and result.kind == "perpendicular"
    assert (result.x1, result.y1) == (0.0, 0.0)
    assert abs(seamsnap._direction_angle_deg(direction(result), edge.unit) - 90.0) < 1e-9
    assert result.note.startswith("squared to panel 2 outer edge")


# ------------------------------------------------------------- the guards

def test_a_correction_that_would_teleport_the_seam_is_refused():
    """A two metre seam four degrees off a distant edge would have its ends
    swung 70 mm to line it up. That is not a correction, that is a different
    seam, so it is refused -- and accepted again once the guard allows it."""

    edge = line_ref(0.0, 0.0, 2000.0, 0.0, label="panel 3 outer edge", panel_id=3)
    drawn = (0.0, 100.0, 2000.0, 100.0 + 2000.0 * math.tan(math.radians(4.0)))

    assert not seamsnap.snap_seam(*drawn, [edge], options()).applied

    allowed = seamsnap.snap_seam(*drawn, [edge], options(seam_snap_max_move_mm=200.0))
    assert allowed.applied and allowed.kind == "parallel"
    assert 60.0 < allowed.moved_mm < 200.0


def test_a_parallel_edge_two_metres_away_cannot_reach_the_seam():
    """Reach is what stops the far side of the boat grabbing a seam it has no
    business touching, even though it is perfectly parallel to it."""

    edge = line_ref(0.0, 0.0, 400.0, 0.0)
    assert not seamsnap.snap_seam(*drawn_off(edge, 3.0, 2000.0), [edge], options()).applied

    assert seamsnap.snap_seam(*drawn_off(edge, 3.0, 2000.0), [edge],
                              options(seam_snap_reach_mm=4000.0,
                                      seam_snap_max_move_mm=4000.0)).applied


def test_a_zero_length_seam_is_left_alone():
    result = seamsnap.snap_seam(200.0, 30.0, 200.0, 30.0, [line_ref(0.0, 0.0, 400.0, 0.0)],
                                options(), axis=UP_THE_BOAT)
    assert not result.applied
    assert (result.x1, result.y1, result.x2, result.y2) == (200.0, 30.0, 200.0, 30.0)


def test_a_stray_click_of_a_seam_is_left_alone():
    drawn = (200.0, 30.0, 202.0, 33.0)          # 3.6 mm: under MIN_SEAM_LENGTH_MM
    result = seamsnap.snap_seam(*drawn, [], options(), axis=UP_THE_BOAT)
    assert not result.applied
    assert (result.x1, result.y1, result.x2, result.y2) == drawn


def test_no_references_and_no_axis_means_no_correction():
    result = seamsnap.snap_seam(0.0, 0.0, 300.0, 10.0, [], options())
    assert not result.applied and result.reference_label == ""


def test_the_axis_alone_snaps_a_seam_with_no_fitted_geometry_at_all():
    """A run whose seams are drawn before anything else is fitted still gets
    square seams -- the boat direction is all the corrector needs."""

    result = seamsnap.snap_seam(*seam_at(6.0, (0.0, 0.0)), [], options(), axis=UP_THE_BOAT)
    assert result.applied and result.kind == "across-boat"


def test_the_corrector_can_be_switched_off_entirely():
    edge = line_ref(0.0, 0.0, 400.0, 0.0)
    drawn = drawn_off(edge, 3.0, 8.0)
    result = seamsnap.snap_seam(*drawn, [edge], options(seam_snap_enabled=False),
                                axis=UP_THE_BOAT)
    assert not result.applied
    assert (result.x1, result.y1, result.x2, result.y2) == drawn


# ----------------------------------------------------------- no boat axis

def test_with_no_boat_axis_the_seam_falls_back_to_edges_and_says_so():
    """A run with no pattern frame still snaps to fitted geometry, and the user
    is told why nothing was squared, because otherwise it looks like it worked."""

    edge = line_ref(0.0, 0.0, 400.0, 0.0)
    result = seamsnap.snap_seam(*drawn_off(edge, 3.0, 8.0), [edge], options())

    assert result.applied and result.kind == "collinear"
    assert result.note == "straightened 3.0 deg onto panel 1 cut-out edge"
    assert result.warnings == [seamsnap.NO_AXIS_WARNING]
    assert "grain angle" in result.warnings[0]


def test_the_warning_is_there_even_when_nothing_was_near_the_seam():
    result = seamsnap.snap_seam(0.0, 0.0, 300.0, 10.0, [], options())
    assert not result.applied
    assert result.warnings == [seamsnap.NO_AXIS_WARNING]
    assert result.to_dict()["warnings"] == [seamsnap.NO_AXIS_WARNING]


# ------------------------------------------------- where the axis comes from

def test_the_axis_can_travel_in_the_reference_list_instead():
    """A caller that already builds one reference list does not have to pass
    the axis separately."""

    axis = boat(63.0)
    references = seamsnap.axis_references(axis)
    assert [r.label for r in references] == [seamsnap.ALONG_LABEL, seamsnap.ACROSS_LABEL]

    drawn = seam_at(66.0, (100.0, 100.0), 500.0)
    from_list = seamsnap.snap_seam(*drawn, references, options())
    passed_in = seamsnap.snap_seam(*drawn, [], options(), axis=axis)

    assert from_list.applied and from_list.kind == "along-boat"
    assert from_list.warnings == []
    assert (from_list.x1, from_list.y1) == pytest.approx((passed_in.x1, passed_in.y1), abs=1e-9)


def test_either_half_of_the_axis_pair_describes_the_same_boat():
    axis = boat(63.0)
    along_ref, across_ref = seamsnap.axis_references(axis)

    from_along = seamsnap.axis_from_references([along_ref])
    from_across = seamsnap.axis_from_references([across_ref])

    assert parallelism(from_along, axis) == pytest.approx(1.0, abs=1e-12)
    assert parallelism(from_across, axis) == pytest.approx(1.0, abs=1e-12)
    assert seamsnap.axis_from_references([line_ref(0.0, 0.0, 400.0, 0.0)]) is None


# -------------------------------------------- signs, wraps and awkward angles

@pytest.mark.parametrize("drawn_deg,expected", [
    (89.4, "along-boat"), (90.6, "along-boat"),         # nearly vertical, either side
    (-0.4, "across-boat"), (0.4, "across-boat"),        # nearly horizontal, either side
    (179.6, "across-boat"), (180.4, "across-boat"),     # ... drawn right to left
    (269.5, "along-boat"), (271.0, "along-boat"),       # ... drawn top to bottom
])
def test_a_master_is_recognised_whichever_way_the_seam_was_dragged(drawn_deg, expected):
    """A seam has no head and no tail: a line drawn stern to bow is zero degrees
    off the boat, not a hundred and eighty."""

    result = seamsnap.snap_seam(*seam_at(drawn_deg, (300.0, 400.0), 500.0), [], options(),
                                axis=UP_THE_BOAT)
    assert result.applied and result.kind == expected
    if expected == "along-boat":
        assert result.x1 == result.x2
    else:
        assert result.y1 == result.y2


def test_a_seam_dragged_backwards_keeps_its_ends_in_the_order_it_was_drawn():
    """The endpoints are what the UI draws the handles on; swapping them under
    the user's mouse would be its own bug."""

    drawn = seam_at(268.0, (300.0, 400.0), 500.0)       # down the boat, 2 deg out
    result = seamsnap.snap_seam(*drawn, [], options(), axis=UP_THE_BOAT)

    assert result.applied and result.kind == "along-boat"
    assert result.y1 > result.y2
    assert math.hypot(result.x1 - drawn[0], result.y1 - drawn[1]) < 20.0


# ------------------------------------------------------------ idempotence

def _across_case():
    return seam_at(12.0, (200.0, 100.0)), [], {"axis": UP_THE_BOAT}


def _refined_case():
    return (seam_at(12.0, (200.0, 100.0)), [line_ref(0.0, 104.0, 400.0, 104.0)],
            {"axis": UP_THE_BOAT})


def _collinear_case():
    edge = line_ref(0.0, 0.0, 400.0, 0.0)
    return drawn_off(edge, 3.0, 8.0), [edge], {}


def _tangent_case():
    return (-60.0, 108.0, 60.0, 104.0), fillet_references(100.0), {}


def _perpendicular_case():
    edge = edge_at(45.0, (0.0, 0.0), 600.0)
    return ((0.0, 0.0, *seam_at(-43.0, np.zeros(2), 600.0)[2:]), [edge],
            {"axis": UP_THE_BOAT})


@pytest.mark.parametrize("case", [_across_case, _refined_case, _collinear_case,
                                  _tangent_case, _perpendicular_case])
def test_snapping_an_already_snapped_seam_does_nothing(case):
    """Every UI edit re-runs the corrector, so a snap that nudged the seam a
    little further each time would walk it off the deck."""

    drawn, references, kwargs = case()
    first = seamsnap.snap_seam(*drawn, references, options(), **kwargs)
    assert first.applied

    second = seamsnap.snap_seam(first.x1, first.y1, first.x2, first.y2, references,
                                options(), **kwargs)
    assert not second.applied and second.note == ""
    assert (second.x1, second.y1, second.x2, second.y2) == (first.x1, first.y1, first.x2, first.y2)


@pytest.mark.parametrize("axis", [None, UP_THE_BOAT, boat(63.0)])
@pytest.mark.parametrize("priority", [True, False])
def test_snapping_is_idempotent_over_a_deck_full_of_random_seams(axis, priority):
    """The property has to hold for every seam a mouse can produce, not just the
    ones a test author thought of: a thousand random lines against a real panel
    outline with a console in it, snapped twice, must come back bit identical
    the second time."""

    settings = options(seam_axis_priority=priority)
    references = seamsnap.references_from_loops(
        {1: [rounded_rect(1600.0, 900.0, 120.0), rounded_rect(400.0, 300.0, 40.0, x=600.0, y=300.0)]},
        settings)
    rng = np.random.default_rng(11)

    for _ in range(1000):
        start = rng.uniform(-200.0, 1800.0, 2)
        angle = rng.uniform(0.0, math.pi)
        length = rng.uniform(100.0, 1200.0)
        end = start + length * np.array([math.cos(angle), math.sin(angle)])

        first = seamsnap.snap_seam(start[0], start[1], end[0], end[1], references,
                                   settings, axis=axis)
        second = seamsnap.snap_seam(first.x1, first.y1, first.x2, first.y2, references,
                                    settings, axis=axis)

        assert not second.applied, f"{first.note} then {second.note}"
        assert (second.x1, second.y1, second.x2, second.y2) == (first.x1, first.y1, first.x2, first.y2)


def test_a_seam_already_on_the_edge_comes_back_bit_identical():
    edge = line_ref(0.0, 0.0, 400.0, 0.0)
    result = seamsnap.snap_seam(100.0, 0.0, 300.0, 0.0, [edge], options())

    assert not result.applied and result.note == ""
    assert (result.x1, result.y1, result.x2, result.y2) == (100.0, 0.0, 300.0, 0.0)


# --------------------------------------------------------- several seams

def test_every_long_seam_in_a_job_comes_out_exactly_parallel():
    """Three joins down a deck, drawn by hand at three different angles, end up
    exactly parallel to each other and to the planks -- whether or not they are
    anywhere near each other, because they are all squared to the same boat."""

    axis = boat(63.0)
    drawn = [sheets.Seam(f"s{i}", *seam_at(63.0 + offset, (400.0 * i, 200.0 * i), 700.0))
             for i, offset in enumerate((4.0, -6.5, 11.0))]

    results = seamsnap.snap_seams(drawn, [], options(), axis=axis)

    assert [r.kind for r in results] == ["along-boat"] * 3
    for result in results:
        assert parallelism(direction(result), axis) == pytest.approx(1.0, abs=1e-12)
        assert parallelism(direction(result), direction(results[0])) == pytest.approx(1.0, abs=1e-12)


def test_the_second_seam_lands_exactly_on_the_first():
    """Two joins across the same deck, drawn a few millimetres apart, must be
    one join: the first is squared, and the second is squared and then slid onto
    it -- which it is allowed to do only because the first is now exactly square
    by construction."""

    first = sheets.Seam("s1", 0.0, 0.0, 400.0, 400.0 * math.tan(math.radians(3.0)))
    second = sheets.Seam("s2", 500.0, 6.0, 900.0, 6.0 + 400.0 * math.tan(math.radians(2.0)))

    results = seamsnap.snap_seams([first, second], [], options(), axis=UP_THE_BOAT)

    assert [r.kind for r in results] == ["across-boat", "across-boat"]
    assert results[0].y1 == results[0].y2
    assert results[1].reference_label == "seam s1"
    assert results[1].note.startswith("squared across the boat, then moved ")
    assert results[1].y1 == pytest.approx(results[0].y1, abs=1e-9)
    assert results[1].y2 == pytest.approx(results[0].y1, abs=1e-9)


def test_two_joins_across_the_same_deck_are_not_merged_into_one():
    """Two seams placed SIDE BY SIDE, close enough to be inside the snap
    offset, stay two seams.

    A seam already on the deck is offered as a reference so that the next one
    can CONTINUE it, which is the test above.  Sliding one onto another that
    runs alongside it is a different thing entirely: it deletes the strip of
    deck between them, and the piece the fabricator meant to cut is simply not
    there.  Twenty-four millimetres apart -- inside the twenty-five millimetre
    `seam_snap_offset_mm` -- used to come back exactly on top of each other, and
    the hover preview drew them apart right up to the click, because the preview
    does not know about seams that already exist.
    """

    gap = 24.0
    assert gap < seamsnap.SNAP_DEFAULTS["seam_snap_offset_mm"]
    first = sheets.Seam("s1", 0.0, 0.0, 900.0, 0.0)
    second = sheets.Seam("s2", 100.0, gap, 800.0, gap)

    results = seamsnap.snap_seams([first, second], [], options(), axis=UP_THE_BOAT)

    assert results[1].y1 == pytest.approx(gap, abs=1e-12)
    assert results[1].y2 == pytest.approx(gap, abs=1e-12)
    assert results[1].reference_label != "seam s1"
    assert "seam s1" not in results[1].note
    # ...and they are still exactly parallel, which is what the axis is for.
    assert parallelism(direction(results[0]), direction(results[1])) == pytest.approx(1.0, abs=1e-12)


def test_a_fitted_edge_alongside_still_captures_the_seam():
    """The rule above is about seams, and only about seams.  Running a squared
    seam ALONG a console edge is the whole point of the position refinement, and
    a console edge is not a cut, so nothing is lost by landing on it."""

    edge = line_ref(0.0, 0.0, 900.0, 0.0)
    (result,) = seamsnap.snap_seams([sheets.Seam("s1", 100.0, 8.0, 800.0, 8.0)],
                                    [edge], options(), axis=UP_THE_BOAT)

    assert result.applied and result.reference_label == "panel 1 cut-out edge"
    assert result.y1 == pytest.approx(0.0, abs=1e-9)
    assert result.y2 == pytest.approx(0.0, abs=1e-9)


def test_a_seam_that_only_touches_the_end_of_another_still_continues_it():
    """The boundary of the same rule: two joins that meet end to end are one
    join, and have to come out as one straight line across the gap."""

    first = sheets.Seam("s1", 0.0, 0.0, 400.0, 0.0)
    second = sheets.Seam("s2", 400.0, 9.0, 800.0, 9.0)

    results = seamsnap.snap_seams([first, second], [], options(), axis=UP_THE_BOAT)

    assert results[1].reference_label == "seam s1"
    assert results[1].y1 == pytest.approx(0.0, abs=1e-9)


def test_seams_snap_from_bare_coordinate_tuples_too():
    edge = line_ref(0.0, 0.0, 400.0, 0.0)
    (result,) = seamsnap.snap_seams([drawn_off(edge, 3.0, 8.0)], [edge], options())
    assert result.applied and result.kind == "collinear"


# --------------------------------------------------------------- references

def test_references_from_loops_name_outer_and_cut_out_edges():
    panel = rounded_rect(1600.0, 900.0, 120.0)
    console = rounded_rect(400.0, 300.0, 40.0, x=600.0, y=300.0)
    references = seamsnap.references_from_loops({1: [panel, console]}, options())

    outer = [r for r in references if r.label == "panel 1 outer edge"]
    cutout = [r for r in references if r.label == "panel 1 cut-out edge"]
    assert len(references) == len(outer) + len(cutout)
    # four straight sides and four fillets, each way round.
    assert sorted(r.kind for r in outer) == ["arc"] * 4 + ["line"] * 4
    assert sorted(r.kind for r in cutout) == ["arc"] * 4 + ["line"] * 4
    assert all(r.panel_id == 1 for r in references)
    assert {round(float(r.radius), 6) for r in cutout if r.kind == "arc"} == {40.0}


def test_short_stubs_and_tiny_radii_are_not_offered_as_straightedges():
    """Scan noise leaves 10 mm slivers behind; lining a two metre seam up with
    one of those would be worse than leaving it as drawn."""

    loop = sheets.Loop(np.array([
        [0.0, 0.0, 0.0],        # 10 mm stub -- too short
        [10.0, 0.0, 1.0],       # 5 mm radius half turn -- too tight
        [10.0, 10.0, 0.0],
        [400.0, 10.0, 0.0],
        [400.0, 400.0, 0.0],
        [0.0, 400.0, 0.0],
    ]))
    references = seamsnap.references_from_loops({1: [loop]}, options(), hole_ids={1: []})
    assert [r.kind for r in references] == ["line"] * 4
    assert min(r.length_mm for r in references) >= options()["seam_snap_min_ref_length_mm"]


def test_the_axis_references_read_as_english_in_a_note():
    """The notes are built by putting a verb in front of these labels, so they
    have to be phrases and not names."""

    along, across = seamsnap.axis_references(UP_THE_BOAT)
    assert f"lined up with {along.label}" == "lined up with the boat and the teak lines"
    assert f"squared {across.label}" == "squared across the boat"
    assert [r.kind for r in (along, across)] == ["axis", "axis"]
    assert [r.panel_id for r in (along, across)] == [None, None]


# ------------------------------------------------------------ real geometry

# The two cached runs these tests are written against.  They are the same scan
# fitted twice, and the difference between them is the whole point:
#
#   AXIS_RUN     pattern "teak", so run.json carries a real boat frame -- a boat
#                lying 3.68 degrees off the placed frame's +X.  Everything that
#                is about the boat's own direction has to be measured against
#                this one.
#   NO_AXIS_RUN  pattern "none", so `teak.frame` is null.  It can only exercise
#                the fallback, and a test that used it to check axis behaviour
#                would pass while asserting nothing.
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"
NO_AXIS_RUN = RUNS / "21kwcockpit-1-20260901-172519"


def _cached_fitted_loops(run_dir=None):
    """The fitted loops of a cached run whose DXF has a panel with a cut-out in
    it, since a run of plain outlines cannot exercise the cut-out labelling.

    Pinned to a named run rather than "whichever is newest": the runs directory
    is a live scratch area on the developer's machine, and a test that silently
    re-aims itself at whatever was fitted last hour is not a regression test.
    """

    path = (run_dir or AXIS_RUN) / "final_auto.dxf"
    if not path.is_file():
        return None
    loops, _pattern, _kind = sheets.read_fitted_dxf(path)
    if not any(len(panel) > 1 for panel in loops.values()):
        return None
    return loops


def _stored_frame(run_dir):
    """run.json -> teak.frame, read the way `sheetjob.boat_axis` reads it."""

    meta_path = run_dir / "run.json"
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return ((meta.get("teak") or {}).get("frame") or None)


def test_references_from_a_real_fitted_dxf_look_sane():
    loops = _cached_fitted_loops()
    if loops is None:
        pytest.skip("no cached run with a final_auto.dxf containing cut-outs")
    opts = options()
    references = seamsnap.references_from_loops(loops, opts)

    assert len(references) >= 20
    labels = {r.label for r in references}
    assert all(re.fullmatch(r"panel \d+ (outer|cut-out) edge", label) for label in labels)
    assert any(label.endswith("outer edge") for label in labels)
    assert any(label.endswith("cut-out edge") for label in labels)   # the console
    # Every panel with fitted loops contributes at least its outer boundary.
    assert {r.panel_id for r in references} == set(loops)
    assert all(r.length_mm >= opts["seam_snap_min_ref_length_mm"]
               for r in references if r.kind == "line")
    assert all(r.radius >= opts["seam_snap_min_ref_radius_mm"]
               for r in references if r.kind == "arc")

    # The outer/cut-out labelling is the same split the cut itself uses.
    for panel_id, panel_loops in sorted(loops.items()):
        _outer, holes = sheets.classify_loops(panel_loops, opts["sample_step_mm"])
        cutouts = [r for r in references if r.label == f"panel {panel_id} cut-out edge"]
        assert len(cutouts) <= sum(len(hole.vertices) for hole in holes)
        if not holes:
            assert cutouts == []


def test_a_seam_drawn_along_a_real_fitted_edge_is_squared_and_lands_beside_it():
    """End to end on real scanned geometry: take the longest fitted edge as the
    boat direction -- which is roughly what it is on a deck panel -- draw a seam
    three degrees off it and 8 mm to one side, and it comes back exactly along
    the boat AND running with a real fitted edge, half a seam gap off it on the
    far side of the material so the kerf shaves nothing off the part."""

    loops = _cached_fitted_loops()
    if loops is None:
        pytest.skip("no cached run with a final_auto.dxf containing cut-outs")
    opts = options(seam_gap_mm=6.0)
    references = seamsnap.references_from_loops(loops, opts)
    edge = max((r for r in references if r.kind == "line"), key=lambda r: r.length_mm)

    result = seamsnap.snap_seam(*drawn_off(edge, 3.0, 8.0, 200.0), references, opts,
                                axis=edge.unit)

    assert result.applied and result.kind == "along-boat"
    assert parallelism(direction(result), edge.unit) == pytest.approx(1.0, abs=1e-12)
    half_gap = float(opts["seam_gap_mm"]) / 2.0
    landed = min(r.offset_of(endpoints(result)[0]) for r in references
                 if r.kind == "line" and seamsnap._direction_angle_deg(r.unit, edge.unit) < 0.5)
    # Exactly half a gap off the edge: close enough that the join reads as
    # running on it, far enough that the kerf (half a gap either side of the
    # seam) never crosses into the material.
    assert landed == pytest.approx(half_gap, abs=1e-6)
    # ...and on the side AWAY from the material, which is the whole point: the
    # piece keeps the fitted outline and the hatch stays its fitted size.
    assert any(r.material_normal is not None
               and float(np.dot(np.array(endpoints(result)[0]) - r.p0, r.material_normal)) < 0.0
               for r in references
               if r.kind == "line" and seamsnap._direction_angle_deg(r.unit, edge.unit) < 0.5
               and r.offset_of(endpoints(result)[0]) < half_gap + 1e-6)

    again = seamsnap.snap_seam(result.x1, result.y1, result.x2, result.y2, references, opts,
                               axis=edge.unit)
    assert not again.applied


# ------------------------------------------- against the boat this run stores
#
# Everything above builds its own axis out of an angle, which proves the maths
# but never touches a boat.  These read the axis the pattern stage actually
# detected and wrote into run.json, on a real scanned deck, and they exist
# because the run these tests were first written against had `teak.frame` null:
# it could not exercise a single axis rule, and every axis assertion made
# against it would have been vacuous.


def test_the_fixture_run_really_does_carry_a_boat_frame():
    """A guard on the fixture, not on the code.

    If this run ever loses its pattern frame -- refitted with pattern "none",
    say -- every test below it would quietly stop testing the boat instead of
    failing.  So the frame is asserted here, once, loudly.
    """

    frame = _stored_frame(AXIS_RUN)
    if frame is None:
        pytest.skip(f"cached run {AXIS_RUN.name} is not present")

    assert frame["longitudinal_axis"] == pytest.approx([0.9979376404082442, 0.06419085492829811])
    assert frame["axis_angle_degrees"] == pytest.approx(3.680395518430263)
    assert frame["bow_sign"] == 1
    assert frame["bow_confidence"] == pytest.approx(0.824070661710407)
    # And the other one genuinely has none, which is what makes it the fallback
    # fixture rather than a second axis fixture.
    assert _stored_frame(NO_AXIS_RUN) is None


def test_on_a_real_boat_square_is_not_zero_or_ninety_degrees():
    """This boat lies 3.68 degrees off the placed frame's +X, so a seam squared
    across it sits at 93.68 degrees and NOT at 90.

    A test that expected 90 here would be testing the coordinate system rather
    than the boat, and would pass on a boat that happened to be laid out axis
    aligned while saying nothing about any other.
    """

    frame = _stored_frame(AXIS_RUN)
    if frame is None:
        pytest.skip(f"cached run {AXIS_RUN.name} is not present")
    axis = np.asarray(frame["longitudinal_axis"], dtype=float)

    along = seamsnap.snap_seam(*seam_at(9.0, (0.0, 0.0), 900.0), [], options(), axis=axis)
    across = seamsnap.snap_seam(*seam_at(99.0, (0.0, 0.0), 900.0), [], options(), axis=axis)

    assert [along.kind, across.kind] == ["along-boat", "across-boat"]
    assert math.degrees(math.atan2(*direction(along)[::-1])) % 180.0 == pytest.approx(3.6804, abs=1e-3)
    assert math.degrees(math.atan2(*direction(across)[::-1])) % 180.0 == pytest.approx(93.6804, abs=1e-3)
    # Ninety degrees apart exactly, wherever that lands on the page.
    assert float(np.dot(direction(along), direction(across))) == pytest.approx(0.0, abs=1e-15)


def test_a_seam_squared_to_the_real_boat_is_exactly_parallel_to_its_teak_lines():
    """The planks in this run's fitted DXF were drawn along the same stored
    axis, so a long seam squared to that axis has to come out exactly parallel
    to them.  That is the "no pie shape things" promise, measured against the
    actual plank lines the fabricator will be looking at rather than against a
    number in a test.
    """

    frame = _stored_frame(AXIS_RUN)
    source = AXIS_RUN / "final_auto.dxf"
    if frame is None or not source.is_file():
        pytest.skip(f"cached run {AXIS_RUN.name} is not present")
    _loops, pattern, kind = sheets.read_fitted_dxf(source)
    lines = [seg for segments in pattern.values() for seg in segments]
    if not lines:
        pytest.skip("the cached run has no pattern lines in its fitted DXF")
    assert kind == "teak"

    axis = np.asarray(frame["longitudinal_axis"], dtype=float)
    seam = seamsnap.snap_seam(*seam_at(frame["axis_angle_degrees"] + 7.5, (0.0, 0.0), 1200.0),
                              [], options(), axis=axis)
    assert seam.kind == "along-boat"

    # The DXF holds the planks to a hundredth of a millimetre, so a 63 mm plank
    # segment can read up to about 0.02 deg out of true no matter how exactly it
    # was generated. The seam has to sit inside that, not merely near it.
    worst = max(seamsnap._direction_angle_deg(direction(seam), _unit_of(seg)) for seg in lines)
    assert worst < 0.05, f"a long seam is {worst:.4f} deg off the planks it runs beside"


def _unit_of(segment):
    span = np.asarray(segment, dtype=float)[1, :2] - np.asarray(segment, dtype=float)[0, :2]
    return span / float(np.hypot(*span))


def test_this_runs_own_saved_seams_come_back_square_and_stay_there(tmp_path):
    """The four seams the user actually drew on this deck, straightened against
    this deck's own axis and its own fitted edges: every one ends up on a master
    direction, and straightening the result again moves nothing.

    The seams are PINNED as data (shared with test_nesting_speed) rather than
    read from the run's live seams.json -- that file belongs to the app, and
    placing one seam in the product used to point this test at data created
    minutes earlier.
    """

    from test_nesting_speed import PINNED_SEAMS

    frame = _stored_frame(AXIS_RUN)
    loops = _cached_fitted_loops(AXIS_RUN)
    if frame is None or loops is None:
        pytest.skip(f"cached run {AXIS_RUN.name} is not present, or has no seams")
    (tmp_path / "seams.json").write_text(
        json.dumps({"seams": PINNED_SEAMS[AXIS_RUN.name]}, indent=2), encoding="utf-8")
    saved = sheets.read_seams(tmp_path)
    if not saved:
        pytest.skip("no pinned seams for this run")
    axis = np.asarray(frame["longitudinal_axis"], dtype=float)
    along, across = seamsnap.master_directions(axis)
    opts = options(seam_gap_mm=6.0)
    references = seamsnap.references_from_loops(loops, opts)

    results = seamsnap.snap_seams(saved, references, opts, axis=axis)

    assert len(results) == len(saved)
    assert all(r.kind in ("along-boat", "across-boat") for r in results), \
        [r.kind for r in results]
    for result in results:
        master = along if result.kind == "along-boat" else across
        assert parallelism(direction(result), master) == pytest.approx(1.0, abs=1e-12)
        assert not result.warnings
        again = seamsnap.snap_seam(result.x1, result.y1, result.x2, result.y2,
                                   references, opts, axis=axis)
        assert not again.applied, again.note
        assert (again.x1, again.y1, again.x2, again.y2) == (result.x1, result.y1, result.x2, result.y2)

    # Every long seam in the job is exactly parallel to every other long seam.
    long_seams = [direction(r) for r in results if r.kind == "along-boat"]
    for other in long_seams[1:]:
        assert parallelism(long_seams[0], other) == pytest.approx(1.0, abs=1e-12)


def test_the_run_with_no_pattern_frame_is_the_one_that_warns(tmp_path):
    """The fallback fixture, doing the only job it can do: with `teak.frame`
    null there is no direction to square to, so the seams are lined up with
    fitted edges only and every result says so.

    Pinned like the test above: the live seams.json is the app's to rewrite.
    """

    from test_nesting_speed import PINNED_SEAMS

    loops = _cached_fitted_loops(NO_AXIS_RUN)
    if loops is None:
        pytest.skip(f"cached run {NO_AXIS_RUN.name} is not present")
    (tmp_path / "seams.json").write_text(
        json.dumps({"seams": PINNED_SEAMS[NO_AXIS_RUN.name]}, indent=2), encoding="utf-8")
    saved = sheets.read_seams(tmp_path)
    if not saved:
        pytest.skip("no pinned seams for this run")
    assert _stored_frame(NO_AXIS_RUN) is None
    opts = options(seam_gap_mm=6.0)
    references = seamsnap.references_from_loops(loops, opts)

    results = seamsnap.snap_seams(saved, references, opts, axis=None)

    assert all(seamsnap.NO_AXIS_WARNING in r.warnings for r in results)
    assert all(r.kind not in ("along-boat", "across-boat") for r in results)
