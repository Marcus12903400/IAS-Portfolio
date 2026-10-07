"""The two ~26-40 mm 'across' seams in seams_optimiser_run.json.bak: what do
they actually cut?  Runs plan(write_files=False) on the staged copy with the
six optimiser seams and splits panels 2 and 3 with their own seam alone.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN, dump

for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"):
    sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

config = load_config()
options = sheets.settings(config)
frame, _w = sheetjob.resolve_frame(RUN, options)
axis = np.asarray(frame.axis, float)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
polygons = seamplace.panel_polygons(loops, options)
rotation = sheets.sheet_transform(axis)
step = float(options["sample_step_mm"])
Seam = sheets.Seam

data = json.loads((RUN / "seams_optimiser_run.json.bak").read_text())["seams"]
seams = [Seam.from_dict(s) for s in data]
result = sheetjob.plan(RUN, config, seams=seams, write_files=False)
print("optimiser 6 seams: status", result["status"], "pieces", result["piece_count"], "sheets", result["summary"]["sheet_count"],
      "oversize", [o["piece_id"] for o in result["oversize"]], "unplaced", result["summary"].get("unplaced_piece_ids"))
print("snaps:", [(s["kind"], s["note"], s["moved_mm"]) for s in result["seam_snaps"]])
print("warnings:", result["warnings"])
out = {"plan": {k: result[k] for k in ("status", "piece_count", "oversize", "warnings")}, "seams": []}
for seam in seams:
    pid = seam.panel_id
    outer, holes = sheets.classify_loops(loops[pid], step)
    pieces, pw = sheets.split_panel(pid, outer, holes, [seam], options)
    sizes = [(p.piece_id, *[round(v, 1) for v in sheets.oriented_extent(p, rotation, step)], round(p.area_mm2)) for p in pieces]
    mid = np.array([(seam.x1 + seam.x2) / 2, (seam.y1 + seam.y2) / 2])
    unit = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1]); unit /= np.hypot(*unit)
    chords = seamplace.seam_through(mid, unit, polygons, options, panel_ids=[pid])
    stored_len = math.hypot(seam.x2 - seam.x1, seam.y2 - seam.y1)
    row = dict(seam=seam.seam_id, panel=pid, stored_len=round(stored_len, 1),
               chords_on_line=[round(c["length_mm"], 1) for c in chords],
               stored_is_one_of_them=any(abs(c["length_mm"] - stored_len) < 0.5 and
                                         math.hypot((c["x1"] + c["x2"]) / 2 - mid[0], (c["y1"] + c["y2"]) / 2 - mid[1]) < 0.5 for c in chords),
               pieces_alone=sizes, split_warnings=pw)
    out["seams"].append(row)
    print(json.dumps(row))
# width profile of panels 2 and 3 across the boat, every 25 mm along the axis
across = seamsnap.master_directions(axis)[1]
for pid in (2, 3):
    poly = polygons[pid]
    pts = np.asarray(poly.exterior.coords)
    along_coord = pts @ axis
    lo, hi = along_coord.min(), along_coord.max()
    profile = []
    for a in np.arange(lo + 5, hi - 5, 25.0):
        # a point on the line: along = a, across through the polygon centroid
        base = axis * a + across * float(np.asarray(poly.centroid.coords[0]) @ across)
        chords = seamplace.seam_through(base, across, {pid: poly}, options)
        profile.append((round(float(a), 0), [round(c["length_mm"], 1) for c in chords]))
    widths = [(a, sum(c)) for a, c in profile if c]
    narrow = sorted(widths, key=lambda w: w[1])[:5]
    print(f"panel {pid}: across-boat width along the axis -- min {narrow}, max {max(widths, key=lambda w: w[1])}")
    out[f"profile_{pid}"] = profile
dump("optimiser_short_seams.json", out)
