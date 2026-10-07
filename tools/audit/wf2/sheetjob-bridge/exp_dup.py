import json, math, types, shutil
from pathlib import Path
import numpy as np
from shapely import affinity
def cross2(a, b): return float(a[0]*b[1]-a[1]*b[0])
S = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/sheetjob-bridge")
src = S / "runs" / "axis"; run = S / "runs" / "axis_dup"
if run.exists(): shutil.rmtree(run)
shutil.copytree(src, run)
from autodeck_app import bridge
from autodeck2 import sheets, sheetjob, seamplace, seamsnap
from autodeck2.config import load_config
config = load_config(); options = sheets.settings(config)
view = types.SimpleNamespace(run_dir=run, sheet_cache={}, placements={}, lifters={})
frame, _ = sheetjob.resolve_frame(run, options)
along, across = seamsnap.master_directions(frame.axis)
loops, polys = bridge.panel_shapes(view, options)

# 1. Save the run's 4 old-schema seams through sheet_preview(save=True) so seams.json holds CUT coordinates (as the app would).
res = bridge.sheet_preview(run, {}, seams=[s.to_dict() for s in sheets.read_seams(run)], save=True)
cut = sheets.read_seams(run)
print("pieces with the 4 seams:", res["piece_count"], "sheets", res["summary"]["sheet_count"])
for s in cut:
    print("  ", s.seam_id, "cut", [round(v,1) for v in (s.x1, s.y1, s.x2, s.y2)], "len %.0f" % math.hypot(s.x2-s.x1, s.y2-s.y1), "mode", repr(s.mode), "panel", s.panel_id)

# 2. 3D-vs-flat extent: what seam_world_polylines would draw for each seam (all chords of the infinite line through the crossed panels)
for s in cut:
    unit = np.array([s.x2-s.x1, s.y2-s.y1]); unit /= np.hypot(*unit)
    crossed = seamplace.panels_crossed(s.x1, s.y1, s.x2, s.y2, polys, options, s.panel_id)
    mid = np.array([(s.x1+s.x2)/2, (s.y1+s.y2)/2])
    segs = seamplace.seam_through(mid, unit, polys, options, panel_ids=crossed)
    print(f"  {s.seam_id}: flat view draws {math.hypot(s.x2-s.x1, s.y2-s.y1):.0f} mm; 3D view draws {len(segs)} chord(s) totalling {sum(x['length_mm'] for x in segs):.0f} mm on panels {sorted(set(x['panel_id'] for x in segs))}")

# 3. Duplicate slide: hover an across seam 10 mm beside s1's line, on the far side of the console (end to end) and alongside it.
s1 = cut[0]
u = np.array([s1.x2-s1.x1, s1.y2-s1.y1]); u /= np.hypot(*u)
n = np.array([-u[1], u[0]])
mid1 = np.array([(s1.x1+s1.x2)/2, (s1.y1+s1.y2)/2])
for label, point in (("far side of console (end to end)", mid1 + u*1500 + n*10.0),
                     ("alongside s1 (overlapping extent)", mid1 + n*10.0),
                     ("far side, 30 mm off (beyond offset limit)", mid1 + u*1500 + n*30.0)):
    raw = seamplace.seam_through(point, across, polys, options)
    res = bridge.seam_hover(view, {}, "across", None, point_flat=point, with_world=False)
    def gap(sg):
        dx, dy = sg["x2"]-sg["x1"], sg["y2"]-sg["y1"]; L2 = dx*dx+dy*dy
        t = ((point[0]-sg["x1"])*dx + (point[1]-sg["y1"])*dy)/L2
        return (abs(t) if t < 0 else (t-1 if t > 1 else 0))*math.sqrt(L2)
    segs = res["segments"]
    if not segs or res.get("reason"):
        print(label, "->", res.get("reason")); continue
    i = min(range(len(segs)), key=lambda k: gap(segs[k])); sg = segs[i]
    r0 = min([x for x in raw if x["panel_id"] == sg["panel_id"]], key=lambda x: abs(gap(x)))
    # perpendicular distance of the settled chord from s1's infinite line
    d_before = abs(cross2(u, np.array([(r0["x1"]+r0["x2"])/2, (r0["y1"]+r0["y2"])/2]) - mid1))
    d_after = abs(cross2(u, np.array([(sg["x1"]+sg["x2"])/2, (sg["y1"]+sg["y2"])/2]) - mid1))
    snap = seamsnap.snap_seam(r0["x1"], r0["y1"], r0["x2"], r0["y2"], bridge.edge_references(view, options) + bridge.seam_references(view, options), options, direction_locked=True)
    print(f"{label}: chord on panel {sg['panel_id']} len {sg['length_mm']} mm; distance from s1's line before {d_before:.2f} mm -> after settle {d_after:.4f} mm; note {snap.note!r}")
    if d_after < 1e-6:
        # place it as the page would and re-plan: does it cut anything new?
        new = {"x1": sg["x1"], "y1": sg["y1"], "x2": sg["x2"], "y2": sg["y2"], "panel_id": sg["panel_id"], "mode": "across", "angle_deg": None, "snap": True, "raw": [sg["x1"], sg["y1"], sg["x2"], sg["y2"]]}
        res2 = bridge.sheet_preview(run, {}, seams=[s.to_dict() for s in cut] + [new], save=False)
        print(f"   placed: seam_count {res2['seam_count']}, pieces {res2['piece_count']} (was {len(cut)} seams / same pieces?), new seam listed as {res2['seams'][-1]['length_mm']} mm, note {res2['seams'][-1]['snap_note']!r}")

# 4. Bow-up overlap of panel 2 (drawn back at placed - nest_offset) with panel 1
meta = json.loads((run/"run.json").read_text(encoding="utf-8"))
off = {p["panel_id"]: p["placement"]["nest_offset_mm"] for p in meta["panels"]}
for pid in (2, 4, 5):
    back = affinity.translate(polys[pid], -off[pid][0], -off[pid][1])
    inter = back.intersection(polys[1])
    print(f"panel {pid} at boat-plan position: overlap with panel 1 = {inter.area:.0f} mm2, overlap bbox {None if inter.is_empty else [round(v,1) for v in inter.bounds]}, panel {pid} area {back.area:.0f}")

# 5. Server-side seam id assignment after a removal: page holds [s1, s3] and places a new seam with no id.
rows = [cut[0].to_dict(), cut[2].to_dict(), {"x1": 700.0, "y1": -1000.0, "x2": 700.0 + across[0]*1500, "y2": -1000.0 + across[1]*1500, "panel_id": 1, "mode": "across", "angle_deg": None, "snap": True}]
res3 = bridge.sheet_preview(run, {}, seams=rows, save=False)
print("ids assigned by sheet_preview for [s1, s3, new]:", [s["seam_id"] for s in res3["seams"]])
