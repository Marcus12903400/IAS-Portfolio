"""The button that works out where the seams should go, on its own.

    "I also want there to be a button that calculates the best seam positions
     for the lowest waste percentage that fits in the 39"x79" envelope"

What this file is really guarding is honesty, because that is where a search
like this fails quietly.  A bounded heuristic that reports a number it measured
with a coarse proxy, or that calls its answer "optimal", or that quietly leaves
a piece too big to cut, is worse than no button at all: the fabricator finds out
at the router, with the material already paid for.  So the tests here are
mostly not about finding a good arrangement.  They are about the reported
numbers being the ones the production pipeline actually produces, about the
failures saying so plainly, and about the user's own work surviving the press.

Three properties carry most of the weight:

  * Every number shown to the user comes from `sheetjob.plan` at production
    tolerances.  The search runs on 4 mm sampling and a 25 mm nesting grid, and
    a proxy is never allowed to be the thing anyone is told, so the tests
    re-plan the chosen seams from scratch and demand the same answer.
  * Every seam this module places is EXACTLY along the boat or EXACTLY square
    across it -- asserted at 1e-12, not with a tolerance, because a seam a
    hundredth of a degree out is the "pie shape thing" the user complained
    about and the whole reason the axis is the master reference.
  * A panel that already fits a sheet is left whole, and a hand-placed seam set
    is copied before it is replaced.  Both are cases where the right answer is
    to do nothing, and doing nothing is the hardest thing to test for.

The cached run used here is the one with a real boat frame.  A run fitted with
pattern "none" carries no axis, and this module refuses to invent one, so the
no-axis run is only ever used to check that the refusal is clean.
"""

import json
import math
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from autodeck2 import seamplan, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

RUNS = Path(__file__).resolve().parents[1] / "outputs" / "runs"
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"        # pattern "teak": has a boat frame
NO_AXIS_RUN = RUNS / "21kwcockpit-1-20260901-172519"     # pattern "none": has none


def has(run_dir):
    return (run_dir / "run.json").is_file() and (run_dir / "final_auto.dxf").is_file()


axis_run = pytest.mark.skipif(
    not has(AXIS_RUN), reason="the cached run with a boat frame is not in this checkout")
no_axis_run = pytest.mark.skipif(
    not has(NO_AXIS_RUN), reason="the cached run without a boat frame is not in this checkout")


# The seams the fabricator drew on this run by hand, kept here as data rather
# than read from the run's own seams.json.  Two reasons, and the second one is
# the one that matters: the file on disk is what the button REPLACES, so a test
# that read its baseline from there would be comparing the optimiser against
# whatever the optimiser last wrote, and would still pass after the comparison
# had stopped meaning anything.  These four lines produce, through the real
# pipeline, exactly the job the user was looking at when they asked for the
# button: 13 pieces, 5 of them too big to cut, on 2 sheets.  The two utilisation
# figures were 58.4% and 34.3% when the button was asked for; they are now 58.9%
# and 35.2%, because `read_fitted_dxf` used to drop every circle in the file and
# this deck has two -- a 187 mm round part of its own and a 207 mm round cut-out
# in panel 1.  Same seams, same job, one more part that always should have been
# on the sheet.
HAND_PLACED = [
    {"seam_id": "s1", "x1": 110.61724090576172, "y1": -1005.9564819335938,
     "x2": 86.17920684814453, "y2": -453.04376220703125, "panel_id": None},
    {"seam_id": "s2", "x1": 9.80996036529541, "y1": 451.1668701171875,
     "x2": -23.79237174987793, "y2": 1004.07958984375, "panel_id": None},
    {"seam_id": "s3", "x1": -1474.8062744140625, "y1": 374.7977600097656,
     "x2": 40.35771179199219, "y2": 484.76934814453125, "panel_id": None},
    {"seam_id": "s4", "x1": 1836.56005859375, "y1": 344.2501525878906,
     "x2": 1469.9881591796875, "y2": 328.97625732421875, "panel_id": None},
]

# The panel that already fits a sheet on this boat.  It is 881 x 106 mm, well
# inside the 990.6 x 2006.6 mm envelope, so cutting it would add a join for
# nothing.
FITS_ALREADY = 4


def config_with(**overrides):
    config = load_config()
    return {**config, "sheets": {**(config.get("sheets") or {}), **overrides}}


def make_run(tmp_path, source, seams=HAND_PLACED):
    """An isolated copy of a cached run, with a known seam set on it.

    Only the three files the optimiser reads are copied, which keeps this to a
    tenth of a second rather than the two megabytes of scan the run also holds.
    Working on a copy is not tidiness: `optimise` reads the run's seams.json to
    measure what it is being asked to beat, and the app writes that same file,
    so a test pointed at the real run would be reading a moving target.
    """

    work = tmp_path / source.name
    work.mkdir(parents=True, exist_ok=True)
    for name in ("final_auto.dxf", "run.json"):
        shutil.copyfile(source / name, work / name)
    if seams is not None:
        (work / "seams.json").write_text(json.dumps({"seams": seams}, indent=2), encoding="utf-8")
    return work


def masters_of(run_dir, options):
    frame, _warnings = sheetjob.resolve_frame(run_dir, options)
    return seamsnap.master_directions(frame.axis)


def unit(seam):
    vector = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1], dtype=float)
    return vector / float(np.hypot(*vector))


def objective(metrics):
    """The user's objective, in their order: a piece that will not fit is a
    failure and not a cost, then sheets, then waste, then joins."""

    return (len(metrics["oversize"]), metrics["sheet_count"],
            round(metrics["waste_percent"], 2), metrics["seam_count"])


# --------------------------------------------------------------------------
# One search, shared.  A full run is 27 seconds and every property below is a
# question about the SAME answer, so running it once and interrogating it is
# not just faster -- it means the tests cannot disagree about which arrangement
# they are talking about.


@pytest.fixture(scope="module")
def searched(tmp_path_factory):
    if not has(AXIS_RUN):
        pytest.skip("the cached run with a boat frame is not in this checkout")
    work = make_run(tmp_path_factory.mktemp("optimise"), AXIS_RUN)
    config = load_config()
    started = time.monotonic()
    result = seamplan.optimise(work, config, time_budget_s=90.0)
    return {"run_dir": work, "config": config, "result": result,
            "wall_s": time.monotonic() - started}


# --------------------------------------------------------------------------
# does it do the job at all


