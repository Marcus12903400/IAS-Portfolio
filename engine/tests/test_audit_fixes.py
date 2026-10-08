"""Regression tests for the 2026-10-07 seam/sheet audit fixes.

Every test here closes a gap the audit's section 5 named: a behaviour the
product relied on that no test would have caught.  They are grouped by the
audit's finding ids (B1, A1, A2, B2/B3, E1, D1, G24, ...) so the next reader
can find the defect behind each assertion.
"""

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

from autodeck2 import nesting, seamplace, sheetjob, sheets
from autodeck2.config import load_config

RUNS = Path(__file__).resolve().parents[1] / "outputs" / "runs"
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"


def options(**overrides):
    config = load_config()
    return sheets.settings({**config, "sheets": {**(config.get("sheets") or {}), **overrides}})


def rounded_rect(width, height, radius, x=0.0, y=0.0):
    b = math.tan(math.pi / 8)
    r = radius
    return sheets.Loop(np.array([
        [x + r, y, 0.0], [x + width - r, y, b], [x + width, y + r, 0.0],
        [x + width, y + height - r, b], [x + width - r, y + height, 0.0],
        [x + r, y + height, b], [x, y + height - r, 0.0], [x, y + r, b],
    ], dtype=float))


def circle(radius, cx=0.0, cy=0.0):
    """A round loop the way the fitter writes circles: two vertices, bulge +-
    one -- two semicircles."""
    return sheets.Loop(np.array([
        [cx - radius, cy, 1.0], [cx + radius, cy, 1.0]], dtype=float))


# --------------------------------------------------------------------------
# B1: holes survive the split (audit section 5 item 1)

def test_a_round_cutout_survives_a_seam_that_cuts_its_panel():
    """The 207 mm round cut-out used to vanish from every cut piece: it
    rebuilds to a two-vertex bulged ring and only three-vertex rings were
    kept."""
    opts = options()
    panel = rounded_rect(1600.0, 900.0, 80.0)
    hole = circle(100.0, 400.0, 450.0)
    seam = sheets.Seam("s", 800.0, -50.0, 800.0, 950.0, panel_id=1)

    pieces, _warnings = sheets.split_panel(1, panel, [hole], [seam], opts)

    assert len(pieces) == 2
    # The hole lands whole on one side of the cut...
    assert sum(len(p.holes) for p in pieces) == 1
    bearer = next(p for p in pieces if p.holes)
    rebuilt = bearer.holes[0]
    # ...as bulged arcs (one run per semicircle), not a thousand-chord
    # polyline, and it still encloses the circle's own area.
    assert np.count_nonzero(np.abs(rebuilt.bulges) > 0.9) >= 2
    points, _src = sheets.sample_loop(rebuilt, opts["sample_step_mm"])
    assert abs(Polygon(points).area) == pytest.approx(math.pi * 100.0 ** 2, rel=0.01)


def test_a_filleted_cutout_survives_and_keeps_its_arcs():
    opts = options()
    panel = rounded_rect(1600.0, 900.0, 80.0)
    hole = rounded_rect(300.0, 200.0, 40.0, 200.0, 350.0)
    seam = sheets.Seam("s", 800.0, -50.0, 800.0, 950.0, panel_id=1)

    pieces, _warnings = sheets.split_panel(1, panel, [hole], [seam], opts)

    assert sum(len(p.holes) for p in pieces) == 1
    bearer = next(p for p in pieces if p.holes)
    # Four fillets in, four fillets out.
    assert int(np.count_nonzero(np.abs(bearer.holes[0].bulges) > 1e-12)) == 4
    assert sum(p.arc_fallbacks for p in pieces) == 0


