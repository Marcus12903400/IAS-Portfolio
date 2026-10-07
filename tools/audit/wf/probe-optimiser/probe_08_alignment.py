"""Which proposed cuts sit on a fitted edge, how far off, whether the corrector
moved them, and the alignment term recomputed by hand (needs out/main_result.json
and out/main_plan_from_disk.json from probe_01_main.py).

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_08_alignment.py"
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import SCRATCH  # noqa: E402
from autodeck2 import seamplan, seamsnap, sheets, sheetjob  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

work = SCRATCH / "runs" / "main"
config = load_config()
options = sheets.settings(config)
frame, _ = sheetjob.resolve_frame(work, options)
masters = seamsnap.master_directions(frame.axis)
rotation = sheets.sheet_transform(frame.axis)
loops, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(work))
panels = seamplan._read_panels(loops, rotation, options, seamplan._nest_offsets(work))
p1 = [p for p in panels if p.panel_id == 1][0]
for direction in (seamplan.ALONG, seamplan.ACROSS):
    edges = seamplan._panel_edges(p1, direction, masters, options)
    print(f"panel 1 fitted {direction} edges (sheet-frame offsets): {[round(e, 1) for e in edges]}")
disk = json.loads((SCRATCH / "out" / "main_plan_from_disk.json").read_text(encoding="utf-8"))
reach = float(options["seam_snap_offset_mm"])
print("\nseam, dir, as-cut offset, nearest fitted edge, gap mm, credited(<=25), chord mm, corrector applied, moved mm, note")
for s, snap in zip(disk["seams_as_cut"], disk["seam_snaps"]):
    if s["panel_id"] != 1:
        continue
    along, across = masters
    normal = across if s["mode"] == "along" else along
    offset = float(np.dot(np.array([(s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2]), normal))
    edges = seamplan._panel_edges(p1, s["mode"], masters, options)
    nearest = min(edges, key=lambda e: abs(offset - e))
    gap = abs(offset - nearest)
    print("  ", (s["seam_id"], s["mode"], round(offset, 1), round(nearest, 1), round(gap, 1), gap <= reach,
                round(seamplan._chord_mm(p1, s["mode"], offset, masters), 1), snap["applied"], snap["moved_mm"], snap["note"]))
placed = []
for s in disk["seams_as_cut"]:
    panel = [p for p in panels if p.panel_id == s["panel_id"]][0]
    along, across = masters
    normal = across if s["mode"] == "along" else along
    offset = float(np.dot(np.array([(s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2]), normal))
    placed.append(seamplan._placed(panel, s["mode"], offset, masters, options))
print("alignment by hand:", round(seamplan._alignment(placed, options), 4), " (report said 0.7514)")
for p in placed:
    print(f"   P{p.panel_id} {p.direction} off={p.offset_mm:.1f} chord={p.chord_mm:.1f} edge_gap={p.edge_gap_mm:.1f} credited={p.edge_gap_mm <= reach}")
outer, holes = sheets.classify_loops(loops[1], 1.0)
console = holes[0]
xy, bul = console.xy, console.bulges
n = len(xy)
along, across = masters
print("\nconsole straight segments >= 15 mm within 2 deg of a master (sheet-frame offset, length):")
for i in range(n):
    if abs(bul[i]) > 1e-12:
        continue
    p0, p1_ = xy[i], xy[(i + 1) % n]
    span = p1_ - p0
    L = float(np.hypot(*span))
    if L < 15:
        continue
    u = span / L
    for name, want, nrm in (("along", along, across), ("across", across, along)):
        if abs(float(np.dot(u, want))) >= np.cos(np.radians(2.0)):
            print(f"   {name}: offset {float(np.dot((p0 + p1_) / 2, nrm)):8.1f}  length {L:7.1f}")
