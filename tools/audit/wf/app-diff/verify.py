import sys, json
from pathlib import Path
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/app-diff")
from autodeck_app import server, bridge
from autodeck2 import sheets
from autodeck2.config import load_config

print("--- 1. seam_gap_mm = 0 through server.sheet_options then sheets.settings ---")
ov = server.sheet_options({"seam_gap_mm": "0", "part_spacing_mm": "20"})
print("sheet_options accepted:", ov)
cfg = load_config()
try:
    sheets.settings({**cfg, "sheets": {**cfg["sheets"], **ov}})
    print("sheets.settings: accepted (no error)")
except ValueError as e:
    print("sheets.settings raised ValueError:", str(e)[:160])

print("--- 2. hover before auto-fit (run.json only, no final_auto.dxf), stub view ---")
class StubView:
    run_dir = S / "axisrun_nofit"
    sheet_cache = {}
    placements = {}
    lifters = {}
res = bridge.seam_hover(StubView(), {}, "along", None, point_flat=[0.0, 0.0], point_world=None)
print({k: res[k] for k in res if k != "boat"})
print("boat.axis:", res["boat"].get("axis"), "warnings:", res["boat"].get("warnings"))

print("--- 3. what sheet_options does with seam_gap_mm 0.4 / part_spacing 0 / grain '' ---")
print(server.sheet_options({"seam_gap_mm": "0.4", "part_spacing_mm": "0", "grain_angle_deg": ""}))
