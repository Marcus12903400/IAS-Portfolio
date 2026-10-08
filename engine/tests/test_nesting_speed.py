"""The nester's speed, and the placements it is not allowed to move to get it.

Nesting used to be the whole cost of a sheet job: a full `plan()` on a real
five-panel deck spent about 14 of its 15 seconds inside `nesting.nest`, because
the search built a fresh translated copy of a 1500-to-4000 point polygon at
every one of the ~82000 grid positions on a 1016 x 2032 mm sheet, for every
piece, in both allowed rotations.  That is far too slow to sit under a button
that wants to try several seam sets and keep the best one.

The search is now filtered: a handful of sample points taken from the piece
itself prove most positions blocked in one vectorised call per row, and only
what survives reaches the real polygon test.  That is a pure filter -- it can
only skip positions that were going to come back "blocked" anyway -- so the
answer must not move by so much as a bit.

THIS FILE IS THE PROOF OF THAT.  The numbers in EXPECTED are what the nester as
it stood BEFORE any of the speed work puts out, on the two runs cached in
engine/outputs/runs, and they are compared with `==` rather than
`pytest.approx` deliberately: this is the code that decides what gets cut out
of the customer's material, and a nester that is faster but puts a part
somewhere else is a regression, not an improvement.  If a change to the NESTER
makes these fail, the change is wrong -- do not restate the table.

The table may only be restated when the PIECES change, and then only by
re-capturing it from the old nester on the new pieces, so that it goes on
proving the one thing it is here to prove.  That has happened once: circular
parts and circular cut-outs are written to DXF as two-vertex bulged polylines,
`sheets.read_fitted_dxf` used to drop every one of them, and both cached runs
turned out to be missing a 187 mm round part (P5) and a 207 mm round cut-out in
panel 1.  Both runs were re-captured under `ecb7b30`'s nester (that commit is `958d8f0` since the 2026-10-07 history rewrite) with those loops
restored, and the current nester reproduces the result bit for bit.

The seams are PINNED here as data rather than read from each run's own
seams.json.  That file belongs to the app: placing a seam rewrites it, and so
does the "find the best seam layout" button, so a table keyed to it would fail
the moment anyone used the product -- pointing the next engineer straight at
nesting.py, which would not be the cause.

The two cached runs differ in a way that matters.  AXIS_RUN was fitted with a
teak pattern and so carries a real boat frame: its sheet transform is a genuine
3.68 degree rotation and its pieces are the awkward shapes a real deck yields.
NO_AXIS_RUN was fitted with pattern "none", has no frame at all, and so covers
the unrotated fallback.  Both are needed; neither alone would do.
"""

import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import Polygon
from shapely.prepared import prep

from autodeck2 import nesting, sheetjob, sheets
from autodeck2.config import load_config

RUNS = Path(__file__).resolve().parents[1] / "outputs" / "runs"
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"        # pattern "teak": has a boat frame
NO_AXIS_RUN = RUNS / "21kwcockpit-1-20260901-172519"     # pattern "none": has none

# The seams each cached run is measured with.  Data, not the run's own
# seams.json -- see the module docstring.
PINNED_SEAMS = {
    AXIS_RUN.name: [
        {"seam_id": "s1", "x1": 110.61724090576172, "y1": -1005.9564819335938,
         "x2": 86.17920684814453, "y2": -453.04376220703125, "panel_id": None},
        {"seam_id": "s2", "x1": 9.80996036529541, "y1": 451.1668701171875,
         "x2": -23.79237174987793, "y2": 1004.07958984375, "panel_id": None},
        {"seam_id": "s3", "x1": -1474.8062744140625, "y1": 374.7977600097656,
         "x2": 40.35771179199219, "y2": 484.76934814453125, "panel_id": None},
        {"seam_id": "s4", "x1": 1836.56005859375, "y1": 344.2501525878906,
         "x2": 1469.9881591796875, "y2": 328.97625732421875, "panel_id": None},
    ],
    NO_AXIS_RUN.name: [
        {"seam_id": "s1", "x1": -4.544900894165039, "y1": 449.0463562011719,
         "x2": -51.352622985839844, "y2": 1001.9615478515625, "panel_id": None},
        {"seam_id": "s2", "x1": 74.44297790527344, "y1": -460.7771911621094,
         "x2": 118.32514190673828, "y2": -993.2139282226562, "panel_id": None},
        {"seam_id": "s3", "x1": 1923.344482421875, "y1": 320.3253173828125,
         "x2": 1513.7779541015625, "y2": 305.6979064941406, "panel_id": None},
        {"seam_id": "s4", "x1": 1935.0467529296875, "y1": -45.35935592651367,
         "x2": 1537.181396484375, "y2": -71.68865203857422, "panel_id": None},
        {"seam_id": "s5", "x1": 1221.22998046875, "y1": 601.171142578125,
         "x2": 1186.1241455078125, "y2": 899.56982421875, "panel_id": None},
        {"seam_id": "s6", "x1": 1297.29248046875, "y1": -393.4910583496094,
         "x2": 1329.472900390625, "y2": -724.0701293945312, "panel_id": None},
        {"seam_id": "s7", "x1": 36.41168975830078, "y1": 478.30108642578125,
         "x2": -1449.7305908203125, "y2": 361.28204345703125, "panel_id": None},
        {"seam_id": "s8", "x1": 103.69784545898438, "y1": -452.0007629394531,
         "x2": -1376.59375, "y2": -601.2000732421875, "panel_id": None},
    ],
}

