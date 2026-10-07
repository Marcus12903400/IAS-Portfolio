"""Probe 4: synthetic edge cases through nesting.nest with production options.

Axis (1, 0) unless stated, so sheet_transform maps placed +X -> sheet +Y:
rect_loop(width_along_x, height_y) is `height_y` ACROSS the boat (sheet X) by
`width_along_x` ALONG it (sheet Y).
"""

import math
import traceback

import numpy as np
from shapely.geometry import Polygon

from probe_common import (ENVELOPE, MARGIN_X, MARGIN_Y, OPTIONS, OUT, SPACING, STEP, USABLE_L, USABLE_W,
                          dump_json, nesting, placed_polygon, poly_loop, rect_loop, sheets, verify)

AX = sheets.sheet_transform(np.array([1.0, 0.0]))
REAL_AXIS = np.array([0.9979376404082442, 0.06419085492829811])
RX = sheets.sheet_transform(REAL_AXIS)
results = {}


def piece(pid, loop, holes=(), panel=1):
    poly = sheets.loop_polygon(loop, list(holes), STEP)
    return sheets.Piece(pid, panel, loop, list(holes), float(poly.area))


def run_case(name, pieces, rotation=AX, options=OPTIONS, single_nest_check=False):
    print(f"\n=== {name} ===")
    try:
        sheet_list, summary, warnings = nesting.nest(pieces, rotation, options)
    except Exception as exc:  # noqa: BLE001
        print("  EXCEPTION:", repr(exc))
        traceback.print_exc()
        results[name] = {"exception": repr(exc)}
        return None
    report = verify(pieces, sheet_list, summary, rotation, options, single_nest_check=single_nest_check)
    placements = [(p.sheet_index, p.piece_id, p.rotation_deg, round(float(p.offset[0]), 3),
                   round(float(p.offset[1]), 3), round(p.width_mm, 4), round(p.length_mm, 4))
                  for s in sheet_list for p in s.placements]
    print(f"  sheets={summary['sheet_count']} placed={summary['piece_count']} unplaced={summary['unplaced_piece_ids']}")
    print(f"  utilisation={summary['utilisation']}")
    for pl in placements[:12]:
        print("   ", pl)
    if len(placements) > 12:
        print(f"    ... {len(placements)} placements")
    for w in warnings:
        print("  warning:", w)
    print(f"  violations={report['violations']} empty_sheets={report['empty_sheets']} "
          f"worst_outside={report['worst_outside_mm']:+.3e} min_clr={report['min_clearance_mm']:.3f}")
    results[name] = {"summary": {k: summary[k] for k in ("sheet_count", "piece_count", "unplaced_piece_ids", "utilisation")},
                     "placements": placements, "warnings": warnings,
                     "violations": report["violations"], "empty_sheets": report["empty_sheets"]}
    return sheet_list, summary, warnings, report


# (a) rectangles at and around the usable envelope, axis-aligned
for w_across, l_along in [(990.6, 2006.6), (990.59, 2006.6), (990.61, 2006.6), (990.6, 2006.59),
                          (990.6, 2006.61), (990.6 + 1e-9, 2006.6), (990.6 + 5e-10, 2006.6)]:
    run_case(f"rect {w_across} across x {l_along} along (axis 1,0)",
             [piece("R", rect_loop(l_along, w_across))])

# (a2) the same exact-size rectangle, but drawn in the placed frame of the REAL
# 3.68 degree axis so that after sheet_transform it is 990.6 x 2006.6
corners_sheet = np.array([[0, 0], [990.6, 0], [990.6, 2006.6], [0, 2006.6]], dtype=float)
corners_placed = corners_sheet @ RX            # inverse of (xy @ RX.T) is xy @ RX
loop = poly_loop(corners_placed)
pts, _ = sheets.sample_loop(loop, STEP)
xy = pts @ RX.T
print(f"\nreal-axis exact rect: measured extent in sheet frame = "
      f"{xy[:, 0].max() - xy[:, 0].min():.15f} x {xy[:, 1].max() - xy[:, 1].min():.15f}")
run_case("rect exactly 990.6 x 2006.6 on the real 3.68 deg axis", [piece("RX", loop)], rotation=RX)
for shrink in (1e-9, 1e-6, 1e-3):
    corners_sheet = np.array([[0, 0], [990.6 - shrink, 0], [990.6 - shrink, 2006.6 - shrink], [0, 2006.6 - shrink]])
    run_case(f"rect 990.6-{shrink:g} square on the real axis", [piece("RXs", poly_loop(corners_sheet @ RX))], rotation=RX)

