"""Probe 5: write_sheet_dxfs round trip.  Writes sheet DXFs for the chosen seam
sets into ./out/export_<family>_<seed>/ and reads them back with ezdxf.

Checks per CAM polyline: closed; rebuilt-from-bulge geometry within 0.1 mm
(symmetric max point-to-boundary distance, 0.2 mm sampling) of the in-memory
placed polygon; bulges carried unchanged (also for 180 deg pieces); layer
names; $INSUNITS; pattern lines inside their piece and their angle to sheet Y.
Usage: probe5_export.py [family] seed [seed ...]   (default: fitted 0 7 20)
"""

import math
import sys
from pathlib import Path

import ezdxf
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

from probe_common import (ENVELOPE, OPTIONS, OUT, STEP, build_pieces, dump_json, load_run, nesting, placed_polygon,
                          sheets)

args = sys.argv[1:]
family = args[0] if args and not args[0].isdigit() else "fitted"
seeds = [int(a) for a in args if a.isdigit()] or [0, 7, 20]

run = load_run()
rotation = run["rotation"]
pattern, kind = run["pattern"], run["kind"]
FINE = 0.2


def loop_points(loop: sheets.Loop, step=FINE):
    pts, _s = sheets.sample_loop(loop, step)
    return pts


def sym_distance(a_pts, b_poly_boundary, b_pts, a_poly_boundary):
    da = float(np.max(shapely.distance(shapely.points(a_pts), b_poly_boundary)))
    db = float(np.max(shapely.distance(shapely.points(b_pts), a_poly_boundary)))
    return max(da, db)