@axis_run
def test_the_baseline_this_button_was_asked_to_beat_is_what_we_think_it_is(tmp_path):
    """Guard the fixture itself.

    Every comparison in this file is against these four hand-drawn seams, so if
    the cached run is ever refitted and they stop producing the job the user was
    looking at, that has to fail loudly here rather than quietly weaken the
    tests that follow.
    """

    work = make_run(tmp_path, AXIS_RUN)
    before = sheetjob.plan(work, load_config(), write_files=False)

    assert before["status"] == "NEEDS_SEAMS"
    assert before["piece_count"] == 13
    assert len(before["oversize"]) == 5
    assert len(before["sheets"]) == 2
    utilisation = [sheet["utilisation"] for sheet in before["sheets"]]
    assert utilisation == pytest.approx([0.5888, 0.3518], abs=5e-4)


@axis_run
def test_every_oversize_piece_is_gone_measured_by_the_real_pipeline(searched):
    """The headline promise: nothing is left that cannot be cut.

    Asserted by re-planning the chosen seams through `sheetjob.plan` from
    scratch -- the same call the Export Sheets button makes -- and not by
    reading the optimiser's own report.  The report is checked separately, in
    `test_the_reported_numbers_survive_a_fresh_production_plan`; if this test
    trusted it, both tests would be reading the same number and neither would
    be measuring anything.

    A failure to clear the envelope is a legal outcome, so the alternative
    branch demands the honesty instead: the status has to say so and the reason
    has to name the pieces the fabricator still has to deal with.
    """

    result = searched["result"]
    if result["status"] != "OK":
        assert result["status"] == "NEEDS_SEAMS"
        after = result["report"]["after"]
        assert after["oversize"], "NEEDS_SEAMS has to name the pieces that are still too big"
        for piece_id in after["oversize"]:
            assert piece_id in result["reason"]
        assert "hand" in result["reason"]
        return

    fresh = sheetjob.plan(searched["run_dir"], searched["config"],
                          seams=result["seams"], write_files=False)
    assert fresh["oversize"] == []
    assert fresh["summary"]["unplaced_piece_ids"] == []
    assert fresh["status"] == "OK"


@axis_run
def test_the_reported_numbers_survive_a_fresh_production_plan(searched):
    """The proxy is never allowed to be the thing the user is told.

    The search cuts at 4 mm and nests on a 25 mm grid; production cuts at 1 mm,
    rebuilds the arcs and nests on a 5 mm grid.  So the report is only worth
    anything if re-running the chosen seams through production reproduces it
    exactly -- not approximately, since these are counts.
    """

    result = searched["result"]
    after = result["report"]["after"]
    fresh = sheetjob.plan(searched["run_dir"], searched["config"],
                          seams=result["seams"], write_files=False)

    assert len(fresh["sheets"]) == after["sheet_count"]
    assert fresh["seam_count"] == after["seam_count"]
    assert fresh["piece_count"] == after["piece_count"]
    assert len(fresh["oversize"]) == len(after["oversize"])
    assert fresh["status"] == after["status"]


@axis_run
def test_it_is_at_least_as_good_as_the_hand_placed_seams(searched):
    """Better on the objective the user gave, in the order they gave it.

    Sheet count going UP is not a failure here and the test must not treat it as
    one: the hand-placed job fitted on two sheets only because five of its
    thirteen pieces were too big to nest at all, and a piece that cannot be cut
    is not a saving.  That is exactly why oversize count sorts ahead of sheets
    in the objective, and why this compares the whole tuple rather than any one
    number.
    """

    report = searched["result"]["report"]
    before, after = report["before"], report["after"]

    if not report["improved"]:
        # Losing is allowed; pretending to have won is not.
        assert "no better than the seams already on this run" in searched["result"]["reason"]
        return

    assert objective(after) < objective(before)
    # On this boat the win is specifically that nothing is left uncuttable.
    assert len(before["oversize"]) == 5
    assert after["oversize"] == []
    assert after["waste_percent"] < before["waste_percent"]


@axis_run
def test_a_panel_that_already_fits_a_sheet_is_left_whole(searched):
    """Cutting a part that does not need cutting is a defect, not a trade-off.

    Every join has to be made, hidden and trusted, and no packing gain buys one
    back.  Panel 4 on this boat is 881 x 106 mm -- comfortably inside the
    envelope -- so a single seam on it would be a seam the fabricator was given
    for nothing.
    """

    result = searched["result"]
    assert {seam.panel_id for seam in result["seams"]}, "the search placed no seams at all"
    assert FITS_ALREADY not in {seam.panel_id for seam in result["seams"]}

    reported = {panel["panel_id"]: panel for panel in result["report"]["panels"]}
    assert reported[FITS_ALREADY]["along_cuts"] == 0
    assert reported[FITS_ALREADY]["across_cuts"] == 0
    assert reported[FITS_ALREADY]["note"] == "left whole -- it already fits a sheet"


# --------------------------------------------------------------------------
# the seams themselves


@axis_run
def test_every_seam_is_exactly_along_or_exactly_across_the_boat(searched):
    """1e-12, and the tolerance is the point.

    The masters come from `seamsnap.master_directions`, which is where the seam
    corrector's own masters come from, so a seam placed here has nothing left
    for the corrector to do to it.  Anything looser than this would let the long
    seams fan a fraction of a degree open against the plank lines, which is the
    defect the whole axis-first rule exists to design out.
    """

    result = searched["result"]
    options = sheets.settings(searched["config"])
    along, across = masters_of(searched["run_dir"], options)

    assert result["seams"]
    for seam in result["seams"]:
        assert seam.mode in ("along", "across")
        master = along if seam.mode == "along" else across
        assert abs(abs(float(np.dot(unit(seam), master))) - 1.0) < 1e-12


@axis_run
def test_two_seams_in_the_same_direction_are_exactly_parallel(searched):
    """So the pieces between them cannot fan open.

    Placing each seam exactly on a master is what makes this true, but it is
    worth asserting on the seams themselves: this is the property the
    fabricator can see on the finished deck, and it is one floating-point slip
    away at any time.
    """

    result = searched["result"]
    for mode in ("along", "across"):
        same = [seam for seam in result["seams"] if seam.mode == mode]
        for other in same[1:]:
            assert abs(abs(float(np.dot(unit(same[0]), unit(other)))) - 1.0) < 1e-12


