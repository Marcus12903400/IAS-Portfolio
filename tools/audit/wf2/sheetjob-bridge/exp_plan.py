import sys, json, math, time, shutil
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheetjob-bridge")
src = S / "runs" / "axis"
run = S / "runs" / "axis_plan"
if run.exists(): shutil.rmtree(run)
shutil.copytree(src, run)
from autodeck_app import bridge
from autodeck2 import sheets, sheetjob
from autodeck2.config import load_config
config = load_config()
options = sheets.settings(config)

t0 = time.perf_counter()
pv = sheetjob.preview(run, config)
t1 = time.perf_counter()
pl = sheetjob.plan(run, config, write_files=True)
t2 = time.perf_counter()
print("preview %.1fs plan(write) %.1fs" % (t1-t0, t2-t1))
print("status", pv["status"], pl["status"], "seam_count", pv["seam_count"], "pieces", pv["piece_count"], pl["piece_count"])
print("oversize", [o["piece_id"] for o in pv["oversize"]], "unplaced", pv["summary"]["unplaced_piece_ids"], "sheets", pv["summary"]["sheet_count"])
same_pieces = pv["pieces"] == pl["pieces"]
same_sheets = pv["sheets"] == pl["sheets"]
same_seams = pv["seams"] == pl["seams"]
print("preview==plan pieces", same_pieces, "sheets", same_sheets, "seams", same_seams)
print("seams as cut:")
for s, sn in zip(pv["seams"], pv["seam_snaps"]):
    print("  ", s["seam_id"], "mode", repr(s["mode"]), "raw?", s["raw"] is not None, "snap:", sn["kind"], sn["note"], "moved", sn["moved_mm"])
print("files after export:", [f["name"] for f in pl["files"]], "refused", pl["refused"])
print("exported_sheets:", [(f["name"], f["sheet"], len(f["pieces"])) for f in sheetjob.exported_sheets(run)])
# preview rings per sheet
for sh in pv["preview"]["sheets"]:
    print("  sheet", sh["sheet"], "rings", len(sh["rings"]), "pieces", sorted({r["piece_id"] for r in sh["rings"]}))
# Now re-export with NO seams -> fewer sheets; see what is left on disk
pl2 = sheetjob.plan(run, config, seams=[], write_files=True)
print("no-seam export: status", pl2["status"], "sheets", pl2["summary"]["sheet_count"], "files", [f["name"] for f in pl2["files"]], "oversize", [o["piece_id"] for o in pl2["oversize"]], "unplaced", pl2["summary"]["unplaced_piece_ids"])
print("on disk:", sorted(p.name for p in run.glob("sheet_*.dxf")))
ex = sheetjob.exported_sheets(run)
print("exported_sheets now:", [(f["name"], f["sheet"], len(f["pieces"]), f["utilisation"]) for f in ex])
# what the page would show after the preview (seams back to the 4)
pv2 = sheetjob.preview(run, config)
print("preview files (page download list):", [(f["name"], f["sheet"], len(f["pieces"])) for f in pv2["files"]], "but preview sheets:", pv2["summary"]["sheet_count"])
# does sheets.json carry the seams the files were cut with?
written = json.loads((run/"sheets.json").read_text())
print("sheets.json seam_count", written["seam_count"], "status", written["status"], "vs live preview seam_count", pv2["seam_count"])
