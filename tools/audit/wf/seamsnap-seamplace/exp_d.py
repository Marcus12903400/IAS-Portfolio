import json, math
from pathlib import Path
import numpy as np
from autodeck2 import sheets, seamplace, seamsnap, sheetjob
from autodeck2.config import load_config
S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/seamsnap-seamplace/axisrun")
opts = sheets.settings(load_config())
loops = sheets.read_fitted_dxf(S / "final_auto.dxf")[0]
refs = seamsnap.references_from_loops(loops, opts)
print("== reference census")
for pid in sorted(loops):
    rs = [r for r in refs if r.panel_id == pid]
    lines = [r for r in rs if r.kind == "line"]; arcs = [r for r in rs if r.kind == "arc"]
    total_segments = sum(len(l.vertices) for l in loops[pid])
    short = sum(1 for l in loops[pid] for i in range(len(l.vertices)) if abs(l.bulges[i]) < 1e-12 and math.hypot(*(l.xy[(i+1) % len(l.xy)] - l.xy[i])) < opts["seam_snap_min_ref_length_mm"])
    tiny_arcs = sum(1 for l in loops[pid] for i in range(len(l.vertices)) if abs(l.bulges[i]) >= 1e-12 and sheets.bulge_to_arc(l.xy[i], l.xy[(i+1) % len(l.xy)], float(l.bulges[i]))[1] < opts["seam_snap_min_ref_radius_mm"])
    print(f"panel {pid}: loops={len(loops[pid])} segments={total_segments} -> lines={len(lines)} arcs={len(arcs)} dropped short lines={short} dropped tiny arcs={tiny_arcs}; "
          f"line lengths {min((r.length_mm for r in lines), default=0):.1f}..{max((r.length_mm for r in lines), default=0):.0f}; arc radii {min((r.radius for r in arcs), default=0):.1f}..{max((r.radius for r in arcs), default=0):.0f}; "
          f"labels={sorted(set(r.label for r in rs))}")

print("\n== arc convention check on every real arc reference: point_at_parameter(0)==p0? (1)==p1? tangent at 0 matches sampled direction?")
worst0 = worst1 = worstt = 0.0; bad = []
for r in refs:
    if r.kind != "arc":
        continue
    d0 = float(np.hypot(*(r.point_at_parameter(0.0) - r.p0)))
    d1 = float(np.hypot(*(r.point_at_parameter(1.0) - r.p1)))
    # sampled direction near p0
    loop = next(l for l in loops[r.panel_id] if any(np.allclose(l.xy[i], r.p0) for i in range(len(l.xy))))
    i = next(i for i in range(len(loop.xy)) if np.allclose(loop.xy[i], r.p0))
    pts, src = sheets.sample_loop(loop, 1.0)
    seg_pts = pts[src == i]
    if len(seg_pts) >= 2:
        v = seg_pts[1] - seg_pts[0]; v = v / np.hypot(*v)
        t = r.tangent_at_parameter(0.0)
        ang = math.degrees(math.acos(min(1.0, abs(float(v @ t)))))
        same_way = float(v @ t) > 0
    else:
        ang = 0.0; same_way = True
    worst0 = max(worst0, d0); worst1 = max(worst1, d1); worstt = max(worstt, ang)
    if d1 > 1e-6 or ang > 0.5 or not same_way:
        bad.append((r.label, round(r.radius, 1), round(d1, 4), round(ang, 3), same_way))
print(f"arcs={sum(1 for r in refs if r.kind=='arc')} worst |P(0)-p0|={worst0:.2e} worst |P(1)-p1|={worst1:.2e} worst tangent angle={worstt:.4f} deg; anomalies={bad[:5]} ({len(bad)})")

print("\n== synthetic bulge signs/magnitudes through _loop_references")
for bulge in (0.2, -0.2, 0.9, -0.9, 1.0, -1.0, 1.5, -1.5, 3.0):
    loop = sheets.Loop(np.array([[0, 0, bulge], [100, 0, 0.0], [100, -500, 0.0], [0, -500, 0.0]], dtype=float))
    rs = seamsnap._loop_references(loop, 1, "t", 15.0, 10.0)
    arc = [r for r in rs if r.kind == "arc"]
    if not arc:
        print(f"bulge {bulge}: no arc ref"); continue
    a = arc[0]
    pts, src = sheets.sample_loop(loop, 1.0)
    sampled_mid = pts[src == 0][len(pts[src == 0]) // 2]
    print(f"bulge {bulge:+.1f}: radius={a.radius:.2f} sweep={math.degrees(a.sweep):.1f} |P(1)-p1|={np.hypot(*(a.point_at_parameter(1)-a.p1)):.2e} "
          f"|P(0.5)-sampled mid|={np.hypot(*(a.point_at_parameter(0.5)-sampled_mid)):.2f} centre={np.round(a.centre,1)}")

print("\n== numerical floors")
rng = np.random.default_rng(0)
worst = 0.0
for _ in range(20000):
    u = rng.normal(size=2); u /= np.hypot(*u)
    worst = max(worst, seamsnap._direction_angle_deg(u, u))
print(f"_direction_angle_deg(u,u) worst over 20000 random units: {worst:.2e} deg (floor _NO_CHANGE_DEG={seamsnap._NO_CHANGE_DEG})")
for v in ((-1.0, 0.0), (-1.0, -1e-18), (1.0, -1e-18), (0.0, -1.0), (-1e-18, 1.0)):
    print(f"direction_degrees{v} = {seamplace.direction_degrees(v)}")
print("angle_parameter wrap: arc from 170deg sweep 20deg, query at -175deg ->",
      seamsnap.Reference("arc", "t", 1, np.array([math.cos(math.radians(170)), math.sin(math.radians(170))]), np.array([math.cos(math.radians(190)), math.sin(math.radians(190))]), centre=np.zeros(2), radius=1.0, angles=(math.radians(170), math.radians(190))).angle_parameter(math.radians(-175)))
# degenerate loops
for name, verts in (("2-vertex", [[0,0,0],[100,0,0]]), ("repeated vertex", [[0,0,0],[0,0,0],[100,0,0],[100,100,0]]), ("repeated vertex with bulge", [[0,0,0.5],[0,0,0],[100,0,0],[100,100,0]])):
    try:
        rs = seamsnap._loop_references(sheets.Loop(np.array(verts, dtype=float)), 1, "t", 15.0, 10.0)
        print(f"{name}: {[(r.kind, round(r.length_mm,1)) for r in rs]}")
    except Exception as e:
        print(f"{name}: {type(e).__name__}: {e}")