@axis_run
def test_an_along_seam_is_parallel_to_the_teak_lines_on_this_boat(searched):
    """The same direction in three places, and this is the one the user sees.

    The boat axis oriented the pattern grooves that are already baked into
    final_auto.dxf, it orients the sheet, and it now orients the seams.  So a
    long seam has to come out parallel to the planks it runs beside -- measured
    against the actual grooves in the fitted DXF, not against the axis the seam
    was built from, which would only be checking arithmetic against itself.
    """

    _loops, pattern, _kind = sheets.read_fitted_dxf(AXIS_RUN / "final_auto.dxf")
    segments = [line for lines in pattern.values() for line in lines]
    if not segments:
        pytest.skip("this fitted DXF carries no pattern grooves to compare against")

    along_seams = [seam for seam in searched["result"]["seams"] if seam.mode == "along"]
    if not along_seams:
        pytest.skip("the winning arrangement has no along-boat seam")

    direction = unit(along_seams[0])
    worst = 0.0
    for segment in segments:
        span = segment[1] - segment[0]
        length = float(np.hypot(*span))
        if length < 1.0:
            continue
        cosine = abs(float(np.dot(span / length, direction)))
        worst = max(worst, math.degrees(math.acos(min(1.0, cosine))))
    assert worst < 0.1, f"an along-boat seam is {worst:.3f} deg off the planks"


@axis_run
def test_the_seams_come_back_ready_to_store(searched):
    """Real `sheets.Seam` objects in the placed frame, not a sketch of one.

    `panel_id` set so the cut lands on the panel it was chosen for, `mode` set
    so the direction is re-derived if the grain angle is changed later rather
    than frozen at today's axis, and `raw` set because every later correction
    restarts from the drawing.  Without these the seams would still cut
    correctly today and would drift the first time anything else moved.
    """

    for seam in searched["result"]["seams"]:
        assert isinstance(seam, sheets.Seam)
        assert seam.panel_id is not None
        assert seam.mode in ("along", "across")
        assert seam.raw is not None and len(seam.raw) == 4
        assert seam.snap is True
        assert math.hypot(seam.x2 - seam.x1, seam.y2 - seam.y1) > 1.0
        # It has to round-trip through the file format unchanged, since that is
        # how it reaches the cutter.
        assert sheets.Seam.from_dict(seam.to_dict()).drawn == seam.drawn


@axis_run
def test_each_seam_actually_crosses_the_panel_it_was_placed_on(searched):
    """A seam that misses its panel is a join the user is shown and never gets.

    `split_panel` extends every seam before cutting, so a seam that only nearly
    reaches would still cut -- which is precisely why this is worth asserting
    separately: the geometry would not complain.
    """

    from shapely.geometry import LineString

    options = sheets.settings(searched["config"])
    loops, _pattern, _kind = sheets.read_fitted_dxf(AXIS_RUN / "final_auto.dxf")
    step = float(options["sample_step_mm"])
    polygons = {}
    for panel_id, panel_loops in loops.items():
        outer, holes = sheets.classify_loops(panel_loops, step)
        if outer is not None:
            polygons[panel_id] = sheets.loop_polygon(outer, list(holes), step)

    for seam in searched["result"]["seams"]:
        drawn = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
        assert drawn.intersects(polygons[seam.panel_id])


# --------------------------------------------------------------------------
# repeatability and the clock


@axis_run
def test_the_same_run_gives_the_same_seams(searched, tmp_path):
    """Determinism, at the budget the button actually uses.

    Two identical presses have to give identical seams, or the fabricator
    cannot tell an improvement from noise, and a job re-run tomorrow cuts
    differently from the one quoted today.  There is no randomness in the
    search, but there is a clock, so this is asserted at the default budget --
    where the search finishes with time to spare and the clock therefore
    decides nothing.  At a budget short enough to bite, the answer legitimately
    depends on how far it got; `test_the_time_budget_is_honoured` covers that
    case instead.
    """

    again = seamplan.optimise(make_run(tmp_path, AXIS_RUN), load_config(), time_budget_s=90.0)
    first, second = searched["result"], again

    assert first["report"]["budget_exhausted"] is False
    assert second["report"]["budget_exhausted"] is False
    assert first["report"]["candidates_evaluated"] == second["report"]["candidates_evaluated"]
    assert [seam.to_dict() for seam in first["seams"]] == [seam.to_dict() for seam in second["seams"]]
    assert objective(first["report"]["after"]) == objective(second["report"]["after"])


@axis_run
def test_the_time_budget_is_honoured(tmp_path):
    """A budget the button can actually be held to, and a clean stop.

    Eight seconds is far less than this boat needs, so the search is cut off
    part way through: the point is that it comes back inside the budget, says
    the budget ran out, and still returns a complete, exactly measured answer
    rather than a partial one or an exception.  One exact confirmation always
    runs however little time is left, because an unchecked number is worse than
    a slow one.
    """

    work = make_run(tmp_path, AXIS_RUN)
    started = time.monotonic()
    result = seamplan.optimise(work, load_config(), time_budget_s=8.0)
    elapsed = time.monotonic() - started

    # The last exact confirmation is allowed to finish, so the ceiling is the
    # budget plus one production plan and not the budget alone.
    assert elapsed < 8.0 + 6.0
    assert result["report"]["elapsed_s"] <= elapsed + 0.5
    assert result["report"]["budget_exhausted"] is True
    assert result["report"]["candidates_confirmed"] >= 1
    assert result["report"]["after"] is not None
    # Whatever it found in the time, it is still a measured answer.
    fresh = sheetjob.plan(work, load_config(), seams=result["seams"], write_files=False)
    assert len(fresh["sheets"]) == result["report"]["after"]["sheet_count"]


@axis_run
def test_it_never_claims_to_be_optimal(searched):
    """"Best found", never "best possible".

    This is a few hundred arrangements out of an uncountable number, and the
    difference matters to someone deciding whether to accept the answer or move
    a seam themselves.
    """

    report = searched["result"]["report"]
    assert report["search_is_exhaustive"] is False
    assert "not a proof" in report["search_note"]
    assert report["candidates_evaluated"] > 0

    words = (searched["result"]["reason"] + " " + report["search_note"]).lower()
    assert "optimal" not in words
    assert "best possible" not in words
    assert "best found" in words


# --------------------------------------------------------------------------
# when it cannot do the job


@no_axis_run
def test_a_run_with_no_boat_axis_refuses_cleanly(tmp_path):
    """No axis, no seams, and a sentence that says what to do about it.

    Every seam this button places runs along the boat or square across it, so
    with no axis there is nothing to be square to.  Inventing one is the one
    thing that must not happen: the material is directional, and a guessed grain
    direction ruins the sheet rather than merely wasting some of it.
    """

    work = make_run(tmp_path, NO_AXIS_RUN, seams=[])
    result = seamplan.optimise(work, load_config(), time_budget_s=30.0)

    assert result["status"] == "NO_AXIS"
    assert result["seams"] == []
    assert "grain angle" in result["reason"]
    assert "along the boat" in result["reason"]
    # The refusal has the same shape as a success, so no caller has to branch
    # on whether the keys are there.
    report = result["report"]
    assert report["before"] is None and report["after"] is None
    assert report["improved"] is False
    assert report["warnings"], "the user has to be told why there is no axis"


