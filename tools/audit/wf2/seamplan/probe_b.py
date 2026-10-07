import json, math, sys, time, shutil
from pathlib import Path
import numpy as np
from shapely.geometry import LineString, Polygon

from autodeck2 import seamplan as sp, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/seamplan")
RUN = SCRATCH / "axisrun"
NOAXIS = SCRATCH / "noaxisrun"
config = load_config()
options = sheets.settings(config)
frame, _w = sheetjob.resolve_frame(RUN, options)
masters = seamsnap.master_directions(frame.axis)
along, across = masters
rotation = sheets.sheet_transform(frame.axis)
loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
panels = sp._read_panels(loops, rotation, options, sp._nest_offsets(RUN))
by = {p.panel_id: p for p in panels}
centre = sp._centreline(RUN, rotation)

# ---------------------------------------------------------------- panel 4 reach demonstration
print("=== panel 4: proxy cut vs production cut for an along cut through its middle ===")
p4 = by[4]
lo, hi = sp._extent(p4, sp.ALONG)
mid = (lo + hi) / 2
cut = sp.Cut(sp.ALONG, mid)
split = sp._split(p4, (cut,), masters, rotation, options)
print(f"panel 4 along extent {lo:.1f}..{hi:.1f}; cut at {mid:.1f}; proxy _split pieces: {len(split.pieces)} extents {[(round(w,1), round(l,1)) for w,l in split.extents]} chord {sp._chord_mm(p4, sp.ALONG, mid, masters):.1f}")
minx, miny, maxx, maxy = p4.polygon.bounds
reach = math.hypot(maxx-minx, maxy-miny) + 10
centre_pt = across * mid
print(f"  placed bbox x {minx:.0f}..{maxx:.0f} y {miny:.0f}..{maxy:.0f}; reach {reach:.0f}; line anchor (foot from origin) {centre_pt.round(0)}; line x-range {(centre_pt - along*reach)[0]:.0f}..{(centre_pt + along*reach)[0]:.0f}")
layout = sp._Layout(cuts={4: (cut,)}, oversize=[], sheets=0, waste=0, seam_count=1, seam_length_mm=0, piece_count=0, shape=sp._Shape(None,None,None,None), tidiness_penalty_percent=0)
seam4 = sp._seams_for([p4], layout, masters)[0]
drawn = LineString([(seam4.x1, seam4.y1), (seam4.x2, seam4.y2)])
print(f"  _seams_for seam: ({seam4.x1:.0f},{seam4.y1:.0f})-({seam4.x2:.0f},{seam4.y2:.0f}) length {drawn.length:.0f} mm; intersects panel 4? {drawn.intersects(p4.polygon)}; distance to panel {drawn.distance(p4.polygon):.0f} mm")
prod, warn = sheets.split_panel(4, p4.outer, p4.holes, [seam4], options)
print(f"  production split_panel with that seam: {len(prod)} piece(s) {warn}")

# ---------------------------------------------------------------- console edges vs corrector slide window
print("\n=== panel 1 ALONG fitted-edge offsets: angle to master vs the corrector's slide window ===")
p1 = by[1]
minimum = float(options["seam_snap_min_ref_length_mm"])
limit = math.cos(math.radians(sp.EDGE_TOLERANCE_DEG))
rows = []
for li, loop in enumerate([p1.outer, *p1.holes]):
    xy = loop.xy; bulges = loop.bulges; n = len(xy)
    for i in range(n):
        if abs(float(bulges[i])) > 1e-12: continue
        p0 = xy[i]; q0 = xy[(i+1) % n]; span = q0 - p0; L = float(math.hypot(*span))
        if L < minimum: continue
        c = abs(float(np.dot(span / L, along)))
        if c < limit: continue
        ang = math.degrees(math.acos(min(1.0, c)))
        half = 0.0
        rows.append((round(float(np.dot((p0+q0)/2, across)),1), round(L,0), round(ang,3), "hole" if li else "outer"))
rows.sort()
for r in rows: print("  offset %8.1f len %6.0f angle-to-master %6.3f deg (%s)" % r)
print("  corrector slide needs angle <= min(0.5 deg, atan(gap/half_len)); for a 2 m seam that is ~%.3f deg (gap=%s)" % (math.degrees(math.atan2(getattr(seamsnap, '_REFINE_MAX_GAP_MM', float('nan')), 1000.0)), getattr(seamsnap, '_REFINE_MAX_GAP_MM', None)))

# ---------------------------------------------------------------- the real optimiser, with _confirm wrapped
print("\n=== full optimise on the scratch copy, default budget ===")
pairs = []
orig_confirm = sp._confirm
state = {"last_words": None}
def wrapped_confirm(run_dir, cfg, seams, opts, panels_=None, masters_=None, centre_=None):
    out = orig_confirm(run_dir, cfg, seams, opts, panels_, masters_, centre_)
    pairs.append((state["last_words"], out["sheet_count"], out["waste_percent"], len(out["oversize"]), out["seam_count"], out["cost_percent"]))
    return out
sp._confirm = wrapped_confirm
msgs = []
def say(m):
    msgs.append(m)
    if m.startswith("Checking"): state["last_words"] = m
