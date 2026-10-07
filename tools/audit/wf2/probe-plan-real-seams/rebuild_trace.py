"""Trace sheets._rebuild_ring / rebuild_loop for one piece: which rebuilt edges
leave the raw outline, what their bulge is, and which provenance groups made
them.  Re-runs the production functions on the production ring (no fixes).

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/rebuild_trace.py D P1-3 [A P1-1 ...]
"""
import math
import sys

import numpy as np
from shapely.geometry import LineString, Point

from common import OUT, copy_dir, load_json, resplit
from area_diff import raw_parts
from autodeck2 import sheets
from autodeck2.config import load_config


def main(args):
    config = load_config()
    options = sheets.settings(config)
    pairs = list(zip(args[0::2], args[1::2]))
    for name, pid in pairs:
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        raw = raw_parts(rs, options)[pid]
        piece = rs["pieces"][pid]
        outer_l, holes_l, panel_poly = rs["panels"][piece.panel_id]
        step = float(options["sample_step_mm"])
        print("=" * 110)
        print(f"SET {name} {pid}: raw area {raw.area:.1f}, raw exterior points {len(raw.exterior.coords) - 1}, "
              f"interiors {len(raw.interiors)}; rebuilt outer verts {len(piece.outer.vertices)}, holes {len(piece.holes)}")
        # 1) rebuilt edges that leave the raw outline, sampled finely
        boundary = raw.boundary
        xy, bl = piece.outer.xy, piece.outer.bulges
        n = len(xy)
        bad = []
        for i in range(n):
            p0, p1 = xy[i], xy[(i + 1) % n]
            seg = sheets.Loop(np.array([[p0[0], p0[1], bl[i]], [p1[0], p1[1], 0.0]]))
            pts, _s = sheets.sample_loop(seg, 0.5)
            # sample_loop closes the 2-vertex loop (adds the return edge); keep the first edge only
            chord = float(np.hypot(*(p1 - p0)))
            if abs(bl[i]) < 1e-12:
                pts = np.linspace(p0, p1, max(2, int(chord / 0.5)))
            else:
                centre, radius, start, theta = sheets.bulge_to_arc(p0, p1, bl[i])
                k = max(4, int(abs(theta) * radius / 0.5))
                ang = np.linspace(start, start + theta, k)
                pts = centre + radius * np.column_stack([np.cos(ang), np.sin(ang)])
            dev = max(boundary.distance(Point(p)) for p in pts[::max(1, len(pts) // 200)])
            if dev > 0.3:
                bad.append((i, dev, chord, bl[i]))
        print(f"  rebuilt outer edges leaving the raw outline by > 0.3 mm: {len(bad)}")
        for i, dev, chord, b in bad:
            p0, p1 = xy[i], xy[(i + 1) % n]
            desc = f"bulge {b:+.6f}" if abs(b) > 1e-12 else "straight"
            extra = ""
            if abs(b) > 1e-12:
                centre, radius, start, theta = sheets.bulge_to_arc(p0, p1, b)
                extra = f" radius {radius:.1f} sweep {math.degrees(theta):.1f} deg sagitta {radius * (1 - math.cos(theta / 2)):.1f}"
            print(f"    edge {i}: ({p0[0]:.1f},{p0[1]:.1f}) -> ({p1[0]:.1f},{p1[1]:.1f}) chord {chord:.1f} mm {desc}{extra}; max off raw {dev:.1f} mm")

        # 2) reproduce the provenance for the raw exterior ring the way split_panel does
        from scipy.spatial import cKDTree
        sources = []
        for loop in [outer_l, *holes_l]:
            pts, src = sheets.sample_loop(loop, step)
            sources.append((loop, pts, src))
        ring = np.asarray(raw.exterior.coords)[:-1]
        all_points = np.vstack([p for _l, p, _s in sources])
        owner = np.concatenate([np.full(len(p), i) for i, (_l, p, _s) in enumerate(sources)])
        all_src = np.concatenate([s for _l, _p, s in sources])
        mids = 0.5 * (ring + np.roll(ring, -1, axis=0))
        dist, near = cKDTree(all_points).query(mids, k=1)
        on = dist <= max(step * 0.75, 1e-6)
        seg_src = np.where(on, all_src[near], -1)
        loop_of = np.where(on, owner[near], -1)
        dominant = int(np.bincount(loop_of[loop_of >= 0], minlength=len(sources)).argmax())
        seg_src = np.where(loop_of == dominant, seg_src, -1)
        orig = sources[dominant][0]
        print(f"  ring points {len(ring)}; dominant source loop index {dominant} (0 = outer) with {len(orig.vertices)} original vertices; "
              f"ring segments on-original {int(on.sum())}, introduced {int((~on).sum())}, other-loop {int(((loop_of >= 0) & (loop_of != dominant)).sum())}")
        # groups as rebuild_loop forms them
        groups = []
        for idx in range(len(ring)):
            s = int(seg_src[idx])
            if groups and groups[-1][0] == s and s >= 0:
                groups[-1][1].append(idx)
            else:
                groups.append((s, [idx]))
        merged = False
        if len(groups) > 1 and groups[0][0] == groups[-1][0] and groups[0][0] >= 0:
            merged = True
        print(f"  groups {len(groups)}; first group src {groups[0][0]} ({len(groups[0][1])} segs), last group src {groups[-1][0]} "
              f"({len(groups[-1][1])} segs); start/end merge would apply: {merged}")
        # which original segments appear in more than one group (= ring visits the same original segment twice)
        seen = {}
        for gi, (s, idxs) in enumerate(groups):
            if s >= 0:
                seen.setdefault(s, []).append((gi, len(idxs)))
        multi = {s: g for s, g in seen.items() if len(g) > 1}
        for s, g in sorted(multi.items()):
            b = float(orig.bulges[s]) if s < len(orig.bulges) else float("nan")
            v0 = orig.xy[s]; v1 = orig.xy[(s + 1) % len(orig.xy)]
            print(f"  original segment {s} (bulge {b:+.6f}, from ({v0[0]:.1f},{v0[1]:.1f}) to ({v1[0]:.1f},{v1[1]:.1f}), chord {np.hypot(*(v1 - v0)):.1f}) "
                  f"appears in groups {g} (group index, segment count)")
        # original segment lengths: is there one huge arc covering most of the outline?
        lens = []
        for i in range(len(orig.xy)):
            v0 = orig.xy[i]; v1 = orig.xy[(i + 1) % len(orig.xy)]
            b = float(orig.bulges[i])
            chord = float(np.hypot(*(v1 - v0)))
            if abs(b) > 1e-12 and chord > 1e-9:
                _c, r, _st, th = sheets.bulge_to_arc(v0, v1, b)
                lens.append((abs(th) * r, i, r, math.degrees(th)))
            else:
                lens.append((chord, i, 0.0, 0.0))
        lens.sort(reverse=True)
        print(f"  longest original segments of the dominant loop (length, index, radius, sweep deg): {[(round(a, 1), i, round(r, 1), round(t, 1)) for a, i, r, t in lens[:6]]}")


if __name__ == "__main__":
    main(sys.argv[1:])
