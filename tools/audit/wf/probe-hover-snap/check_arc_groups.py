"""Why does P1-1's outer ring come back with thousands of vertices after one seam?
Replicates sheets._rebuild_ring / rebuild_loop on the real boolean output with diagnostics."""
import sys, json, math
from pathlib import Path
import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from scipy.spatial import cKDTree
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN
for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"): sys.path.insert(0, p)
from autodeck2 import sheetjob, sheets
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config); step = float(options["sample_step_mm"])
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
outer, holes = sheets.classify_loops(loops[1], step)
six = json.loads(Path(sys.argv[1]).read_text())["six"]
seam = sheets.Seam.from_dict(six[0])
panel = sheets.loop_polygon(outer, holes, step)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerf = seam.extended(reach).buffer(3.0, cap_style=2, join_style=2)
remainder = panel.difference(kerf)
parts = sorted(list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder], key=lambda g: -g.area)
part = orient(parts[0], 1.0)
ring = np.asarray(part.exterior.coords)[:-1]
print("panel exterior sampled points:", len(np.asarray(panel.exterior.coords)) - 1, "; P1-1 exterior ring points:", len(ring))
# how many ring points are NOT original sampled points (i.e. created or moved by the boolean)?
src_pts, src_idx = sheets.sample_loop(outer, step)
d, _ = cKDTree(src_pts).query(ring)
print("ring points farther than 1e-6 mm from any sampled outer point:", int((d > 1e-6).sum()), "; farther than 1e-3:", int((d > 1e-3).sum()), "; max", d.max())
# replicate the provenance step
sources = [(l, *sheets.sample_loop(l, step)) for l in [outer, *holes]]
all_points = np.vstack([pts for _l, pts, _s in sources]); owner = np.concatenate([np.full(len(pts), i) for i, (_l, pts, _s) in enumerate(sources)])
all_src = np.concatenate([s for _l, _p, s in sources])
mid = 0.5 * (ring + np.roll(ring, -1, axis=0))
dist, nearest = cKDTree(all_points).query(mid)
on = dist <= max(step * 0.75, 1e-6)
seg_src = np.where(on, all_src[nearest], -1); loop_of = np.where(on, owner[nearest], -1)
dominant = int(np.bincount(loop_of[loop_of >= 0], minlength=len(sources)).argmax())
seg_src = np.where(loop_of == dominant, seg_src, -1)
print("edges with no provenance (-1):", int((seg_src < 0).sum()), "of", len(seg_src), "; dominant loop", dominant)
# group like rebuild_loop and report arc groups that fail the tolerance
n = len(ring); bulges = outer.bulges
groups = []
for i in range(n):
    s = int(seg_src[i])
    if groups and groups[-1][0] == s and s >= 0: groups[-1][1].append(i)
    else: groups.append((s, [i]))
if len(groups) > 1 and groups[0][0] == groups[-1][0] and groups[0][0] >= 0:
    groups[0] = (groups[0][0], groups[-1][1] + groups[0][1]); groups.pop()
tol = float(options["arc_rebuild_tolerance_mm"])
failed = []; passed = 0; verts = 0
for s, segs in groups:
    is_arc = 0 <= s < len(bulges) and abs(float(bulges[s])) > 1e-12
    if not is_arc or len(segs) < 3:
        verts += 1; continue
    sp, ep = ring[segs[0]], ring[(segs[-1] + 1) % n]; mp = ring[segs[len(segs) // 2]]
    b = sheets._bulge_through(sp, mp, ep)
    if abs(b) < 1e-9: verts += 1; continue
    c, r, _a, _t = sheets.bulge_to_arc(sp, ep, b)
    dev = float(np.abs(np.linalg.norm(ring[segs] - c, axis=1) - r).max())
    if dev > tol:
        failed.append((s, len(segs), round(dev, 3), round(r, 1), round(float(bulges[s]), 4))); verts += len(segs)
    else:
        passed += 1; verts += 1
print(f"arc groups: passed {passed}, failed {len(failed)} -> rebuilt vertex count {verts}")
print("failed arc groups (orig segment, points, deviation mm, radius, orig bulge):", failed[:12])
# the same arcs on the panel's own untouched exterior ring, for comparison
own = np.asarray(panel.exterior.coords)[:-1]
print("untouched exterior ring == sampled outer points?", len(own) == len(src_pts) and np.allclose(own, src_pts))
# is it the ring start?  where does the boolean ring start relative to the sampled loop
print("ring[0]", ring[0], "nearest sampled index", int(cKDTree(src_pts).query(ring[0])[1]), "src segment", int(src_idx[int(cKDTree(src_pts).query(ring[0])[1])]))
# consecutive duplicate / reversed direction check
rev = np.allclose(ring[:5], own[:5])
seg_lengths = np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1)
print("ring edge length: min %.4f median %.4f max %.2f; edges < 1e-6: %d" % (seg_lengths.min(), np.median(seg_lengths), seg_lengths.max(), int((seg_lengths < 1e-6).sum())))

print("\n--- group diagnostics")
for s, segs in groups:
    if s not in (13, 20, 22): continue
    segs = list(segs)
    gaps = np.diff(segs)
    sp, ep = ring[segs[0]], ring[(segs[-1] + 1) % n]; mp = ring[segs[len(segs) // 2]]
    b = sheets._bulge_through(sp, mp, ep); c, r, _a, _t = sheets.bulge_to_arc(sp, ep, b)
    dev = np.abs(np.linalg.norm(ring[segs] - c, axis=1) - r)
    worst = int(np.argmax(dev))
    # the original arc of segment s
    xy = outer.xy; p0, p1 = xy[s], xy[(s + 1) % len(xy)]
    c0, r0, a0, t0 = sheets.bulge_to_arc(p0, p1, float(bulges[s]))
    dev0 = np.abs(np.linalg.norm(ring[segs] - c0, axis=1) - r0)
    print(f"segment {s}: {len(segs)} pts, index gaps max {gaps.max()} (contiguous={gaps.max()==1}), "
          f"fit radius {r:.1f} vs original {r0:.1f}; deviation from 3-pt fit max {dev.max():.3f} at position {worst}; "
          f"deviation of the same points from the ORIGINAL arc max {dev0.max():.4f} mm; "
          f"start {sp.round(2).tolist()} mid {mp.round(2).tolist()} end {ep.round(2).tolist()}; orig arc ends {p0.round(2).tolist()} {p1.round(2).tolist()}")
    # how many of the group's points are really on the original arc?  (chord sampling puts them all on it)
    off = int((dev0 > 0.01).sum())
    print(f"   points more than 0.01 mm off the original arc: {off}; nearest sampled source segment of those: "
          f"{sorted(set(int(src_idx[j]) for j in cKDTree(src_pts).query(ring[np.asarray(segs)[dev0 > 0.01]])[1]))[:10] if off else []}")
