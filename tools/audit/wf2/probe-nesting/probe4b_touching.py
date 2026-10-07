"""Probe 4b: WHY the 60-square job packs 45 per sheet instead of 55.

Hypothesis: `_find_spot` rejects a candidate that merely TOUCHES the
spacing-buffered occupancy (shapely `intersects` counts touching), so a piece
can never sit at exactly `part_spacing_mm` from its neighbour on the 5 mm grid
-- except when the 180 degree variant's coordinates carry ~1e-14 of rounding
from sin(pi), which sometimes lifts it a hair clear.  Also: the bowtie lobe
loss, and a correctly sized L-pair that only shares a sheet through a 180 tuck.
"""

import math

import numpy as np
import shapely
from shapely import affinity
from shapely.prepared import prep

from probe_common import OPTIONS, OUT, STEP, dump_json, nesting, poly_loop, rect_loop, sheets, verify

AX = sheets.sheet_transform(np.array([1.0, 0.0]))
out = {}


def variant(piece, angle):
    shell, holes = nesting._sampled_rings(piece, STEP)
    polygon = nesting._piece_polygon(shell, holes, nesting._rotation(angle) @ AX)
    minx, miny, maxx, maxy = polygon.bounds
    moved = affinity.translate(polygon, -minx, -miny)
    v = nesting._Variant(moved, maxx - minx, maxy - miny, moved.bounds)
    v.probes = nesting._probes(moved, nesting._PROBE_LEVELS)
    return v


sq = sheets.Piece("sq", 1, rect_loop(150.0, 150.0), [], 22500.0)
v0, v180 = variant(sq, 0), variant(sq, 180)
print("variant 0 bounds  :", v0.bounds)
print("variant 180 bounds:", v180.bounds)
print("variant 180 coords (first 5):", np.asarray(v180.polygon.exterior.coords)[:5])

# occupancy after one square at the origin, exactly as _commit builds it
placed = affinity.translate(v0.polygon, 0.0, 0.0)
occupied = placed.buffer(20.0, join_style=1)
blocked = prep(occupied)
print("occupied bounds:", occupied.bounds)

for angle, v in ((0, v0), (180, v180)):
    for x, y in ((0.0, 170.0), (170.0, 0.0), (0.0, 175.0), (175.0, 0.0), (735.0, 170.0)):
        cand = affinity.translate(v.polygon, x, y)
        hit = blocked.intersects(cand)
        dist = placed.distance(cand)
        coords = np.asarray(cand.exterior.coords)
        print(f"angle {angle:3d} at ({x:6.1f},{y:6.1f}): intersects={hit!s:5} distance={dist:.17g} "
              f"min_y={coords[:, 1].min():.17g} min_x={coords[:, 0].min():.17g}")
        out[f"square angle{angle} at {x},{y}"] = {"intersects": bool(hit), "distance": dist,
                                                 "min_y": float(coords[:, 1].min()), "min_x": float(coords[:, 0].min())}

# the same with `touches`-tolerant tests, for the record
cand = affinity.translate(v0.polygon, 0.0, 170.0)
print("angle 0 at (0,170): touches =", occupied.touches(cand), " overlaps =", occupied.overlaps(cand),
      " intersection area =", occupied.intersection(cand).area)

# How many 150 squares does a row take on the 990.6 envelope at 20 vs 25 mm pitch?
for pitch in (20.0, 25.0):
    cols = math.floor((990.6 + pitch) / (150 + pitch))
    rows = math.floor((2006.6 + pitch) / (150 + pitch))
    print(f"pitch {pitch}: {cols} columns x {rows} rows = {cols * rows} per sheet")

# ------------------------------------------------------------ the bowtie
bow = sheets.Piece("BOW", 1, poly_loop([(0, 0), (800, 600), (800, 0), (0, 600)]), [], 0.0)
shell, holes = nesting._sampled_rings(bow, STEP)
raw = shapely.Polygon(shell @ AX.T)
fixed = nesting._piece_polygon(shell, holes, AX)
print("\nbowtie: raw valid =", raw.is_valid, " raw bounds =", raw.bounds, " raw |area| of lobes = 2 x 120000")
print("bowtie: buffer(0) type =", fixed.geom_type, " area =", fixed.area, " bounds =", fixed.bounds)
out["bowtie"] = {"raw_bounds": raw.bounds, "fixed_type": fixed.geom_type, "fixed_area": fixed.area, "fixed_bounds": fixed.bounds}

# ------------------------------------------------------------ L-pair sized to tuck
# 1200 along x 600 across, thin leg 250 wide over the last 600 along (notch 350 x 600).
# Two of them need 2*1200 - 600 + 20 = 1820 along and 600 across when interlocked
# (one at 180), and 2420 along -- two sheets -- when not.
def L(pid):
    return sheets.Piece(pid, 1, poly_loop([(0, 0), (1200, 0), (1200, 250), (600, 250), (600, 600), (0, 600)]), [],
                        1200 * 600 - 600 * 350)


for name, pieces in (("L pair", [L("L1"), L("L2")]), ("L triple", [L("L1"), L("L2"), L("L3")])):
    sheet_list, summary, warnings = nesting.nest(pieces, AX, OPTIONS)
    report = verify(pieces, sheet_list, summary, AX, OPTIONS, single_nest_check=False)
    pl = [(p.sheet_index, p.piece_id, p.rotation_deg, round(float(p.offset[0]), 1), round(float(p.offset[1]), 1))
          for s in sheet_list for p in s.placements]
    print(f"\n{name}: sheets={summary['sheet_count']} placements={pl} violations={report['violations']} "
          f"min_clr={report['min_clearance_mm']:.3f}")
    out[name] = {"sheets": summary["sheet_count"], "placements": pl}

# can the tuck be found by hand?  L1 at angle 0 (0,0); L2 at 180 somewhere in the grid
l1 = variant(L("L1"), 0)
l2_0, l2_180 = variant(L("L2"), 0), variant(L("L2"), 180)
occ = affinity.translate(l1.polygon, 0, 0).buffer(20.0, join_style=1)
best = None
for angle, v in ((0, l2_0), (180, l2_180)):
    for y in nesting._grid_steps(2006.6 - v.length, 5.0):
        for x in nesting._grid_steps(990.6 - v.width, 5.0):
            c = affinity.translate(v.polygon, x, y)
            if not occ.intersects(c):
                if best is None or (y, x) < (best[2], best[1]):
                    best = (angle, x, y)
                break
        if best is not None and best[2] <= y:
            break
print("hand search for L2 on L1's sheet (lowest-left free spot):", best)
if best is not None:
    c = affinity.translate((l2_0 if best[0] == 0 else l2_180).polygon, best[1], best[2])
    print("  clearance to L1:", affinity.translate(l1.polygon, 0, 0).distance(c))
# and what does the production _find_spot say?
spot = nesting._find_spot({0: l2_0, 180: l2_180}, occ, 990.6, 2006.6, 5.0, (0, 180))
print("production _find_spot:", spot)
out["L tuck"] = {"hand": best, "find_spot": spot}

dump_json(OUT / "probe4b_touching.json", out)
