import json, math
from pathlib import Path
import numpy as np
from shapely.geometry import Point, Polygon
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "axisrun"
config = load_config()
options = sheets.settings(config)
step = options["sample_step_mm"]
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
frame, _w = sheetjob.resolve_frame(RUN, options)
outer, holes = sheets.classify_loops(loops[1], step)
print("panel 1 holes (verts, bulges, centroid, area):")
hole_info = []
for i, h in enumerate(holes):
    pts, _s = sheets.sample_loop(h, step)
    poly = Polygon(pts)
    hole_info.append((i, len(h.vertices), int(np.count_nonzero(np.abs(h.bulges) > 1e-12)), poly.centroid, abs(poly.area)))
    print(f"  hole {i}: verts {len(h.vertices)} bulges {int(np.count_nonzero(np.abs(h.bulges) > 1e-12))} centroid ({poly.centroid.x:.0f},{poly.centroid.y:.0f}) area {abs(poly.area):.0f} bbox {[round(b) for b in poly.bounds]}")

for name in ["seams.json", "seams_previous.json", "seams_optimiser_run.json.bak"]:
    seams = [sheets.Seam.from_dict(s) for s in json.loads((RUN / name).read_text(encoding="utf-8"))["seams"]]
    snapped, _r, _sw = sheetjob.apply_snap(seams, loops, frame.axis, options)
    pieces, warns = sheets.split_panel(1, outer, holes, snapped, options)
    print(f"\n== {name}: {len(pieces)} pieces")
    total_lost = 0.0
    for p in pieces:
        poly = p.polygon(step)             # rebuilt loops, holes subtracted
        shell = Polygon(sheets.sample_loop(p.outer, step)[0])
        inside = [i for (i, _v, _b, c, _a) in hole_info if shell.contains(c)]
        diff = poly.area - p.area_mm2       # + means the piece has MORE area than the shapely part: a hole went missing
        if abs(diff) > 50 or inside:
            print(f"  {p.piece_id:6s} shapely-part area {p.area_mm2:10.0f}  rebuilt area {poly.area:10.0f}  diff {diff:+9.0f}  original holes whose centroid is inside this piece's shell: {inside}  rebuilt holes: {[len(h.vertices) for h in p.holes]}")
        if diff > 50: total_lost += diff
    print(f"  total hole area missing from rebuilt pieces: {total_lost:.0f} mm2")
