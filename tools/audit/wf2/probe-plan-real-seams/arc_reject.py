"""Why do arcs come back as 1 mm polylines?  Replicates sheets.rebuild_loop's
grouping and arc test on the production ring of each piece (same provenance
_rebuild_ring computes) and reports, per arc group: accepted or rejected, the
deviation that decided it, and the deviation with the group's first/last ring
point left out (to show whether one stray point from the neighbouring segment
is what fails the 0.05 mm tolerance).  Nothing here changes the production code.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/arc_reject.py D P1-3 A P1-2 C P1-1 C P3-1
"""
import math
import sys

import numpy as np
from scipy.spatial import cKDTree

from common import OUT, copy_dir, load_json, resplit
from area_diff import raw_parts
from autodeck2 import sheets
from autodeck2.config import load_config


def provenance(ring, sources, step):
    all_points = np.vstack([p for _l, p, _s in sources])
    owner = np.concatenate([np.full(len(p), i) for i, (_l, p, _s) in enumerate(sources)])
    all_src = np.concatenate([s for _l, _p, s in sources])
    mids = 0.5 * (ring + np.roll(ring, -1, axis=0))
    dist, near = cKDTree(all_points).query(mids, k=1)
    on = dist <= max(step * 0.75, 1e-6)
    seg_src = np.where(on, all_src[near], -1)
    loop_of = np.where(on, owner[near], -1)
    dominant = int(np.bincount(loop_of[loop_of >= 0], minlength=len(sources)).argmax())
    return np.where(loop_of == dominant, seg_src, -1), dominant, dist, near, owner


def main(args):
    config = load_config()
    options = sheets.settings(config)
    step = float(options["sample_step_mm"])
    tol = float(options["arc_rebuild_tolerance_mm"])
    for name, pid in zip(args[0::2], args[1::2]):
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        piece = rs["pieces"][pid]
        outer_l, holes_l, _poly = rs["panels"][piece.panel_id]
        raw = raw_parts(rs, options)[pid]
        sources = []
        for loop in [outer_l, *holes_l]:
            pts, src = sheets.sample_loop(loop, step)
            sources.append((loop, pts, src))
        rings = [("exterior", np.asarray(raw.exterior.coords)[:-1])] + \
                [(f"interior {i}", np.asarray(r.coords)[:-1]) for i, r in enumerate(raw.interiors)]
        print("=" * 110)
        print(f"SET {name} {pid}: rebuilt outer verts {len(piece.outer.vertices)} (arcs {int((np.abs(piece.outer.bulges) > 1e-12).sum())})")
        for label, ring in rings:
            seg_src, dominant, dist, near, owner = provenance(ring, sources, step)
            orig = sources[dominant][0]
            n = len(ring)
            groups = []
            for idx in range(n):
                s = int(seg_src[idx])
                if groups and groups[-1][0] == s and s >= 0:
                    groups[-1][1].append(idx)
                else:
                    groups.append((s, [idx]))
            if len(groups) > 1 and groups[0][0] == groups[-1][0] and groups[0][0] >= 0:
                groups[0] = (groups[0][0], groups[-1][1] + groups[0][1])
                groups.pop()
            accepted = rejected = 0
            rejected_len = 0.0
            details = []
            for src, segs in groups:
                is_arc = 0 <= src < len(orig.bulges) and abs(float(orig.bulges[src])) > 1e-12
                if not is_arc or len(segs) < 3:
                    continue
                start = ring[segs[0]]; end = ring[(segs[-1] + 1) % n]; mid = ring[segs[len(segs) // 2]]
                bulge = sheets._bulge_through(start, mid, end)
                if abs(bulge) < 1e-9:
                    continue
                centre, radius, _s, _t = sheets.bulge_to_arc(start, end, bulge)
                covered = ring[segs]
                dev_all = float(np.abs(np.linalg.norm(covered - centre, axis=1) - radius).max())
                inner = ring[segs[1:-1]] if len(segs) > 2 else covered
                dev_inner = float(np.abs(np.linalg.norm(inner - centre, axis=1) - radius).max())
                # which ring points of the group are far from the original segment's own samples
                own_pts = sources[dominant][1][sources[dominant][2] == src]
                d_first = float(np.min(np.linalg.norm(own_pts - ring[segs[0]], axis=1)))
                d_last = float(np.min(np.linalg.norm(own_pts - ring[segs[-1]], axis=1)))
                length = sum(float(np.hypot(*(ring[(i + 1) % n] - ring[i]))) for i in segs)
                if dev_all > tol:
                    rejected += 1; rejected_len += length
                    details.append(f"      REJECTED arc group src {src}: {len(segs)} segs, {length:.0f} mm, dev {dev_all:.3f} mm (tol {tol}); "
                                   f"dev without first/last point {dev_inner:.4f}; first ring pt is {d_first:.2f} mm, last ring pt {d_last:.2f} mm "
                                   f"from that original segment's own samples")
                else:
                    accepted += 1
            print(f"  {label}: {n} ring points, dominant loop {dominant}, {len(groups)} groups; arc groups accepted {accepted}, "
                  f"rejected {rejected} ({rejected_len:.0f} mm of outline written as 1 mm facets)")
            for d in details[:12]:
                print(d)
            if len(details) > 12:
                print(f"      ... {len(details) - 12} more rejected arc groups")


if __name__ == "__main__":
    main(sys.argv[1:])
