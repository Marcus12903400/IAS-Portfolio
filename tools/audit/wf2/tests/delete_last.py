"""Deleting the last seam through the bridge path the API uses, on scratch copies."""
import json, shutil, sys
from pathlib import Path
import ezdxf
from autodeck_app import bridge
SCRATCH = Path(sys.argv[1]); RUNS = Path("D:/AutoDeck/engine/outputs/runs")
src = RUNS / "21kwcockpit-1-20260901-180939"
one = [{"seam_id": "s1", "x1": 110.6, "y1": -1005.9, "x2": 86.2, "y2": -453.0, "panel_id": None}]

def copy(tag, dxf=True):
    work = SCRATCH / tag / src.name; work.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "run.json", work / "run.json")
    if dxf: shutil.copyfile(src / "final_auto.dxf", work / "final_auto.dxf")
    (work / "seams.json").write_text(json.dumps({"seams": one}), encoding="utf-8")
    return work

# 1. normal run, oversize layout (no seams -> panel 1 is 2058 x 3994): can the last seam be removed?
work = copy("del_ok")
res = bridge.sheet_preview(work, {}, seams=[], save=True)
print("1. oversize deck, POST seams=[]: available=", res.get("available"), "status=", res.get("status"), "oversize=", len(res.get("oversize", [])),
      "| seams.json now:", (work / "seams.json").read_text(encoding="utf-8").strip().replace("\n", ""))

# 2. a run whose fitted DXF has no CAM loops: plan() raises ValueError before the save
work = copy("del_nocam", dxf=False)
doc = ezdxf.new("R2010"); doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "NOTHING"}); doc.saveas(work / "final_auto.dxf")
try:
    res = bridge.sheet_preview(work, {}, seams=[], save=True)
    print("2. no-CAM-loops run, POST seams=[]: returned", res.get("available"), res.get("status"))
except Exception as e:
    print("2. no-CAM-loops run, POST seams=[]: RAISED", type(e).__name__, str(e)[:90])
print("   seams.json after:", (work / "seams.json").read_text(encoding="utf-8").strip())

# 3. seam_gap_mm = 0 through the server's own validator
from autodeck_app import server
from werkzeug.exceptions import BadRequest
for gap in (0.0, 0.5):
    try:
        print(f"3. server.sheet_options(seam_gap_mm={gap}) ->", server.sheet_options({"seam_gap_mm": gap}))
    except BadRequest as e:
        print(f"3. server.sheet_options(seam_gap_mm={gap}) -> 400:", e.description)
work = copy("del_gap0")
try:
    res = bridge.sheet_preview(work, {"seam_gap_mm": 0.0}, seams=[], save=True)
    print("   bridge.sheet_preview(gap 0, seams=[]) returned", res.get("status"))
except Exception as e:
    print("   bridge.sheet_preview(gap 0, seams=[]) RAISED", type(e).__name__, str(e)[:100])
print("   seams.json after:", (work / "seams.json").read_text(encoding="utf-8").strip())
