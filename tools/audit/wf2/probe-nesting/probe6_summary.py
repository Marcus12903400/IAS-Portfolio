"""Probe 6: tables out of the probe-1 JSONs: sheet counts, what sits on the
last sheet, rotation usage, timing."""

import json
from collections import Counter

import numpy as np

from probe_common import OUT

for family in ("random", "fitted"):
    rows = json.loads((OUT / f"probe1_{family}.json").read_text(encoding="utf-8"))
    print(f"\n===== {family}: {len(rows)} sets")
    print("sheet counts:", dict(sorted(Counter(r["sheets"] for r in rows).items())))
    print("pieces per set: min/median/max =", min(r["pieces"] for r in rows), np.median([r["pieces"] for r in rows]), max(r["pieces"] for r in rows))
    print("placements total:", sum(r["placed"] for r in rows), " rotated 180:", sum(r["rotations_180"] for r in rows))
    last = [r["utilisation"][-1] for r in rows if r["utilisation"]]
    print(f"last-sheet utilisation: <5%: {sum(1 for u in last if u < 0.05)}, <10%: {sum(1 for u in last if u < 0.10)}, "
          f"<25%: {sum(1 for u in last if u < 0.25)} of {len(last)}")
    print("nest time s: median %.2f max %.2f" % (np.median([r["nest_s"] for r in rows]), max(r["nest_s"] for r in rows)))
    print("min clearance over family: %.4f mm; worst outside envelope: %+.2e mm" % (
        min(r["min_clearance_mm"] for r in rows), max(r["worst_outside_mm"] for r in rows)))
    # what sits alone on a nearly empty last sheet
    print("nearly empty last sheets (<10%):")
    for r in rows:
        if r["utilisation"] and r["utilisation"][-1] < 0.10:
            last_index = r["sheets"] - 1
            on_last = [(pl[1], pl[2], r["piece_sizes"].get(pl[1])) for pl in r["layout"] if pl[0] == last_index]
            print(f"  seed {r['seed']:2d}: sheets={r['sheets']} util={r['utilisation'][-1]:.3f} pieces on last sheet: {on_last}")
