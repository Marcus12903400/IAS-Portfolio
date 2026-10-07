import sys
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
import nesting_old
from autodeck2 import nesting, sheets
from autodeck2.config import load_config
from shapely.geometry import Polygon

def loop(pts):
    pts = np.asarray(pts, float); return sheets.Loop(np.column_stack([pts, np.zeros(len(pts))]))
def rect(w, l, x=0.0, y=0.0):
    return loop([[x, y], [x + w, y], [x + w, y + l], [x, y + l]])
base = load_config()
def options(**ov):
    return sheets.settings({**base, "sheets": {**(base.get("sheets") or {}), **ov}})
opts = options(); rot = sheets.sheet_transform(np.array([1.0, 0.0]))

print("== OLD nester on an empty (collinear) piece: what did it do? ==")
flat = sheets.Piece("Z1", 1, loop([[0, 0], [100, 0], [200, 0]]), [], 0.0)
sl, summ, warn = nesting_old.nest([sheets.Piece("A", 1, rect(400, 300), [], 120000.0), flat], rot, opts)
for s in sl:
    for p in s.placements:
        print(f"  sheet {s.index} {p.piece_id} rot={p.rotation_deg} offset={p.offset.tolist()} origin={p.origin.tolist()} size={p.width_mm}x{p.length_mm}")
poly = nesting_old._piece_polygon(flat, rot, 1.0)
print("  old variant polygon empty:", poly.is_empty, "bounds:", poly.bounds)
print("  0.0 <= nan + 1e-9 ->", 0.0 <= float('nan') + 1e-9, "; usable_l - nan ->", 2006.6 - float('nan'))

print("== grid granularity (fixed orientation: rect(along, across)) ==")
# A: 403 across at x=0 -> buffered to 423; B: 567.6 across -> needs x in [423, 990.6-567.6=423.0]
A = sheets.Piece("A", 1, rect(1500, 403), [], 403*1500.0)
B = sheets.Piece("B", 1, rect(1500, 567.6), [], 567.6*1500.0)
for step in (5.0, 1.0, 0.5):
    sl, summ, warn = nesting.nest([A, B], rot, options(nest_step_mm=step))
    print(f"  step {step}: {summ['sheet_count']} sheets;", [(p.piece_id, s.index, p.offset.round(2).tolist()) for s in sl for p in s.placements])
print("  (B needs sheet x = 423.0 exactly; 990.6 - 567.6 =", 990.6 - 567.6, ")")
# how much room is wasted per piece by the grid: 1 piece 300 wide followed by pieces that need x=321, 322, 323, 324
for need in (320, 321, 322, 323, 324, 325):
    A = sheets.Piece("A", 1, rect(1500, need - 20.0), [], 1.0e6)
    B = sheets.Piece("B", 1, rect(1500, 990.6 - need), [], 0.9e6)
    sl, summ, warn = nesting.nest([A, B], rot, opts)
    print(f"  A {need-20:.0f} wide then B {990.6-need:.1f} wide (needs x={need}): sheets={summ['sheet_count']}")
