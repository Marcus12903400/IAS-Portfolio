import json, math, time, sys, glob
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon
from autodeck2 import sheets, sheetjob, nesting
from autodeck2.config import load_config

S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/engine-diff")
copy = S / "axis_run"
config = load_config()
options = sheets.settings(config)

print("=== 1. legacy seams through the new corrector (snapped_seams on the copy) ===")
seams, results, warnings = sheetjob.snapped_seams(copy, config)
for s, r in zip(seams, results):
    d = s.drawn
    print(f"{s.seam_id}: drawn ({d[0]:.1f},{d[1]:.1f})-({d[2]:.1f},{d[3]:.1f}) -> cut ({s.x1:.1f},{s.y1:.1f})-({s.x2:.1f},{s.y2:.1f})"
          f" applied={r.applied} kind={r.kind!r} angle={r.angle_change_deg:.2f} moved={r.moved_mm:.1f} note={r.note!r} mode={s.mode!r} snap={s.snap}")
print("warnings:", warnings)

print("\n=== 2. timing plan(write_files=False) vs preview() ===")
t0 = time.perf_counter(); res = sheetjob.plan(copy, config, write_files=False); t1 = time.perf_counter()
print(f"plan(no files): {t1-t0:.2f}s status={res['status']} sheets={res['summary']['sheet_count']} pieces={res['piece_count']} oversize={len(res['oversize'])}")
t0 = time.perf_counter(); pre = sheetjob.preview(copy, config); t1 = time.perf_counter()
print(f"preview():      {t1-t0:.2f}s status={pre['status']} rings={sum(len(s['rings']) for s in pre['preview']['sheets'])} holes={sum(1 for s in pre['preview']['sheets'] for r in s['rings'] if r['hole'])}")
# time the split alone
loops, pattern, kind = sheets.read_fitted_dxf(copy / "final_auto.dxf")
step = float(options["sample_step_mm"])
seam_list = [sheets.Seam.from_dict(i) for i in res["seams"]]
t0 = time.perf_counter()
for pid in sorted(loops):
    outer, holes = sheets.classify_loops(loops[pid], step)
    if outer is None: continue
    sheets.split_panel(pid, outer, holes, seam_list, options)
t1 = time.perf_counter()
print(f"split_panel over all panels: {t1-t0:.2f}s ; read_fitted_dxf:", end=" ")
t0 = time.perf_counter(); sheets.read_fitted_dxf(copy / "final_auto.dxf"); print(f"{time.perf_counter()-t0:.2f}s")

print("\n=== 3. plan(write_files=True) into the COPY: refused / status ===")
t0 = time.perf_counter(); full = sheetjob.plan(copy, config, write_files=True); t1 = time.perf_counter()
print(f"plan(files): {t1-t0:.2f}s status={full['status']} refused={full['refused']} files={[ (f['name'], f['pieces'], f['refused']) for f in full['files']]}")
print("sheets.json keys:", sorted(json.loads((copy/'sheets.json').read_text()).keys()))

print("\n=== 4. NaN angle_deg through Seam.from_dict / preview ===")
try:
    bad = sheets.Seam.from_dict({"seam_id": "n", "x1": 0, "y1": 0, "x2": 400, "y2": 0, "mode": "angle", "angle_deg": "nan"})
    print("from_dict accepted angle_deg nan:", bad.angle_deg, "to_dict json:", json.dumps(bad.to_dict())[:120])
    try:
        out = sheetjob.preview(copy, config, seams=[bad])
        print("preview returned; seams:", out["seams"][0]["x1"], out["seams"][0]["angle_deg"], "status", out["status"])
        print("json.dumps of result seams:", json.dumps(out["seams"])[:200])
    except Exception as e:
        print("preview raised:", type(e).__name__, str(e)[:200])
except Exception as e:
    print("from_dict raised:", type(e).__name__, e)

print("\n=== 5. _encloses_area on a D-shape (one bulged + one straight segment) ===")
print("D-shape:", sheets._encloses_area(np.array([[0.0, 0.0, 1.0], [100.0, 0.0, 0.0]])),
      "| circle:", sheets._encloses_area(np.array([[0.0, 0.0, 1.0], [100.0, 0.0, 1.0]])),
      "| 2 straight:", sheets._encloses_area(np.array([[0.0, 0.0, 0.0], [100.0, 0.0, 0.0]])))

print("\n=== 6. nesting._probes on an empty polygon ===")
try:
    print(nesting._probes(Polygon(), nesting._PROBE_LEVELS))
except Exception as e:
    print("raised:", type(e).__name__, str(e)[:160])

print("\n=== 7. tolerant parse of every fixture seams*.json ===")
for path in sorted(glob.glob(r"D:/AutoDeck/engine/outputs/runs/*/seams*.json*")):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        parsed = [sheets.Seam.from_dict(i) for i in data.get("seams", [])]
        modes = sorted({p.mode for p in parsed}); raws = sum(1 for p in parsed if p.raw is not None); snaps = sum(1 for p in parsed if not p.snap)
        print(f"OK  {Path(path).parent.name}/{Path(path).name}: {len(parsed)} seams modes={modes} with_raw={raws} snap_off={snaps}")
    except Exception as e:
        print(f"ERR {path}: {type(e).__name__}: {e}")

print("\n=== 8. seam_gap_mm = 0 through the server table and the engine ===")
sys.path.insert(0, r"D:/AutoDeck/app")
from autodeck_app import server
ov = server.sheet_options({"seam_gap_mm": 0})
print("server.sheet_options accepts:", ov)
try:
    sheets.settings({**config, "sheets": {**config["sheets"], **ov}})
    print("settings accepted 0")
except ValueError as e:
    print("settings raised:", e)
print("MAX_AXIS_CAPTURE_DEG", __import__("autodeck2.seamsnap", fromlist=["x"]).MAX_AXIS_CAPTURE_DEG, "server range", server._SHEET_NUMBERS["seam_axis_snap_deg"])
