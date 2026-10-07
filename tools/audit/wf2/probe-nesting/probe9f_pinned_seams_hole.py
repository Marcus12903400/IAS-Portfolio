"""Probe 9f: the round cut-out through the PRODUCTION path with the repo's own
pinned fixture seams (engine/tests/test_nesting_speed.py PINNED_SEAMS for the
AXIS run), on a scratch copy, write_files=False."""

import shutil

import numpy as np
from shapely.geometry import Polygon

from probe_common import CONFIG, HERE, OPTIONS, RUN, STEP, load_run, sheetjob, sheets

PINNED = [
    {"seam_id": "s1", "x1": 110.61724090576172, "y1": -1005.9564819335938, "x2": 86.17920684814453, "y2": -453.04376220703125, "panel_id": None},
    {"seam_id": "s2", "x1": 9.80996036529541, "y1": 451.1668701171875, "x2": -23.79237174987793, "y2": 1004.07958984375, "panel_id": None},
    {"seam_id": "s3", "x1": -1474.8062744140625, "y1": 374.7977600097656, "x2": 40.35771179199219, "y2": 484.76934814453125, "panel_id": None},
    {"seam_id": "s4", "x1": 1836.56005859375, "y1": 344.2501525878906, "x2": 1469.9881591796875, "y2": 328.97625732421875, "panel_id": None},
]
copy = HERE / "runs" / "axis_pinned"
if copy.exists():
    shutil.rmtree(copy)
shutil.copytree(RUN, copy)
seams = [sheets.Seam.from_dict(s) for s in PINNED]
sheets.write_seams(copy, seams)
result = sheetjob.plan(copy, CONFIG, write_files=False)
print("status:", result["status"], "pieces:", result["piece_count"], "sheets:", result["summary"]["sheet_count"],
      "unplaced:", result["summary"]["unplaced_piece_ids"])
print("pieces (id, holes):", [(p["piece_id"], p["holes"]) for p in result["pieces"]])
print("max_arc_rebuild_error_mm reported:", result["max_arc_rebuild_error_mm"])

# which piece contains the round cut-out's centre, and does it carry the hole?
run = load_run()
outer, holes, _p = run["panels"][1]
round_hole = next(h for h in holes if len(h.vertices) == 2)
centre = Polygon(sheets.sample_loop(round_hole, STEP)[0]).centroid
loops, _pat, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(copy))
seam_list = [sheets.Seam.from_dict(s) for s in result["seams"]]
pieces, _w = sheets.split_panel(1, outer, holes, seam_list, OPTIONS)
for p in pieces:
    if Polygon(sheets.sample_loop(p.outer, STEP)[0]).contains(centre):
        has = any(Polygon(sheets.sample_loop(h, STEP)[0]).contains(centre) for h in p.holes)
        n = len(p.outer.vertices)
        print(f"round cut-out centre ({centre.x:.1f}, {centre.y:.1f}) lies in {p.piece_id}: hole {'present' if has else 'MISSING'}; "
              f"piece outline has {n} vertices, {len(p.holes)} holes")
total_holes = sum(len(p.holes) for p in pieces)
print(f"panel 1 holes in source: {len(holes)}; holes carried by the split pieces: {total_holes} "
      f"(a seam through a cut-out legitimately merges it into the outline)")
