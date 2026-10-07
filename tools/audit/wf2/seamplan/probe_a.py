import json, math, sys, time
from pathlib import Path
import numpy as np
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely import affinity

from autodeck2 import seamplan as sp, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf2/seamplan")
RUN = SCRATCH / "axisrun"
config = load_config()
options = sheets.settings(config)
print("options subset:", {k: options[k] for k in ("max_part_width_mm","max_part_length_mm","seam_gap_mm","part_spacing_mm","sample_step_mm","nest_step_mm","min_piece_area_mm2","seam_tidiness_weight","seam_snap_offset_mm","seam_snap_reach_mm","seam_snap_max_move_mm","seam_snap_min_ref_length_mm","grain_angle_deg")})

frame, warnings = sheetjob.resolve_frame(RUN, options)
masters = seamsnap.master_directions(frame.axis)
along, across = masters
rotation = sheets.sheet_transform(frame.axis)
print("axis", frame.axis, "rotation", rotation.tolist())
print("across==rotation[0]?", np.array_equal(across, rotation[0]), "along==rotation[1]?", np.array_equal(along, rotation[1]))

loops, _p, _k = sheets.read_fitted_dxf(RUN / "final_auto.dxf")
panels = sp._read_panels(loops, rotation, options, sp._nest_offsets(RUN))
centre = sp._centreline(RUN, rotation)
print("centre_mm (sheet X of origin):", centre)
gap = options["seam_gap_mm"]
for p in panels:
    u0,u1,v0,v1 = p.bounds
    print(f"panel {p.panel_id}: polygon type {p.polygon.geom_type} width(acrossX) {p.width_mm:.1f} length(alongY) {p.length_mm:.1f} area {p.area_mm2/1e6:.3f} m2 boat_shift {p.boat_shift_mm:.1f} holes {len(p.holes)}"
          f" min_cuts along {sp._minimum_cuts(p.width_mm, options['max_part_width_mm']-sp.BAND_MARGIN_MM, gap)} across {sp._minimum_cuts(p.length_mm, options['max_part_length_mm']-sp.BAND_MARGIN_MM, gap)}")

# ---- frame sign check: mirror panel 2 about the centreline (boat frame) and compare with panel 3
by = {p.panel_id: p for p in panels}
def sheet_poly(p, sign):
    coords = np.asarray(p.polygon.exterior.coords) @ rotation.T
    coords[:,0] -= sign * p.boat_shift_mm
    return Polygon(coords)
if 2 in by and 3 in by:
    p3 = sheet_poly(by[3], +1)
    for sign, label in ((+1, "shift as coded (placed = boat + nest_offset)"), (-1, "opposite sign")):
        p2s = sheet_poly(by[2], sign)
        mirrored = affinity.scale(p2s, xfact=-1, yfact=1, origin=(centre, 0))
        bb2 = mirrored.bounds; bb3 = p3.bounds
        inter = mirrored.buffer(30).intersection(p3.buffer(30)).area
        print(f"[{label}] mirrored panel2 bbox x {bb2[0]:.0f}..{bb2[2]:.0f} y {bb2[1]:.0f}..{bb2[3]:.0f} | panel3 bbox x {bb3[0]:.0f}..{bb3[2]:.0f} y {bb3[1]:.0f}..{bb3[3]:.0f} | overlap(30mm buffered) {inter/1e6:.3f} m2 of p3 {p3.buffer(30).area/1e6:.3f}")

# ---- positions for panel 1
p1 = by[1]
for direction in (sp.ALONG, sp.ACROSS):
    lo, hi = sp._extent(p1, direction)
    usable = sp._usable(direction, options)
    pos_nolimit = sp._positions(p1, direction, masters, options, None, centre)
    pos_plain = sp._positions(p1, direction, masters, options, None, None)
    edges = sp._panel_edges(p1, direction, masters, options)
    print(f"\npanel1 {direction}: lo {lo:.1f} hi {hi:.1f} span {hi-lo:.1f}; positions (with centre) {len(pos_nolimit)}, (no centre) {len(pos_plain)}; edge offsets {len(edges)}: {[round(e,1) for e in edges]}")
    fill = []
    position = lo + usable + gap/2
    while position < hi:
        fill.append(position); position += usable + gap
    position = hi - usable - gap/2
    while position > lo:
        fill.append(position); position -= usable + gap
    print("  sheet-filling positions:", [round(f,2) for f in fill], "present in positions:", [any(abs(f-v)<1e-9 for v in pos_nolimit) for f in fill])
    fewest = sp._minimum_cuts(hi-lo, usable - sp.BAND_MARGIN_MM, gap)
    for count in range(fewest, fewest + sp.EXTRA_CUTS + 1):
        limit = sp._position_limit(count)
        pos = sp._positions(p1, direction, masters, options, limit, centre)
        sets = sp._cut_sets(p1, direction, pos, count, options)
        with_fill = sum(1 for s in sets if any(any(abs(f-v)<1e-9 for v in s) for f in fill))
        print(f"  count {count}: limit {limit} positions {len(pos)} feasible sets {len(sets)}; sets containing a sheet-filling position: {with_fill}")
    if fill:
        f = fill[0]
        w = sp._band_widths(lo, hi, [f], gap)
        print(f"  first band of sheet-filling cut {f:.2f}: width {w[0]:.3f} vs _cut_sets usable {usable - sp.BAND_MARGIN_MM:.3f} -> {'REJECTED' if w[0] > usable - sp.BAND_MARGIN_MM else 'accepted'}")

