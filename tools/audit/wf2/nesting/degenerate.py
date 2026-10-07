"""Degenerate inputs: collinear (empty) piece, bow-tie piece, zero spacing, hole-nesting."""
import sys
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
import nesting_old
from autodeck2 import nesting, sheets
from autodeck2.config import load_config
from shapely.geometry import Polygon
import shapely

def loop(pts, bulges=None):
    pts = np.asarray(pts, float)
    b = np.zeros(len(pts)) if bulges is None else np.asarray(bulges, float)
    return sheets.Loop(np.column_stack([pts, b]))
def rect(w, l, x=0.0, y=0.0):
    return loop([[x, y], [x + w, y], [x + w, y + l], [x, y + l]])
base = load_config()
def options(**ov):
    return sheets.settings({**base, "sheets": {**(base.get("sheets") or {}), **ov}})
opts = options()
rot = sheets.sheet_transform(np.array([1.0, 0.0]))

print("== 1. collinear (zero-area) piece ==")
flat = sheets.Piece("Z1", 1, loop([[0, 0], [100, 0], [200, 0]]), [], 0.0)
shell, holes = nesting._sampled_rings(flat, 1.0)
poly = nesting._piece_polygon(shell, holes, np.eye(2))
print("  repaired polygon:", poly.geom_type, "empty=", poly.is_empty, "bounds=", poly.bounds)
for label, mod in (("OLD", nesting_old), ("NEW", nesting)):
    try:
        sl, summ, warn = mod.nest([sheets.Piece("A", 1, rect(300, 400), [], 120000.0), flat], rot, opts)
        print(f"  {label}: sheets={summ['sheet_count']} placed={summ['piece_count']} unplaced={summ['unplaced_piece_ids']} empty_sheets={[s.index for s in sl if not s.placements]} warn={warn}")
    except Exception as exc:
        print(f"  {label}: EXCEPTION {type(exc).__name__}: {exc}")

print("== 2. bow-tie (self-crossing) outline, big CW lobe + small CCW lobe ==")
# figure 8: big lobe 400x300 on the left, small lobe 60x40 on the right, crossing at (400, 150)
bow = loop([[0, 0], [400, 150], [460, 130], [460, 170], [400, 150], [0, 300]])
bow_cw = loop(np.asarray(bow.xy)[::-1])
for label, lp in (("as drawn", bow), ("reversed", bow_cw)):
    shell, holes = nesting._sampled_rings(sheets.Piece("B", 1, lp, [], 0.0), 1.0)
    raw = Polygon(shell)
    rep = nesting._piece_polygon(shell, holes, np.eye(2))
    print(f"  {label}: raw valid={raw.is_valid} raw bounds={tuple(round(v,1) for v in raw.bounds)} shoelace area={raw.area:.0f} -> repaired {rep.geom_type} area={rep.area:.0f} bounds={tuple(round(v,1) for v in rep.bounds)}")
    print("      explain:", shapely.is_valid_reason(raw))
# nest the bow-tie next to a rectangle and see whether the ORIGINAL loop (what the DXF gets) overlaps the neighbour
for label, lp in (("as drawn", bow), ("reversed", bow_cw)):
    shell, _ = nesting._sampled_rings(sheets.Piece("B", 1, lp, [], 0.0), 1.0)
    rep = nesting._piece_polygon(shell, [], np.eye(2))
    pieces = [sheets.Piece("B", 1, lp, [], float(rep.area)), sheets.Piece("A", 1, rect(300, 400), [], 120000.0)]
    sl, summ, warn = nesting.nest(pieces, rot, opts)
    placed = {}
    for s in sl:
        for p in s.placements:
            pc = next(x for x in pieces if x.piece_id == p.piece_id)
            pts = p.apply(sheets.sample_loop(pc.outer, 1.0)[0], rot)
            placed[p.piece_id] = (Polygon(pts), p)
    if "A" in placed and "B" in placed and placed["A"][1].sheet_index == placed["B"][1].sheet_index:
        a, b = placed["A"][0], placed["B"][0]
        bb = shapely.make_valid(b)
        print(f"  {label}: B placed as {placed['B'][1].width_mm:.0f}x{placed['B'][1].length_mm:.0f} at {placed['B'][1].offset.round(1)}; A at {placed['A'][1].offset.round(1)}; "
              f"true-outline distance A<->B = {a.distance(bb):.2f} mm; overlap area = {a.intersection(bb).area:.0f} mm2")
    else:
        print(f"  {label}: on different sheets / unplaced: {summ}")

print("== 3. part_spacing_mm = 0 (UI allows 0.0) ==")
opts0 = options(part_spacing_mm=0.0)
pieces = [sheets.Piece("A", 1, rect(300, 400), [], 120000.0), sheets.Piece("B", 1, rect(300, 400, 500, 0), [], 120000.0)]
sl, summ, warn = nesting.nest(pieces, rot, opts0)
pl = {p.piece_id: p for s in sl for p in s.placements}
print("  offsets:", {k: v.offset.round(2).tolist() for k, v in pl.items()}, "-> gap between A right edge and B left edge:",
      round(float(pl["B"].offset[0] - (pl["A"].offset[0] + pl["A"].width_mm)), 3), "mm")

print("== 4. a small piece nested inside a bigger piece's hole ==")
big = sheets.Piece("BIG", 1, rect(900, 900), [rect(400, 400, 250, 250)], 900*900 - 400*400)
small = sheets.Piece("SM", 1, rect(200, 200), [], 40000.0)
sl, summ, warn = nesting.nest([big, small], rot, opts)
for s in sl:
    for p in s.placements:
        print(f"  sheet {s.index} {p.piece_id} rot={p.rotation_deg} offset={p.offset.round(1).tolist()} size={p.width_mm:.0f}x{p.length_mm:.0f}")

print("== 5. grid granularity: rectangles that need a non-multiple-of-5 x ==")
# A is 403 wide at x=0 -> buffered to 423; B is 567.6 wide -> needs x in [423, 990.6-567.6=423.0]; the only grid x <= 423 is 420 (blocked)
A = sheets.Piece("A", 1, rect(403, 1500), [], 403*1500.0)
B = sheets.Piece("B", 1, rect(567.6, 1500), [], 567.6*1500.0)
sl, summ, warn = nesting.nest([A, B], rot, opts)
print("  step 5:", summ["sheet_count"], "sheets;", [(p.piece_id, p.offset.round(1).tolist()) for s in sl for p in s.placements])
sl, summ, warn = nesting.nest([A, B], rot, options(nest_step_mm=1.0))
print("  step 1:", summ["sheet_count"], "sheets;", [(p.piece_id, p.offset.round(1).tolist()) for s in sl for p in s.placements])
print("  usable_w - 403 - 20 =", 990.6 - 403 - 20, "-> B would fit at x=423 exactly (B width 567.6)")

print("== 6. envelope-edge piece: width == usable exactly, and usable + 5e-10 ==")
for w in (990.6, 990.6 + 5e-10, 990.6 + 2e-9):
    P = sheets.Piece("E", 1, rect(2006.6, w), [], w * 2006.6)   # sheet_transform maps +X along boat to +Y
    sl, summ, warn = nesting.nest([P], rot, opts)
    print(f"  width {w!r}: sheets={summ['sheet_count']} placed={summ['piece_count']} warn={warn[:1]}")

