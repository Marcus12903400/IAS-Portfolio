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
print("axis", frame.axis, "along", along, "across", across)
for pid, poly in sorted(polys.items()):
    print(f"panel {pid}: type={poly.geom_type} bounds={[round(v,1) for v in poly.bounds]} area={poly.area:.0f} holes={len(poly.interiors)} "
          f"convexity={poly.area/poly.convex_hull.area:.4f} loops={len(loops[pid])} valid={poly.is_valid}")
    # extents in the boat frame
    xy = np.asarray(poly.exterior.coords)
    a = xy @ along; c = xy @ across
    print(f"   along-extent {a.max()-a.min():.0f}  across-extent {c.max()-c.min():.0f}")
    for k, ring in enumerate(poly.interiors):
        hb = ring.bounds
        print(f"   hole {k}: bounds={[round(v,1) for v in hb]} size={hb[2]-hb[0]:.0f}x{hb[3]-hb[1]:.0f}")
pids = sorted(polys)
for i in range(len(pids)):
    for j in range(i+1, len(pids)):
        print(f"gap between panel {pids[i]} and {pids[j]}: {polys[pids[i]].distance(polys[pids[j]]):.1f} mm")

print("\n== chord-count scan (hover grid inside each panel, both masters)")
for pid, poly in sorted(polys.items()):
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + 5, maxx, 40.0); ys = np.arange(miny + 5, maxy, 40.0)
    for name, unit in (("along", along), ("across", across)):
        worst = 0; ex = None; tiny = []; n = 0
        for x in xs:
            for y in ys:
                if not poly.contains(Point(x, y)):
                    continue
                n += 1
                segs = seamplace.seam_through((x, y), unit, polys, opts, panel_ids=[pid])
                if len(segs) > worst:
                    worst = len(segs); ex = (round(x, 1), round(y, 1), [round(s["length_mm"], 1) for s in segs])
                for s in segs:
                    if s["length_mm"] < 60:
                        tiny.append((round(x, 1), round(y, 1), round(s["length_mm"], 1)))
        print(f"panel {pid} {name}: hovers={n} max chords on one line={worst} example={ex} chords<60mm: {len(tiny)} e.g. {tiny[:4]}")

print("\n== hover INSIDE each cut-out of panel 1")
poly = polys[1]
for k, ring in enumerate(poly.interiors):
    from shapely.geometry import Polygon
    hole = Polygon(ring)
    pt = hole.representative_point()
    for name, unit in (("along", along), ("across", across)):
        segs = seamplace.seam_through((pt.x, pt.y), unit, polys, opts)
        def contains(s):
            d = np.array([s["x2"]-s["x1"], s["y2"]-s["y1"]]); L2 = d @ d
            t = ((pt.x - s["x1"]) * d[0] + (pt.y - s["y1"]) * d[1]) / L2
            return 0 <= t <= 1
        print(f"hole {k} ({pt.x:.0f},{pt.y:.0f}) {name}: {len(segs)} chords, lengths {[round(s['length_mm']) for s in segs]}, panels {[s['panel_id'] for s in segs]}, any contains hover point: {any(contains(s) for s in segs)}")

print("\n== hover exactly ON the outer boundary of panel 1")
ext = poly.exterior
for frac in (0.1, 0.37, 0.62, 0.85):
    pt = ext.interpolate(frac, normalized=True)
    for name, unit in (("along", along), ("across", across)):
        segs = seamplace.seam_through((pt.x, pt.y), unit, polys, opts, panel_ids=[1])
        ends = [min(math.hypot(s["x1"]-pt.x, s["y1"]-pt.y), math.hypot(s["x2"]-pt.x, s["y2"]-pt.y)) for s in segs]
        print(f"  boundary pt ({pt.x:.1f},{pt.y:.1f}) {name}: {len(segs)} chords lengths={[round(s['length_mm']) for s in segs]} nearest-end-to-hover={[round(e,3) for e in ends]}")

print("\n== hover with the line lying ALONG a straight fitted edge (direction within 0.5 deg of a master)")
refs = seamsnap.references_from_loops(loops, opts)
cases = 0
for r in refs:
    if r.kind != "line" or r.length_mm < 100:
        continue
    for name, unit in (("along", along), ("across", across)):
        if seamsnap._direction_angle_deg(r.unit, unit) <= 0.5:
            m = r.midpoint
            segs = seamplace.seam_through((m[0], m[1]), unit, polys, opts, panel_ids=[r.panel_id])
            on_boundary = [polys[r.panel_id].boundary.buffer(1e-6).contains(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])])) for s in segs]
            print(f"  {r.label} len={r.length_mm:.0f} dir={name}: chords={[round(s['length_mm']) for s in segs]} chord-lies-on-boundary={on_boundary}")
            cases += 1
    if cases >= 8:
        break
print("straight edges within 0.5 deg of a master:",
      sum(1 for r in refs if r.kind == "line" and min(seamsnap._direction_angle_deg(r.unit, along), seamsnap._direction_angle_deg(r.unit, across)) <= 0.5),
      "of", sum(1 for r in refs if r.kind == "line"))

# render
from PIL import Image, ImageDraw
allb = np.array([polys[p].bounds for p in polys])
minx, miny, maxx, maxy = allb[:,0].min(), allb[:,1].min(), allb[:,2].max(), allb[:,3].max()
W = 1800; sc = (W - 40) / (maxx - minx); H = int((maxy - miny) * sc) + 40
img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
def T(x, y): return (20 + (x - minx) * sc, H - 20 - (y - miny) * sc)
for pid, poly in polys.items():
    d.line([T(*c) for c in poly.exterior.coords], fill="black", width=2)
    for ring in poly.interiors:
        d.line([T(*c) for c in ring.coords], fill="gray", width=2)
    cx, cy = poly.representative_point().coords[0]
    d.text(T(cx, cy), f"P{pid}", fill="blue")
seams = json.load(open(S / "seams_optimiser_run.json.bak"))["seams"]
for s in seams:
    d.line([T(s["x1"], s["y1"]), T(s["x2"], s["y2"])], fill="red", width=3)
seams = json.load(open(S / "seams.json"))["seams"]
for s in seams:
    d.line([T(s["x1"], s["y1"]), T(s["x2"], s["y2"])], fill="green", width=3)
# axis arrow
o = np.array([minx + 300, maxy - 200]); e = o + along * 400
d.line([T(*o), T(*e)], fill="purple", width=4); d.text(T(*e), "along(bow)", fill="purple")
img.save(S.parent / "panels.png")
print("rendered", S.parent / "panels.png")
