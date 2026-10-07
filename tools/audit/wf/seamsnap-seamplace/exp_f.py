"""The chord under the pointer vs what split_panel cuts along the whole line."""
import json, math
from pathlib import Path
import numpy as np
from shapely.geometry import Point, LineString
from autodeck2 import sheets, seamplace, seamsnap, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/seamsnap-seamplace/axisrun")
opts = sheets.settings(load_config())
loops = sheets.read_fitted_dxf(S / "final_auto.dxf")[0]
polys = seamplace.panel_polygons(loops, opts)
frame = sheetjob.boat_axis(S)
along, across = seamsnap.master_directions(frame.axis)
rotation = sheets.sheet_transform(frame.axis)


def chord_under(segs, pt):
    """What app.js segmentUnder does: the chord whose parameter range brackets the point (2 mm grace)."""
    best, gap_best = None, math.inf
    for s in segs:
        dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
        L2 = dx * dx + dy * dy
        t = ((pt[0] - s["x1"]) * dx + (pt[1] - s["y1"]) * dy) / L2
        gap = (-t if t < 0 else (t - 1 if t > 1 else 0)) * math.sqrt(L2)
        if gap < gap_best:
            best, gap_best = s, gap
    return best if gap_best <= 2.0 else None


cases = [
    (1, "along", (-1236.3, 390.6)),   # 8 chords on the line
    (1, "along", (-1236.3, -529.4)),
    (1, "across", (-796.3, 470.6)),   # 4 chords
    (1, "across", (-796.3, -400.0)),
    (1, "along", (2000.0, 150.0)),    # bow extension
    (1, "across", (300.0, 800.0)),    # beside the console
    (4, "across", (1041.3, -1486.2)),
    (2, "along", (-682.6, -1381.8)),
]
for pid, name, pt in cases:
    unit = along if name == "along" else across
    if not polys[pid].contains(Point(*pt)):
        pt2 = polys[pid].representative_point()
        print(f"  ({pt} not inside panel {pid}; skipping)")
        continue
    segs = seamplace.seam_through(pt, unit, polys, opts, panel_ids=[pid])
    picked = chord_under(segs, pt)
    if picked is None:
        print(f"panel {pid} {name} hover {pt}: {len(segs)} chords, none under the pointer")
        continue
    seam = sheets.Seam("h", picked["x1"], picked["y1"], picked["x2"], picked["y2"], panel_id=pid, mode=name,
                       raw=(picked["x1"], picked["y1"], picked["x2"], picked["y2"]))
    outer, holes = sheets.classify_loops(loops[pid], opts["sample_step_mm"])
    pieces, warn = sheets.split_panel(pid, outer, holes, [seam], opts)
    sizes = [tuple(round(v) for v in sheets.oriented_extent(p, rotation, opts["sample_step_mm"])) for p in pieces]
    other = [round(s["length_mm"]) for s in segs if s is not picked]
    print(f"panel {pid} {name} hover {pt}: preview shows ONE chord of {picked['length_mm']:.0f} mm (hint: '{picked['length_mm']:.0f} mm on panel {pid}'); "
          f"the same line has {len(segs) - 1} other chord(s) on this panel {other}; split_panel with that one stored chord -> {len(pieces)} pieces {sizes} {warn}")

print("\n== what seam_world_polylines (3D view) would draw for the stored chord vs the flat view (x1..y2 only)")
pid, name, pt = cases[0]
segs = seamplace.seam_through(pt, along, polys, opts, panel_ids=[pid])
picked = chord_under(segs, pt)
mid = np.array([(picked["x1"] + picked["x2"]) / 2, (picked["y1"] + picked["y2"]) / 2])
crossed = seamplace.panels_crossed(picked["x1"], picked["y1"], picked["x2"], picked["y2"], polys, opts, pid)
world_pieces = seamplace.seam_through(mid, along, polys, opts, panel_ids=crossed)
print(f"  stored chord {picked['length_mm']:.0f} mm; 3D overlay re-trims through its middle -> {len(world_pieces)} pieces {[round(s['length_mm']) for s in world_pieces]}; flat view draws 1 piece")

print("\n== a FREE seam (panel_id None, old-style drag) only cuts panels its drawn segment touches; a bound one cuts the whole line")
x1, y1, x2, y2 = picked["x1"], picked["y1"], picked["x2"], picked["y2"]
free = sheets.Seam("f", x1, y1, x2, y2, panel_id=None)
outer, holes = sheets.classify_loops(loops[1], opts["sample_step_mm"])
pieces, warn = sheets.split_panel(1, outer, holes, [free], opts)
print(f"  same chord as a free seam: panels_crossed={seamplace.panels_crossed(x1, y1, x2, y2, polys, opts)} -> split_panel(panel 1) {len(pieces)} pieces (relevance is by the drawn segment, the cut is still the whole line)")
