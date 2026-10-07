"""Run the PRODUCTION sheetjob.plan() (writes sheets.json / sheet_report.md /
sheet_NN.dxf INTO THE COPY) and sheetjob.preview() on each staged set; dump the
results to out/<set>_plan.json and out/<set>_preview.json; print the headline
numbers; compare preview vs plan; time both.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/probe_plan.py [A B C D]
"""
import json
import math
import sys
import time

import numpy as np

from common import SETS, OUT, copy_dir, resplit
from autodeck2 import sheetjob, sheets
from autodeck2.config import load_config


def strip(r):
    d = {k: v for k, v in r.items() if k not in ("files", "preview")}
    return json.dumps(d, sort_keys=True)


def main(names):
    config = load_config()
    options = sheets.settings(config)
    timings = {}
    for name in names:
        work = copy_dir(name)
        print("=" * 110)
        print(f"SET {name}: {work}")
        seams_in = sheets.read_seams(work)
        before = (work / "seams.json").read_bytes()

        t0 = time.perf_counter()
        plan = sheetjob.plan(work, config, seams=seams_in)
        t_plan = time.perf_counter() - t0
        assert (work / "seams.json").read_bytes() == before, "plan() rewrote seams.json"

        t0 = time.perf_counter()
        prev = sheetjob.preview(work, config, seams=sheets.read_seams(work))
        t_prev = time.perf_counter() - t0

        t0 = time.perf_counter()
        plan_nf = sheetjob.plan(work, config, seams=sheets.read_seams(work), write_files=False)
        t_nf = time.perf_counter() - t0

        t0 = time.perf_counter()
        plan2 = sheetjob.plan(work, config, seams=sheets.read_seams(work))
        t_plan2 = time.perf_counter() - t0

        timings[name] = {"plan_write_s": round(t_plan, 2), "plan_write_2nd_s": round(t_plan2, 2),
                         "preview_s": round(t_prev, 2), "plan_nofiles_s": round(t_nf, 2)}
        (OUT / f"{name}_plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
        (OUT / f"{name}_preview.json").write_text(json.dumps(prev, indent=1), encoding="utf-8")

        s = plan["summary"]
        print(f"status={plan['status']}  source={plan['source_dxf']}  seams={plan['seam_count']}  "
              f"pieces={plan['piece_count']}  sheets={s['sheet_count']}  placed={s['piece_count']}  "
              f"unplaced={s['unplaced_piece_ids']}  utilisation={s['utilisation']}  "
              f"max_arc_rebuild_error_mm={plan['max_arc_rebuild_error_mm']}")
        print(f"boat_axis={plan['boat_axis']} conf={plan['boat_axis_confidence']} frame={plan['boat_frame']}")
        print(f"timing: plan(write)={t_plan:.2f}s  plan(write, 2nd)={t_plan2:.2f}s  "
              f"preview={t_prev:.2f}s  plan(no files)={t_nf:.2f}s")
        print(f"plan(write) == plan(no files) apart from files: {strip(plan) == strip(plan_nf)};  "
              f"plan repeatable: {strip(plan) == strip(plan2)};  "
              f"preview carries same plan: {strip(prev) == strip(plan_nf)}")

        # every piece: size via production oriented_extent on a production re-split
        rs = resplit(work, plan, options)
        step = float(options["sample_step_mm"])
        placed = {pl["piece_id"]: (sh["sheet"], pl) for sh in plan["sheets"] for pl in sh["placements"]}
        over = {o["piece_id"]: o for o in plan["oversize"]}
        print("-- pieces (size = production oriented_extent: across x along, mm) --")
        for p in plan["pieces"]:
            piece = rs["pieces"].get(p["piece_id"])
            if piece is not None:
                w, l = sheets.oriented_extent(piece, rs["rotation"], step)
                verts = len(piece.outer.vertices)
                arc_err = f"{piece.max_arc_error_mm:.4f}"
            else:
                w, l, verts, arc_err = float("nan"), float("nan"), "?", "?"
            if p["piece_id"] in placed:
                sh, pl = placed[p["piece_id"]]
                where = f"sheet {sh} rot {pl['rotation_deg']} at {pl['offset_mm']}"
            else:
                where = "NOT PLACED"
            flag = " OVERSIZE" if p["piece_id"] in over else ""
            print(f"  {p['piece_id']:<8} panel {p['panel_id']}  {w:8.1f} x {l:8.1f}  area {p['area_mm2']:12.1f}  "
                  f"holes {p['holes']}  from_seam {p['from_seam']}  verts {verts}  arc_err {arc_err}  {where}{flag}")
        if plan["oversize"]:
            print("-- oversize --")
            for o in plan["oversize"]:
                print("  ", o)
        if plan["refused"]:
            print("-- REFUSED at export --")
            for o in plan["refused"]:
                print("  ", o)
        print("-- sheets --")
        for sh in plan["sheets"]:
            print(f"  sheet {sh['sheet']}  utilisation {sh['utilisation'] * 100:.1f}%  pieces "
                  + ", ".join(f"{pl['piece_id']}({pl['width_mm']:.0f}x{pl['length_mm']:.0f}"
                              f"@{pl['offset_mm'][0]:.0f},{pl['offset_mm'][1]:.0f} r{pl['rotation_deg']})"
                              for pl in sh["placements"]))
        print("-- files --")
        for f in plan["files"]:
            print(f"  {f['name']} cam_polylines={f['cam_polylines']} all_closed={f['all_closed']} "
                  f"pattern_lines={f['pattern_lines']} pieces={f['pieces']} refused={f['refused']}")
        print("-- warnings --")
        for w in plan["warnings"]:
            print("  *", w)
        print("-- seams as cut (x1,y1 -> x2,y2; length) vs drawn --")
        for sm, snap in zip(plan["seams"], plan["seam_snaps"]):
            L = math.hypot(sm["x2"] - sm["x1"], sm["y2"] - sm["y1"])
            raw = sm["raw"]
            Lr = math.hypot(raw[2] - raw[0], raw[3] - raw[1]) if raw else L
            print(f"  {sm['seam_id']:<16} panel={sm['panel_id']} mode={sm['mode']!r} snap={sm['snap']} "
                  f"cut ({sm['x1']:.1f},{sm['y1']:.1f})->({sm['x2']:.1f},{sm['y2']:.1f}) len {L:.1f}  raw len {Lr:.1f}  "
                  f"| snap: applied={snap.get('applied')} kind={snap.get('kind')!r} ref={snap.get('reference_label')!r} "
                  f"turned {snap.get('angle_change_deg', 0):.3f} deg moved {snap.get('moved_mm', 0):.2f} mm "
                  f"note={snap.get('note')!r}")

        # preview vs plan: ids, counts, bboxes
        print("-- preview vs plan --")
        pv = prev["preview"]
        ring_ids = [r["piece_id"] for sh in pv["sheets"] for r in sh["rings"] if not r["hole"]]
        plan_ids = [pl["piece_id"] for sh in plan["sheets"] for pl in sh["placements"]]
        hole_rings = sum(1 for sh in pv["sheets"] for r in sh["rings"] if r["hole"])
        plan_holes = sum(p["holes"] for p in plan["pieces"] if p["piece_id"] in placed)
        print(f"  outer rings {len(ring_ids)} vs placements {len(plan_ids)}; same ids & order: {ring_ids == plan_ids}; "
              f"hole rings {hole_rings} vs plan holes {plan_holes}")
        worst = 0.0
        for sh in pv["sheets"]:
            for r in sh["rings"]:
                if r["hole"]:
                    continue
                pts = np.asarray(r["points"], float)
                mn, mx = pts.min(axis=0), pts.max(axis=0)
                pl = placed[r["piece_id"]][1]
                exp_min = np.asarray(pl["offset_mm"], float)
                exp_max = exp_min + np.array([pl["width_mm"], pl["length_mm"]])
                dev = max(np.abs(mn - exp_min).max(), np.abs(mx - exp_max).max())
                worst = max(worst, dev)
                if dev > 0.5:
                    print(f"  bbox mismatch {r['piece_id']}: ring {mn.round(2)}..{mx.round(2)} vs plan "
                          f"{exp_min.round(2)}..{exp_max.round(2)} dev {dev:.2f}")
        print(f"  worst ring-bbox vs placement deviation: {worst:.3f} mm "
              f"(rings sampled at 4 mm, so a few tenths is expected)")
        print(f"  split warnings on re-split: {rs['split_warnings']}")
        print(f"  files listed by preview: {[f['name'] for f in prev['files']]}")
    (OUT / "timings.json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
    print("timings:", json.dumps(timings))


if __name__ == "__main__":
    main(sys.argv[1:] or list(SETS))
