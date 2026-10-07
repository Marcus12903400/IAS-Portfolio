"""Replicates sheets._rebuild_ring / rebuild_loop with tracing, on the fixture's untouched holes."""
import json
from pathlib import Path
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from scipy.spatial import cKDTree
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "axisrun"
config = load_config()
options = sheets.settings(config)
step = options["sample_step_mm"]; tol = options["arc_rebuild_tolerance_mm"]
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
frame, _w = sheetjob.resolve_frame(RUN, options)
outer, holes = sheets.classify_loops(loops[1], step)
seams = [sheets.Seam.from_dict(s) for s in json.loads((RUN / "seams_optimiser_run.json.bak").read_text(encoding="utf-8"))["seams"]]
snapped, _r, _sw = sheetjob.apply_snap(seams, loops, frame.axis, options)
panel = sheets.loop_polygon(outer, holes, step)
relevant = [s for s in snapped if s.panel_id == 1]
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerfs = [s.extended(reach).buffer(3.0, cap_style=2, join_style=2) for s in relevant]
remainder = panel.difference(unary_union(kerfs))
parts = sorted([p for p in remainder.geoms if p.area >= 400], key=lambda g: (-g.area, g.bounds))
sources = []
for loop in [outer, *holes]:
    pts, src = sheets.sample_loop(loop, step)
    sources.append((loop, pts, src))
all_points = np.vstack([pts for _l, pts, _s in sources])
owner = np.concatenate([np.full(len(pts), i) for i, (_l, pts, _s) in enumerate(sources)])
all_src = np.concatenate([src for _l, _p, src in sources])
tree = cKDTree(all_points)

def trace(ring, label):
    midpoints = 0.5 * (ring + np.roll(ring, -1, axis=0))
    distance, nearest = tree.query(midpoints, k=1)
    on = distance <= step * 0.75
    seg = np.where(on, all_src[nearest], -1)
    lo = np.where(on, owner[nearest], -1)
    dominant = int(np.bincount(lo[lo >= 0], minlength=len(sources)).argmax())
    seg = np.where(lo == dominant, seg, -1)
    foreign = int(((lo >= 0) & (lo != dominant)).sum())
    original = sources[dominant][0]
    n = len(ring)
    groups = []
    for i in range(n):
        s = int(seg[i])
        if groups and groups[-1][0] == s and s >= 0:
            groups[-1][1].append(i)
        else:
            groups.append((s, [i]))
    if len(groups) > 1 and groups[0][0] == groups[-1][0] and groups[0][0] >= 0:
        groups[0] = (groups[0][0], groups[-1][1] + groups[0][1]); groups.pop()
    print(f"  {label}: ring pts {n}, dominant loop {dominant} ({'outer' if dominant == 0 else 'hole %d' % (dominant - 1)}), segments tagged -1: {int((seg < 0).sum())} (of which from another loop: {foreign})")
    for s, segs in groups:
        if s < 0: continue
        b = float(original.bulges[s])
        if abs(b) < 1e-12 or len(segs) < 3: continue
        start = ring[segs[0]]; end = ring[(segs[-1] + 1) % n]; mid = ring[segs[len(segs) // 2]]
        bulge = sheets._bulge_through(start, mid, end)
        centre, radius, _s, _t = sheets.bulge_to_arc(start, end, bulge)
        covered = ring[segs]
        dev = float(np.abs(np.linalg.norm(covered - centre, axis=1) - radius).max())
        # how far is the group's first/last point from the TRUE arc of the original segment?
        p0 = original.xy[s]; p1 = original.xy[(s + 1) % len(original.xy)]
        tc, tr, _a, _b = sheets.bulge_to_arc(p0, p1, b)
        off_start = abs(np.linalg.norm(start - tc) - tr); off_end = abs(np.linalg.norm(end - tc) - tr)
        verdict = "ARC kept" if dev <= tol else "FLATTENED to %d straight verts" % len(segs)
        print(f"     seg {s:3d} r={tr:7.1f} n={len(segs):4d} dev={dev:.4f} (tol {tol})  start off true arc {off_start:.4f}  end off true arc {off_end:.4f}  -> {verdict}")

for idx, part in enumerate(parts[:4]):
    part = orient(part, 1.0)
    print(f"piece {idx + 1} area {part.area:.0f}")
    trace(np.asarray(part.exterior.coords)[:-1], "exterior")
    for j, ring in enumerate(part.interiors):
        trace(np.asarray(ring.coords)[:-1], f"interior {j}")
