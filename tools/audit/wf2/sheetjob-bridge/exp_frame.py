import math
from pathlib import Path
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheetjob-bridge")
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config
from autodeck_app import server
config = load_config()
for name in ("axis", "noaxis"):
    run = S / "runs" / name
    for ov in (None, 3.68, 0.0, 93.68, 183.68, -86.32, 60.0, 75.0, 80.0):
        opts = sheets.settings({**config, "sheets": {**config.get("sheets", {}), "grain_angle_deg": ov}})
        fr, w = sheetjob.resolve_frame(run, opts)
        d = fr.to_dict()
        print(f"{name:6} override={ov!s:7} axis={d['axis']} conf={d['confidence']} bow_sign={d['bow_sign']} bow_conf={d['bow_conf' if False else 'bow_confidence']} bow_dir={d['bow_direction']}")
        for x in w: print("        warn:", x[:110])
# seam gap zero path
try:
    print("sheet_options gap0 ->", server.sheet_options({"seam_gap_mm": 0}))
    sheets.settings({**config, "sheets": {**config.get("sheets", {}), "seam_gap_mm": 0.0}})
except Exception as e:
    print("settings() raised:", type(e).__name__, str(e)[:120])