def test_a_hole_survives_a_round_trip_through_the_sheet_writer(tmp_path):
    """A piece with a cut-out must come out of the DXF as TWO closed CAM
    polylines on the same layer -- the part and its cut-out."""
    import ezdxf

    opts = options()
    panel = rounded_rect(1200.0, 800.0, 60.0)
    hole = circle(90.0, 900.0, 400.0)
    seam = sheets.Seam("s", 600.0, -50.0, 600.0, 850.0, panel_id=1)
    pieces, _warnings = sheets.split_panel(1, panel, [hole], [seam], opts)
    rotation = sheets.sheet_transform(None)
    by_id = {p.piece_id: p for p in pieces}
    sheet_list, _summary, _w = nesting.nest(pieces, rotation, opts)
    nesting.write_sheet_dxfs(tmp_path, sheet_list, by_id, rotation, {}, None, opts)

    written = sorted(tmp_path.glob("sheet_*.dxf"))[0]
    doc = ezdxf.readfile(str(written))
    cam = [e for e in doc.modelspace().query("LWPOLYLINE") if str(e.dxf.layer).startswith("CAM__")]
    assert len(cam) >= 3          # two outer parts + the hole that survived
    assert all(e.closed for e in cam)


def test_a_round_part_split_in_two_comes_out_as_two_half_discs():
    """A circular panel cut by one seam: each half keeps its semicircular edge
    as an arc, and neither half is dropped as 'degenerate'."""
    opts = options()
    part = circle(120.0)
    seam = sheets.Seam("s", 0.0, -200.0, 0.0, 200.0, panel_id=1)

    pieces, warnings = sheets.split_panel(1, part, [], [seam], opts)

    assert len(pieces) == 2, warnings
    half_area = math.pi * 120.0 ** 2 / 2.0
    for piece in pieces:
        assert len(piece.outer.vertices) >= 2
        # The round side of each half survives as a bulge.
        assert np.count_nonzero(np.abs(piece.outer.bulges) > 1e-12) >= 1
        # Each half loses half the seam gap along the diameter.
        assert piece.area_mm2 == pytest.approx(half_area - 120.0 * 2.0 * 3.0, rel=0.01)


def test_holes_in_equal_holes_out_when_no_kerf_touches_them():
    """Summed over the pieces, no cut-out a seam did not touch may be lost."""
    opts = options()
    panel = rounded_rect(1800.0, 900.0, 80.0)
    holes = [circle(80.0, 300.0, 450.0), circle(60.0, 1500.0, 450.0),
             rounded_rect(150.0, 100.0, 20.0, 900.0, 200.0)]
    seam = sheets.Seam("s", 600.0, -50.0, 600.0, 950.0, panel_id=1)

    pieces, _warnings = sheets.split_panel(1, panel, holes, [seam], opts)

    assert sum(len(p.holes) for p in pieces) == len(holes)


# --------------------------------------------------------------------------
# A1: the chord the user clicked is the chord that is cut (item 2)

def _panel_axis_aligned():
    """A panel with a console-shaped gap, so a boat-square line crosses it as
    TWO chords -- the situation the whole-line cut used to split in half."""
    opts = options()
    outer = rounded_rect(2000.0, 900.0, 60.0)
    console = rounded_rect(400.0, 600.0, 20.0, 800.0, 150.0)
    return opts, outer, console


def test_a_moded_seam_cuts_only_the_chord_it_was_given():
    """A seam placed with a direction is a chord: the hover showed one stretch
    of the line, and that stretch -- not the whole infinite line -- is what
    gets kerfed.  A chord that spans only the middle of the panel used to
    reach the whole way through and split the deck in half (audit A1: a
    450 mm join beside the console halved the boat)."""
    opts = options()
    panel = rounded_rect(2000.0, 900.0, 60.0)
    # A 600 mm vertical chord in the middle of the panel: mode "along".
    middle_chord = sheets.Seam("s", 1000.0, 150.0, 1000.0, 750.0, panel_id=1, mode="along",
                               raw=(1000.0, 150.0, 1000.0, 750.0))
    pieces, warnings = sheets.split_panel(1, panel, [], [middle_chord], opts)

    # The chord takes its slot of material out but never reaches the panel's
    # top or bottom edges, so it cannot separate anything: ONE piece with a
    # notch, not the two the whole-line cut produced.
    assert len(pieces) == 1, warnings
    full = sheets.loop_polygon(panel, [], opts["sample_step_mm"]).area
    removed = full - pieces[0].area_mm2
    # The slot is 6 mm of kerf over the 612 mm chord-plus-reach: about 3.7 cm2,
    # and nothing else may have gone.
    assert 3000.0 < removed < 4200.0