# ---- spans / chords for every candidate position on every panel
print("\n--- _span vs chord ---")
for p in panels:
    for direction in (sp.ALONG, sp.ACROSS):
        unit = along if direction == sp.ALONG else across
        normal = across if direction == sp.ALONG else along
        pos = sp._positions(p, direction, masters, options, None, centre)
        if not pos:
            print(f"panel {p.panel_id} {direction}: no positions"); continue
        multi = 0; worst_ratio = 0; fallback = 0
        details = None
        for off in pos:
            centre_pt = normal * off
            start, end = sp._span(p, centre_pt, unit)
            seam_len = float(np.hypot(*(end-start)))
            chord = sp._chord_mm(p, direction, off, masters)
            clipped = p.polygon.intersection(LineString([centre_pt - unit*5000, centre_pt + unit*5000]))
            n = len(getattr(clipped, "geoms", [clipped])) if not clipped.is_empty else 0
            if n > 1: multi += 1
            if chord > 0 and seam_len/chord > worst_ratio:
                worst_ratio = seam_len/chord; details = (round(off,1), round(seam_len,1), round(chord,1), n)
            minx,miny,maxx,maxy = p.polygon.bounds
            if seam_len > math.hypot(maxx-minx, maxy-miny) + 1: fallback += 1
        print(f"panel {p.panel_id} {direction}: {len(pos)} positions; cuts crossing the material in >1 chord: {multi}; fallback-length seams: {fallback}; worst seam_len/chord {worst_ratio:.2f} at (offset, seam_len, chord, n_chords)={details}")

# ---- the proxy split vs production split on the stored optimiser seams (.bak)
print("\n--- proxy _split vs production split_panel on the .bak optimiser seams ---")
bak = json.loads(Path("D:/AutoDeck/engine/outputs/runs/21kwcockpit-1-20260901-180939/seams_optimiser_run.json.bak").read_text())
bak_seams = [sheets.Seam.from_dict(s) for s in bak["seams"]]
cuts_by_panel = {p.panel_id: [] for p in panels}
for s in bak_seams:
    mid = np.array([(s.x1+s.x2)/2, (s.y1+s.y2)/2])
    normal = across if s.mode == "along" else along
    cuts_by_panel[s.panel_id].append(sp.Cut(s.mode, float(np.dot(mid, normal))))
for p in panels:
    cuts = tuple(cuts_by_panel[p.panel_id])
    split = sp._split(p, cuts, masters, rotation, options)
    prod, warn = sheets.split_panel(p.panel_id, p.outer, p.holes, [s for s in bak_seams if s.panel_id == p.panel_id], options)
    prod_ext = [sheets.oriented_extent(pc, rotation, options["sample_step_mm"]) for pc in prod]
    print(f"panel {p.panel_id}: cuts {[(c.direction, round(c.offset_mm,1)) for c in cuts]} proxy pieces {len(split.pieces)} dropped {split.dropped} | production pieces {len(prod)} warn {warn}")
    for (pw,pl),(qw,ql) in zip(sorted(split.extents), sorted(prod_ext)):
        if abs(pw-qw) > 0.3 or abs(pl-ql) > 0.3:
            print(f"   extent differs: proxy {pw:.2f}x{pl:.2f} prod {qw:.2f}x{ql:.2f}")

# ---- how the corrector slides generated seams
print("\n--- apply_snap on generated single seams for every candidate position of panel 1 ---")
moved_stats = []
total = 0
for direction in (sp.ALONG, sp.ACROSS):
    pos = sp._positions(p1, direction, masters, options, None, centre)
    edges = sp._panel_edges(p1, direction, masters, options)
    for off in pos:
        total += 1
        layout = sp._Layout(cuts={1: (sp.Cut(direction, off),)}, oversize=[], sheets=0, waste=0, seam_count=1, seam_length_mm=0, piece_count=0, shape=sp._Shape(None,None,None,None), tidiness_penalty_percent=0)
        seams_ = sp._seams_for([p1], layout, masters)
        snapped, results, w = sheetjob.apply_snap(seams_, loops, frame.axis, options)
        r = results[0]
        if r.applied and r.moved_mm > 1e-6:
            normal = across if direction == sp.ALONG else along
            new_off = float(np.dot(np.array([(snapped[0].x1+snapped[0].x2)/2,(snapped[0].y1+snapped[0].y2)/2]), normal))
            gapedge = min(abs(off-e) for e in edges) if edges else None
            moved_stats.append((direction, round(off,1), round(new_off,1), round(r.moved_mm,2), r.reference_label, None if gapedge is None else round(gapedge,2)))
print(f"generated single seams that the corrector moved: {len(moved_stats)} of {total}")
for m in moved_stats[:60]:
    print("  ", m)

# ---- read_seams on degenerate files
print("\n--- read_seams on degenerate seams.json ---")
cases = [("0-byte", ""), ("empty-object", "{}"), ("bad-mode", json.dumps({"seams":[{"seam_id":"x","x1":0,"y1":0,"x2":1,"y2":1,"mode":"diag"}]})), ("list-not-dict", "[]"), ("seams-null", json.dumps({"seams": None}))]
for label, content in cases:
    d = SCRATCH / ("degenerate_" + label)
    d.mkdir(exist_ok=True)
    (d / "seams.json").write_text(content, encoding="utf-8")
    try:
        got = sheets.read_seams(d)
        print(f"  {label}: ok -> {len(got)} seams")
    except Exception as e:
        print(f"  {label}: RAISES {type(e).__name__}: {str(e)[:80]}")