# (sheet_index, piece_id, rotation_deg, offset_x, offset_y, origin_x, origin_y)
# in the order the nester emits them, which is itself part of the contract: it
# is the order the pieces went down, largest first.
EXPECTED = {
    AXIS_RUN.name: (
        (0, "P1-3", 0, 12.699999999999989, 12.700000000000045, -1023.2192060722128, -1466.8477000978717),
        (0, "P1-5", 0, 582.7, 12.700000000000045, -471.9364256347082, -1472.9982876335914),
        (0, "P4", 180, 12.699999999999989, 1522.7, -2277.7812911583396, -1041.7934660902604),
        (0, "P5", 0, 757.7, 1632.7, 1415.5861619661946, 1228.7980118344517),
        (0, "P1-7", 0, 842.7, 12.700000000000045, -471.9364256347082, 377.95056401394066),
        (0, "P1-9", 0, 12.699999999999989, 1652.7, -1006.2605250073861, 42.729969885962156),
        (1, "P1-4", 0, 12.699999999999989, 12.700000000000045, -1005.4805618899479, 54.368054850137604),
        (1, "P1-6", 0, 562.7, 12.700000000000045, -471.9364256347082, 1266.4780582295812),
    ),
    NO_AXIS_RUN.name: (
        (0, "P1-9", 0, 12.699999999999989, 12.700000000000045, 1349.7554329065936, -695.4923786058981),
        (0, "P1-10", 180, 327.7, 212.70000000000005, -1915.3312892239371, -604.1005969698006),
        (0, "P1-11", 180, 532.7, 487.70000000000005, -1665.8307049951156, -883.3065323585397),
        (0, "P1-12", 180, 12.699999999999989, 532.7, -1929.1018723863222, 54.47264717569975),
        (0, "P4", 180, 12.699999999999989, 727.7, -1168.8963807792218, 1328.467032420881),
        (0, "P5", 0, 347.7, 12.700000000000045, 1322.92889259806, -1514.2335361575197),
        (0, "P1-13", 0, 152.7, 792.7, 1300.4335953485786, -711.5511003923843),
        (0, "P1-14", 180, 142.7, 1207.7, -1179.5190774998616, -562.25866181836),
        (0, "P1-15", 180, 942.7, 767.7, -1236.5560147288045, -887.4937547511045),
        (0, "P1-16", 0, 507.7, 12.700000000000045, 2142.004097070252, 328.4524691256013),
        (0, "P1-17", 0, 137.7, 12.700000000000045, 95.81211065707996, 478.07943382309503),
    ),
}

# The rest of what `nest` reported on those runs, captured at the same time.
# Both runs leave pieces unplaced -- the deck needs more seams than the pinned
# seams draw -- and that is part of the answer too.
EXPECTED_SUMMARY = {
    AXIS_RUN.name: {
        "sheet_count": 2, "piece_count": 8, "utilisation": [0.5888, 0.3518],
        "unplaced_piece_ids": ["P1-1", "P1-2", "P1-8", "P2", "P3"],
    },
    NO_AXIS_RUN.name: {
        "sheet_count": 1, "piece_count": 11, "utilisation": [0.2465],
        "unplaced_piece_ids": ["P1-1", "P1-2", "P1-3", "P1-4", "P1-5", "P1-6",
                               "P1-7", "P1-8", "P2", "P3"],
    },
}

