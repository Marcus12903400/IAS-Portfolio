"""Probe 10: digests.  (a) arc flattening over all cached pieces; (b) probe-2
quality JSONs: extra sheets, winning orderings, free space under pieces."""

import json
from collections import Counter

import numpy as np

from probe_common import OUT, build_pieces

# (a)
dense_pieces = total_pieces = 0
total_vertices = 0
worst = (0, None)
orig_arcs_lost = 0
by_panel = Counter()
dense_by_panel = Counter()
for family in ("random", "fitted"):
    for seed in range(40):
        _s, pieces, _w = build_pieces(seed, family)
        for p in pieces:
            if not p.from_seam:
                continue
            n = len(p.outer.vertices)
            xy = p.outer.xy
            d = np.hypot(*(np.roll(xy, -1, axis=0) - xy).T)
            dense = int(np.sum(d < 1.5))
            total_pieces += 1
            total_vertices += n
            by_panel[p.panel_id] += 1
            if dense > 0.5 * n and n > 20:
                dense_pieces += 1
                dense_by_panel[p.panel_id] += 1
            if n > worst[0]:
                worst = (n, (family, seed, p.piece_id))
print(f"(a) split pieces: {total_pieces}; with >50% of edges < 1.5 mm (flattened arcs): {dense_pieces} "
      f"({100 * dense_pieces / total_pieces:.0f}%); mean vertices per split piece {total_vertices / total_pieces:.0f}; "
      f"worst {worst}; by panel: pieces {dict(by_panel)}, flattened {dict(dense_by_panel)}")

# (b)
for family in ("random", "fitted"):
    rows = json.loads((OUT / f"probe2_{family}.json").read_text(encoding="utf-8"))
    print(f"\n(b) {family}: {len(rows)} sets")
    print("   extra sheets vs best alternative:", dict(sorted(Counter(r["extra_vs_best"] for r in rows).items())))
    print("   extra sheets vs area lower bound:", dict(sorted(Counter(r["extra_vs_lb"] for r in rows).items())))
    winners = Counter()
    for r in rows:
        if r["extra_vs_best"] > 0:
            for name, c in r["alternatives"].items():
                if c < r["production"]:
                    winners["random_*" if name.startswith("random_") else name] += 1
    print("   orderings that beat production (count of sets):", dict(winners.most_common()))
    big = sorted((g for r in rows for g in r["gaps"]), key=lambda g: -g["free_below_mm2"])[:6]
    print("   largest free space directly under a placed piece:")
    for g in big:
        print(f"     {g}")
    gaps = [g for r in rows for g in r["gaps"]]
    print(f"   placements with >= 100 mm of clear air below them: {sum(1 for g in gaps if g['gap_below_mm'] >= 100)} of "
          f"{sum(len([pl for pl in r['gaps']]) for r in rows)} non-floor placements; "
          f">= 300 mm: {sum(1 for g in gaps if g['gap_below_mm'] >= 300)}")
    util_last = [r["production_util"][-1] for r in rows]
    print(f"   mean utilisation of the last sheet: {np.mean(util_last):.3f}; of all sheets: "
          f"{np.mean([u for r in rows for u in r['production_util']]):.3f}")
