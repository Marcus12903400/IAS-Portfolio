"""Probe (4): straightening the real run's four OLD-schema hand seams, and two
free-drawn seams 25 deg and 19 deg off the axis.

Reads the staged copy ./runs/axis only (apply_snap writes nothing).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RUN, REAL_RUN, angle_between_deg, dump

for p in ("D:/AutoDeck/engine-v1/src", "D:/AutoDeck/engine", "D:/AutoDeck/app"):
    sys.path.insert(0, p)
from autodeck2 import seamplace, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

Seam = sheets.Seam
config = load_config()
options = sheets.settings(config)
frame, warnings = sheetjob.resolve_frame(RUN, options)
axis = np.asarray(frame.axis, float)
along, across = seamsnap.master_directions(axis)
loops = sheets.read_fitted_dxf(sheetjob.source_dxf(RUN))[0]
polygons = seamplace.panel_polygons(loops, options)
print("axis", axis, "deg", math.degrees(math.atan2(axis[1], axis[0])))

seams = sheets.read_seams(RUN)
assert (RUN / "seams.json").read_bytes() == (REAL_RUN / "seams.json").read_bytes()
print("old-schema seams:", [(s.seam_id, s.raw, s.mode, s.snap, s.panel_id) for s in seams])


def unit_of(x1, y1, x2, y2):
    d = np.array([x2 - x1, y2 - y1], float)
    return d / np.hypot(*d)


def nearest_master(u):
    a, b = angle_between_deg(u, along), angle_between_deg(u, across)
    return ("along", a) if a <= b else ("across", b)


def describe(seam: Seam, out: Seam, res):
    u0 = unit_of(*seam.drawn)
    u1 = unit_of(out.x1, out.y1, out.x2, out.y2)
    m0, a0 = nearest_master(u0)
    m1, a1 = nearest_master(u1)
    moved = max(math.hypot(out.x1 - seam.drawn[0], out.y1 - seam.drawn[1]),
                math.hypot(out.x2 - seam.drawn[2], out.y2 - seam.drawn[3]))
    return dict(id=seam.seam_id, kind=res.kind, applied=res.applied, label=res.reference_label, note=res.note,
                before=dict(master=m0, off_deg=a0), after=dict(master=m1, off_deg=a1),
                reported_angle_change=res.angle_change_deg, reported_moved=res.moved_mm, moved_mm=moved,
                len_before=math.hypot(seam.drawn[2] - seam.drawn[0], seam.drawn[3] - seam.drawn[1]),
                len_after=math.hypot(out.x2 - out.x1, out.y2 - out.y1),
                out=[out.x1, out.y1, out.x2, out.y2], raw_after=out.raw, warnings=list(res.warnings))


first, results, warns = sheetjob.apply_snap(seams, loops, axis, options)
rows = [describe(s, o, r) for s, o, r in zip(seams, first, results)]
for r in rows:
    print(json.dumps(r, default=float))
print("warnings:", warns)

# idempotent?  Snap the output again (as every re-plan does) and compare bit for bit.
second, results2, _ = sheetjob.apply_snap(first, loops, axis, options)
third, _r3, _ = sheetjob.apply_snap(second, loops, axis, options)
same12 = [a.to_dict() == b.to_dict() for a, b in zip(first, second)]
same23 = [a.to_dict() == b.to_dict() for a, b in zip(second, third)]
print("idempotent 1->2:", same12, " 2->3:", same23)
for a, b in zip(first, second):
    if a.to_dict() != b.to_dict():
        print("  drift", a.seam_id, [a.x1 - b.x1, a.y1 - b.y1, a.x2 - b.x2, a.y2 - b.y2])
# the production entry point reading the file itself
prod, _pr, _pw = sheetjob.snapped_seams(RUN, config)
print("snapped_seams(file) == apply_snap(read_seams):", [a.to_dict() == b.to_dict() for a, b in zip(prod, first)])

# compare with what the app wrote for the same four drawings in seams_previous.json
prev = json.loads((RUN / "seams_previous.json").read_text())["seams"]
for a in first:
    b = next((p for p in prev if p["seam_id"] == a.seam_id), None)
    if b:
        d = max(abs(a.x1 - b["x1"]), abs(a.y1 - b["y1"]), abs(a.x2 - b["x2"]), abs(a.y2 - b["y2"]))
        print(f"  vs seams_previous.json {a.seam_id}: max coordinate difference {d:.3e} mm")

# does each old seam still cut?  Its drawn stroke vs the panel it crosses.
for s, o in zip(seams, first):
    drawn = LineString([(o.x1, o.y1), (o.x2, o.y2)])
    crossed = seamplace.panels_crossed(o.x1, o.y1, o.x2, o.y2, polygons, options, o.panel_id)
    ends = {pid: [polygons[pid].boundary.distance(Point(o.x1, o.y1)), polygons[pid].boundary.distance(Point(o.x2, o.y2))]
            for pid in crossed}
    print(f"  {o.seam_id} crosses panels {crossed}; endpoint gaps to outline {ends}")

# ---- a seam drawn 25 deg and 19 deg off the axis, free-hand (mode "", raw None, panel None)
rot_cases = []
mid = np.array([300.0, -650.0])          # open deck on panel 1, aft of the console
full = seamplace.seam_through(mid, along, polygons, options, panel_ids=[1])
chord = min(full, key=lambda s: abs(s["along_mm"]))
L = chord["length_mm"]
print(f"\nalong chord through {mid.tolist()}: {L:.1f} mm, from ({chord['x1']:.1f},{chord['y1']:.1f}) to ({chord['x2']:.1f},{chord['y2']:.1f})")
for deg in (25.0, 19.0, 15.0, 44.0, 46.0):
    t = math.radians(deg)
    u = np.array([along[0] * math.cos(t) - along[1] * math.sin(t), along[0] * math.sin(t) + along[1] * math.cos(t)])
    # the user's stroke: the same length as the along chord, spun about its midpoint; then clipped to the
    # panel the way a stroke dragged edge to edge would be
    half = L / 2.0
    stroke = seamplace.seam_through(mid, u, polygons, options, panel_ids=[1])
    stroke = min(stroke, key=lambda s: abs(s["along_mm"]))
    drawn = Seam(f"d{deg:g}", stroke["x1"], stroke["y1"], stroke["x2"], stroke["y2"], panel_id=None)
    (out,), (res,), w = sheetjob.apply_snap([drawn], loops, axis, options)
    u1 = unit_of(out.x1, out.y1, out.x2, out.y2)
    m1, a1 = nearest_master(u1)
    boundary = polygons[1].boundary
    gaps = [boundary.distance(Point(out.x1, out.y1)), boundary.distance(Point(out.x2, out.y2))]
    # the chord the squared line really spans through its own midpoint
    omid = np.array([(out.x1 + out.x2) / 2, (out.y1 + out.y2) / 2])
    span = seamplace.seam_through(omid, u1, polygons, options, panel_ids=[1])
    span = min(span, key=lambda s: abs(s["along_mm"])) if span else None
    inside = polygons[1].buffer(1e-6).contains(LineString([(out.x1, out.y1), (out.x2, out.y2)]))
    # does it still cut the whole panel?
    outer, holes = sheets.classify_loops(loops[1], float(options["sample_step_mm"]))
    pieces, pw = sheets.split_panel(1, outer, holes, [out], options)
    row = dict(drawn_deg_off_axis=deg, stroke_len=stroke["length_mm"], kind=res.kind, applied=res.applied,
               label=res.reference_label, note=res.note, after_master=m1, after_off_deg=a1,
               angle_change=res.angle_change_deg, moved=res.moved_mm, endpoint_gap_to_outline_mm=gaps,
               squared_len=math.hypot(out.x2 - out.x1, out.y2 - out.y1),
               full_chord_len=(span["length_mm"] if span else None),
               stops_short_by_mm=(span["length_mm"] - math.hypot(out.x2 - out.x1, out.y2 - out.y1)) if span else None,
               seam_inside_panel=inside, pieces_after_cut=len(pieces), split_warnings=pw,
               out=[out.x1, out.y1, out.x2, out.y2])
    rot_cases.append(row)
    print(json.dumps(row, default=float))

dump("old_seams_snap.json", dict(rows=rows, idempotent=dict(s12=same12, s23=same23), rot_cases=rot_cases,
                                 warnings=warns))