# `nest` alone was about 8 seconds of wall clock on the axis run; the 14 in the
# module docstring is that same call under cProfile.  These bounds are loose
# enough for a busy machine and still far below where it started.
NEST_BUDGET_S = 2.0
PLAN_BUDGET_S = 4.0


def has(run_dir):
    return (run_dir / "run.json").is_file() and (run_dir / "final_auto.dxf").is_file()


cached_runs = pytest.mark.skipif(
    not (has(AXIS_RUN) and has(NO_AXIS_RUN)),
    reason="the cached runs this file measures are not in this checkout")


def options(**overrides):
    config = load_config()
    return sheets.settings({**config, "sheets": {**(config.get("sheets") or {}), **overrides}})


def pinned_run(run_dir, tmp_path):
    """A copy of a cached run with this file's own seams written into it.

    The geometry is the run's; the seams are ours.  Planning the run where it
    lies would measure the nester against whatever the app last saved into its
    seams.json, so placing one seam in the product would turn this file red and
    blame nesting.py for it.
    """

    work = tmp_path / run_dir.name
    shutil.copytree(run_dir, work)
    (work / "seams.json").write_text(
        json.dumps({"seams": PINNED_SEAMS[run_dir.name]}, indent=2), encoding="utf-8")
    return work


def nested(run_dir, monkeypatch, tmp_path):
    """What `plan` asked the nester for on this run, and what it answered.

    Going through the real `plan` instead of rebuilding the pipeline here is the
    point: it is the production seam split, the production grain axis and the
    production config that produced the table above, so a change anywhere
    upstream that moved a part shows up here rather than being hidden behind a
    private copy of the setup.
    """

    captured = {}
    real = nesting.nest

    def spy(pieces, base_rotation, opts):
        captured["arguments"] = (pieces, base_rotation, opts)
        captured["result"] = real(pieces, base_rotation, opts)
        return captured["result"]

    monkeypatch.setattr(nesting, "nest", spy)
    sheetjob.plan(pinned_run(run_dir, tmp_path), load_config(), write_files=False)
    return captured["arguments"], captured["result"]


def layout(sheet_list):
    return tuple(
        (p.sheet_index, p.piece_id, p.rotation_deg,
         float(p.offset[0]), float(p.offset[1]),
         float(p.origin[0]), float(p.origin[1]))
        for sheet in sheet_list for p in sheet.placements
    )


def rect(width, length, x=0.0, y=0.0):
    corners = np.array([[x, y], [x + width, y], [x + width, y + length], [x, y + length]],
                       dtype=float)
    return sheets.Loop(np.column_stack([corners, np.zeros(len(corners))]))


def variant_for(polygon):
    """The `_Variant` the nester would build from this polygon."""

    minx, miny, maxx, maxy = polygon.bounds
    moved = affinity.translate(polygon, -minx, -miny)
    built = nesting._Variant(moved, maxx - minx, maxy - miny, moved.bounds)
    built.probes = nesting._probes(moved, nesting._PROBE_LEVELS)
    return built


# ------------------------------------------------------------ the placements

@cached_runs
@pytest.mark.parametrize("run_dir", [AXIS_RUN, NO_AXIS_RUN], ids=["axis", "no-axis"])
def test_a_cached_run_nests_to_exactly_the_layout_it_always_did(run_dir, monkeypatch, tmp_path):
    """Every offset to the last bit, in the same order, on the same sheets."""

    (pieces, _rotation, opts), (sheet_list, summary, _warnings) = nested(run_dir, monkeypatch, tmp_path)
    assert pieces, "the run produced no pieces at all, so this would prove nothing"

    assert layout(sheet_list) == EXPECTED[run_dir.name]
    for key, value in EXPECTED_SUMMARY[run_dir.name].items():
        assert summary[key] == value, key
    # And the sizes the sheet report quotes back to the fabricator.
    for sheet in sheet_list:
        for placement in sheet.placements:
            assert placement.width_mm <= opts["max_part_width_mm"] + 1e-9
            assert placement.length_mm <= opts["max_part_length_mm"] + 1e-9


@cached_runs
def test_nesting_the_same_pieces_again_gives_the_same_answer_again(monkeypatch, tmp_path):
    """The sample points are worked out per piece and per rotation, so nothing
    may leak from one call into the next.  A search that calls `nest` a hundred
    times over the same pieces has to get one answer, not two."""

    (pieces, rotation, opts), (first, _summary, _warnings) = nested(AXIS_RUN, monkeypatch, tmp_path)
    monkeypatch.undo()
    again, _s, _w = nesting.nest(pieces, rotation, opts)
    once_more, _s, _w = nesting.nest(list(pieces), rotation, dict(opts))
    assert layout(again) == layout(first)
    assert layout(once_more) == layout(first)


