"""Do cut-outs survive split_panel?  Synthetic proof plus the two fixture runs (scratch copies only)."""
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon

from autodeck2 import sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(sys.argv[1])
RUNS = Path("D:/AutoDeck/engine/outputs/runs")
opts = sheets.settings(load_config())
step = opts["sample_step_mm"]


def rounded_rect(width, height, radius, x=0.0, y=0.0):
    b = math.tan(math.pi / 8)
    r = radius
    return sheets.Loop(np.array([[x + r, y, 0.0], [x + width - r, y, b], [x + width, y + r, 0.0], [x + width, y + height - r, b],
                                 [x + width - r, y + height, 0.0], [x + r, y + height, b], [x, y + height - r, 0.0], [x, y + r, b]], dtype=float))


def circle(cx, cy, r):
    """Exactly how a round cut-out is stored in the DXF: two vertices, bulge 1 each."""
    return sheets.Loop(np.array([[cx + r, cy, 1.0], [cx - r, cy, 1.0]], dtype=float))


# --- synthetic: a panel with a round cut-out and a filleted rectangular cut-out, cut by one seam far from both
outer = rounded_rect(1600.0, 900.0, 120.0)
round_hole = circle(300.0, 450.0, 100.0)
rect_hole = rounded_rect(300.0, 200.0, 30.0, x=1100.0, y=350.0)
seam = sheets.Seam("s", 800.0, -500.0, 800.0, 1400.0)
pieces, warnings = sheets.split_panel(1, outer, [round_hole, rect_hole], [seam], opts)
print("SYNTHETIC: 2 holes in, pieces out:", len(pieces), "holes per piece:", [len(p.holes) for p in pieces], "warnings:", warnings)
for p in pieces:
    for h in p.holes:
        print(f"   piece {p.piece_id}: hole with {len(h.vertices)} vertices, {int(np.count_nonzero(np.abs(h.bulges) > 1e-12))} bulged, area {abs(Polygon(sheets.sample_loop(h, step)[0]).area):.0f}")
    print(f"   piece {p.piece_id}: outer polygon contains circle centre (300,450)? {Polygon(sheets.sample_loop(p.outer, step)[0]).contains(Point(300, 450))}")
# unsplit control: no seams -> the holes are passed through untouched
whole, _w = sheets.split_panel(1, outer, [round_hole, rect_hole], [], opts)
print("   control (no seams): holes kept =", len(whole[0].holes), "vertex counts", [len(h.vertices) for h in whole[0].holes])
# the exact guard: _rebuild_ring on the circle's own ring
ring = sheets.sample_loop(round_hole, step)[0]
sources = [(round_hole, *sheets.sample_loop(round_hole, step))]
rebuilt, err = sheets._rebuild_ring(ring, sources, opts["arc_rebuild_tolerance_mm"], step)
print(f"   _rebuild_ring on the circle ring -> {len(rebuilt.vertices)} vertices (split_panel keeps a hole only if >= 3), bulges {rebuilt.bulges.tolist()}, err {err:.4f}")

# --- the two fixture runs, with the seams test_nesting_speed pins
PINNED = {
    "21kwcockpit-1-20260901-180939": [
        {"seam_id": "s1", "x1": 110.61724090576172, "y1": -1005.9564819335938, "x2": 86.17920684814453, "y2": -453.04376220703125, "panel_id": None},
        {"seam_id": "s2", "x1": 9.80996036529541, "y1": 451.1668701171875, "x2": -23.79237174987793, "y2": 1004.07958984375, "panel_id": None},
        {"seam_id": "s3", "x1": -1474.8062744140625, "y1": 374.7977600097656, "x2": 40.35771179199219, "y2": 484.76934814453125, "panel_id": None},
        {"seam_id": "s4", "x1": 1836.56005859375, "y1": 344.2501525878906, "x2": 1469.9881591796875, "y2": 328.97625732421875, "panel_id": None},
    ],
    "21kwcockpit-1-20260901-172519": [
        {"seam_id": "s1", "x1": -4.544900894165039, "y1": 449.0463562011719, "x2": -51.352622985839844, "y2": 1001.9615478515625, "panel_id": None},
        {"seam_id": "s2", "x1": 74.44297790527344, "y1": -460.7771911621094, "x2": 118.32514190673828, "y2": -993.2139282226562, "panel_id": None},
        {"seam_id": "s3", "x1": 1923.344482421875, "y1": 320.3253173828125, "x2": 1513.7779541015625, "y2": 305.6979064941406, "panel_id": None},
        {"seam_id": "s4", "x1": 1935.0467529296875, "y1": -45.35935592651367, "x2": 1537.181396484375, "y2": -71.68865203857422, "panel_id": None},
        {"seam_id": "s5", "x1": 1221.22998046875, "y1": 601.171142578125, "x2": 1186.1241455078125, "y2": 899.56982421875, "panel_id": None},
        {"seam_id": "s6", "x1": 1297.29248046875, "y1": -393.4910583496094, "x2": 1329.472900390625, "y2": -724.0701293945312, "panel_id": None},
        {"seam_id": "s7", "x1": 36.41168975830078, "y1": 478.30108642578125, "x2": -1449.7305908203125, "y2": 361.28204345703125, "panel_id": None},
        {"seam_id": "s8", "x1": 103.69784545898438, "y1": -452.0007629394531, "x2": -1376.59375, "y2": -601.2000732421875, "panel_id": None},
    ],
}
for name, seams in PINNED.items():
    work = SCRATCH / "holes_runs" / name
    work.mkdir(parents=True, exist_ok=True)
    for f in ("final_auto.dxf", "run.json"):
        shutil.copyfile(RUNS / name / f, work / f)
    (work / "seams.json").write_text(json.dumps({"seams": seams}), encoding="utf-8")
    loops, _p, _k = sheets.read_fitted_dxf(work / "final_auto.dxf")
    outer, holes = sheets.classify_loops(loops[1], step)
    rounds = [h for h in holes if len(h.vertices) == 2]
    print(f"RUN {name}: panel 1 has {len(holes)} cut-outs, {len(rounds)} round (2-vertex)")
    result = sheetjob.plan(work, load_config(), write_files=False)
    seam_list = [sheets.Seam.from_dict(s) for s in result["seams"]]
    pieces, warnings = sheets.split_panel(1, outer, holes, seam_list, opts)
    for rh in rounds:
        c = sheets.bulge_to_arc(rh.vertices[0, :2], rh.vertices[1, :2], rh.vertices[0, 2])[0]
        centre = Point(float(c[0]), float(c[1]))
        for p in pieces:
            shell = Polygon(sheets.sample_loop(p.outer, step)[0])
            if shell.contains(centre):
                covered = any(Polygon(sheets.sample_loop(h, step)[0]).contains(centre) for h in p.holes)
                placed = any(pl["piece_id"] == p.piece_id for sh in result["sheets"] for pl in sh["placements"])
                print(f"   round cut-out centre ({centre.x:.0f},{centre.y:.0f}) lies in piece {p.piece_id}; "
                      f"piece has {len(p.holes)} hole(s); a hole covers the centre: {covered}; piece nested on a sheet: {placed}; "
                      f"piece oversize: {p.piece_id in [o['piece_id'] for o in result['oversize']]}")
    print("   holes per piece:", {p.piece_id: len(p.holes) for p in pieces}, "| split warnings:", warnings)
    print("   bulged vertices in surviving holes:", [int(np.count_nonzero(np.abs(h.bulges) > 1e-12)) for p in pieces for h in p.holes])
