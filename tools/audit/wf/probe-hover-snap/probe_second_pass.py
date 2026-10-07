"""Probe: what the CLICK does to a hovered seam.

For a grid of hover points (along + across + 30 deg), take the chord the page
would preview (settled, the default), then
  (a) run the corrector on it once more exactly as apply_snap does for a
      placed seam (direction locked) -> does it move again?  by how much?
  (b) count the chords the same line has in the hovered panel: split_panel
      cuts the WHOLE line, the preview shows one chord;
  (c) cut the panel with just this seam (sheets.split_panel) -> pieces, and
      any sliver (min oriented extent < 20 mm) the single seam produced.
Writes out/second_pass.json
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN, dump, grid_inside, line_distance, load, segment_under

ctx = load()
bridge, view, options, frame = ctx["bridge"], ctx["view"], ctx["options"], ctx["frame"]
loops, polygons = ctx["loops"], ctx["polygons"]
seamplace, seamsnap, sheetjob, sheets = ctx["seamplace"], ctx["seamsnap"], ctx["sheetjob"], ctx["sheets"]
axis = np.asarray(frame.axis, float)
Seam = sheets.Seam
rotation = sheets.sheet_transform(axis)
step = float(options["sample_step_mm"])
REFS = bridge.edge_references(view, options) + bridge.seam_references(view, options)
MODES = [("along", None), ("across", None), ("angle", 30.0)]
UNITS = {m: seamplace.direction_for(axis, m[0], m[1]) for m in MODES}
PITCH = {1: 50.0, 2: 15.0, 3: 15.0, 4: 15.0, 5: 10.0}
QUICK = "--quick" in sys.argv
if QUICK:
    PITCH = {1: 150.0, 2: 40.0, 3: 40.0, 4: 40.0, 5: 25.0}

classified = {pid: sheets.classify_loops(loops[pid], step) for pid in loops}
counts = Counter()
second_moves = []
extra_chords = defaultdict(list)
sliver_cases = []
multi_cases = []
t0 = time.perf_counter()
n = 0
for pid, poly in sorted(polygons.items()):
    pts = grid_inside(poly, PITCH[pid])
    for pt in pts:
        for mode in MODES:
            n += 1
            r = bridge.seam_hover(view, {}, mode[0], mode[1], point_flat=list(pt), with_world=False)
            segs = r.get("segments") or []
            under = segment_under(r.get("point_flat"), segs)
            if under < 0:
                counts[(mode[0], "no_preview")] += 1
                continue
            s = segs[under]
            if s["panel_id"] != pid:
                counts[(mode[0], "other_panel")] += 1
                continue
            counts[(mode[0], "hovers")] += 1
            # (a) second pass
            res = seamsnap.snap_seam(s["x1"], s["y1"], s["x2"], s["y2"], REFS, options, direction_locked=True)
            if res.applied and res.moved_mm > 1e-9:
                counts[(mode[0], "moves_again_on_click")] += 1
                second_moves.append(dict(panel=pid, mode=mode[0], point=list(pt), moved_mm=res.moved_mm, note=res.note,
                                         chord=[s["x1"], s["y1"], s["x2"], s["y2"]], length=s["length_mm"]))
            # (b) chords of the same line in this panel
            mid = np.array([(s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2])
            chords = seamplace.seam_through(mid, UNITS[mode], polygons, options, panel_ids=[pid])
            others = [c for c in chords if abs(c["along_mm"]) > 1e-6 or abs(c["length_mm"] - s["length_mm"]) > 0.2]
            # the previewed chord itself has along_mm ~ 0 from its own middle
            others = [c for c in chords if math.hypot((c["x1"] + c["x2"]) / 2 - mid[0], (c["y1"] + c["y2"]) / 2 - mid[1]) > 0.5]
            if others:
                counts[(mode[0], "line_has_other_chords_in_panel")] += 1
                extra_chords[(pid, mode[0])].append(sum(c["length_mm"] for c in others))
                if len(multi_cases) < 400:
                    multi_cases.append(dict(panel=pid, mode=mode[0], point=list(pt), shown_len=s["length_mm"],
                                            other_chords=[round(c["length_mm"], 1) for c in others]))
            # (c) cut with just this seam
            seam = Seam("h", s["x1"], s["y1"], s["x2"], s["y2"], panel_id=pid, mode=mode[0], angle_deg=mode[1],
                        raw=(s["x1"], s["y1"], s["x2"], s["y2"]))
            outer, holes = classified[pid]
            pieces, pw = sheets.split_panel(pid, outer, holes, [seam], options)
            counts[(mode[0], f"pieces_{len(pieces)}")] += 1
            sizes = [sheets.oriented_extent(p, rotation, step) for p in pieces]
            slivers = [(p.piece_id, round(w, 1), round(l, 1), round(p.area_mm2)) for p, (w, l) in zip(pieces, sizes) if min(w, l) < 20.0]
            if slivers:
                counts[(mode[0], "single_seam_makes_sliver")] += 1
                sliver_cases.append(dict(panel=pid, mode=mode[0], point=list(pt), shown_len=s["length_mm"],
                                         chord=[s["x1"], s["y1"], s["x2"], s["y2"]], pieces=len(pieces), slivers=slivers,
                                         warnings=pw))
            if pw:
                counts[(mode[0], "split_warning")] += 1
        if n % 600 == 0:
            print(f"  {n} hovers, panel {pid}, {time.perf_counter() - t0:.0f} s", flush=True)

summary = {
    "counts": {"|".join(map(str, k)): v for k, v in sorted(counts.items())},
    "second_moves": dict(n=len(second_moves), max=max((m["moved_mm"] for m in second_moves), default=0.0),
                         examples=sorted(second_moves, key=lambda m: -m["moved_mm"])[:15]),
    "extra_chords_mm": {f"panel{pid}|{mode}": dict(n=len(v), median=float(np.median(v)), max=float(np.max(v)))
                        for (pid, mode), v in extra_chords.items()},
    "sliver_cases": dict(n=len(sliver_cases), by_panel=dict(Counter(c["panel"] for c in sliver_cases)),
                         examples=sliver_cases[:30]),
    "multi_examples": multi_cases[:30],
}
dump(f"second_pass{'_quick' if QUICK else ''}.json", summary)
print(json.dumps({k: v for k, v in summary.items() if k != "multi_examples"}, indent=1, default=float)[:6000])
print("done", round(time.perf_counter() - t0), "s")
