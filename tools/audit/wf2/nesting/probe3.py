import sys, json, shutil
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
from autodeck2 import nesting, sheetjob, sheets
from autodeck2.config import load_config
from shapely.geometry import Polygon
from shapely import affinity
from shapely.prepared import prep

print("== touching the clearance buffer counts as blocked ==")
A = Polygon([(0, 0), (300, 0), (300, 400), (0, 400)])
occupied = A.buffer(20.0, join_style=1)
B = Polygon([(0, 0), (200, 0), (200, 400), (0, 400)])
blocked = prep(occupied)
for x in (319.9999, 320.0, 320.0001, 325.0):
    print(f"   B at x={x}: intersects={blocked.intersects(affinity.translate(B, x, 0))}  distance A<->B={A.distance(affinity.translate(B, x, 0)):.4f}")

print("== holes on the fixture pieces, and anything nested inside another piece's hole ==")
for name in ("axis", "noaxis"):
    work = S / "runs" / name
    res = json.loads((work / "sheets.json").read_text(encoding="utf-8"))
    withholes = [(p["piece_id"], p["holes"]) for p in res["pieces"] if p["holes"]]
    print(f"   {name}: pieces with holes: {withholes}")
    sizes = sorted(((p["width_mm"], p["length_mm"], p["piece_id"]) for s in res["sheets"] for p in s["placements"]))
    print(f"   {name}: narrowest nested pieces (w x l): {sizes[:3]}")

print("== stale sheet DXF from an earlier export survives a re-export and is listed ==")
work = S / "runs" / "axis"
stale = work / "sheet_03.dxf"
stale.write_bytes((work / "sheet_02.dxf").read_bytes())   # pretend an earlier export had 3 sheets
res = sheetjob.plan(work, load_config(), write_files=True)  # scratch copy only
print("   plan wrote:", [f["name"] for f in res["files"]])
print("   exported_sheets lists:", [(f["name"], f.get("sheet"), f.get("pieces"), f.get("exists")) for f in sheetjob.exported_sheets(work)])
print("   files on disk:", sorted(p.name for p in work.glob("sheet_*.dxf")))
stale.unlink()