def test_a_free_seam_still_cuts_the_whole_line():
    """Legacy free seams (no mode) keep the whole-line extension, so runs made
    before the direction tool plan exactly as they always did."""
    opts = options()
    panel = rounded_rect(2000.0, 900.0, 60.0)
    free = sheets.Seam("s", 1000.0, 150.0, 1000.0, 750.0, panel_id=None)
    pieces, _warnings = sheets.split_panel(1, panel, [], [free], opts)
    assert len(pieces) == 2


def test_a_bound_seam_parked_off_the_panel_warns_instead_of_cutting():
    opts = options()
    panel = rounded_rect(600.0, 400.0, 30.0)
    parked = sheets.Seam("s7", 700.0, 100.0, 700.0, 300.0, panel_id=1, mode="across",
                         raw=(700.0, 100.0, 700.0, 300.0))
    pieces, warnings = sheets.split_panel(1, panel, [], [parked], opts)
    assert len(pieces) == 1 and pieces[0].from_seam is False
    assert any("cut nothing" in w and "s7" in w for w in warnings)


# --------------------------------------------------------------------------
# A2: the sliver width guard (item 5)

def test_a_toothpick_wide_piece_is_dropped_with_a_warning_naming_the_seam():
    """A long, thin shaving has area enough to pass the old test and used to
    be nested and exported as a part."""
    opts = options()
    panel = rounded_rect(1200.0, 200.0, 30.0)
    # A seam 8 mm off the panel's own edge leaves a 5 mm strip: about
    # 700 mm2 -- above the area floor, below the width floor.
    shaving = sheets.Seam("s5", 8.0, -50.0, 8.0, 250.0, panel_id=None)
    pieces, warnings = sheets.split_panel(1, panel, [], [shaving], opts)
    assert len(pieces) == 1
    assert any("narrower than" in w and "s5" in w for w in warnings)


def test_a_gunwale_width_strip_survives_the_guard():
    """The reference boat's gunwale panels are 25 mm wide and are legitimately
    cut into pieces of that width -- the floor must stay under them."""
    opts = options(min_piece_width_mm=15.0)
    strip = rounded_rect(1200.0, 25.0, 5.0)
    seam = sheets.Seam("s", 400.0, -50.0, 400.0, 75.0, panel_id=None)
    pieces, _warnings = sheets.split_panel(1, strip, [], [seam], opts)
    assert len(pieces) == 2
    assert all(p.area_mm2 > 8000.0 for p in pieces)


def test_piece_ids_come_from_the_kept_list_not_the_originals():
    """A skipped part used to eat an id, so 'P1-3 needs a seam' pointed at a
    different piece after every edit."""
    opts = options()
    panel = rounded_rect(1200.0, 200.0, 30.0)
    shaving = sheets.Seam("s5", 5.0, -50.0, 5.0, 250.0, panel_id=None)
    pieces, _warnings = sheets.split_panel(1, panel, [], [shaving], opts)
    assert [p.piece_id for p in pieces] == ["P1-1"]


# --------------------------------------------------------------------------
# B2/B3: the ring rebuild keeps arcs from every source loop (item 3)

