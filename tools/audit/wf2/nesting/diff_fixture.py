"""Old (committed) nester vs new nester on the real fixture pieces, bit for bit."""
import sys, json, time, shutil
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
import nesting_old
from autodeck2 import nesting, sheetjob, sheets
from autodeck2.config import load_config

def layout(sheet_list):
    return tuple((p.sheet_index, p.piece_id, p.rotation_deg, float(p.offset[0]), float(p.offset[1]),
                  float(p.origin[0]), float(p.origin[1]), p.width_mm, p.length_mm) for s in sheet_list for p in s.placements)

def capture(run_dir, config):
    got = {}
    real = nesting.nest
    def spy(pieces, rot, opts):
        got["args"] = (pieces, rot, opts)
        return real(pieces, rot, opts)
    nesting.nest = spy
    try:
        sheetjob.plan(run_dir, config, write_files=False)
    finally:
        nesting.nest = real
    return got["args"]

def compare(label, pieces, rot, opts):
    t0 = time.perf_counter(); old = nesting_old.nest(pieces, rot, opts); t1 = time.perf_counter()
    new = nesting.nest(pieces, rot, opts); t2 = time.perf_counter()
    same_layout = layout(old[0]) == layout(new[0])
    same_summary = old[1] == new[1]
    same_warn = old[2] == new[2]
    same_area = [s.used_area_mm2 for s in old[0]] == [s.used_area_mm2 for s in new[0]]
    print(f"{label}: pieces={len(pieces)} sheets={new[1]['sheet_count']} placed={new[1]['piece_count']} "
          f"unplaced={new[1]['unplaced_piece_ids']} identical: layout={same_layout} summary={same_summary} "
          f"warnings={same_warn} used_area={same_area}  old={t1-t0:.1f}s new={t2-t1:.2f}s")
    if not same_layout:
        for a, b in zip(layout(old[0]), layout(new[0])):
            if a != b: print("   OLD", a); print("   NEW", b)
    return same_layout and same_summary and same_warn and same_area

base = load_config()
all_ok = True
for name in ("axis", "noaxis"):
    run = S / "runs" / name
    for seam_file in ("seams.json", "seams_previous.json", "seams_optimiser_run.json.bak"):
        src = run / seam_file
        if not src.is_file():
            continue
        work = S / "runs" / f"{name}_{seam_file.split('.')[0]}"
        if work.exists(): shutil.rmtree(work)
        shutil.copytree(run, work)
        shutil.copy(src, work / "seams.json")
        for overrides in ({}, {"part_spacing_mm": 0.0}, {"grain_angle_deg": 0.0}, {"allow_180_rotation": False}, {"nest_step_mm": 7.3}):
            cfg = {**base, "sheets": {**(base.get("sheets") or {}), **overrides}}
            pieces, rot, opts = capture(work, cfg)
            ok = compare(f"{name}/{seam_file} {overrides}", pieces, rot, opts)
            all_ok = all_ok and ok
print("ALL IDENTICAL:", all_ok)
