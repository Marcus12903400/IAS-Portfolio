"""Does the chord the hover previews equal what split_panel cuts? Scratch copy of the AXIS run."""
import json, math, time
from pathlib import Path
import numpy as np
run = Path(__file__).parent / "axisrun2"
from autodeck_app import bridge
from autodeck2 import sheets, seamplace, sheetjob
from autodeck2.config import load_config

options = sheets.settings(load_config())
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(run))[0]
polygons = seamplace.panel_polygons(loops, options)
frame, _w = sheetjob.resolve_frame(run, options)
unit = seamplace.direction_for(frame.axis, "across", None)
p1 = polygons[1]
holes = sorted(p1.interiors, key=lambda r: -abs(r.length))
console = holes[0]
c = np.array(console.centroid.coords[0])
print("panel 1 bounds", [round(v) for v in p1.bounds], "console centroid", c.round(1), "console bbox", [round(v) for v in console.bounds])
segs = seamplace.seam_through(c, unit, polygons, options)
print("seam_through at console centroid, across ->", len(segs), "segment(s):")
for s in segs: print("   panel", s["panel_id"], "len", round(s["length_mm"]), "from", (round(s["x1"]), round(s["y1"])), "to", (round(s["x2"]), round(s["y2"])))
hover = segs[0]   # the chord the user would see and click on one side of the console
seam = {"x1": hover["x1"], "y1": hover["y1"], "x2": hover["x2"], "y2": hover["y2"], "panel_id": hover["panel_id"],
        "mode": "across", "angle_deg": None, "snap": True, "raw": [hover["x1"], hover["y1"], hover["x2"], hover["y2"]]}
r = bridge.sheet_preview(run, {}, seams=[seam], save=True, view=None)
stored = r["seams"][0]
print("stored seam after corrector: len", stored["length_mm"], "snap_note:", stored["snap_note"][:120])
p1_pieces = [p for p in r["pieces"] if p["panel_id"] == 1]
print("panel 1 pieces after ONE seam previewed on one side of the console:", [(p["piece_id"], round(p["area_mm2"]/1e6, 3)) for p in p1_pieces])
# what the 3D view draws for it (seam_world_polylines logic without a view): the whole line through the panel
crossed = seamplace.panels_crossed(stored["x1"], stored["y1"], stored["x2"], stored["y2"], polygons, options, stored["panel_id"])
mid = np.array([(stored["x1"]+stored["x2"])/2, (stored["y1"]+stored["y2"])/2])
u = np.array([stored["x2"]-stored["x1"], stored["y2"]-stored["y1"]]); u /= np.hypot(*u)
whole = seamplace.seam_through(mid, u, polygons, options, panel_ids=crossed)
print("3D view / seam_world_polylines would draw", len(whole), "piece(s):", [round(s["length_mm"]) for s in whole])