@no_axis_run
def test_a_manual_grain_angle_unlocks_a_run_that_has_no_axis(tmp_path):
    """The documented way out of the refusal above, and it has to work.

    `resolve_frame` is the single place the grain is decided, and the optimiser
    goes through it like everything else, so setting the angle by hand gives the
    button the master direction it was missing.  The budget is deliberately
    short: what is being tested is that the axis arrives and the seams come out
    exactly square to it, not how good an arrangement this run can be given.
    """

    work = make_run(tmp_path, NO_AXIS_RUN, seams=[])
    result = seamplan.optimise(work, config_with(grain_angle_deg=3.680395518430263),
                               time_budget_s=15.0)

    assert result["status"] in ("OK", "NEEDS_SEAMS")
    assert result["seams"]
    along, across = masters_of(work, sheets.settings(config_with(grain_angle_deg=3.680395518430263)))
    for seam in result["seams"]:
        master = along if seam.mode == "along" else across
        assert abs(abs(float(np.dot(unit(seam), master))) - 1.0) < 1e-12


def test_a_run_with_no_fitted_outline_refuses_instead_of_raising(tmp_path):
    """Pressing the button on a run that has not been fitted yet is a mistake
    anyone can make, and it has to come back as a sentence rather than a
    traceback in a background job the user cannot see."""

    empty = tmp_path / "not-fitted-yet"
    empty.mkdir()
    result = seamplan.optimise(empty, load_config(), time_budget_s=5.0)

    assert result["status"] == "NO_GEOMETRY"
    assert result["seams"] == []
    assert "auto-fit" in result["reason"]
    assert result["report"]["after"] is None


# --------------------------------------------------------------------------
# the arithmetic the search rests on
#
# These are the two places a silent off-by-a-gap would let the search believe a
# piece fits when production then calls it too big, which is the one failure the
# user cannot see coming.


def test_the_minimum_cut_count_is_a_geometric_floor():
    """With n cuts there are n + 1 bands and n gaps, so no arrangement of n cuts
    can ever beat (span - n * gap) / (n + 1) per band.  The search starts at
    this floor and only adds cuts when the envelope is still broken, so if the
    floor were ever too low the search would spend its budget on arrangements
    that cannot work."""

    usable, gap = 990.6, 6.0
    assert seamplan._minimum_cuts(900.0, usable, gap) == 0
    assert seamplan._minimum_cuts(usable, usable, gap) == 0
    assert seamplan._minimum_cuts(usable + 0.5, usable, gap) == 1
    assert seamplan._minimum_cuts(1900.0, usable, gap) == 1
    assert seamplan._minimum_cuts(2100.0, usable, gap) == 2

    # Whatever it returns has to actually be enough.
    for span in (500.0, 991.0, 1500.0, 2058.0, 3994.2, 6000.0):
        count = seamplan._minimum_cuts(span, usable, gap)
        assert (span - count * gap) / (count + 1) <= usable + 1e-9
        if count:
            fewer = count - 1
            assert (span - fewer * gap) / (fewer + 1) > usable


def test_a_band_loses_a_whole_gap_only_where_it_meets_another_piece():
    """The kerf is split evenly either side of a seam, so a band between two
    cuts is a whole gap narrower than the distance between them while a band
    running out to the panel's own edge loses only the half on its cut side.
    Getting this asymmetry wrong is how a search convinces itself a piece fits
    when it does not -- and it errs by exactly one gap, which is small enough to
    survive a careless test and large enough to make a part uncuttable."""

    widths = seamplan._band_widths(0.0, 1000.0, [400.0], gap=6.0)
    assert widths == pytest.approx([397.0, 597.0])
    assert sum(widths) == pytest.approx(1000.0 - 6.0)

    widths = seamplan._band_widths(0.0, 1000.0, [300.0, 700.0], gap=6.0)
    assert widths == pytest.approx([297.0, 394.0, 297.0])
    assert sum(widths) == pytest.approx(1000.0 - 2 * 6.0)

    assert seamplan._band_widths(0.0, 1000.0, [], gap=6.0) == pytest.approx([1000.0])


def test_the_band_score_prefers_widths_that_fill_a_sheet():
    """The cheap stand-in that orders the shortlist: a 683 mm band fits a 990.6
    mm sheet once and leaves 300 mm of unusable strip, while a 411 mm band fits
    twice.  It is a ranking and never a prediction, but it has to at least get
    that comparison the right way round."""

    usable, spacing = 990.6, 20.0
    assert (seamplan._band_efficiency(411.0, usable, spacing)
            > seamplan._band_efficiency(683.0, usable, spacing))
    assert seamplan._band_efficiency(usable, usable, spacing) == pytest.approx(1.0)
    assert seamplan._band_efficiency(usable + 1.0, usable, spacing) == 0.0
    assert seamplan._band_efficiency(0.0, usable, spacing) == 0.0


@axis_run
def test_the_two_master_directions_are_exactly_ninety_degrees_apart(searched):
    """The cuts are generated in one frame and measured in another -- the sheet
    rotation for band widths, the master vectors for the seams -- so the two
    have to be the same right angle to the last bit, or a band measured as
    fitting would be cut at a slightly different angle."""

    options = sheets.settings(searched["config"])
    along, across = masters_of(searched["run_dir"], options)
    assert float(np.dot(along, across)) == 0.0

    rotation = sheets.sheet_transform(
        sheetjob.resolve_frame(searched["run_dir"], options)[0].axis)
    assert rotation[1] == pytest.approx(along, abs=1e-15)
    assert rotation[0] == pytest.approx(across, abs=1e-15)


# --------------------------------------------------------------------------
# the button, as the app presses it
#
# The engine can be right and the app still lose the user's work, so these go
# through `bridge.job_optimise_seams` rather than calling `optimise` directly.


def bridge_module():
    return pytest.importorskip(
        "autodeck_app.bridge",
        reason="the app package is not importable from this checkout")


