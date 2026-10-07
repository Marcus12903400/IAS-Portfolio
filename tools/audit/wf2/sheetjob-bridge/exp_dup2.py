import math, types
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheetjob-bridge")
run = S / "runs" / "axis_dup"   # seams.json already holds the 4 seams as cut (written by exp_dup.py)
from autodeck_app import bridge
from autodeck2 import sheets, sheetjob, seamplace, seamsnap
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config)
view = types.SimpleNamespace(run_dir=run, sheet_cache={}, placements={}, lifters={})
frame, _ = sheetjob.resolve_frame(run, options)
along, across = seamsnap.master_directions(frame.axis)
cut = sheets.read_seams(run)
s1 = cut[0]
u = np.array([s1.x2-s1.x1, s1.y2-s1.y1]); u /= np.hypot(*u); n = np.array([-u[1], u[0]])
point = np.array([(s1.x1+s1.x2)/2, (s1.y1+s1.y2)/2]) + n*10.0
res = bridge.seam_hover(view, {}, "across", None, point_flat=point, with_world=False)
def gap(sg):
    dx, dy = sg["x2"]-sg["x1"], sg["y2"]-sg["y1"]; L2 = dx*dx+dy*dy
    t = ((point[0]-sg["x1"])*dx + (point[1]-sg["y1"])*dy)/L2
    return (abs(t) if t < 0 else (t-1 if t > 1 else 0))*math.sqrt(L2)
sg = min(res["segments"], key=gap)
print("hovered chord: panel", sg["panel_id"], "len", sg["length_mm"], "ends", [round(sg[k],1) for k in ("x1","y1","x2","y2")])
base = bridge.sheet_preview(run, {}, seams=[s.to_dict() for s in cut], save=False)
new = {"x1": sg["x1"], "y1": sg["y1"], "x2": sg["x2"], "y2": sg["y2"], "panel_id": sg["panel_id"], "mode": "across", "angle_deg": None, "snap": True, "raw": [sg["x1"], sg["y1"], sg["x2"], sg["y2"]]}
with_new = bridge.sheet_preview(run, {}, seams=[s.to_dict() for s in cut] + [new], save=False)
print("pieces: 4 seams ->", base["piece_count"], "; 5 seams ->", with_new["piece_count"], "; sheets", base["summary"]["sheet_count"], "->", with_new["summary"]["sheet_count"])
print("new seam as the page lists it:", with_new["seams"][-1]["length_mm"], "mm,", repr(with_new["seams"][-1]["snap_note"]))
print("piece ids equal:", [p["piece_id"] for p in base["pieces"]] == [p["piece_id"] for p in with_new["pieces"]], "areas equal:", [p["area_mm2"] for p in base["pieces"]] == [p["area_mm2"] for p in with_new["pieces"]])