def test_console_walls_on_a_cut_piece_keep_their_arcs():
    """One ring can take in the outer boundary AND a cut-out's wall; arcs from
    both used to survive only if their loop won 'dominant', and the loser came
    back as 1 mm chords."""
    opts = options()
    panel = rounded_rect(2000.0, 1000.0, 60.0)
    # A filleted console-shaped cut-out, split by the seam so that both sides
    # of it appear on one piece's ring.
    console = rounded_rect(600.0, 300.0, 50.0, 700.0, 350.0)
    seam = sheets.Seam("s", 1000.0, -50.0, 1000.0, 1050.0, panel_id=1)

    pieces, _warnings = sheets.split_panel(1, panel, [console], [seam], opts)

    assert pieces
    total_arcs = sum(int(np.count_nonzero(np.abs(p.outer.bulges) > 1e-12))
                     + sum(int(np.count_nonzero(np.abs(h.bulges) > 1e-12)) for h in p.holes)
                     for p in pieces)
    total_fallbacks = sum(p.arc_fallbacks for p in pieces)
    # Four corner fillets on the outer boundary, four on the console: all of
    # them come back as single bulges.
    assert total_arcs == 8, f"arcs: {total_arcs}, fallbacks: {total_fallbacks}"
    assert total_fallbacks == 0


def test_small_fillets_survive_a_split():
    """The audit's exact case: 3 mm fillets on a 1600 x 900 panel, one seam."""
    opts = options()
    panel = rounded_rect(1600.0, 900.0, 3.0)
    seam = sheets.Seam("s", 800.0, -50.0, 800.0, 950.0, panel_id=None)

    pieces, _warnings = sheets.split_panel(1, panel, [], [seam], opts)

    assert len(pieces) == 2
    assert sum(int(np.count_nonzero(np.abs(p.outer.bulges) > 1e-12)) for p in pieces) == 4
    assert sum(p.arc_fallbacks for p in pieces) == 0
    assert max(p.max_arc_error_mm for p in pieces) <= opts["arc_rebuild_tolerance_mm"]


# --------------------------------------------------------------------------
# E1: seam ids stay unique through remove-then-place (item 7)

def test_idless_seams_get_ids_past_the_highest_existing_one():
    from autodeck_app import bridge

    posted = [
        {"seam_id": "s2", "x1": 0, "y1": 0, "x2": 100, "y2": 0},
        {"seam_id": "s4", "x1": 0, "y1": 10, "x2": 100, "y2": 10},
        {"x1": 0, "y1": 20, "x2": 100, "y2": 20},        # just placed
    ]
    bridge._fresh_seam_ids(posted)
    ids = [s["seam_id"] for s in posted]
    assert len(set(ids)) == len(ids)
    assert ids[-1] == "s5"


def test_the_server_rejects_duplicate_seam_ids_and_zero_length_seams():
    from werkzeug.exceptions import BadRequest
    from autodeck_app import server

    with pytest.raises(BadRequest) as raised:
        server.checked_seams([
            {"seam_id": "s1", "x1": 0, "y1": 0, "x2": 100, "y2": 0},
            {"seam_id": "s1", "x1": 0, "y1": 9, "x2": 100, "y2": 9},
        ])
    assert "repeats seam_id" in raised.value.description

    with pytest.raises(BadRequest) as raised:
        server.checked_seams([{"seam_id": "s1", "x1": 5, "y1": 5, "x2": 5, "y2": 5}])
    assert "zero length" in raised.value.description

    with pytest.raises(BadRequest) as raised:
        server.checked_seams([{"seam_id": "s1", "x1": 0, "y1": 0, "x2": 100, "y2": 0,
                               "mode": "angle", "angle_deg": "diagonal"}])
    assert "angle_deg" in raised.value.description


def test_the_manager_refuses_a_second_job_under_its_own_lock():
    """Two requests arriving together used to slip between the pre-check and
    the start; the engine is not re-entrant."""
    import time as _time
    from autodeck_app.jobs import JobBusy, JobManager

    manager = JobManager()
    manager.start("slow", lambda log: _time.sleep(0.4))
    with pytest.raises(JobBusy):
        manager.start("second", lambda log: None)