@pytest.fixture(scope="module")
def pressed(tmp_path_factory):
    """One press of the button, through the app, shared by the tests below.

    The press is the expensive part and all three questions are about the same
    press, so it runs once.  The run directory it leaves behind is treated as
    read-only by everything that uses it -- a test that needs to change the
    files copies the directory first -- because a module fixture that one test
    mutates is a test order dependency waiting to happen.
    """

    if not has(AXIS_RUN):
        pytest.skip("the cached run with a boat frame is not in this checkout")
    bridge = bridge_module()
    work = make_run(tmp_path_factory.mktemp("pressed"), AXIS_RUN)
    original = (work / "seams.json").read_bytes()
    messages = []
    payload = bridge.job_optimise_seams(work, {}, messages.append, time_budget_s=60.0)
    return {"work": work, "payload": payload, "messages": messages, "original": original}


@axis_run
def test_the_hand_placed_seams_are_copied_before_they_are_replaced(pressed, tmp_path):
    """The one thing that must never happen is losing work the fabricator did
    by hand, so the copy is taken before anything is written and the log says
    where it went.  The copy has to be byte-identical and it has to restore to
    exactly the job that was there, because a backup nobody has tested is not a
    backup."""

    bridge = bridge_module()
    payload = pressed["payload"]
    backup = pressed["work"] / bridge.SEAMS_BACKUP

    assert backup.is_file()
    assert backup.read_bytes() == pressed["original"]
    assert payload["backup"] == bridge.SEAMS_BACKUP
    assert payload["seams_replaced"] == len(HAND_PLACED)
    assert payload["seams_written"] >= 1
    assert any(bridge.SEAMS_BACKUP in message for message in pressed["messages"])

    # The seams on the run are now the optimiser's, and the old ones come back
    # by copying one file over another.  Done on a copy of the directory, so
    # the shared press is left as it was found.
    assert ([seam.seam_id for seam in sheets.read_seams(pressed["work"])]
            != [s["seam_id"] for s in HAND_PLACED])
    work = tmp_path / "recovered"
    shutil.copytree(pressed["work"], work)
    shutil.copyfile(work / bridge.SEAMS_BACKUP, work / "seams.json")

    restored = sheetjob.plan(work, load_config(), write_files=False)
    assert [seam.seam_id for seam in sheets.read_seams(work)] == [s["seam_id"] for s in HAND_PLACED]
    assert len(restored["oversize"]) == 5
    assert len(restored["sheets"]) == 2


@axis_run
def test_the_seams_the_button_writes_are_the_seams_it_promised(pressed):
    """What lands in seams.json has to be what the reported numbers were
    measured on.

    They go out through `sheet_preview(save=True)`, the same path the seam tab
    writes with, which straightens every seam again on the way through.  These
    seams are already exactly on a master so there is nothing to straighten --
    but the corrector may still slide one onto a nearby fitted edge, so the
    check is that the file on disk re-plans to the numbers the user was given.
    """

    work, payload = pressed["work"], pressed["payload"]
    after = payload["after"]

    stored = sheets.read_seams(work)
    assert len(stored) == payload["seams_written"]

    fresh = sheetjob.plan(work, load_config(), write_files=False)
    assert len(fresh["sheets"]) == after["sheet_count"]
    assert len(fresh["oversize"]) == len(after["oversize"])
    assert fresh["seam_count"] == after["seam_count"]

    # And they are still exactly on the boat after the round trip through the
    # file and the corrector.
    along, across = masters_of(work, sheets.settings(load_config()))
    for seam in stored:
        master = along if seam.mode == "along" else across
        assert abs(abs(float(np.dot(unit(seam), master))) - 1.0) < 1e-12


@axis_run
def test_a_run_it_cannot_improve_on_is_left_completely_alone(pressed, tmp_path):
    """Pressing the button twice must not churn the file.

    The second press is being asked to beat an arrangement the first one just
    chose, so it will not improve on it -- and "did not improve" has to mean
    "leave the fabricator's seams exactly where they are", not "overwrite them
    with something equally good".  Rewriting equal work would also destroy the
    backup from the first press, which is the copy the user would want.

    The second press gets a deliberately shorter budget than the first, which
    makes the test's premise a certainty rather than a hope: a search that looks
    at strictly less cannot come back with strictly more, so this cannot start
    failing on a fast machine that happened to get further the second time.
    """

    bridge = bridge_module()
    work = tmp_path / "second-press"
    shutil.copytree(pressed["work"], work)

    settled = (work / "seams.json").read_bytes()
    backup = (work / bridge.SEAMS_BACKUP).read_bytes()
    messages = []
    payload = bridge.job_optimise_seams(work, {}, messages.append, time_budget_s=10.0)

    assert payload["improved"] is False
    assert payload["seams_written"] == 0
    assert payload["backup"] is None
    assert (work / "seams.json").read_bytes() == settled
    assert (work / bridge.SEAMS_BACKUP).read_bytes() == backup
    assert any("Nothing was changed" in message for message in messages)


@no_axis_run
def test_the_button_on_an_axis_less_run_changes_nothing_and_says_why(tmp_path):
    """The refusal has to survive the trip through the app: no backup taken, no
    file touched, and the reason passed on to the user rather than swallowed
    into a job that merely reports success."""

    bridge = bridge_module()
    work = make_run(tmp_path, NO_AXIS_RUN, seams=HAND_PLACED)
    before = (work / "seams.json").read_bytes()
    messages = []

    payload = bridge.job_optimise_seams(work, {}, messages.append, time_budget_s=20.0)

    assert payload["status"] == "NO_AXIS"
    assert payload["seams_written"] == 0
    assert payload["backup"] is None
    assert not (work / bridge.SEAMS_BACKUP).exists()
    assert (work / "seams.json").read_bytes() == before
    assert any("grain angle" in message for message in messages)


def test_the_backup_can_be_downloaded_again():
    """A copy the user cannot reach is not a recovery.

    `seams_previous.json` has to be in both allowlists -- the one that decides
    what the run's file list shows and the one that decides what the download
    endpoint will serve -- or the button quietly becomes irreversible.
    """

    bridge = bridge_module()
    server = pytest.importorskip(
        "autodeck_app.server", reason="the app package is not importable from this checkout")

    assert bridge.SEAMS_BACKUP == "seams_previous.json"
    assert bridge.SEAMS_BACKUP in server.SAFE_FILES
    listed = bridge.run_files(Path(__file__).parent)
    assert bridge.SEAMS_BACKUP in listed


