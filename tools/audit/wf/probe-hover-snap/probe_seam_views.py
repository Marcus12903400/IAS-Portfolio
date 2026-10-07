"""What the two views draw for the run's own four hand seams after straightening:
the flat view draws the stored x1..y2 (app.js drawSeams -> seamPiecesForDrawing),
the 3D view draws bridge.seam_world_polylines (the whole chord through the
middle).  Measures how far each stored seam stops short of the outline and how
long the 3D polyline is.  Also exercises bridge.sheet_preview(save=False) on
the staged copy.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN, dump, load

ctx = load()
bridge, view, options, config = ctx["bridge"], ctx["view"], ctx["options"], ctx["config"]
polygons, seamplace, sheets = ctx["polygons"], ctx["seamplace"], ctx["sheets"]

before = (RUN / "seams.json").read_bytes()
result = bridge.sheet_preview(RUN, {}, view=view)      # save=False: must not write
assert (RUN / "seams.json").read_bytes() == before, "sheet_preview(save=False) rewrote seams.json"
print("preview status", result["status"], "pieces", result["piece_count"], "sheets", result["summary"]["sheet_count"],
      "oversize", [o["piece_id"] for o in result["oversize"]])
rows = []
for seam, world in zip(result["seams"], result["seams_world"]):
    pid_list = seamplace.panels_crossed(seam["x1"], seam["y1"], seam["x2"], seam["y2"], polygons, options, seam["panel_id"])
    stored = LineString([(seam["x1"], seam["y1"]), (seam["x2"], seam["y2"])])
    gaps = {pid: [polygons[pid].boundary.distance(Point(seam["x1"], seam["y1"])),
                  polygons[pid].boundary.distance(Point(seam["x2"], seam["y2"]))] for pid in pid_list}
    middle = np.array([(seam["x1"] + seam["x2"]) / 2, (seam["y1"] + seam["y2"]) / 2])
    unit = np.array([seam["x2"] - seam["x1"], seam["y2"] - seam["y1"]]); unit /= np.hypot(*unit)
    chords = seamplace.seam_through(middle, unit, polygons, options, panel_ids=pid_list)
    world_len = sum(sum(math.dist(a, b) for a, b in zip(poly[:-1], poly[1:])) for poly in world["world"])
    rows.append(dict(seam=seam["seam_id"], mode=seam["mode"], note=seam["snap_note"], stored_len=round(stored.length, 1),
                     flat_view_stops_short_mm={pid: [round(g, 1) for g in gs] for pid, gs in gaps.items()},
                     three_d_chords=[round(c["length_mm"], 1) for c in chords], three_d_polyline_len=round(world_len, 1)))
    print(json.dumps(rows[-1]))
dump("seam_views.json", rows)
