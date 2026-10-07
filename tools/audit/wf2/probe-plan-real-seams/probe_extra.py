"""Extra measurements on top of probe_plan.py's outputs:

 (a) the seam STORED in seams.json (x1..y2, what the app last showed) vs the
     seam AS CUT by plan() (plan json "seams"), per seam: endpoint distance;
     also stored vs raw (what the app's own snap did when the seam was placed).
 (b) spacing between parallel as-cut seam LINES: any pair closer than 30 mm
     makes a band narrower than 24 mm somewhere both lines cross material.
 (c) set D split in two: the 6 optimiser seams alone and the 6 hand seams
     alone, each planned with write_files=False on its own staged copy, to see
     which half leaves pieces oversize.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/probe_extra.py
"""
import itertools
import json
import math
import shutil

import numpy as np

from common import SETS, OUT, RUN3, SCRATCH, copy_dir, load_json
from autodeck2 import seamsnap, sheetjob, sheets
from autodeck2.config import load_config


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def main():
    config = load_config()
    options = sheets.settings(config)
    print("=== (a) stored seams.json vs as-cut vs raw ===")
    for name in SETS:
        work = copy_dir(name)
        stored = {s["seam_id"]: s for s in load_json(work / "seams.json")["seams"]}
        plan = load_json(OUT / f"{name}_plan.json")
        for cut, snap in zip(plan["seams"], plan["seam_snaps"]):
            st = stored[cut["seam_id"]]
            d_cut = max(dist((st["x1"], st["y1"]), (cut["x1"], cut["y1"])),
                        dist((st["x2"], st["y2"]), (cut["x2"], cut["y2"])))
            raw = st.get("raw")
            d_raw = (max(dist((st["x1"], st["y1"]), raw[:2]), dist((st["x2"], st["y2"]), raw[2:]))
                     if raw else 0.0)
            flag = "  <-- CUT != STORED" if d_cut > 0.01 else ""
            print(f"  {name} {cut['seam_id']:<16} mode={cut['mode'] or '-':<7} stored-vs-raw {d_raw:7.2f} mm   "
                  f"stored-vs-cut {d_cut:7.2f} mm   plan snap: applied={snap['applied']} kind={snap['kind']!r} "
                  f"moved={snap['moved_mm']:.2f}{flag}")

    print("=== (b) parallel as-cut seam lines closer than 30 mm (perpendicular distance) ===")
    for name in SETS:
        plan = load_json(OUT / f"{name}_plan.json")
        seams = plan["seams"]
        found = 0
        for a, b in itertools.combinations(seams, 2):
            ua = np.array([a["x2"] - a["x1"], a["y2"] - a["y1"]]); ua /= np.hypot(*ua)
            ub = np.array([b["x2"] - b["x1"], b["y2"] - b["y1"]]); ub /= np.hypot(*ub)
            ang = seamsnap._direction_angle_deg(ua, ub)
            if ang > 0.5:
                continue
            mb = np.array([(b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2])
            pa = np.array([a["x1"], a["y1"]])
            v = mb - pa
            perp = abs(float(ua[0] * v[1] - ua[1] * v[0]))
            if perp < 30.0:
                found += 1
                # why the corrector did not slide b onto a's line: the tests
                # sheetjob.apply_snap -> seamsnap._locked -> _refine_position applies
                ref = seamsnap.Reference("seam", f"seam {a['seam_id']}", a["panel_id"],
                                         np.array([a["x1"], a["y1"]]), np.array([a["x2"], a["y2"]]))
                pb = np.array([b["x1"], b["y1"]]); qb = np.array([b["x2"], b["y2"]])
                alongside = seamsnap._runs_alongside(pb, qb, ref)
                reach = ref.distance_to(pb, qb)
                print(f"  {name}: {a['seam_id']} ({a['mode'] or '-'}, panel {a['panel_id']}) and {b['seam_id']} "
                      f"({b['mode'] or '-'}, panel {b['panel_id']}): lines {perp:.2f} mm apart, angle {ang:.1e} deg "
                      f"-> band {perp - 6.0:.2f} mm wide where both cross material | slide of {b['seam_id']} onto "
                      f"{a['seam_id']}: runs_alongside={alongside} (excluded if True), end-to-end gap {reach:.0f} mm "
                      f"vs seam_snap_reach_mm {options['seam_snap_reach_mm']:.0f} ({'OUT OF REACH' if reach > options['seam_snap_reach_mm'] else 'in reach'}), "
                      f"offset {perp:.1f} vs seam_snap_offset_mm {options['seam_snap_offset_mm']:.0f}")
        if not found:
            print(f"  {name}: none")

    print("=== (c) set D split: optimiser seams alone / hand seams alone ===")
    d_seams = load_json(RUN3 / "seams.json")["seams"]
    subsets = {"Dauto": [s for s in d_seams if s["seam_id"].startswith("auto-")],
               "Dhand": [s for s in d_seams if not s["seam_id"].startswith("auto-")]}
    for sub, seam_list in subsets.items():
        work = SCRATCH / "runs" / f"set_{sub}"
        work.mkdir(parents=True, exist_ok=True)
        for fn in ("final_auto.dxf", "run.json", "panels.json"):
            shutil.copyfile(RUN3 / fn, work / fn)
        (work / "seams.json").write_text(json.dumps({"seams": seam_list}, indent=2), encoding="utf-8")
        res = sheetjob.plan(work, config, seams=sheets.read_seams(work), write_files=False)
        (OUT / f"{sub}_plan.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        s = res["summary"]
        print(f"  {sub}: seams {[x['seam_id'] for x in seam_list]}")
        print(f"     status={res['status']} pieces={res['piece_count']} sheets={s['sheet_count']} "
              f"utilisation={s['utilisation']} unplaced={s['unplaced_piece_ids']}")
        for o in res["oversize"]:
            print(f"     oversize {o['piece_id']} {o['width_mm']} x {o['length_mm']} ({o['hint']})")
        for w in res["warnings"]:
            if "sliver" in w:
                print(f"     warning: {w}")


if __name__ == "__main__":
    main()
