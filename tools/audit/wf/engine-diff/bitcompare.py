import sys, time
from pathlib import Path
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/engine-diff")
sys.path.insert(0, str(S))
import nesting_old
from autodeck2 import sheets, sheetjob, nesting
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config)
for name in ("axis_run",):
    copy = S / name
    loops, pattern, kind = sheets.read_fitted_dxf(copy / "final_auto.dxf")
    frame, _w = sheetjob.resolve_frame(copy, options)
    snapped, _r, _w = sheetjob.apply_snap(sheets.read_seams(copy), loops, frame.axis, options)
    step = float(options["sample_step_mm"]); pieces = []
    for pid in sorted(loops):
        outer, holes = sheets.classify_loops(loops[pid], step)
        if outer is None: continue
        pieces.extend(sheets.split_panel(pid, outer, holes, snapped, options)[0])
    rotation = sheets.sheet_transform(frame.axis)
    t0 = time.perf_counter(); new_sheets, new_summary, new_w = nesting.nest(pieces, rotation, options); t_new = time.perf_counter() - t0
    t0 = time.perf_counter(); old_sheets, old_summary, old_w = nesting_old.nest(pieces, rotation, options); t_old = time.perf_counter() - t0
    def flat(sheets_):
        return [(s.index, p.piece_id, p.rotation_deg, tuple(float(v) for v in p.offset), tuple(float(v) for v in p.origin), p.width_mm, p.length_mm) for s in sheets_ for p in s.placements]
    a, b = flat(new_sheets), flat(old_sheets)
    print(f"{name}: pieces={len(pieces)} new {t_new:.1f}s vs old {t_old:.1f}s; placements identical={a == b}; summary identical={new_summary == old_summary}; warnings identical={new_w == old_w}")
    if a != b:
        for x, y in zip(a, b):
            if x != y: print("  DIFF new", x, "\n       old", y)
    for p in a: print("  ", p[:4])
