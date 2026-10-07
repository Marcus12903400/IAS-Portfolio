"""Edge case: the hover settles onto seams read from seams.json AS STORED
(bridge.seam_references), while apply_snap offers the CORRECTED positions of
the same seams (pool built from its own results).  With the run's old-schema
seams.json (never re-saved, so the stored lines are the raw drawings, 0.2-1.3
deg off square) the two pools differ.  Hover short across chords next to seam
s2 (0.2 deg off, 0.98 mm correction) and see whether the preview and the
placed seam land on different lines.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN, dump, line_distance, load, segment_under

ctx = load()
bridge, view, options, frame, loops = ctx["bridge"], ctx["view"], ctx["options"], ctx["frame"], ctx["loops"]
polygons, seamsnap, sheetjob, sheets, seamplace = ctx["polygons"], ctx["seamsnap"], ctx["sheetjob"], ctx["sheets"], ctx["seamplace"]
axis = np.asarray(frame.axis, float)
Seam = sheets.Seam
along, across = ctx["masters"]

existing = sheets.read_seams(RUN)
snapped, results, _w = sheetjob.apply_snap(existing, loops, axis, options)
print("stored (raw) vs corrected lines of the run's seams:")
for a, b, r in zip(existing, snapped, results):
    print(f"  {a.seam_id}: stored ({a.x1:.2f},{a.y1:.2f})-({a.x2:.2f},{a.y2:.2f}) corrected moved {r.moved_mm:.2f} mm, {r.note!r}")
refs_hover = bridge.seam_references(view, options)
print("hover seam references (from seams.json as stored):",
      [(r.label, [round(float(v), 2) for v in (*r.p0, *r.p1)]) for r in refs_hover])

rows = []
for seam, fixed in zip(existing, snapped):
    u = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1]); u /= np.hypot(*u)
    mode = "across" if abs(float(np.dot(u, across))) > abs(float(np.dot(u, along))) else "along"
    master = across if mode == "across" else along
    normal = np.array([-master[1], master[0]])
    mid = np.array([(seam.x1 + seam.x2) / 2, (seam.y1 + seam.y2) / 2])
    # hover points that CONTINUE the seam past its end (end to end, not alongside), 10 mm off its line
    for end in (np.array([seam.x1, seam.y1]), np.array([seam.x2, seam.y2])):
        away = (end - mid); away /= np.hypot(*away)
        for d_along in (40.0, 120.0, 250.0):
            for d_side in (-10.0, 10.0):
                pt = end + away * d_along + normal * d_side
                r = bridge.seam_hover(view, {}, mode, None, point_flat=pt.tolist(), with_world=False)
                segs = r.get("segments") or []
                under = segment_under(r.get("point_flat"), segs)
                if under < 0:
                    continue
                s = segs[under]
                placed = Seam.from_dict({**s, "seam_id": "new", "mode": mode, "snap": True,
                                         "raw": [s["x1"], s["y1"], s["x2"], s["y2"]]})
                outs, res, _ = sheetjob.apply_snap(existing + [placed], loops, axis, options)
                cut = outs[-1]
                d_stored = line_distance((seam.x1, seam.y1), s)            # preview line vs stored (raw) seam line
                d_fixed = line_distance((fixed.x1, fixed.y1), s)           # preview line vs corrected seam line
                d_click = max(math.hypot(cut.x1 - s["x1"], cut.y1 - s["y1"]), math.hypot(cut.x2 - s["x2"], cut.y2 - s["y2"]))
                rows.append(dict(seam=seam.seam_id, mode=mode, hover=pt.round(1).tolist(), chord_len=s["length_mm"],
                                 preview_gap_to_stored_line=round(d_stored, 3), preview_gap_to_corrected_line=round(d_fixed, 3),
                                 click_moves_mm=round(d_click, 3), click_note=res[-1].note))
moved = [r for r in rows if r["click_moves_mm"] > 1e-6]
print(f"{len(rows)} hovers next to the old seams; {len(moved)} move again on the click")
for r in moved[:12]:
    print("  ", json.dumps(r))
settled_on_raw = [r for r in rows if r["preview_gap_to_stored_line"] < 1e-6]
print(f"{len(settled_on_raw)} previews settled onto a seam's STORED (uncorrected) line")
for r in settled_on_raw[:8]:
    print("  ", json.dumps(r))
dump("oldseam_refs.json", rows)
