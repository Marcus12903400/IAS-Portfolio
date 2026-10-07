"""Probe 9d: why do split pieces come back as ~1 mm polylines?  Replicates
split_panel's internals on panel 1 with one seam and inspects the provenance
labels and the rebuild decision per group."""

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from probe_common import OPTIONS, STEP, load_run, sheets

run = load_run()
outer, holes, _p = run["panels"][1]
panel = sheets.loop_polygon(outer, holes, STEP)
seam = sheets.Seam("far", -1500.0, 900.0, 2500.0, 900.0, panel_id=1, snap=False)
reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
kerf = seam.extended(reach).buffer(3.0, cap_style=2, join_style=2)
remainder = panel.difference(kerf)
parts = sorted(list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder], key=lambda g: -g.area)
part = orient(parts[0], 1.0)
ring = np.asarray(part.exterior.coords)[:-1]
print(f"panel polygon: {len(panel.exterior.coords)} sampled boundary points; largest part ring: {len(ring)} points")

sources = [(loop, *sheets.sample_loop(loop, STEP)) for loop in [outer, *holes]]
all_points = np.vstack([pts for _l, pts, _s in sources])
owner = np.concatenate([np.full(len(pts), i) for i, (_l, pts, _s) in enumerate(sources)])
all_src = np.concatenate([src for _l, _p, src in sources])
mid = 0.5 * (ring + np.roll(ring, -1, axis=0))
dist, nearest = cKDTree(all_points).query(mid, k=1)
snap = max(STEP * 0.75, 1e-6)
on_original = dist <= snap
seg_src = np.where(on_original, all_src[nearest], -1)
loop_of = np.where(on_original, owner[nearest], -1)
print(f"segments labelled -1 (introduced): {int((seg_src < 0).sum())} of {len(ring)}; midpoint distance to nearest source point: "
      f"median {np.median(dist):.3f} mm, 90th pct {np.percentile(dist, 90):.3f}, max {dist.max():.3f}")
dominant = int(np.bincount(loop_of[loop_of >= 0]).argmax())
seg_src = np.where(loop_of == dominant, seg_src, -1)

# replicate rebuild_loop's grouping and decisions
bulges = outer.bulges
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
print(f"groups: {len(groups)}; by source segment: "
      f"{[(s, len(ix), 'arc' if 0 <= s < len(bulges) and abs(bulges[s]) > 1e-12 else 'line') for s, ix in groups][:40]}")
kept_arc = fallback = straight = 0
fallback_detail = []
for s, segs in groups:
    is_arc = 0 <= s < len(bulges) and abs(float(bulges[s])) > 1e-12
    if not is_arc or len(segs) < 3:
        straight += 1
        continue
    p0, p1 = ring[segs[0]], ring[(segs[-1] + 1) % len(ring)]
    pm = ring[segs[len(segs) // 2]]
    b = sheets._bulge_through(p0, pm, p1)
    centre, radius, _s, _t = sheets.bulge_to_arc(p0, p1, b)
    covered = ring[segs]
    dev = float(np.abs(np.linalg.norm(covered - centre, axis=1) - radius).max())
    if dev > float(OPTIONS["arc_rebuild_tolerance_mm"]):
        fallback += 1
        fallback_detail.append((s, len(segs), round(dev, 3), round(radius, 1), round(float(bulges[s]), 4)))
    else:
        kept_arc += 1
print(f"arc groups kept as one bulge: {kept_arc}; arc groups that fell back to the sampled polyline: {fallback}; straight groups: {straight}")
print("fallback detail (source seg, points, deviation mm, fitted radius mm, original bulge):", fallback_detail[:20])

# and the real thing, for the record
pieces, w = sheets.split_panel(1, outer, holes, [seam], OPTIONS)
p = pieces[0]
print(f"split_panel P1-1: {len(p.outer.vertices)} vertices, max_arc_error {p.max_arc_error_mm:.4f}")