t0 = time.monotonic()
result = sp.optimise(RUN, config, progress=say, time_budget_s=90.0)
wall = time.monotonic() - t0
sp._confirm = orig_confirm
rep = result["report"]
print("status", result["status"], "| wall %.1fs elapsed_s %s search_elapsed_s %s budget_exhausted %s" % (wall, rep["elapsed_s"], rep["search_elapsed_s"], rep["budget_exhausted"]))
print("reason:", result["reason"])
print("evaluated", rep["candidates_evaluated"], "confirmed", rep["candidates_confirmed"], "improved", rep["improved"])
print("before:", {k: rep["before"][k] for k in ("sheet_count","waste_percent","seam_count","oversize","cost_percent","shape")})
print("after :", {k: rep["after"][k] for k in ("sheet_count","waste_percent","seam_count","oversize","cost_percent","shape","piece_count")})
print("tidiness_note:", rep["tidiness_note"])
print("panels:", rep["panels"])
print("warnings:", rep["warnings"])
print("\nproxy (from 'Checking' line) vs exact confirm:")
for words, sc, wp, ov, sn, cost in pairs:
    print(f"  {words!s:70.70} -> exact {sc} sheet(s) {wp:.1f}% waste oversize {ov} seams {sn} cost {cost}")
print("\nprogress log (search lines):")
for m in msgs:
    if m.startswith("Tried") or m.startswith("Panel") or m.startswith("Looking") or m.startswith("Now") or m.startswith("Best"):
        print("  ", m)

print("\nwinning seams:")
for s in result["seams"]:
    L = math.hypot(s.x2-s.x1, s.y2-s.y1)
    p = by[s.panel_id]
    normal = across if s.mode == "along" else along
    off = float(np.dot(np.array([(s.x1+s.x2)/2, (s.y1+s.y2)/2]), normal))
    chord = sp._chord_mm(p, s.mode, off, masters)
    unit = along if s.mode == "along" else across
    clipped = p.polygon.intersection(LineString([normal*off - unit*6000, normal*off + unit*6000]))
    n = len(getattr(clipped, "geoms", [clipped])) if not clipped.is_empty else 0
    edges = sp._panel_edges(p, s.mode, masters, options)
    egap = min((abs(off-e) for e in edges), default=float("nan"))
    print(f"  {s.seam_id}: offset {off:.1f} seam length {L:.0f} mm, material crossed {chord:.0f} mm in {n} chord(s); nearest fitted edge {egap:.1f} mm away")

# apply_snap on the winner
snapped, results, w = sheetjob.apply_snap(result["seams"], loops, frame.axis, options)
moved = [(r.reference_label, round(r.moved_mm,2)) for r in results if r.applied and r.moved_mm > 1e-6]
print("corrector moved winning seams:", moved)

# production vs proxy extents for the winner's pieces
print("\nproduction vs proxy piece extents for the winner (signed prod - proxy, mm):")
worst = (0.0, 0.0)
for p in panels:
    cuts = tuple(c for c in result_cuts) if False else None
cuts_by_panel = {p.panel_id: [] for p in panels}
for s in result["seams"]:
    normal = across if s.mode == "along" else along
    cuts_by_panel[s.panel_id].append(sp.Cut(s.mode, float(np.dot(np.array([(s.x1+s.x2)/2, (s.y1+s.y2)/2]), normal))))
for p in panels:
    cuts = tuple(cuts_by_panel[p.panel_id])
    split = sp._split(p, cuts, masters, rotation, options)
    prod, warn = sheets.split_panel(p.panel_id, p.outer, p.holes, [s for s in snapped if s.panel_id == p.panel_id], options)
    prod_items = sorted(((pc.area_mm2, sheets.oriented_extent(pc, rotation, options["sample_step_mm"])) for pc in prod), key=lambda t: -t[0])
    proxy_items = sorted(zip((pc.area_mm2 for pc in split.pieces), split.extents), key=lambda t: -t[0])
    for (pa, (pw, pl)), (qa, (qw, ql)) in zip(proxy_items, prod_items):
        dw, dl = qw - pw, ql - pl
        worst = (max(worst[0], dw), max(worst[1], dl))
        if abs(dw) > 0.2 or abs(dl) > 0.2 or abs(pa-qa) > 500:
            print(f"  panel {p.panel_id}: proxy {pw:.2f}x{pl:.2f} ({pa/1e4:.1f} dm2) prod {qw:.2f}x{ql:.2f} ({qa/1e4:.1f} dm2) delta ({dw:+.2f}, {dl:+.2f})")
print("  worst production-larger-than-proxy: width %+.2f mm, length %+.2f mm (BAND_MARGIN_MM = %.1f)" % (worst[0], worst[1], sp.BAND_MARGIN_MM))

json.dump({"seams": [s.to_dict() for s in result["seams"]], "report": rep, "status": result["status"], "reason": result["reason"]},
          open(SCRATCH / "optimise_default.json", "w"), indent=1, default=str)

# ---------------------------------------------------------------- short budgets
for budget in (5.0, 8.0):
    print(f"\n=== optimise at time_budget_s={budget} ===")
    m2 = []
    t0 = time.monotonic()
    r2 = sp.optimise(RUN, config, progress=m2.append, time_budget_s=budget)
    print("wall %.1fs" % (time.monotonic()-t0), "status", r2["status"], "evaluated", r2["report"]["candidates_evaluated"], "confirmed", r2["report"]["candidates_confirmed"], "budget_exhausted", r2["report"]["budget_exhausted"], "improved", r2["report"]["improved"])
    a = r2["report"]["after"]
    print("after:", a["sheet_count"], "sheets", a["waste_percent"], "% waste", a["seam_count"], "seams, oversize", a["oversize"])
    for m in m2:
        if m.startswith("Panel") or m.startswith("Tried"): print("  ", m)

# ---------------------------------------------------------------- no-axis run with grain override
print("\n=== no-axis run + grain override 3.68 deg, 20 s ===")
cfg = {**config, "sheets": {**(config.get("sheets") or {}), "grain_angle_deg": 3.680395518430263}}
m3 = []
r3 = sp.optimise(NOAXIS, cfg, progress=m3.append, time_budget_s=20.0)
print("status", r3["status"], "centreline_known", r3["report"]["centreline_known"], "warnings", r3["report"]["warnings"])
print("reason:", r3["reason"][:300])
