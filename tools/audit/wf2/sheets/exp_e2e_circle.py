import json, math
from pathlib import Path
import ezdxf
import numpy as np
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config

S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheets")
RUN = S / "synthrun"
# stage a run: 1800 x 900 rounded panel with a 200 mm round cut-out, boat axis +X
b = math.tan(math.pi / 8); r = 60.0; W, H = 1800.0, 900.0
outer = [(r, 0, b * 0), (W - r, 0, b), (W, r, 0), (W, H - r, b), (W - r, H, 0), (r, H, b), (0, H - r, 0), (0, r, b)]
circle = [(1300 - 100, 450, 1.0), (1300 + 100, 450, 1.0)]
doc = ezdxf.new("R2010", setup=True); msp = doc.modelspace()
doc.layers.add("CAM_USER__PANEL_1")
msp.add_lwpolyline(outer, format="xyb", close=True, dxfattribs={"layer": "CAM_USER__PANEL_1"})
msp.add_lwpolyline(circle, format="xyb", close=True, dxfattribs={"layer": "CAM_USER__PANEL_1"})
doc.saveas(RUN / "final_auto.dxf")
(RUN / "run.json").write_text(json.dumps({"teak": {"frame": {"longitudinal_axis": [1.0, 0.0], "axis_confidence": 1.0, "bow_sign": 1, "bow_confidence": 1.0}}}), encoding="utf-8")
sheets.write_seams(RUN, [sheets.Seam("s1", 900.0, -50.0, 900.0, 950.0, panel_id=1, mode="across", raw=(900.0, -50.0, 900.0, 950.0))])
config = load_config()
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
print("read back loops for panel 1:", [len(l.vertices) for l in loops[1]])
result = sheetjob.plan(RUN, config, write_files=True)
print("status", result["status"], "pieces", [(p["piece_id"], p["holes"]) for p in result["pieces"]], "warnings", result["warnings"])
for entry in result["files"]:
    print("file", entry["name"], "cam_polylines", entry["cam_polylines"], "layer_counts", entry["layer_counts"], "all_closed", entry["all_closed"])
    re = ezdxf.readfile(entry["path"])
    for e in re.modelspace().query("LWPOLYLINE"):
        if str(e.dxf.layer).startswith("CAM__"):
            pts = list(e.get_points("xyb"))
            print("   ", e.dxf.layer, "verts", len(pts), "bulges", sum(1 for p in pts if abs(p[2]) > 1e-9))
prev = sheetjob.preview(RUN, config)
print("preview rings:", [(ring["piece_id"], ring["hole"]) for s in prev["preview"]["sheets"] for ring in s["rings"]])
# the same panel with NO seam: does the circle survive?
sheets.write_seams(RUN, [])
result0 = sheetjob.plan(RUN, config, write_files=False)
print("no-seam pieces:", [(p["piece_id"], p["holes"]) for p in result0["pieces"]])