# ------------------------------------------------------------ the edge cases

def test_a_piece_too_big_for_the_sheet_is_reported_and_no_sheet_is_opened():
    """A part that fits in no allowed rotation never reaches the search at all.
    The answer is a warning telling the user where to add a seam, not an empty
    sheet with nothing on it."""

    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    huge = sheets.Piece("P9", 1, rect(3000.0, 1500.0), [], 4.5e6)
    sheet_list, summary, warnings = nesting.nest([huge], rotation, opts)

    assert sheet_list == []
    assert summary["unplaced_piece_ids"] == ["P9"]
    assert summary["piece_count"] == 0
    assert any("add a seam" in w for w in warnings)


def test_a_piece_that_only_just_fits_still_fits():
    """A part nearly as big as the usable area leaves one row and one column of
    grid positions to try, and the first one works.  The filter has to answer
    the tiny search as well as the big one."""

    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    # sheet_transform turns +X along the boat into +Y, so this is 980 across
    # the boat by 1990 along it -- just inside the 990.6 x 2006.6 usable area.
    tall = sheets.Piece("T1", 1, rect(1990.0, 980.0), [], 1990.0 * 980.0)
    sheet_list, summary, warnings = nesting.nest([tall], rotation, opts)

    assert summary["unplaced_piece_ids"] == []
    assert len(sheet_list) == 1 and len(sheet_list[0].placements) == 1
    assert sheet_list[0].placements[0].rotation_deg == 0
    assert warnings == []


def test_a_single_piece_goes_to_the_bottom_left_corner_of_an_empty_sheet():
    """With nothing to avoid there is no occupied region to test against, so the
    search has to answer from the very first position without the filter."""

    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    piece = sheets.Piece("S1", 1, rect(400.0, 900.0), [], 360000.0)
    sheet_list, summary, _warnings = nesting.nest([piece], rotation, opts)

    margin_x = (opts["sheet_width_mm"] - opts["max_part_width_mm"]) / 2.0
    margin_y = (opts["sheet_length_mm"] - opts["max_part_length_mm"]) / 2.0
    placement = sheet_list[0].placements[0]
    assert summary["sheet_count"] == 1
    assert float(placement.offset[0]) == margin_x
    assert float(placement.offset[1]) == margin_y


def test_many_small_pieces_all_land_and_all_keep_their_spacing():
    """Many pieces is where the occupied region grows most, and the occupied
    region is what the filter is queried against -- so this is the shape of job
    most likely to expose a filter that had started lying."""

    opts = options()
    rotation = sheets.sheet_transform(np.array([1.0, 0.0]))
    pieces = [sheets.Piece(f"m{i:02d}", 1, rect(90.0 + 2 * i, 120.0 + 3 * i), [],
                           (90.0 + 2 * i) * (120.0 + 3 * i))
              for i in range(20)]
    sheet_list, summary, warnings = nesting.nest(pieces, rotation, opts)

    assert summary["unplaced_piece_ids"] == []
    assert summary["piece_count"] == 20
    assert warnings == []
    for sheet in sheet_list:
        placed = []
        for placement in sheet.placements:
            piece = next(p for p in pieces if p.piece_id == placement.piece_id)
            points, _s = sheets.sample_loop(piece.outer, opts["sample_step_mm"])
            placed.append(Polygon(placement.apply(points, rotation)))
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                assert placed[i].distance(placed[j]) >= opts["part_spacing_mm"] - 0.01


# ------------------------------------------------------------ the filter

