import sys
from pathlib import Path
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/nesting")
from autodeck2 import nesting, sheetjob, sheets
from autodeck2.config import load_config
work = S / "runs" / "axis_seams_optimiser_run"
base = load_config()
got = {}; real = nesting.nest
def spy(pieces, rot, opts):
    got["args"] = (pieces, rot, opts); return real(pieces, rot, opts)
nesting.nest = spy
try: sheetjob.plan(work, base, write_files=False)
finally: nesting.nest = real
pieces, rot, opts = got["args"]
print("optimiser 6-seam piece set:", [(p.piece_id, round(p.area_mm2/1e4)) for p in sorted(pieces, key=lambda p: -p.area_mm2)][:14])
for spacing in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0):
    for step in (5.0,):
        o = {**opts, "part_spacing_mm": spacing, "nest_step_mm": step}
        sl, summ, warn = nesting.nest(pieces, rot, o)
        print(f"  spacing {spacing:4.1f} step {step}: sheets={summ['sheet_count']} util={summ['utilisation']} per-sheet pieces={[[p.piece_id for p in s.placements] for s in sl]}")