all_rows = []
for seed in seeds:
    seams, pieces, _w = build_pieces(seed, family)
    sheet_list, summary, nest_warnings = nesting.nest(pieces, rotation, OPTIONS)
    by_id = {p.piece_id: p for p in pieces}
    out_dir = OUT / f"export_{family}_{seed:03d}"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("sheet_*.dxf"):
        old.unlink()
    warnings: list[str] = []
    log: list[str] = []
    written = nesting.write_sheet_dxfs(out_dir, sheet_list, by_id, rotation, pattern, kind, OPTIONS,
                                       progress=log.append, warnings=warnings)
    print(f"\n===== {family} seed {seed}: {len(sheet_list)} sheets, {summary['piece_count']} placed, "
          f"refused={[r for w in written for r in w['refused']]}")
    for entry in written:
        doc = ezdxf.readfile(entry["path"])
        msp = doc.modelspace()
        units = doc.header.get("$INSUNITS")
        layers = sorted(l.dxf.name for l in doc.layers)
        sheet = sheet_list[entry["sheet"] - 1]
        placements = {pl.piece_id: pl for pl in sheet.placements}
        polylines = list(msp.query("LWPOLYLINE"))
        lines = list(msp.query("LINE"))
        cam = {}
        for pl_ent in polylines:
            layer = str(pl_ent.dxf.layer)
            if layer.startswith("CAM__"):
                cam.setdefault(layer, []).append(pl_ent)
        row = {"family": family, "seed": seed, "sheet": entry["sheet"], "units": units, "layers": layers,
               "cam_polylines": len(polylines), "all_closed": all(p.closed for p in polylines),
               "pattern_lines": len(lines), "pieces": [], "refused": entry["refused"]}
        bad_layer = [l for l in layers if not all(ch.isalnum() or ch == "_" for ch in l)]
        row["bad_layer_names"] = bad_layer
        worst_dev = 0.0
        for piece_id, pl in placements.items():
            layer = nesting._layer("CAM", piece_id)
            ents = cam.get(layer, [])
            piece = by_id[piece_id]
            loops = [piece.outer, *piece.holes]
            if len(ents) != len(loops):
                row["pieces"].append({"piece": piece_id, "error": f"{len(ents)} polylines for {len(loops)} loops"})
                continue
            # in-memory placed polygon (fine sampling) and its rings
            mem_rings = [pl.apply(loop_points(loop), rotation) for loop in loops]
            mem_poly = Polygon(mem_rings[0], mem_rings[1:])
            if not mem_poly.is_valid:
                mem_poly = mem_poly.buffer(0)
            # DXF rings rebuilt from bulges, matched to loops by vertex count + bulge pattern
            dxf_loops = []
            for ent in ents:
                verts = np.asarray([(x, y, b) for x, y, b in ent.get_points("xyb")], dtype=float)
                dxf_loops.append(sheets.Loop(verts))
            # match by order of writing: write_sheet_dxfs writes outer then holes in order
            devs = []
            bulge_ok = True
            for loop, dloop in zip(loops, dxf_loops):
                if len(loop.vertices) != len(dloop.vertices):
                    devs.append(float("inf"))
                    continue
                if not np.allclose(loop.bulges, dloop.bulges, rtol=0, atol=1e-9):
                    bulge_ok = False
                a_pts = pl.apply(loop_points(loop), rotation)
                b_pts = loop_points(dloop)
                a_ring, b_ring = LineString(np.vstack([a_pts, a_pts[:1]])), LineString(np.vstack([b_pts, b_pts[:1]]))
                devs.append(sym_distance(a_pts, b_ring, b_pts, a_ring))
            dev = max(devs)
            worst_dev = max(worst_dev, dev)
            # DXF piece polygon vs envelope, and vertex-wise exactness
            dxf_poly = Polygon(loop_points(dxf_loops[0]), [loop_points(h) for h in dxf_loops[1:]])
            if not dxf_poly.is_valid:
                dxf_poly = dxf_poly.buffer(0)
            minx, miny, maxx, maxy = dxf_poly.bounds
            e = ENVELOPE.bounds
            outside = max(e[0] - minx, maxx - e[2], e[1] - miny, maxy - e[3])
            vert_err = float(np.max(np.hypot(*(pl.apply(loops[0].xy, rotation) - dxf_loops[0].xy).T)))
            # pattern lines of this piece
            player = nesting._layer(f"PATTERN_{(kind or 'TEAK').upper()}", piece_id)
            mine = [l for l in lines if str(l.dxf.layer) == player]
            inside = 0
            worst_out = 0.0
            worst_angle = 0.0
            tol_poly = dxf_poly.buffer(1e-6)
            for l in mine:
                seg = LineString([(l.dxf.start.x, l.dxf.start.y), (l.dxf.end.x, l.dxf.end.y)])
                if seg.within(tol_poly):
                    inside += 1
                else:
                    worst_out = max(worst_out, float(seg.difference(dxf_poly).length))
                dx, dy = l.dxf.end.x - l.dxf.start.x, l.dxf.end.y - l.dxf.start.y
                ang = abs(math.degrees(math.atan2(dx, dy)))
                ang = min(ang, 180 - ang)
                worst_angle = max(worst_angle, ang)
            row["pieces"].append({"piece": piece_id, "rot": pl.rotation_deg, "loops": len(loops),
                                  "max_dev_mm": round(dev, 5), "vertex_err_mm": round(vert_err, 9),
                                  "bulges_unchanged": bulge_ok, "nonzero_bulges": int(np.sum(np.abs(loops[0].bulges) > 1e-12)),
                                  "outside_envelope_mm": round(outside, 4),
                                  "pattern_lines": len(mine), "pattern_inside": inside,
                                  "pattern_outside_len_mm": round(worst_out, 4), "pattern_max_angle_to_Y_deg": round(worst_angle, 5)})
        row["worst_dev_mm"] = worst_dev
        all_rows.append(row)
        print(f"  sheet {entry['sheet']}: units={units} polylines={len(polylines)} closed={row['all_closed']} lines={len(lines)} "
              f"bad_layers={bad_layer} worst_dev={worst_dev:.5f} mm")
        for p in row["pieces"]:
            if "error" in p:
                print("    ", p)
                continue
            flag = "" if (p["max_dev_mm"] < 0.1 and p["bulges_unchanged"] and p["outside_envelope_mm"] <= 0.1
                          and p["pattern_inside"] == p["pattern_lines"]) else "   <-- CHECK"
            print(f"    {p['piece']:7s} rot={p['rot']:3d} loops={p['loops']} dev={p['max_dev_mm']:.5f} vert={p['vertex_err_mm']:.2e} "
                  f"bulges_same={p['bulges_unchanged']} arcs={p['nonzero_bulges']:3d} outside={p['outside_envelope_mm']:+.4f} "
                  f"pattern {p['pattern_inside']}/{p['pattern_lines']} in, out_len={p['pattern_outside_len_mm']} "
                  f"angle<= {p['pattern_max_angle_to_Y_deg']:.4f} deg{flag}")
        print("    layers:", layers)
    if warnings or log:
        print("  export warnings:", warnings)
        print("  export log:", log)

dump_json(OUT / f"probe5_{family}.json", all_rows)
print("\nwrote", OUT / f"probe5_{family}.json")