def test_the_sample_points_only_ever_prove_a_position_blocked():
    """The whole speed-up rests on one claim: a sample point of the piece
    landing in the occupied region means the piece overlaps it.  Check that
    claim against the polygon test itself, at every grid position over an
    obstacle, and check the points catch nearly all of the blocked ones --
    a filter that were merely correct and caught nothing would be no faster.

    The piece here has a hole in it on purpose: a hole is where a naive set of
    sample points goes wrong, by reporting a point that is not on the part.
    """

    piece = sheets.Piece("F1", 1, rect(120.0, 260.0), [rect(40.0, 160.0, 40.0, 50.0)], 2.5e4)
    shell, holes = nesting._sampled_rings(piece, 1.0)
    variant = variant_for(nesting._piece_polygon(shell, holes, np.eye(2)))
    obstacle = Polygon([(150.0, 90.0), (330.0, 60.0), (360.0, 300.0), (170.0, 340.0)])
    blocked = prep(obstacle)

    fired = truly_blocked = 0
    for y in nesting._grid_steps(400.0, 7.0):
        for x in nesting._grid_steps(400.0, 7.0):
            really = bool(blocked.intersects(affinity.translate(variant.polygon, x, y)))
            truly_blocked += really
            caught = any(
                shapely.intersects_xy(obstacle, probes[:, 0] + x, probes[:, 1] + y).any()
                for probes in variant.probes
            )
            fired += caught
            assert not (caught and not really), f"a sample point fired at the free position {x}, {y}"

    assert truly_blocked > 100, "the fixture never overlapped, so nothing was tested"
    assert fired >= truly_blocked * 0.95, (
        f"the sample points caught only {fired} of {truly_blocked} blocked positions, "
        "which would put the slow polygon test back in the inner loop")


def test_moving_a_sample_point_lands_it_exactly_on_the_moved_outline():
    """A boundary sample point is a vertex of the piece, and the filter moves it
    with plain numpy addition while shapely moves the outline with an affine
    transform.  If those two disagreed by one bit, a sample point could sit a
    hair outside the shape it is supposed to speak for and report an overlap
    that the polygon test would deny -- which would move a part.  They agree
    because the transform is a pure translation: the rotate and scale terms are
    exactly 1 and 0, so it reduces to the same addition."""

    rng = np.random.default_rng(3)
    for _ in range(200):
        polygon = Polygon(rng.uniform(-2500.0, 2500.0, size=(40, 2)))
        x, y = rng.uniform(-3000.0, 3000.0, 2)
        moved = shapely.get_coordinates(affinity.translate(polygon, x, y))
        added = shapely.get_coordinates(polygon) + (x, y)
        assert np.array_equal(moved, added)


def test_the_position_grid_is_built_by_adding_and_not_by_multiplying():
    """A fractional nest step makes 0.1 * 3 and 0.1 + 0.1 + 0.1 different
    numbers, and the search used to walk the sheet by adding.  Reproducing that
    exactly is what keeps a job planned today identical to the same job planned
    before any of this, whatever step the user set."""

    for step in (5.0, 0.7, 1.0 / 3.0, 11.0):
        walked = []
        value = 0.0
        while value <= 250.0 + 1e-9:
            walked.append(value)
            value += step
        assert nesting._grid_steps(250.0, step).tolist() == walked

    # And multiplying really would have given different numbers, so the check
    # above is not passing by luck.
    multiplied = (np.arange(len(nesting._grid_steps(250.0, 0.7))) * 0.7).tolist()
    assert nesting._grid_steps(250.0, 0.7).tolist() != multiplied


# ------------------------------------------------------------ the speed

@cached_runs
def test_nesting_the_axis_run_is_fast_enough_to_search_with(monkeypatch, tmp_path):
    """A seam search calls `nest` once per candidate seam set, so the whole idea
    depends on one nest being cheap.  This run took about 8 s before the sample
    points went in and takes about 0.2 s after."""

    (pieces, rotation, opts), _result = nested(AXIS_RUN, monkeypatch, tmp_path)
    monkeypatch.undo()
    nesting.nest(pieces, rotation, opts)                 # warm the interpreter, not a cache
    started = time.perf_counter()
    sheet_list, _summary, _warnings = nesting.nest(pieces, rotation, opts)
    elapsed = time.perf_counter() - started

    assert layout(sheet_list) == EXPECTED[AXIS_RUN.name]
    assert elapsed < NEST_BUDGET_S, f"nest() took {elapsed:.2f} s"


@cached_runs
def test_the_whole_plan_is_far_under_the_fifteen_seconds_it_used_to_take(tmp_path):
    """`plan` is what the button in the UI actually calls, and nesting was 14 of
    its 15 seconds.  Read the run once first: this is a bound on the planning,
    not on the first disk read of the day."""

    work = pinned_run(AXIS_RUN, tmp_path)
    sheetjob.plan(work, load_config(), write_files=False)
    started = time.perf_counter()
    result = sheetjob.plan(work, load_config(), write_files=False)
    elapsed = time.perf_counter() - started

    assert result["summary"]["sheet_count"] == EXPECTED_SUMMARY[AXIS_RUN.name]["sheet_count"]
    assert elapsed < PLAN_BUDGET_S, f"plan() took {elapsed:.2f} s"
