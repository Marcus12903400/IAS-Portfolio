"""Why the corrector did not slide the alignment-credited seams onto their edges:
the edge angles versus the locked-direction refine window in seamsnap._refine_position.
Needs out/main_plan_from_disk.json from probe_01_main.py.

Re-run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_09_refine.py"
"""

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import SCRATCH  # noqa: E402
from autodeck2 import seamsnap, sheets, sheetjob  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

work = SCRATCH / "runs" / "main"
config = load_config()
options = sheets.settings(config)
frame, _ = sheetjob.resolve_frame(work, options)
along, across = seamsnap.master_directions(frame.axis)
loops, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(work))
names = {0: "outer", 1: "console", 2: "round", 3: "rect stbd-aft", 4: "rect stbd-fwd", 5: "rect port-aft", 6: "rect port-fwd"}
outer, holes = sheets.classify_loops(loops[1], 1.0)
print("fitted straight segments of panel 1 near the credited offsets (sheet-frame offset, length, angle off the master):")
for li, loop in enumerate([outer, *holes]):
    xy, bul = loop.xy, loop.bulges
    n = len(xy)
    for i in range(n):
        if abs(bul[i]) > 1e-12:
            continue
        p0, p1 = xy[i], xy[(i + 1) % n]
        span = p1 - p0
        L = float(np.hypot(*span))
        if L < 15:
            continue
        u = span / L
        for name, want, nrm in (("along", along, across), ("across", across, along)):
            ang = math.degrees(math.acos(min(1.0, abs(float(np.dot(u, want))))))
            off = float(np.dot((p0 + p1) / 2, nrm))
            if ang <= 2.0 and any(abs(off - target) < 1 for target in (1532.0, 436.2, -427.0, 434.8, 435.6)):
                print(f"   {names.get(li, li):<14} {name:<6} offset {off:8.1f} length {L:6.1f} mm  {ang:.3f} deg off the master")
disk = json.loads((SCRATCH / "out" / "main_plan_from_disk.json").read_text(encoding="utf-8"))
refs = seamsnap.references_from_loops(loops, options)
for sid in ("auto-1-across-2", "auto-1-along-2", "auto-1-along-1"):
    s = [x for x in disk["seams_as_cut"] if x["seam_id"] == sid][0]
    p = np.array([s["x1"], s["y1"]])
    q = np.array([s["x2"], s["y2"]])
    unit = (q - p) / np.hypot(*(q - p))
    half = float(np.hypot(*(q - p))) / 2
    limit = min(seamsnap._REFINE_ANGLE_DEG, math.degrees(math.atan2(seamsnap._REFINE_MAX_GAP_MM, half)))
    refined = seamsnap._refine_position(p, q, unit, refs, options)
    print(f"{sid}: seam length {2 * half:.0f} mm -> corrector parallel window {limit:.3f} deg; _refine_position -> "
          f"{'None (no slide)' if refined is None else (refined[2].label, round(refined[3], 2))}")
