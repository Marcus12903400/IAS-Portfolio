"""Probe 9c: are split pieces still clean LINE/ARC loops?  Vertex counts of the
source loops vs the pieces of a typical split, and how many vertices came from
the 'keep the sampled polyline' fallback in rebuild_loop."""

import numpy as np

from probe_common import OPTIONS, STEP, build_pieces, load_run, sheets

run = load_run()
for pid, (outer, holes, poly) in run["panels"].items():
    print(f"panel {pid}: outer {len(outer.vertices)} vertices ({int(np.sum(np.abs(outer.bulges) > 1e-9))} arcs); "
          f"holes {[len(h.vertices) for h in holes]}")

for family, seed in (("fitted", 7), ("fitted", 14), ("random", 0)):
    seams, pieces, _w = build_pieces(seed, family)
    print(f"\n{family} seed {seed}: {len(seams)} seams -> {len(pieces)} pieces")
    for p in pieces:
        n = len(p.outer.vertices)
        arcs = int(np.sum(np.abs(p.outer.bulges) > 1e-9))
        # consecutive straight vertices ~1 mm apart = sampled-polyline fallback
        xy = p.outer.xy
        d = np.hypot(*(np.roll(xy, -1, axis=0) - xy).T)
        dense = int(np.sum(d < 1.5))
        print(f"  {p.piece_id:6s}: {n:5d} vertices, {arcs:3d} arcs, {dense:5d} edges shorter than 1.5 mm "
              f"({100 * dense / n:4.0f}% of vertices), holes {[len(h.vertices) for h in p.holes]}, arc_err {p.max_arc_error_mm:.4f}")
