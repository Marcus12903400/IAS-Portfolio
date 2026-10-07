import json, math
from pathlib import Path
import numpy as np
from shapely.geometry import Point, LineString
from autodeck2 import sheets, seamplace, seamsnap, sheetjob
from autodeck2.config import load_config
S = Path("C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/seamsnap-seamplace/axisrun")
opts = sheets.settings(load_config())
loops = sheets.read_fitted_dxf(S / "final_auto.dxf")[0]
polys = seamplace.panel_polygons(loops, opts)
frame = sheetjob.boat_axis(S)
axis = frame.axis
along, across = seamsnap.master_directions(axis)
print("== direction_for table (axis angle %.4f deg)" % math.degrees(math.atan2(axis[1], axis[0])))
for mode, ang in [("along", None), ("across", None), ("angle", 0), ("angle", 30), ("angle", 90), ("angle", 150), ("angle", -30), ("angle", 180), ("angle", 270), ("angle", 360), ("angle", 90.0000001), ("angle", None), ("angle", float("nan")), ("angle", float("inf")), ("bogus", None), ("ALONG", None)]:
    d = seamplace.direction_for(axis, mode, ang)
    extra = ""
    if d is not None:
        extra = f" deg={seamplace.direction_degrees(d):.6f} off-along={seamsnap._direction_angle_deg(d, along):.6f} ccw(cross(along,d)>0)={seamsnap._cross(along, d) > 0}"
    print(f"  {mode!r:9} {ang!s:>12}: {None if d is None else np.round(d, 9)}{extra}")
print("  axis None:", seamplace.direction_for(None, "along"), " zero axis:", seamplace.direction_for([0, 0], "along"), " 1-element axis:", seamplace.direction_for([1.0], "along"))
print("  across vs transverse_axis in run.json:", json.load(open(S / "run.json"))["teak"]["frame"].get("transverse_axis"), "across=", across)

print("\n== dict input to snap_seams")
try:
    seamsnap.snap_seams([{"x1": 0, "y1": 0, "x2": 100, "y2": 0}], [], opts, axis=axis)
    print("  dict accepted")
except Exception as e:
    print(f"  {type(e).__name__}: {e}")
try:
    seamsnap.snap_seams([(0, 0, 100, 0)], [], opts, axis=axis); print("  tuple accepted")
except Exception as e:
    print(f"  tuple {type(e).__name__}: {e}")

print("\n== Seam(mode='angle', angle_deg=None) through _aimed")
s = sheets.Seam("t", 0, 0, 0, 500, panel_id=1, mode="angle", angle_deg=None)
aimed, warn = sheetjob._aimed(s, axis)
u = np.array([aimed[2] - aimed[0], aimed[3] - aimed[1]]); u /= np.hypot(*u)
print(f"  aimed direction off-along = {seamsnap._direction_angle_deg(u, along):.6f} deg, warning={warn!r}, mode words: {sheetjob._mode_words(s)!r}")

print("\n== 45 degree tie and capture widths")
for capture in (20.0, 45.0, 60.0):
    o = {**opts, "seam_axis_snap_deg": capture}
    base = math.degrees(math.atan2(along[1], along[0]))
    for off in (44.999, 45.0, 45.001, 19.99, 20.01):
        t = math.radians(base + off); d = np.array([math.cos(t), math.sin(t)])
        r = seamsnap.snap_seam(0, 0, d[0] * 1000, d[1] * 1000, [], o, axis=axis)
        print(f"  capture {capture} drawn {off:+.3f} deg off along -> kind={r.kind!r} angle_change={r.angle_change_deg:.3f} moved={r.moved_mm:.1f}")

print("\n== axis capture keeps length, swings ends: 2000 mm seam drawn 20 deg off across")
base = math.degrees(math.atan2(across[1], across[0]))
t = math.radians(base + 19.9); d = np.array([math.cos(t), math.sin(t)])
r = seamsnap.snap_seam(-d[0] * 1000, -d[1] * 1000, d[0] * 1000, d[1] * 1000, [], opts, axis=axis)
print(f"  kind={r.kind} length after={math.hypot(r.x2-r.x1, r.y2-r.y1):.6f} end moved={r.moved_mm:.1f} mm note={r.note!r}")

print("\n== grain override re-aim: a hovered across chord on panel 1, then grain_angle_deg = axis+10")
poly = polys[1]
pt = poly.representative_point()
chords = seamplace.seam_through((pt.x, pt.y), across, polys, opts, panel_ids=[1])
c = max(chords, key=lambda s: s["length_mm"])
seam = sheets.Seam("h", c["x1"], c["y1"], c["x2"], c["y2"], panel_id=1, mode="across", raw=(c["x1"], c["y1"], c["x2"], c["y2"]))
for delta in (0.0, 3.0, 10.0):
    o = {**opts, "grain_angle_deg": math.degrees(math.atan2(axis[1], axis[0])) + delta}
    fr, w = sheetjob.resolve_frame(S, o)
    out, res, warns = sheetjob.apply_snap([seam], loops, fr.axis, o)
    s2 = out[0]
    ends = [Point(s2.x1, s2.y1), Point(s2.x2, s2.y2)]
    print(f"  delta {delta:+.0f}: note={res[0].note!r} ends outside panel: {[not poly.covers(e) for e in ends]} distance to boundary: {[round(poly.boundary.distance(e),1) for e in ends]} "
          f"length {math.hypot(s2.x2-s2.x1, s2.y2-s2.y1):.1f} (hovered {c['length_mm']:.1f})")

print("\n== deleting an earlier seam re-snaps later ones from raw: a second seam that slid onto the first")
# seam A along boat through the deck; seam B drawn 10 mm beside it, end to end (continuing), mode '' free
A = sheets.Seam("A", c["x1"], c["y1"], c["x2"], c["y2"], panel_id=1, mode="across", raw=(c["x1"], c["y1"], c["x2"], c["y2"]))
# B continues A's line beyond its end, offset 10 mm sideways, 300 mm long
e = np.array([c["x2"], c["y2"]]); n = along
start = e + across * 50 + n * 10; end = e + across * 350 + n * 10
B = sheets.Seam("B", start[0], start[1], end[0], end[1], panel_id=None, mode="", raw=None)
out2, res2, _ = sheetjob.apply_snap([A, B], loops, axis, opts)
out1, res1, _ = sheetjob.apply_snap([B], loops, axis, opts)
print(f"  with A present: B -> {res2[1].note!r} ends ({out2[1].x1:.1f},{out2[1].y1:.1f})")
print(f"  A deleted     : B -> {res1[0].note!r} ends ({out1[0].x1:.1f},{out1[0].y1:.1f})  (moved {math.hypot(out1[0].x1-out2[1].x1, out1[0].y1-out2[1].y1):.1f} mm)")
