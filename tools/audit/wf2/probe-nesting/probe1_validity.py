"""Probe 1: nest 40 random seam sets of the real AXIS deck and verify every
placement with shapely.  Usage:  probe1_validity.py [family] [first] [last]

  family  random (default, the spec's 1-3 along / 1-4 across at uniform offsets)
          fitted (same, but offsets re-drawn until every strip fits the envelope)
"""

import sys
import time

import numpy as np

from probe_common import (OPTIONS, OUT, STEP, Timer, build_pieces, dump_json, layout, load_run,
                          nesting, sheets, verify)

family = sys.argv[1] if len(sys.argv) > 1 else "random"
first = int(sys.argv[2]) if len(sys.argv) > 2 else 0
last = int(sys.argv[3]) if len(sys.argv) > 3 else 39

run = load_run()
rotation = run["rotation"]
print(f"axis={run['axis']}  rotation=\n{rotation}")
print(f"panels: { {pid: (len(h), round(p.area)) for pid, (o, h, p) in run['panels'].items()} }")

rows = []
total_violations = 0
for seed in range(first, last + 1):
    with Timer() as split_t:
        seams, pieces, split_warnings = build_pieces(seed, family)
    with Timer() as nest_t:
        sheet_list, summary, nest_warnings = nesting.nest(pieces, rotation, OPTIONS)
    with Timer() as verify_t:
        report = verify(pieces, sheet_list, summary, rotation, OPTIONS)
    total_violations += len(report["violations"])
    row = {
        "seed": seed, "family": family,
        "seams": len(seams),
        "seam_counts": {"p1_along": sum(1 for s in seams if s.panel_id == 1 and s.mode == "along"),
                        "p1_across": sum(1 for s in seams if s.panel_id == 1 and s.mode == "across"),
                        "p2": sum(1 for s in seams if s.panel_id == 2),
                        "p3": sum(1 for s in seams if s.panel_id == 3)},
        "pieces": len(pieces),
        "placed": report["placed"],
        "unplaced": report["unplaced_ids"],
        "oversize": report["oversize_ids"],
        "sheets": summary["sheet_count"],
        "utilisation": summary["utilisation"],
        "rotations_180": sum(1 for s in sheet_list for p in s.placements if p.rotation_deg == 180),
        "worst_outside_mm": report["worst_outside_mm"],
        "worst_arc_overrun_mm": report["worst_arc_overrun_mm"],
        "min_clearance_mm": report["min_clearance_mm"],
        "max_overlap_mm2": report["max_overlap_mm2"],
        "empty_sheets": report["empty_sheets"],
        "invalid_polygons": report["invalid_polygons"],
        "violations": report["violations"],
        "split_warnings": split_warnings,
        "nest_warnings": nest_warnings,
        "split_s": round(split_t.elapsed, 2), "nest_s": round(nest_t.elapsed, 2),
        "verify_s": round(verify_t.elapsed, 2),
        "layout": layout(sheet_list),
        "piece_sizes": {p.piece_id: [round(v, 1) for v in sheets.oriented_extent(p, rotation, STEP)]
                        for p in pieces},
    }
    rows.append(row)
    print(f"seed {seed:2d} seams={len(seams)} ({row['seam_counts']}) pieces={len(pieces):2d} "
          f"placed={report['placed']:2d} unplaced={len(report['unplaced_ids'])} sheets={summary['sheet_count']} "
          f"util={summary['utilisation']} rot180={row['rotations_180']} "
          f"outside={report['worst_outside_mm']:+.2e} arcover={report['worst_arc_overrun_mm']:+.2e} "
          f"minclr={report['min_clearance_mm']:.3f} overlap={report['max_overlap_mm2']:.1f} "
          f"viol={len(report['violations'])} invalid={report['invalid_polygons']} "
          f"t(split/nest/verify)={split_t.elapsed:.1f}/{nest_t.elapsed:.2f}/{verify_t.elapsed:.1f}s",
          flush=True)
    if report["violations"]:
        for v in report["violations"]:
            print("    VIOLATION:", v)
    if split_warnings:
        print("    split warnings:", split_warnings)

dump_json(OUT / f"probe1_{family}.json", rows)
print(f"\nTOTAL violations over {len(rows)} sets: {total_violations}")
print(f"sets with every piece placed: {sum(1 for r in rows if not r['unplaced'])}/{len(rows)}")
print(f"worst outside envelope: {max(r['worst_outside_mm'] for r in rows):+.3e} mm")
print(f"worst arc overrun (0.1 mm sampling): {max(r['worst_arc_overrun_mm'] for r in rows):+.3e} mm")
print(f"min clearance: {min(r['min_clearance_mm'] for r in rows):.4f} mm")
print(f"max overlap: {max(r['max_overlap_mm2'] for r in rows):.4f} mm2")
print(f"empty sheets: {sum(r['empty_sheets'] for r in rows)}; invalid raw polygons: {sum(r['invalid_polygons'] for r in rows)}")
print(f"wrote {OUT / f'probe1_{family}.json'}")
