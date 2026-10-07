"""Probe 3: the working-tree nester (sample-point prefilter) against the nester
committed at HEAD (ecb7b30, `git show HEAD:engine/autodeck2/nesting.py`, saved
as nesting_committed_raw.py and import-patched into nesting_committed.py).

Compares `layout()` tuples with == for every seam set, as test_nesting_speed.py
does with its pinned table.  Usage: probe3_bitidentity.py [family] [first] [last]
"""

import importlib.util
import sys

from probe_common import HERE, OPTIONS, OUT, Timer, build_pieces, dump_json, layout, load_run, nesting

spec = importlib.util.spec_from_file_location("nesting_committed", HERE / "nesting_committed.py")
committed = importlib.util.module_from_spec(spec)
sys.modules["nesting_committed"] = committed       # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(committed)
assert not hasattr(committed, "_probes"), "the committed copy should be the unfiltered nester"

family = sys.argv[1] if len(sys.argv) > 1 else "random"
first = int(sys.argv[2]) if len(sys.argv) > 2 else 0
last = int(sys.argv[3]) if len(sys.argv) > 3 else 39

run = load_run()
rotation = run["rotation"]
rows = []
differences = 0
for seed in range(first, last + 1):
    seams, pieces, _w = build_pieces(seed, family)
    with Timer() as t_new:
        new_sheets, new_summary, new_warnings = nesting.nest(pieces, rotation, OPTIONS)
    with Timer() as t_old:
        old_sheets, old_summary, old_warnings = committed.nest(pieces, rotation, OPTIONS)
    same_layout = layout(new_sheets) == layout(old_sheets)
    same_summary = {k: new_summary[k] for k in ("sheet_count", "piece_count", "unplaced_piece_ids", "utilisation")} == \
                   {k: old_summary[k] for k in ("sheet_count", "piece_count", "unplaced_piece_ids", "utilisation")}
    same_warnings = new_warnings == old_warnings
    same_sizes = [(p.width_mm, p.length_mm) for s in new_sheets for p in s.placements] == \
                 [(p.width_mm, p.length_mm) for s in old_sheets for p in s.placements]
    ok = same_layout and same_summary and same_warnings and same_sizes
    differences += 0 if ok else 1
    row = {"seed": seed, "family": family, "pieces": len(pieces), "identical": ok,
           "same_layout": same_layout, "same_summary": same_summary, "same_warnings": same_warnings,
           "same_sizes": same_sizes, "new_s": round(t_new.elapsed, 3), "old_s": round(t_old.elapsed, 3),
           "sheets": new_summary["sheet_count"], "placed": new_summary["piece_count"]}
    if not same_layout:
        new_l, old_l = layout(new_sheets), layout(old_sheets)
        row["first_difference"] = next(((i, a, b) for i, (a, b) in enumerate(zip(new_l, old_l)) if a != b),
                                       ("length", len(new_l), len(old_l)))
    rows.append(row)
    print(f"seed {seed:2d} pieces={len(pieces):2d} identical={ok} layout={same_layout} summary={same_summary} "
          f"warnings={same_warnings} sizes={same_sizes} new={t_new.elapsed:.2f}s old={t_old.elapsed:.2f}s "
          f"speedup={t_old.elapsed / max(t_new.elapsed, 1e-9):.1f}x", flush=True)
    if not same_layout:
        print("    first difference:", row["first_difference"])

dump_json(OUT / f"probe3_{family}.json", rows)
print(f"\n{differences} of {len(rows)} seam sets differ between the working-tree and HEAD nesters")
print(f"total time new={sum(r['new_s'] for r in rows):.1f}s old={sum(r['old_s'] for r in rows):.1f}s")