# --------------------------------------------------------------------------
# making the pieces look right
#
#     "i want the algorythm to try and make the pices somewhat symetrical ...
#      and I like the pices to be as square/rectangular as possible so that the
#      seams are more in line with features like the edges/walls of the center
#      console of the boat."
#
# Three preferences, one dial, and one thing that is NOT a preference: the
# envelope and the sheet count sort ahead of all of it, at every setting.  The
# tests below measure both ends of the dial against each other rather than
# against a number typed in here, because the numbers a real deck produces are
# a property of that deck and would have to be re-typed every time the fitter
# changed.  What has to hold is the RELATION: tidier at 0.35 than at 0.00, and
# never at the cost of a sheet or of a piece that will not fit.


class _Loops:
    """Just enough of a panel for `_edge_offsets`, which only ever looks at the
    two loop lists.  Used to ask which of a panel's edges belong to a CUT-OUT --
    the console walls -- separately from its own outline."""

    def __init__(self, outer, holes=()):
        self.outer = outer
        self.holes = list(holes)


@pytest.fixture(scope="module")
def by_weight(tmp_path_factory):
    """The same search at both ends of the dial, each run twice.

    Four searches is the expensive part of this file, so they happen once and
    every question below is asked of the same four answers -- which also means
    the comparison tests and the determinism test cannot end up talking about
    different runs.  The middle of the dial is the `searched` fixture: 0.35 is
    the default, so that search is already being made.
    """

    if not has(AXIS_RUN):
        pytest.skip("the cached run with a boat frame is not in this checkout")
    out = {}
    for weight in (0.0, 1.0):
        attempts = []
        for index in (1, 2):
            work = make_run(tmp_path_factory.mktemp(f"tidy-{weight}-{index}"), AXIS_RUN)
            config = config_with(seam_tidiness_weight=weight)
            attempts.append((work, config, seamplan.optimise(work, config, time_budget_s=90.0)))
        out[weight] = attempts
    return out


def shape_of(result):
    return result["report"]["after"]["shape"]


@axis_run
def test_the_tidiness_weight_makes_the_pieces_more_symmetric_and_more_square(
        searched, by_weight):
    """The headline of the third piece of feedback, measured rather than hoped.

    Both numbers come from `sheetjob.plan`'s own output -- the pieces it cut and
    the seams it cut them with, after the corrector has moved them -- so this is
    a comparison of two decks and not of two search proxies.

    Nothing here is a hand-picked value.  The assertion is that turning the dial
    up produces a MORE symmetric and MORE rectangular deck than turning it off,
    on the same run, with the same budget.  On the cached boat that is symmetry
    0.61 -> 1.00 and squareness 0.63 -> 0.79, but the day the fitter changes,
    those move and the property is still the property.
    """

    off = shape_of(by_weight[0.0][0][2])
    on = shape_of(searched["result"])

    assert on["symmetry"] > off["symmetry"], (
        f"tidiness made the deck LESS symmetric: {off['symmetry']} -> {on['symmetry']}")
    assert on["rectangularity"] > off["rectangularity"], (
        f"tidiness made the pieces LESS square: "
        f"{off['rectangularity']} -> {on['rectangularity']}")

    # And the trade has to be visible, because a preference whose price the user
    # cannot see is one they cannot argue with.
    note = searched["result"]["report"]["tidiness_note"]
    assert "0.35" in note
    assert "symmetry" in note and "squareness" in note
    assert searched["result"]["report"]["tidiness_weight"] == pytest.approx(0.35)
    assert searched["result"]["report"]["centreline_known"] is True


@axis_run
def test_tidiness_never_costs_a_sheet_and_never_breaks_the_envelope(searched, by_weight):
    """The line tidiness is not allowed to cross, at every setting of the dial.

    A tidier deck that needs another sheet of TruGrain is not tidier, it is more
    expensive; a tidier deck with a piece that will not fit is not a deck at
    all.  Both are ordered ahead of tidiness in the objective, but an ordering
    only helps if the cheaper arrangement is actually FOUND -- so this is really
    a test of the two-climb search and of the shortlist being a superset, which
    is where two earlier attempts at this feature quietly lost a whole sheet.

    Measured against the pure-waste search on the same run, and re-measured
    through the production pipeline rather than read off the report.
    """

    baseline = by_weight[0.0][0][2]["report"]["after"]
    cases = [(0.0, by_weight[0.0][0]),
             (0.35, (searched["run_dir"], searched["config"], searched["result"])),
             (1.0, by_weight[1.0][0])]

    for weight, (work, config, result) in cases:
        after = result["report"]["after"]
        assert after["sheet_count"] <= baseline["sheet_count"], (
            f"tidiness weight {weight} added a sheet: "
            f"{baseline['sheet_count']} -> {after['sheet_count']}")
        assert after["oversize"] == [], (
            f"tidiness weight {weight} left a piece too big: {after['oversize']}")

        # Not from the report: cut the chosen seams again and measure every
        # finished piece the way the export does.
        fresh = sheetjob.plan(work, config, seams=result["seams"], write_files=False)
        assert fresh["oversize"] == []
        assert fresh["refused"] == []
        assert fresh["summary"]["unplaced_piece_ids"] == []
        assert len(fresh["sheets"]) <= baseline["sheet_count"]


@axis_run
def test_a_cut_that_can_sit_on_a_console_edge_does(searched, by_weight):
    """"so that the seams are more in line with features like the edges/walls
    of the center console of the boat".

    The console shows up in the fitted DXF as a cut-out in the deck panel, so
    "on the console wall" is "within the corrector's own reach of an edge that
    belongs to a hole and runs the same way as the cut".  Counted directly off
    the chosen seams, on both settings, so this measures the deck rather than
    the scorer that chose it.

    The reach is `seam_snap_offset_mm` because that is how far sideways the seam
    corrector will slide a seam to land it on an edge: a cut inside it is one
    that WILL be made on the wall, not one that nearly is.
    """

    options = sheets.settings(searched["config"])
    masters = masters_of(searched["run_dir"], options)
    reach = float(options["seam_snap_offset_mm"])
    loops, _pattern, _kind = sheets.read_fitted_dxf(AXIS_RUN / "final_auto.dxf")
    rotation = sheets.sheet_transform(
        sheetjob.resolve_frame(searched["run_dir"], options)[0].axis)
    panels = {panel.panel_id: panel
              for panel in seamplan._read_panels(loops, rotation, options)}

    def on_console(result):
        """How many of these seams run along a console cut-out edge."""

        along, across = masters
        count = 0
        for seam in result["seams"]:
            panel = panels[seam.panel_id]
            if not panel.holes:
                continue
            heading = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1], dtype=float)
            heading = heading / float(np.hypot(*heading))
            is_along = abs(float(np.dot(heading, along))) > 0.99
            normal = across if is_along else along
            middle = np.array([(seam.x1 + seam.x2) / 2.0, (seam.y1 + seam.y2) / 2.0])
            offset = float(np.dot(middle, normal))
            cutouts = seamplan._edge_offsets(
                _Loops(panel.holes[0], panel.holes[1:]),
                seamplan.ALONG if is_along else seamplan.ACROSS, masters, options)
            if any(abs(offset - edge) <= reach for edge in cutouts):
                count += 1
        return count

    tidy = on_console(searched["result"])
    plain = on_console(by_weight[0.0][0][2])

    assert tidy > plain, (
        f"tidiness put no more seams on a console edge than pure waste did "
        f"({plain} -> {tidy})")
    assert tidy >= 1

    # And it cost next to nothing, which is the whole claim: a cut that CAN sit
    # on a console edge does, when the material it costs is small.
    assert (searched["result"]["report"]["after"]["waste_percent"]
            <= by_weight[0.0][0][2]["report"]["after"]["waste_percent"] + 2.0)


