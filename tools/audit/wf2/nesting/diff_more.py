import sys, time
from pathlib import Path
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
sys.path.insert(0, str(S))
import nesting_old
from autodeck2 import nesting, sheetjob
from autodeck2.config import load_config
def layout(sl):
    return tuple((p.sheet_index, p.piece_id, p.rotation_deg, float(p.offset[0]), float(p.offset[1]), float(p.origin[0]), float(p.origin[1]), p.width_mm, p.length_mm) for s in sl for p in s.placements)
for name in ("kw3", "kw2"):
    work = S / "runs" / name
    if not (work / "run.json").is_file(): print(name, "missing"); continue
    got = {}; real = nesting.nest
    def spy(pieces, rot, opts):
        got["args"] = (pieces, rot, opts); return real(pieces, rot, opts)
    nesting.nest = spy
    try: sheetjob.plan(work, load_config(), write_files=False)
    finally: nesting.nest = real
    pieces, rot, opts = got["args"]
    t0 = time.perf_counter(); old = nesting_old.nest(pieces, rot, opts); t1 = time.perf_counter(); new = nesting.nest(pieces, rot, opts); t2 = time.perf_counter()
    print(f"{name}: pieces={len(pieces)} sheets={new[1]['sheet_count']} placed={new[1]['piece_count']} unplaced={new[1]['unplaced_piece_ids']} identical layout={layout(old[0]) == layout(new[0])} summary={old[1] == new[1]} warnings={old[2] == new[2]} old={t1-t0:.1f}s new={t2-t1:.2f}s")
