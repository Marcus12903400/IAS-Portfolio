import json, shutil, time
from pathlib import Path
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheetjob-bridge")
src = S / "runs" / "axis"; run = S / "runs" / "axis_opt"
if run.exists(): shutil.rmtree(run)
shutil.copytree(src, run)
from autodeck_app import bridge
before = sorted(p.name for p in run.glob("seams*"))
print("before:", before)
lines = []
t0 = time.perf_counter()
payload = bridge.job_optimise_seams(run, {}, lines.append, time_budget_s=8.0)
print("elapsed %.0fs" % (time.perf_counter()-t0))
for l in lines: print("  log:", l[:150])
print("payload:", {k: payload[k] for k in ("status","reason","improved","seams_written","seams_replaced","backup")})
print("after:", sorted(p.name for p in run.glob("seams*")))
cur = json.loads((run/"seams.json").read_text()); prev = json.loads((run/"seams_previous.json").read_text())
print("seams.json ids:", [s["seam_id"] for s in cur["seams"]])
print("seams_previous.json ids:", [s["seam_id"] for s in prev["seams"]], "(the 4 hand seams)")
