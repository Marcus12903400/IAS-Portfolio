"""Probe 2: how good is largest-area-first bottom-left?  For each seam set:
area lower bound vs production sheet count, and the same nester fed other
orderings (through the piece's own `area_mm2`, which is the only thing `nest`
sorts on) and with 180 tried first (pieces pre-rotated by 180 so the nester's
tie-break lands on it).  Also measures the free space left UNDER each placed
piece.  Usage: probe2_quality.py [family] [first] [last] [random_orders]
"""

import dataclasses
import math
import sys

import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union

from probe_common import (MARGIN_X, MARGIN_Y, OPTIONS, OUT, STEP, USABLE_L, USABLE_W, Timer, area_lower_bound,
                          build_pieces, dump_json, load_run, nesting, placeable, placed_polygon, sheets)

family = sys.argv[1] if len(sys.argv) > 1 else "fitted"
first = int(sys.argv[2]) if len(sys.argv) > 2 else 0
last = int(sys.argv[3]) if len(sys.argv) > 3 else 39
random_orders = int(sys.argv[4]) if len(sys.argv) > 4 else 12

run = load_run()
rotation = run["rotation"]


def ranked(pieces, key):
    """Clones whose area_mm2 encodes the wanted order (largest 'area' first)."""

    ordered = sorted(pieces, key=key)
    return [dataclasses.replace(p, area_mm2=1e12 - i) for i, p in enumerate(ordered)]


def flipped(pieces):
    """The same pieces turned 180 degrees in the placed frame, so the nester's
    angle 0 is the original's angle 180 and wins ties."""

    out = []
    for p in pieces:
        outer = sheets.Loop(np.column_stack([-p.outer.xy, p.outer.bulges]))
        holes = [sheets.Loop(np.column_stack([-h.xy, h.bulges])) for h in p.holes]
        out.append(dataclasses.replace(p, outer=outer, holes=holes))
    return out


def count(pieces):
    sheet_list, summary, _w = nesting.nest(pieces, rotation, OPTIONS)
    return summary["sheet_count"], summary["utilisation"], sheet_list


def free_below(sheet_list, by_id):
    """For each placement: free area in the column directly beneath it."""

    rows = []
    for sheet in sheet_list:
        polys = {pl.piece_id: placed_polygon(by_id[pl.piece_id], pl, rotation) for pl in sheet.placements}
        for pl in sheet.placements:
            poly = polys[pl.piece_id]
            minx, miny, maxx, maxy = poly.bounds
            if miny - MARGIN_Y < 1e-6:
                continue
            shadow = box(minx, MARGIN_Y, maxx, miny)
            others = unary_union([q for k, q in polys.items() if k != pl.piece_id]) if len(polys) > 1 else None
            free = shadow if others is None else shadow.difference(others)
            # highest point of anything under the piece within its x-span
            under = [q.bounds[3] for k, q in polys.items() if k != pl.piece_id and q.intersects(shadow)]
            top_under = max(under) if under else MARGIN_Y
            rows.append({"sheet": sheet.index + 1, "piece": pl.piece_id, "rot": pl.rotation_deg,
                         "bottom_mm": round(miny - MARGIN_Y, 1), "gap_below_mm": round(miny - top_under, 1),
                         "free_below_mm2": round(float(free.area)), "free_below_pct_of_sheet": round(100 * free.area / (USABLE_W * USABLE_L), 1)})
    return rows