@axis_run
def test_the_same_seams_come_back_at_every_tidiness_weight(searched, by_weight):
    """Determinism, at both ends of the dial as well as in the middle.

    The middle is `test_the_same_run_gives_the_same_seams`.  These two matter
    separately because the tidy path takes a different route through the code:
    a second hill climb, an extra candidate list per panel, and a re-scoring
    step that sorts on floating-point penalties.  A sort that fell back on the
    order a dict happened to be built in would show up here and nowhere else.
    """

    for weight, attempts in by_weight.items():
        first, second = attempts[0][2], attempts[1][2]
        assert first["report"]["budget_exhausted"] is False, (
            f"weight {weight} ran out of budget, so this proves nothing")
        assert second["report"]["budget_exhausted"] is False
        assert (first["report"]["candidates_evaluated"]
                == second["report"]["candidates_evaluated"]), f"weight {weight}"
        assert ([seam.to_dict() for seam in first["seams"]]
                == [seam.to_dict() for seam in second["seams"]]), f"weight {weight}"
        assert first["report"]["after"]["shape"] == second["report"]["after"]["shape"]


@axis_run
def test_the_tidiness_numbers_are_measured_on_the_production_pieces(searched):
    """The same honesty rule the waste number already lives under.

    A shape score taken off the 4 mm search proxy, or off the cuts the search
    picked rather than the seams the corrector actually cut with, would be a
    number nobody could check against the deck they get.  So the terms are
    re-derived here from an independent production plan and have to match what
    was reported.
    """

    result = searched["result"]
    options = sheets.settings(searched["config"])
    masters = masters_of(searched["run_dir"], options)
    rotation = sheets.sheet_transform(
        sheetjob.resolve_frame(searched["run_dir"], options)[0].axis)
    loops, _pattern, _kind = sheets.read_fitted_dxf(AXIS_RUN / "final_auto.dxf")
    panels = seamplan._read_panels(loops, rotation, options,
                                   seamplan._nest_offsets(searched["run_dir"]))
    centre = seamplan._centreline(searched["run_dir"], rotation)

    fresh = sheetjob.plan(searched["run_dir"], searched["config"],
                          seams=result["seams"], write_files=False)
    measured = seamplan._measured_shape(fresh, options, panels, masters, centre)

    assert measured.to_dict() == result["report"]["after"]["shape"]
    # The blended cost the objective sorted on is the exact waste plus the exact
    # penalty, and nothing else.
    after = result["report"]["after"]
    assert after["cost_percent"] == pytest.approx(
        after["waste_percent"] + after["tidiness_penalty_percent"], abs=5e-3)


@axis_run
def test_the_centreline_is_the_stored_one_and_not_a_bounding_box(searched):
    """Mirroring about the wrong line is worse than not mirroring at all.

    `teak.frame.origin_mm` is fitted to the midpoints of the boat's cross-boat
    width slices; a bounding-box centre is wherever the cockpit happens to be
    asymmetric.  On this deck the two agree to within a millimetre on the big
    panel and are hundreds of millimetres apart on the toe rails, which are
    nested well away from where they sit on the boat -- so a run that quietly
    used the box would look right on the panel that proves least and mirror a
    rail onto open water.
    """

    options = sheets.settings(searched["config"])
    rotation = sheets.sheet_transform(
        sheetjob.resolve_frame(searched["run_dir"], options)[0].axis)
    centre = seamplan._centreline(searched["run_dir"], rotation)

    stored = json.loads((AXIS_RUN / "run.json").read_text(encoding="utf-8"))
    origin = np.asarray(stored["teak"]["frame"]["origin_mm"], dtype=float)
    assert centre == pytest.approx(float(np.dot(rotation[0], origin)))

    # The toe rails are the case that proves it is not a bounding box: each is
    # nested away from the boat, and the shift is what puts it back.
    loops, _pattern, _kind = sheets.read_fitted_dxf(AXIS_RUN / "final_auto.dxf")
    panels = seamplan._read_panels(loops, rotation, options,
                                   seamplan._nest_offsets(searched["run_dir"]))
    shifted = [panel for panel in panels if abs(panel.boat_shift_mm) > 100.0]
    assert shifted, "this run has no nested panel, so the shift is untested"
    for panel in shifted:
        box_centre = (panel.bounds[0] + panel.bounds[1]) / 2.0
        assert abs(box_centre - (centre + panel.boat_shift_mm)) > 1.0

    # No stored frame, no mirror line, and no invented one.
    assert seamplan._centreline(Path(searched["run_dir"]) / "nothing-here", rotation) is None


def test_the_even_split_fallback_never_leaves_a_band_outside_the_envelope():
    """The fallback a panel gets when the clock beats it has to be CUTTABLE.

    Dividing a span into equal parts is not the same as leaving equal finished
    bands: the two outer bands lose kerf on one side only, so they come out a
    half gap wider than the inner ones, while `_minimum_cuts` sizes the cut
    count on the assumption that every band is (span - n * gap) / (n + 1).  The
    difference is gap * (n - 1) / (2 * (n + 1)) -- 1.75 mm at seven cuts -- and
    a panel sized so the minimum count only just works then produces a band
    OUTSIDE the envelope, silently, from the code path that runs when there was
    no time to do better.

    Swept over every span a boat could have, at the settings that ship.
    """

    usable, gap = 990.6, 6.0
    limit = usable - seamplan.BAND_MARGIN_MM
    worst_naive = 0.0
    worst = 0.0
    for tenths in range(5000, 80001):
        span = tenths / 10.0
        count = seamplan._minimum_cuts(span, limit, gap)

        naive = [span * (index + 1) / (count + 1) for index in range(count)]
        worst_naive = max(worst_naive,
                          max(seamplan._band_widths(0.0, span, naive, gap)) - usable)

        cuts = seamplan._even_bands(0.0, span, count, gap)
        assert len(cuts) == count
        widths = seamplan._band_widths(0.0, span, cuts, gap)
        assert widths == pytest.approx([widths[0]] * len(widths))
        worst = max(worst, max(widths) - usable)

    assert worst <= 0.0, f"an even split still overflows the envelope by {worst:.3f} mm"
    # And the guard is guarding something: the arithmetic it replaced really does
    # overflow, so this test cannot quietly pass on a no-op.
    assert worst_naive > 1.0, (
        "the naive even split no longer overflows, so this test proves nothing")