# (b) thin strips
run_case("strip 5 across x 1900 along", [piece("S", rect_loop(1900.0, 5.0))])
run_case("strip 1900 across x 5 along (needs 90 deg, must be refused)", [piece("S90", rect_loop(5.0, 1900.0))])
run_case("two 5 x 1900 strips", [piece("S1", rect_loop(1900.0, 5.0)), piece("S2", rect_loop(1900.0, 5.0))])

# (c) L-shapes: two identical Ls, 600 across x 1500 along with a 300 x 900 notch.
# Two of them only share a sheet if one goes in at 180 and tucks into the notch.
def l_shape(pid):
    # in placed frame: x along the boat, y across
    pts = [(0, 0), (1500, 0), (1500, 300), (900, 300), (900, 600), (0, 600)]
    return piece(pid, poly_loop(pts))
run_case("two L-shapes (tuck needs 180)", [l_shape("L1"), l_shape("L2")])
run_case("three L-shapes", [l_shape("L1"), l_shape("L2"), l_shape("L3")])

# (d) ring: 900 across x 1800 along with a 700 x 1500 hole, plus a 500 x 1200 rect
ring = piece("RING", rect_loop(1800.0, 900.0), [rect_loop(1500.0, 700.0, 150.0, 100.0)])
inner = piece("INNER", rect_loop(1200.0, 500.0))
run_case("ring + rect that fits inside its hole", [ring, inner])
# ring whose hole fits two small squares with clearance
small = [piece(f"Q{i}", rect_loop(300.0, 300.0)) for i in range(2)]
run_case("ring + two 300 squares", [ring, *small])

# (e) 60 squares of 150 x 150
squares = [piece(f"sq{i:02d}", rect_loop(150.0, 150.0)) for i in range(60)]
res = run_case("60 squares 150 x 150", squares)
if res:
    per_sheet = [len(s.placements) for s in res[0]]
    print(f"  per sheet: {per_sheet}; analytic: 5 columns x 11 rows = 55 per sheet -> 2 sheets")

# (f) empty list
run_case("empty piece list", [])

# (g) invalid outlines: a bowtie (self-intersecting), and a hole touching the outer
bow = piece("BOW", poly_loop([(0, 0), (800, 600), (800, 0), (0, 600)]))
print(f"\nbowtie raw area {Polygon(sheets.sample_loop(bow.outer, STEP)[0]).area:.1f}  "
      f"buffer(0) area {sheets.loop_polygon(bow.outer, [], STEP).area:.1f} "
      f"type {sheets.loop_polygon(bow.outer, [], STEP).geom_type}")
run_case("bowtie outline + a 400 x 400 square", [bow, piece("SQ", rect_loop(400.0, 400.0))])
touching = piece("TOUCH", rect_loop(1000.0, 600.0), [rect_loop(400.0, 300.0, 0.0, 150.0)])
run_case("hole touching the outer edge + square", [touching, piece("SQ2", rect_loop(400.0, 400.0))])

# (h) rotation disabled
run_case("two L-shapes with allow_180_rotation False", [l_shape("L1"), l_shape("L2")],
         options={**OPTIONS, "allow_180_rotation": False})

# (i) piece whose 180 variant is the only one that fits? impossible (same bbox) -- skip.
# (j) a piece exactly usable width next to the edge plus one more: forced to a new sheet
run_case("full-width piece then a small one", [piece("FULL", rect_loop(1000.0, 990.6)), piece("SM", rect_loop(300.0, 300.0))])

# (k) a round part (2-vertex bulged polyline) like P5
circle = sheets.Loop(np.array([[0.0, 0.0, 1.0], [187.0, 0.0, 1.0]]))
run_case("187 mm circle x 3", [piece(f"C{i}", circle) for i in range(3)])

# (l) duplicate ids
run_case("two pieces sharing an id", [piece("DUP", rect_loop(500.0, 400.0)), piece("DUP", rect_loop(500.0, 400.0))])

dump_json(OUT / "probe4_edgecases.json", results)
print("\nwrote", OUT / "probe4_edgecases.json")
