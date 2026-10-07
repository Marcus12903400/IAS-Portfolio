"""Probe 9e: for the arc groups that rebuild_loop rejects, compare the group's
points and its three fit points against the ORIGINAL arc (centre/radius from
the fitted loop's own bulge)."""

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import MultiPolygon
from shapely.geometry.polygon import orient

from probe_common import OPTIONS, STEP, load_run, sheets

run = load_run()
outer, holes, _p = run["panels"][1]
panel = sheets.loop_polygon(outer, holes, STEP)
seam = sheets.Seam("far", -1500.0, 900.0, 2500.0, 900.0, panel_id=1, snap=False)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
remainder = panel.difference(seam.extended(reach).buffer(3.0, cap_style=2, join_style=2))
parts = sorted(list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder], key=lambda g: -g.area)
part = orient(parts[0], 1.0)
ring = np.asarray(part.exterior.coords)[:-1]
xy, bulges = outer.xy, outer.bulges
n_orig = len(xy)
print("original loop winding (signed area > 0 = CCW):", float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]) / 2))
print("panel polygon valid before repair:", sheets.Polygon(sheets.sample_loop(outer, STEP)[0]).is_valid)

sources = [(loop, *sheets.sample_loop(loop, STEP)) for loop in [outer, *holes]]
all_points = np.vstack([pts for _l, pts, _s in sources])
owner = np.concatenate([np.full(len(pts), i) for i, (_l, pts, _s) in enumerate(sources)])
all_src = np.concatenate([src for _l, _p, src in sources])
mid = 0.5 * (ring + np.roll(ring, -1, axis=0))
dist, nearest = cKDTree(all_points).query(mid, k=1)
seg_src = np.where(dist <= 0.75, all_src[nearest], -1)
loop_of = np.where(dist <= 0.75, owner[nearest], -1)
seg_src = np.where(loop_of == 0, seg_src, -1)
groups = []
for i in range(len(ring)):
    s = int(seg_src[i])
    if groups and groups[-1][0] == s and s >= 0:
        groups[-1][1].append(i)
    else:
        groups.append((s, [i]))
if len(groups) > 1 and groups[0][0] == groups[-1][0] and groups[0][0] >= 0:
    groups[0] = (groups[0][0], groups[-1][1] + groups[0][1])
    groups.pop()

for s, segs in groups:
    if not (0 <= s < n_orig and abs(bulges[s]) > 1e-12) or len(segs) < 3:
        continue
    p0o, p1o = xy[s], xy[(s + 1) % n_orig]
    centre, radius, start, theta = sheets.bulge_to_arc(p0o, p1o, float(bulges[s]))
    covered = ring[segs]
    d_cov = np.abs(np.linalg.norm(covered - centre, axis=1) - radius)
    a, b, c = ring[segs[0]], ring[segs[len(segs) // 2]], ring[(segs[-1] + 1) % len(ring)]
    d3 = [abs(np.linalg.norm(p - centre) - radius) for p in (a, b, c)]
    fit_b = sheets._bulge_through(a, b, c)
    fc, fr, _s, _t = sheets.bulge_to_arc(a, c, fit_b)
    dev_fit = float(np.abs(np.linalg.norm(covered - fc, axis=1) - fr).max())
    flag = "FALLBACK" if dev_fit > 0.05 else "ok"
    print(f"src {s:2d}: bulge {float(bulges[s]):+.4f} sweep {np.degrees(theta):+7.1f} deg r={radius:7.2f}; {len(segs)} pts, "
          f"max |pt - true arc| = {d_cov.max():.4f} mm (pts off arc: {int((d_cov > 0.01).sum())}); fit points off true arc: "
          f"start {d3[0]:.4f} mid {d3[1]:.4f} end {d3[2]:.4f}; 3-pt fit r={fr:.2f} bulge {fit_b:+.4f} dev {dev_fit:.3f} -> {flag}")
    if flag == "FALLBACK":
        # where along the group do the off-arc points sit?
        off = np.where(d_cov > 0.01)[0]
        print(f"      off-arc point positions within group: {off[:10].tolist()} ... of {len(segs)}; "
              f"ring index of group start {segs[0]}, end {segs[-1]}; first point {a}, last {c}")
