import json, math, sys, time, shutil
from pathlib import Path
import numpy as np
from shapely.geometry import LineString, Polygon

from autodeck2 import seamplan as sp, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/seamplan")
RUN = SCRATCH / "axisrun"
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
step = options["sample_step_mm"]

saved = json.load(open(SCRATCH / "optimise_default.json"))
winner = [sheets.Seam.from_dict(s) for s in saved["seams"]]

# ---------------------------------------------------------------- which seam slid onto what
print("=== corrector on the winner's seams ===")
snapped, results, w = sheetjob.apply_snap(winner, loops, frame.axis, options)
for s, r in zip(winner, results):
    if r.applied:
        print(f"  {s.seam_id}: applied={r.applied} kind={r.kind} onto={r.reference_label!r} moved={r.moved_mm:.2f} note={r.note!r}")
print("  (others unchanged)")

# ---------------------------------------------------------------- production pieces: thin features (slivers / lips)
print("\n=== production pieces of the winner: thin features ===")
all_pieces = []
for p in panels:
    prod, warn = sheets.split_panel(p.panel_id, p.outer, p.holes, [s for s in snapped if s.panel_id == p.panel_id], options)
    for pc in prod:
        poly = pc.polygon(step)
        w_, l_ = sheets.oriented_extent(pc, rotation, step)
        thin = poly.difference(poly.buffer(-4.0).buffer(4.5)).area   # material in features thinner than ~8 mm
        all_pieces.append((pc.piece_id, w_, l_, pc.area_mm2, thin))
for pid, w_, l_, a, thin in all_pieces:
    flag = "  <-- thin feature" if thin > 1500 else ""
    print(f"  {pid}: {w_:.0f} x {l_:.0f} mm, area {a/1e4:.1f} dm2, material in features thinner than ~8 mm: {thin:.0f} mm2{flag}")

# where is the lip? examine the piece(s) adjacent to the across cut at 1522.7 on panel 1
p1 = by[1]
for s in snapped:
    if s.seam_id == "auto-1-across-2":
        off = float(np.dot(np.array([(s.x1+s.x2)/2, (s.y1+s.y2)/2]), along))
        print(f"  auto-1-across-2 as cut: sheet-Y offset {off:.2f}; kerf occupies {off-3:.2f}..{off+3:.2f}; console end wall edge offset 1532.0 -> material strip between kerf and wall = {1532.0-(off+3):.2f} mm")

# ---------------------------------------------------------------- sampling-only proxy vs production extents over the shortlist (no snap)
print("\n=== proxy vs production piece extents, sampling only (no corrector), over panel 1's shortlist ===")
sp._plan_candidates([p1], masters, rotation, options, lambda m: None, None, centre)
cands = list(p1.candidates) + list(p1.tidy_candidates)
print(f"  {len(cands)} candidate cut sets on panel 1")
worst_w = worst_l = -9; worst_info = None
t0 = time.monotonic()
for cuts in cands[:60]:
    split = sp._split(p1, cuts, masters, rotation, options)
    layout = sp._Layout(cuts={1: cuts}, oversize=[], sheets=0, waste=0, seam_count=len(cuts), seam_length_mm=0, piece_count=0, shape=sp._Shape(None,None,None,None), tidiness_penalty_percent=0)
    seams_ = sp._seams_for([p1], layout, masters)
    prod, warn = sheets.split_panel(1, p1.outer, p1.holes, seams_, options)
    if len(prod) != len(split.pieces):
        print(f"  piece COUNT differs for {[(c.direction, round(c.offset_mm,1)) for c in cuts]}: proxy {len(split.pieces)} prod {len(prod)} {warn}")
        continue
    prod_items = sorted(((pc.area_mm2, sheets.oriented_extent(pc, rotation, step)) for pc in prod), key=lambda t: -t[0])
    proxy_items = sorted(zip((pc.area_mm2 for pc in split.pieces), split.extents), key=lambda t: -t[0])
    for (pa, (pw, pl)), (qa, (qw, ql)) in zip(proxy_items, prod_items):
        if qw - pw > worst_w: worst_w = qw - pw; worst_info_w = (cuts, pw, qw)
        if ql - pl > worst_l: worst_l = ql - pl; worst_info_l = (cuts, pl, ql)
print(f"  checked {min(60, len(cands))} sets in {time.monotonic()-t0:.1f}s; worst production-larger-than-proxy: width {worst_w:+.3f} mm, length {worst_l:+.3f} mm")

# ---------------------------------------------------------------- write sheet files into the SCRATCH copy for rendering
print("\n=== writing the winner's sheets into the scratch copy for a picture ===")
res = sheetjob.plan(RUN, config, seams=winner, write_files=True)
print("  status", res["status"], "sheets", len(res["sheets"]), "files", [f.get("name") for f in res["files"]], "max_arc_err", res["max_arc_rebuild_error_mm"])
for sh in res["sheets"]:
    print("   sheet", sh["sheet"], "util %.3f" % sh["utilisation"], [(pl["piece_id"], pl["rotation_deg"], round(pl["width_mm"]), round(pl["length_mm"])) for pl in sh["placements"]])

# ---------------------------------------------------------------- one-panel and fits-already DXFs
print("\n=== single-panel runs ===")
import ezdxf
def subset_run(name, keep):
    d = SCRATCH / name
    d.mkdir(exist_ok=True)
    shutil.copyfile(RUN / "run.json", d / "run.json")
    doc = ezdxf.readfile(str(RUN / "final_auto.dxf"))
    msp = doc.modelspace()
    for e in list(msp):
        layer = str(e.dxf.layer)
        if "__PANEL_" in layer:
            try: pid = int(layer.rsplit("__PANEL_", 1)[1])
            except ValueError: continue
            if pid not in keep: msp.delete_entity(e)
    doc.saveas(str(d / "final_auto.dxf"))
    return d
for name, keep in (("only_panel1", {1}), ("only_panel4", {4})):
    d = subset_run(name, keep)
    msgs = []
    t0 = time.monotonic()
    try:
        r = sp.optimise(d, config, progress=msgs.append, time_budget_s=25.0)
        a = r["report"]["after"]
        print(f"  {name}: status {r['status']} in {time.monotonic()-t0:.1f}s; seams {len(r['seams'])}; after {a['sheet_count']} sheets {a['waste_percent']}% oversize {a['oversize']}; improved {r['report']['improved']}; reason: {r['reason'][:140]}")
    except Exception as e:
        print(f"  {name}: RAISED {type(e).__name__}: {e}")

# ---------------------------------------------------------------- final.dxf preference
print("\n=== final.dxf beats final_auto.dxf ===")
d = SCRATCH / "with_final"
d.mkdir(exist_ok=True)
shutil.copyfile(RUN / "run.json", d / "run.json")
shutil.copyfile((SCRATCH / "only_panel4") / "final_auto.dxf", d / "final.dxf")      # final.dxf = only panel 4
shutil.copyfile(RUN / "final_auto.dxf", d / "final_auto.dxf")                         # final_auto = whole deck
msgs = []
r = sp.optimise(d, config, progress=msgs.append, time_budget_s=20.0)
print("  source line:", [m for m in msgs if m.startswith("Reading")][:1], "| status", r["status"], "| panels reported", [p["panel_id"] for p in r["report"]["panels"]])