# --------------------------------------------------------------------------
# G14 / E6: removing seams always works, whatever the plan state (item 6)

def has_axis_run():
    return (AXIS_RUN / "run.json").is_file() and (AXIS_RUN / "final_auto.dxf").is_file()


@pytest.mark.skipif(not has_axis_run(), reason="the cached axis run is not in this checkout")
def test_a_seam_removal_lands_on_disk_even_when_the_plan_refuses(tmp_path):
    """Remove every seam from a run whose pieces do not fit: the removal is
    saved, the plan answers NEEDS_SEAMS, and nothing is lost."""
    from autodeck_app import bridge

    work = tmp_path / AXIS_RUN.name
    shutil.copytree(AXIS_RUN, work)
    (work / "seams.json").write_text(json.dumps({"seams": [
        {"seam_id": "s1", "x1": 110.6, "y1": -1005.9, "x2": 86.2, "y2": -453.0,
         "panel_id": None}]}, indent=2), encoding="utf-8")

    result = bridge.sheet_preview(work, {}, seams=[], save=True)
    assert result["available"] and result["status"] == "NEEDS_SEAMS"
    stored = json.loads((work / "seams.json").read_text(encoding="utf-8"))
    assert stored == {"seams": []}


@pytest.mark.skipif(not has_axis_run(), reason="the cached axis run is not in this checkout")
def test_a_seam_removal_lands_even_when_the_settings_cannot_be_cut_with(tmp_path):
    """A seam gap of zero used to turn the removal into a 400 and leave the
    old seams on disk forever."""
    from autodeck_app import bridge

    work = tmp_path / AXIS_RUN.name
    shutil.copytree(AXIS_RUN, work)
    (work / "seams.json").write_text(json.dumps({"seams": [
        {"seam_id": "s1", "x1": 110.6, "y1": -1005.9, "x2": 86.2, "y2": -453.0,
         "panel_id": None}]}, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="seam_gap_mm"):
        bridge.sheet_preview(work, {"seam_gap_mm": 0.0}, seams=[], save=True)
    stored = json.loads((work / "seams.json").read_text(encoding="utf-8"))
    assert stored == {"seams": []}


def test_a_nonsense_seam_gap_or_step_is_a_400_naming_the_field():
    """The 0 in the box used to reach the geometry as a legal number."""
    from werkzeug.exceptions import BadRequest
    from autodeck_app import server

    for payload in ({"seam_gap_mm": 0.0}, {"seam_gap_mm": 1.0}, {"part_spacing_mm": 0.0}):
        with pytest.raises(BadRequest) as raised:
            server.sheet_options(payload)
        assert next(iter(payload)) in raised.value.description


# --------------------------------------------------------------------------
# G24: settings validate the numbers that break the geometry

@pytest.mark.parametrize("key,value", [
    ("sample_step_mm", 0.0), ("nest_step_mm", 0.0),
    ("sample_step_mm", float("nan")), ("nest_step_mm", float("inf")),
    ("sheet_width_mm", float("nan")), ("part_spacing_mm", float("nan")),
])
def test_settings_refuse_a_zero_step_or_a_nan(key, value):
    with pytest.raises(ValueError, match=key):
        options(**{key: value})


# --------------------------------------------------------------------------
# D1: stale sheet DXFs are removed and labelled (item 9)

@pytest.mark.skipif(not has_axis_run(), reason="the cached axis run is not in this checkout")
def test_a_smaller_re_export_leaves_no_stale_file_behind(tmp_path):
    work = tmp_path / AXIS_RUN.name
    shutil.copytree(AXIS_RUN, work)
    config = load_config()
    seams = [sheets.Seam("s1", 110.6, -1005.9, 86.2, -453.0, panel_id=None),
             sheets.Seam("s2", 9.8, 451.2, -23.8, 1004.1, panel_id=None),
             sheets.Seam("s3", -1474.8, 374.8, 40.4, 484.8, panel_id=None),
             sheets.Seam("s4", 1836.6, 344.3, 1470.0, 329.0, panel_id=None)]

    # First export WITHOUT the 180 degree turn: worse packing, so this is the
    # run's BIGGEST layout in sheets.
    tighter = {**config, "sheets": {**(config.get("sheets") or {}), "allow_180_rotation": False}}
    sheetjob.plan(work, tighter, seams=seams, write_files=True)
    files_big = sorted(p.name for p in work.glob("sheet_*.dxf"))
    assert files_big, "the fixture produced no sheets to compare"

    # Then the normal export: fewer sheets, and the leftovers must be gone.
    smaller = sheetjob.plan(work, config, seams=seams, write_files=True)
    files_small = sorted(p.name for p in work.glob("sheet_*.dxf"))
    written_small = {entry["name"] for entry in smaller["files"]}

    assert set(files_small) <= set(files_big)
    assert set(files_small) == written_small, "a file from the bigger export survived"


@pytest.mark.skipif(not has_axis_run(), reason="the cached axis run is not in this checkout")
def test_exported_sheets_are_marked_stale_when_the_seams_move(tmp_path):
    work = tmp_path / AXIS_RUN.name
    shutil.copytree(AXIS_RUN, work)
    config = load_config()
    seams = [sheets.Seam("s1", 110.6, -1005.9, 86.2, -453.0, panel_id=None),
             sheets.Seam("s2", 9.8, 451.2, -23.8, 1004.1, panel_id=None),
             sheets.Seam("s3", -1474.8, 374.8, 40.4, 484.8, panel_id=None),
             sheets.Seam("s4", 1836.6, 344.3, 1470.0, 329.0, panel_id=None)]
    result = sheetjob.plan(work, config, seams=seams, write_files=True)
    assert result["files"], "the fixture produced no sheet files to mark"

    fresh = sheetjob.exported_sheets(work)
    assert fresh and all(not entry["stale"] for entry in fresh)

    sheets.write_seams(work, list(seams) + [sheets.Seam("s5", 0.0, -800.0, 0.0, 800.0)])
    stale = sheetjob.exported_sheets(work)
    assert all(entry["stale"] for entry in stale)


# --------------------------------------------------------------------------
# G47: the user's drawn outline wins

def test_a_final_dxf_beats_the_auto_fit_one(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "final_auto.dxf").write_text("auto", encoding="utf-8")
    assert sheetjob.source_dxf(run).name == "final_auto.dxf"
    (run / "final.dxf").write_text("drawn", encoding="utf-8")
    assert sheetjob.source_dxf(run).name == "final.dxf"


# --------------------------------------------------------------------------
# G40: a piece exactly the usable size still nests on a rotated axis

def test_an_exactly_usable_piece_on_a_rotated_axis_still_nests():
    opts = options()
    axis = np.array([math.cos(math.radians(3.68)), math.sin(math.radians(3.68))])
    rotation = sheets.sheet_transform(axis)
    # The usable rectangle, built in the SHEET frame and expressed back in
    # placed coordinates -- so it is exactly 990.6 x 2006.6 measured through
    # the rotation, and float trigonometry puts it a few 1e-13 over.
    width, length = opts["max_part_width_mm"], opts["max_part_length_mm"]
    corners = np.array([[0.0, 0.0], [width, 0.0], [width, length], [0.0, length]])
    placed = corners @ rotation          # inverse of  s = p @ R.T  is  p = s @ R
    loop = sheets.Loop(np.column_stack([placed, np.zeros(4)]))
    piece = sheets.Piece("exact", 1, loop, [], width * length)

    sheet_list, summary, warnings = nesting.nest([piece], rotation, opts)

    assert summary["unplaced_piece_ids"] == []
    assert len(sheet_list) == 1
    assert warnings == []


# --------------------------------------------------------------------------
# item 11: the preview draws exactly what the placements say

@pytest.mark.skipif(not has_axis_run(), reason="the cached axis run is not in this checkout")
def test_every_preview_ring_matches_its_own_placement(tmp_path):
    work = tmp_path / AXIS_RUN.name
    shutil.copytree(AXIS_RUN, work)
    (work / "seams.json").write_text(json.dumps({"seams": [
        {"seam_id": "s1", "x1": 110.6, "y1": -1005.9, "x2": 86.2, "y2": -453.0,
         "panel_id": None},
        {"seam_id": "s2", "x1": 9.8, "y1": 451.2, "x2": -23.8, "y2": 1004.1,
         "panel_id": None}]}, indent=2), encoding="utf-8")

    result = sheetjob.preview(work, load_config())
    rotation = sheets.sheet_transform(None)
    opts = options()

    # The placements live on result["sheets"]; the drawn rings on
    # result["preview"]["sheets"] -- same sheet numbers, in the same order.
    for placed_sheet, drawn_sheet in zip(result["sheets"], result["preview"]["sheets"]):
        assert placed_sheet["sheet"] == drawn_sheet["sheet"]
        by_id = {p["piece_id"]: p for p in placed_sheet["placements"]}
        rings = [ring for ring in drawn_sheet["rings"] if not ring["hole"]]
        assert rings
        for ring in rings:
            placement = by_id.get(ring["piece_id"])
            assert placement is not None, f"{ring['piece_id']} drawn on a sheet it was not placed on"
            moved = nesting.Placement(
                piece_id=placement["piece_id"], panel_id=placement["panel_id"],
                sheet_index=placement["sheet_index"], rotation_deg=int(placement["rotation_deg"]),
                offset=np.asarray(placement["offset_mm"], dtype=float),
                origin=np.asarray(placement["origin_mm"], dtype=float))
            # The drawn ring, pushed back through the placement, must sit
            # inside the piece's own measured extent.  apply() is
            # xy @ M.T - origin + offset, so the inverse puts the drawn sheet
            # points back in the piece's placed frame.
            points = np.asarray(ring["points"], dtype=float)[:-1]
            placed = (points - moved.offset + moved.origin) @ np.linalg.inv(moved.matrix(rotation)).T
            width = placed[:, 0].max() - placed[:, 0].min()
            length = placed[:, 1].max() - placed[:, 1].min()
            assert width <= float(placement["width_mm"]) + 1.0
            assert length <= float(placement["length_mm"]) + 1.0
            # And inside the usable area of the sheet it claims.
            x0 = points[:, 0].min(); x1 = points[:, 0].max()
            y0 = points[:, 1].min(); y1 = points[:, 1].max()
            assert x0 >= 12.7 - 1.0 and y0 >= 12.7 - 1.0
            assert x1 <= opts["max_part_width_mm"] + 12.7 + 1.0
            assert y1 <= opts["max_part_length_mm"] + 12.7 + 1.0


# --------------------------------------------------------------------------
# E8: a torn seams.json reads as empty and keeps a backup

def test_a_torn_seams_file_reads_as_no_seams_and_keeps_a_copy(tmp_path):
    (tmp_path / "seams.json").write_text('{"seams": [{"seam_id": "s1", "x1": 0', encoding="utf-8")
    assert sheets.read_seams(tmp_path) == []
    assert (tmp_path / "seams.json.bak").is_file()

    # A BOM from a hand edit in Notepad is not a reason to fail either.
    (tmp_path / "seams.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"seams": [
            {"seam_id": "s1", "x1": 0, "y1": 0, "x2": 100, "y2": 0}]}).encode("utf-8"))
    assert len(sheets.read_seams(tmp_path)) == 1