results = []
for seed in range(first, last + 1):
    seams, pieces, _w = build_pieces(seed, family)
    fit = placeable(pieces, rotation)
    lb, total_area = area_lower_bound(fit, rotation)
    sizes = {p.piece_id: sheets.oriented_extent(p, rotation, STEP) for p in fit}
    with Timer() as t:
        prod_count, prod_util, prod_sheets = count(fit)
    by_id = {p.piece_id: p for p in fit}
    alternatives = {
        "length_then_width": ranked(fit, lambda p: (-sizes[p.piece_id][1], -sizes[p.piece_id][0])),
        "width_then_length": ranked(fit, lambda p: (-sizes[p.piece_id][0], -sizes[p.piece_id][1])),
        "bbox_area": ranked(fit, lambda p: -(sizes[p.piece_id][0] * sizes[p.piece_id][1])),
        "perimeter": ranked(fit, lambda p: -(sizes[p.piece_id][0] + sizes[p.piece_id][1])),
        "max_dim": ranked(fit, lambda p: (-max(sizes[p.piece_id]), -min(sizes[p.piece_id]))),
        "area_180_first": flipped(fit),
        "length_180_first": flipped(ranked(fit, lambda p: (-sizes[p.piece_id][1], -sizes[p.piece_id][0]))),
    }
    rng = np.random.default_rng(1000 + seed)
    for k in range(random_orders):
        perm = rng.permutation(len(fit))
        alternatives[f"random_{k:02d}"] = [dataclasses.replace(fit[i], area_mm2=1e12 - r) for r, i in enumerate(perm)]
    counts = {}
    utils = {}
    for name, alt in alternatives.items():
        c, u, _s = count(alt)
        counts[name], utils[name] = c, u
    best_name = min(counts, key=lambda n: (counts[n], -max(utils[n][-1:] or [0])))
    best = counts[best_name]
    gaps = free_below(prod_sheets, by_id)
    worst_gap = max(gaps, key=lambda g: g["free_below_mm2"]) if gaps else None
    last_util = prod_util[-1] if prod_util else None
    results.append({"seed": seed, "family": family, "placeable": len(fit), "total_area_mm2": round(total_area),
                    "lower_bound": lb, "production": prod_count, "production_util": prod_util,
                    "best_alternative": best, "best_alternative_name": best_name, "alternatives": counts,
                    "extra_vs_lb": prod_count - lb, "extra_vs_best": prod_count - best,
                    "last_sheet_util": last_util, "worst_free_below": worst_gap, "gaps": gaps,
                    "nest_s": round(t.elapsed, 2)})
    beaten = sorted({n for n, c in counts.items() if c < prod_count})
    print(f"seed {seed:2d} placeable={len(fit):2d} LB={lb} prod={prod_count} util={prod_util} best={best} ({best_name}) "
          f"alts={{{', '.join(f'{n}:{c}' for n, c in counts.items() if not n.startswith('random_'))}}} "
          f"random_min={min(c for n, c in counts.items() if n.startswith('random_')) if random_orders else '-'} "
          f"beaten_by={len(beaten)} worst_free_below={worst_gap['free_below_mm2'] if worst_gap else 0} mm2 "
          f"({worst_gap['piece'] if worst_gap else '-'} sheet {worst_gap['sheet'] if worst_gap else '-'})", flush=True)

dump_json(OUT / f"probe2_{family}.json", results)
n = len(results)
print(f"\n{family}: {n} sets")
print(f"production == lower bound: {sum(1 for r in results if r['extra_vs_lb'] == 0)}/{n}; "
      f"+1: {sum(1 for r in results if r['extra_vs_lb'] == 1)}; +2: {sum(1 for r in results if r['extra_vs_lb'] == 2)}; "
      f">=+3: {sum(1 for r in results if r['extra_vs_lb'] >= 3)}")
print(f"an alternative ordering used FEWER sheets than production in {sum(1 for r in results if r['extra_vs_best'] > 0)}/{n} sets "
      f"(by 2 in {sum(1 for r in results if r['extra_vs_best'] >= 2)})")
for name in ("length_then_width", "width_then_length", "bbox_area", "perimeter", "max_dim", "area_180_first", "length_180_first"):
    better = sum(1 for r in results if r["alternatives"][name] < r["production"])
    worse = sum(1 for r in results if r["alternatives"][name] > r["production"])
    print(f"  {name:20s}: better in {better:2d}, worse in {worse:2d}, mean sheets {np.mean([r['alternatives'][name] for r in results]):.2f}")
print(f"  production mean sheets {np.mean([r['production'] for r in results]):.2f}; best-of-all mean {np.mean([r['best_alternative'] for r in results]):.2f}; LB mean {np.mean([r['lower_bound'] for r in results]):.2f}")
print(f"last sheet utilisation < 10%: {sum(1 for r in results if r['last_sheet_util'] is not None and r['last_sheet_util'] < 0.10)}/{n}")