def test_a_seam_gap_of_zero_is_refused_because_it_cannot_cut():
    """A seam cuts by having its kerf subtracted from the panel, so a gap of
    zero subtracts nothing and the panel comes back whole.

    Every seam on the job then silently does nothing: the optimiser searches
    arrangements that can never be cut, reports the uncut panel as a
    2058 x 3994 mm piece, and the user is left looking at a "piece" the size of
    their deck with no explanation.  Found by running the optimiser at the
    extremes of every setting; it is the one setting that could still produce an
    oversize result, and it is refused here rather than defended against in five
    places downstream.
    """

    for gap in (0.0, -1.0):
        with pytest.raises(ValueError) as raised:
            sheets.settings({"sheets": {"seam_gap_mm": gap}})
        assert "seam_gap_mm" in str(raised.value)
        assert "does not cut" in str(raised.value)

    # Positive gaps are fine, however small.
    assert sheets.settings({"sheets": {"seam_gap_mm": 0.001}})["seam_gap_mm"] == 0.001

    # And the dial itself has to be a fraction.
    for weight in (-0.1, 1.5):
        with pytest.raises(ValueError) as raised:
            sheets.settings({"sheets": {"seam_tidiness_weight": weight}})
        assert "seam_tidiness_weight" in str(raised.value)
    assert sheets.DEFAULTS["seam_tidiness_weight"] == 0.35


def test_the_export_refuses_to_write_a_piece_that_cannot_be_cut(tmp_path):
    """The last gate, and the one that trusts nothing upstream.

    A DXF that quietly contains a part nobody can cut is the worst thing this
    program can produce: the material is bought and the router is running before
    anyone finds out.  So every piece is measured again as it is written, and
    one that is over is left OUT of the file -- loudly.

    The piece is made oversize behind the nester's back on purpose.  That is
    what a bug upstream looks like from here: the placement is valid, every
    check passed, and the geometry is wrong anyway.  A guard that only fired
    when the nester already knew would be guarding nothing.
    """

    from autodeck2 import nesting

    def rectangle(piece_id, width, length):
        ring = np.array([[0.0, 0.0, 0.0], [width, 0.0, 0.0],
                         [width, length, 0.0], [0.0, length, 0.0]])
        return sheets.Piece(piece_id=piece_id, panel_id=1, outer=sheets.Loop(ring),
                            holes=[], area_mm2=width * length)

    options = sheets.settings(None)
    rotation = np.eye(2)
    good, bad = rectangle("P-good", 400.0, 600.0), rectangle("P-bad", 900.0, 1900.0)
    sheet_list, summary, _warnings = nesting.nest([good, bad], rotation, options)
    assert summary["unplaced_piece_ids"] == [], "both have to nest for this to test anything"

    grown = {"P-good": good, "P-bad": rectangle("P-bad", 900.0, 2400.0)}
    messages, warnings = [], []
    written = nesting.write_sheet_dxfs(tmp_path, sheet_list, grown, rotation, {}, None,
                                       options, progress=messages.append, warnings=warnings)

    refused = [item for entry in written for item in entry["refused"]]
    assert [item["piece_id"] for item in refused] == ["P-bad"]
    assert refused[0]["over_length_mm"] == pytest.approx(2400.0 - 2006.6, abs=0.2)
    assert refused[0]["over_width_mm"] == 0.0
    assert "across the boat" in refused[0]["reason"]

    # Loud in the job log and loud in the report.
    assert any("REFUSED" in message and "P-bad" in message for message in messages)
    assert any("REFUSED" in warning for warning in warnings)

    # And the file really is short that part, rather than merely annotated.
    import ezdxf
    layers = set()
    for entry in written:
        assert "P-bad" not in entry["pieces"]
        doc = ezdxf.readfile(entry["path"])
        layers |= {str(item.dxf.layer) for item in doc.modelspace()
                   if str(item.dxf.layer).startswith("CAM__")}
    assert layers == {"CAM__P_good"}

    # A piece that fits is written untouched, so the guard is not simply
    # refusing everything.
    fine = nesting.write_sheet_dxfs(tmp_path, sheet_list, {"P-good": good, "P-bad": bad},
                                    rotation, {}, None, options)
    assert [item for entry in fine for item in entry["refused"]] == []
    assert sorted(p for entry in fine for p in entry["pieces"]) == ["P-bad", "P-good"]


@axis_run
def test_a_layout_it_cannot_clear_says_so_unmistakably(tmp_path):
    """"I could not fix this" must never read like "here is your layout".

    Forced by shrinking the envelope until nothing the button can do will fit,
    which is the position the user was in.  The sentence has to name every
    piece, give its size, say by how much it is over and say WHICH WAY the seam
    it still needs has to run -- a list of piece ids is not something anyone can
    act on without going and measuring them again.
    """

    work = make_run(tmp_path, AXIS_RUN)
    config = config_with(max_part_width_mm=120.0, max_part_length_mm=150.0,
                         min_piece_area_mm2=40000.0)
    result = seamplan.optimise(work, config, time_budget_s=20.0)

    assert result["status"] in ("NEEDS_SEAMS", "NO_LAYOUT"), (
        "this envelope turned out to be satisfiable, so the refusal is untested")
    reason = result["reason"]
    assert "CANNOT BE CUT" in reason or "placed by hand" in reason

    if result["status"] == "NEEDS_SEAMS":
        after = result["report"]["after"]
        assert after["oversize"]
        for piece_id in after["oversize"]:
            assert piece_id in reason
        assert "mm" in reason
        assert "ALONG the boat" in reason or "ACROSS the boat" in reason
        assert "by hand" in reason
        # Nothing about this may read as a success.
        assert "every piece fits" not in reason
