import sys, json, math, types, time
from pathlib import Path
import numpy as np
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheetjob-bridge")
run = S / "runs" / "axis"
from autodeck_app import bridge
from autodeck2 import sheets, sheetjob, seamplace, seamsnap
from autodeck2.config import load_config
config = load_config()
options = sheets.settings(config)
view = types.SimpleNamespace(run_dir=run, sheet_cache={}, placements={}, lifters={})
frame, warns = sheetjob.resolve_frame(run, options)
print("frame", frame.to_dict(), warns)
loops, polys = bridge.panel_shapes(view, options)
for pid, poly in polys.items():
    print("panel", pid, "bounds", [round(v,1) for v in poly.bounds], "holes", len(poly.interiors))
along, across = seamsnap.master_directions(frame.axis)
print("along", along, "across", across)
refs = bridge.edge_references(view, options)
print("edge refs", len(refs))
# across-running line references on panels other than 1
t0 = time.perf_counter()
cands = [r for r in refs if r.kind == "line" and r.panel_id != 1 and seamsnap._direction_angle_deg(r.unit, across) <= 0.5]
print("across-running line refs on panels != 1:", len(cands))
for r in cands:
    print("  ", r.label, "panel", r.panel_id, "len %.1f" % r.length_mm, "mid", np.round(r.midpoint,1))
# For each candidate, find a hover point on panel 1 along the across line through the ref midpoint, offset 10 mm sideways
p1 = polys[1]
from shapely.geometry import Point, LineString
hits = []
for r in cands:
    base = r.midpoint
    normal = np.array([-across[1], across[0]])
    start = base + normal * 10.0   # 10 mm beside the reference line
    # walk along the across direction to find a point inside panel 1
    found = None
    for t in np.arange(-3000, 3000, 20.0):
        q = start + across * t
        if p1.contains(Point(q)):
            found = q; break
    if found is None:
        print("no panel-1 point on the line through", r.label); continue
    raw = seamplace.seam_through(found, across, polys, options)
    res = bridge.seam_hover(view, {}, "across", None, point_flat=found, with_world=False)
    segs = res["segments"]
    # which chord brackets the point
    def gap(s):
        dx, dy = s["x2"]-s["x1"], s["y2"]-s["y1"]; L2 = dx*dx+dy*dy
        t = ((found[0]-s["x1"])*dx + (found[1]-s["y1"])*dy)/L2
        return (abs(t) if t < 0 else (t-1 if t > 1 else 0))*math.sqrt(L2)
    under = min(range(len(segs)), key=lambda i: gap(segs[i]))
    s = segs[under]; r0 = [x for x in raw if x["panel_id"] == s["panel_id"]]
    r0 = min(r0, key=lambda x: abs((x["x1"]+x["x2"])/2 - (s["x1"]+s["x2"])/2) + abs((x["y1"]+x["y2"])/2 - (s["y1"]+s["y2"])/2))
    shift = np.hypot((s["x1"]+s["x2"])/2 - (r0["x1"]+r0["x2"])/2, (s["y1"]+s["y2"])/2 - (r0["y1"]+r0["y2"])/2)
    snap = seamsnap.snap_seam(r0["x1"], r0["y1"], r0["x2"], r0["y2"], refs + bridge.seam_references(view, options), options, direction_locked=True)
    print(f"ref {r.label} ({r.panel_id}) mid {np.round(r.midpoint,1)} -> hover pt {np.round(found,1)}: chords={[ (x['panel_id'], round(x['length_mm'])) for x in segs]} under=panel {s['panel_id']} len {s['length_mm']} slid {shift:.2f} mm; snap note: {snap.note!r} applied={snap.applied}")
print("elapsed %.2fs" % (time.perf_counter()-t0))
