import json, math
from pathlib import Path
import numpy as np
from shapely.geometry import Point, LineString
from shapely.ops import unary_union
from autodeck2 import sheets, seamplace, seamsnap, sheetjob
from autodeck2.config import load_config
S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/seamsnap-seamplace/axisrun")
opts = sheets.settings(load_config())
loops = sheets.read_fitted_dxf(S / "final_auto.dxf")[0]
polys = seamplace.panel_polygons(loops, opts)
frame = sheetjob.boat_axis(S)
along, across = seamsnap.master_directions(frame.axis)
rotation = sheets.sheet_transform(frame.axis)
seams = [sheets.Seam.from_dict(d) for d in json.load(open(S / "seams_optimiser_run.json.bak"))["seams"]]
print("== each optimiser seam alone: chords of its INFINITE line on its panel vs the stored segment")
for seam in seams:
    pid = seam.panel_id
    poly = polys[pid]
    reach = float(np.hypot(*(np.asarray(poly.bounds[2:]) - np.asarray(poly.bounds[:2])))) + 10.0
    cut_line = seam.extended(reach)
    inter = poly.intersection(cut_line)
    parts = seamplace._straight_parts(inter)
    lens = [round(float(np.hypot(*(b - a))), 1) for a, b in parts]
    stored_len = math.hypot(seam.x2 - seam.x1, seam.y2 - seam.y1)
    outer, holes = sheets.classify_loops(loops[pid], opts["sample_step_mm"])
    pieces, warn = sheets.split_panel(pid, outer, holes, [seam], opts)
    print(f"{seam.seam_id}: panel {pid} stored length {stored_len:.1f} mm; infinite line crosses panel in {len(parts)} chord(s) lengths={lens}; "
          f"split_panel -> {len(pieces)} pieces areas={[round(p.area_mm2) for p in pieces]} warnings={warn}")
    for a, b in parts:
        print(f"      chord ({a[0]:.0f},{a[1]:.0f})-({b[0]:.0f},{b[1]:.0f})")
    # local across-width of the panel at the seam midpoint
    mid = np.array([(seam.x1 + seam.x2) / 2, (seam.y1 + seam.y2) / 2])
    segs = seamplace.seam_through(mid, across if seam.mode == "across" else along, polys, opts, panel_ids=[pid])
    print(f"      seam_through at the midpoint gives chords {[round(s['length_mm'],1) for s in segs]}")

print("\n== panels 2 and 3 shape: width across the boat sampled along their length")
for pid in (2, 3):
    poly = polys[pid]
    xy = np.asarray(poly.exterior.coords)
    a = xy @ along
    widths = []
    for t in np.linspace(a.min() + 20, a.max() - 20, 12):
        p = along * t + across * float((xy @ across).mean())
        # find a point inside near this along-station: scan across
        found = None
        for c in np.linspace((xy @ across).min(), (xy @ across).max(), 200):
            q = along * t + across * c
            if poly.contains(Point(q[0], q[1])):
                found = q; break
        if found is None:
            widths.append((round(t), None)); continue
        segs = seamplace.seam_through(found, across, polys, opts, panel_ids=[pid])
        widths.append((round(t), [round(s["length_mm"]) for s in segs]))
    print(f"panel {pid}: across-chords by along-station: {widths}")

print("\n== the whole 6-seam optimiser set through split_panel + oversize_report")
for pid in sorted(polys):
    outer, holes = sheets.classify_loops(loops[pid], opts["sample_step_mm"])
    pieces, warn = sheets.split_panel(pid, outer, holes, seams, opts)
    report = sheets.oversize_report(pieces, rotation, opts)
    print(f"panel {pid}: {len(pieces)} pieces, warnings={warn}, oversize={[(r['piece_id'], r['width_mm'], r['length_mm']) for r in report]}")
    for p in pieces:
        w, l = sheets.oriented_extent(p, rotation, opts["sample_step_mm"])
        print(f"     {p.piece_id}: {w:.0f} x {l:.0f} mm (across x along), area {p.area_mm2:.0f}")
