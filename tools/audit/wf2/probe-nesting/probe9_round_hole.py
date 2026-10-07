"""Probe 9: does the 207 mm ROUND cut-out of panel 1 survive sheets.split_panel?

sheets.py: `_rebuild_ring` -> `rebuild_loop` collapses each original arc run
into ONE bulged vertex, so a circle written as two semicircles comes back as a
2-vertex loop, and split_panel keeps a hole only `if len(rebuilt.vertices) >= 3`.
"""

import numpy as np
from shapely.geometry import Point, Polygon

from probe_common import OPTIONS, OUT, STEP, build_pieces, dump_json, load_run, sheets

run = load_run()
outer, holes, panel_poly = run["panels"][1]
print("panel 1 holes as read from final_auto.dxf:")
targets = {}
for i, h in enumerate(holes):
    pts, _s = sheets.sample_loop(h, STEP)
    poly = Polygon(pts)
    minx, miny, maxx, maxy = poly.bounds
    kind = "ROUND" if len(h.vertices) == 2 else "rect/other"
    print(f"  hole {i}: {len(h.vertices)} vertices, bulges {np.round(h.bulges, 3).tolist()[:4]}, "
          f"bbox {maxx - minx:.1f} x {maxy - miny:.1f} mm at ({poly.centroid.x:.1f}, {poly.centroid.y:.1f}) {kind}")
    targets[i] = (poly.centroid, poly.area, kind)

# direct: what does _rebuild_ring make of the round hole's own ring?
round_index = next(i for i, h in enumerate(holes) if len(h.vertices) == 2)
ring, _s = sheets.sample_loop(holes[round_index], STEP)
sources = [(loop, *sheets.sample_loop(loop, STEP)) for loop in [outer, *holes]]
rebuilt, err = sheets._rebuild_ring(ring, sources, float(OPTIONS["arc_rebuild_tolerance_mm"]), STEP)
print(f"\n_rebuild_ring on the round hole ring ({len(ring)} points): {len(rebuilt.vertices)} vertices, "
      f"bulges {np.round(rebuilt.bulges, 4).tolist()}, arc error {err:.4f} mm  -> split_panel keeps holes only with >= 3 vertices")

# unsplit panel keeps all holes
pieces0, w0 = sheets.split_panel(1, outer, holes, [], OPTIONS)
print(f"split_panel with NO seams: {len(pieces0)} piece(s), holes={[len(p.holes) for p in pieces0]}")

# a single seam far from every hole: which holes survive?
far = sheets.Seam("far", -1500.0, 900.0, 2500.0, 900.0, panel_id=1, snap=False)
pieces1, w1 = sheets.split_panel(1, outer, holes, [far], OPTIONS)
print(f"split_panel with ONE seam (y=900, misses every cut-out): pieces={len(pieces1)}, holes per piece={[len(p.holes) for p in pieces1]}, "
      f"total holes {sum(len(p.holes) for p in pieces1)} of {len(holes)}; warnings={w1}")
for p in pieces1:
    poly = Polygon(sheets.sample_loop(p.outer, STEP)[0])
    for i, (c, area, kind) in targets.items():
        if poly.contains(c):
            kept = any(Polygon(sheets.sample_loop(h, STEP)[0]).contains(c) for h in p.holes)
            print(f"   hole {i} ({kind}) lies in {p.piece_id}: {'kept' if kept else 'LOST'}")

# every probe set: is the round hole present in the piece that contains its centre?
stats = {"round_lost": 0, "round_kept": 0, "round_cut_by_seam": 0, "rect_lost": 0, "rect_kept": 0, "rect_cut": 0}
examples = []
for family in ("random", "fitted"):
    for seed in range(40):
        seams, pieces, _w = build_pieces(seed, family)
        p1 = [p for p in pieces if p.panel_id == 1]
        polys = {p.piece_id: Polygon(sheets.sample_loop(p.outer, STEP)[0]) for p in p1}
        for i, (c, area, kind) in targets.items():
            owner = next((pid for pid, poly in polys.items() if poly.contains(c)), None)
            key = "round" if kind == "ROUND" else "rect"
            if owner is None:
                stats[key + ("_cut_by_seam" if key == "round" else "_cut")] += 1   # a seam runs through the cut-out
                continue
            piece = next(p for p in p1 if p.piece_id == owner)
            kept = any(Polygon(sheets.sample_loop(h, STEP)[0]).contains(c) for h in piece.holes)
            # also: does the hole still fit inside the piece (not cut by the kerf)?  if the kerf crosses
            # the hole the hole merges with the outline and 'owner' would not contain c -> handled above.
            stats[key + ("_kept" if kept else "_lost")] += 1
            if not kept and len(examples) < 8:
                examples.append((family, seed, owner, i, kind))
print("\nover 80 seam sets:", stats)
print("examples of a lost hole (family, seed, piece, hole index):", examples)
dump_json(OUT / "probe9_round_hole.json", {"stats": stats, "examples": examples,
                                            "rebuilt_vertices": len(rebuilt.vertices)})
