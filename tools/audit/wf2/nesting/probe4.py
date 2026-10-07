import sys, json
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
from autodeck2 import nesting, sheets
from autodeck2.config import load_config
for name in ("axis", "noaxis"):
    res = json.loads((S / "runs" / name / "sheets.json").read_text(encoding="utf-8"))
    areas = {p["piece_id"]: p["area_mm2"] for p in res["pieces"]}
    thin = [(p["piece_id"], p["width_mm"], p["length_mm"], areas[p["piece_id"]]) for s in res["sheets"] for p in s["placements"] if min(p["width_mm"], p["length_mm"]) < 40]
    print(name, "nested pieces thinner than 40 mm (id, w, l, area mm2):", thin)
    print(name, "warnings mentioning sliver:", [w for w in res["warnings"] if "sliver" in w])
def loop(pts):
    pts = np.asarray(pts, float); return sheets.Loop(np.column_stack([pts, np.zeros(len(pts))]))
def rect(w, l, x=0.0, y=0.0):
    return loop([[x, y], [x + w, y], [x + w, y + l], [x, y + l]])
opts = sheets.settings(load_config()); rot = sheets.sheet_transform(np.array([1.0, 0.0]))
A = sheets.Piece("A", 1, rect(1500, 300), [], 1500*300.0); B = sheets.Piece("B", 1, rect(1400, 200), [], 1400*200.0)
sl, summ, warn = nesting.nest([A, B], rot, opts)
pl = {p.piece_id: p for s in sl for p in s.placements}
print("spacing 20, A 300 wide at x=0 -> B placed at usable x =", round(float(pl["B"].offset[0] - 12.7), 3), "=> gap", round(float(pl["B"].offset[0] - pl["A"].offset[0] - 300), 3), "mm (asked 20)")
