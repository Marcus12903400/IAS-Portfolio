import time
from pathlib import Path
from autodeck2 import sheets, sheetjob, nesting, seamsnap
from autodeck2.config import load_config
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/engine-diff")
copy = S / "axis_run"
config = load_config(); options = sheets.settings(config)
loops, pattern, kind = sheets.read_fitted_dxf(copy / "final_auto.dxf")
frame, _w = sheetjob.resolve_frame(copy, options)
seam_list = sheets.read_seams(copy)
def timed(label, fn, n=3):
    best = 1e9
    for _ in range(n):
        t0 = time.perf_counter(); out = fn(); best = min(best, time.perf_counter() - t0)
    print(f"{label}: {best*1000:.0f} ms (best of {n})"); return out
refs = timed("references_from_loops", lambda: seamsnap.references_from_loops(loops, options))
print("  reference count:", len(refs))
timed("apply_snap(4 seams)", lambda: sheetjob.apply_snap(seam_list, loops, frame.axis, options))
snapped, _r, _w = sheetjob.apply_snap(seam_list, loops, frame.axis, options)
step = float(options["sample_step_mm"])
def split():
    pieces = []
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], step)
        if outer is None: continue
        pieces.extend(sheets.split_panel(pid, outer, holes, snapped, options)[0])
    return pieces
pieces = timed("split_panel(all)", split)
rotation = sheets.sheet_transform(frame.axis)
timed("oversize_report", lambda: sheets.oversize_report(pieces, rotation, options))
timed("nesting.nest", lambda: nesting.nest(pieces, rotation, options))
timed("plan(write_files=False)", lambda: sheetjob.plan(copy, config, write_files=False))
timed("preview()", lambda: sheetjob.preview(copy, config))
