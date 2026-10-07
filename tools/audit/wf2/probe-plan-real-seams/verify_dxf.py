"""Invariants measured directly on the sheet_NN.dxf files plan() wrote into each
copy, with ezdxf + shapely (bulge->arc sampling re-implemented here so this does
not trust sheets.sample_loop):

  * every CAM__ polyline closed
  * each piece (outer minus holes) inside the usable envelope
    [12.7, 12.7]-[1003.3, 2019.3] to 0.01 mm
  * pairwise clearance between pieces on one sheet >= 20 - 0.01 mm, no overlaps
  * DXF piece area vs the area plan() reported for the piece
  * pattern (groove) line angles in the sheet frame, and whether each lies in
    its piece
  * straight kerf edges in the SHEET frame: exactly along +Y (along boat) or
    +X (across boat)?  Kerf edge = straight segment whose midpoint is > 0.75 mm
    from the original panel boundary transformed by the production placement.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/verify_dxf.py [A B C D]
"""
import itertools
import json
import math
import sys

import ezdxf
import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import unary_union

from common import SETS, OUT, copy_dir, load_json, resplit
from autodeck2 import nesting, sheets
from autodeck2.config import load_config

USABLE = (12.7, 12.7, 1003.3, 2019.3)
TOL = 0.01
SPACING = 20.0
STEP = 0.25        # fine sampling: inscribed chord error step^2/8r = 0.0016 mm at r=5
ON_BOUNDARY_MM = 0.75


def sample(points_xyb, step=STEP):
    pts = np.asarray(points_xyb, float)
    n = len(pts)
    out = []
    for i in range(n):
        x0, y0, b = pts[i]
        x1, y1, _ = pts[(i + 1) % n]
        p0 = np.array([x0, y0]); p1 = np.array([x1, y1])
        chord = float(np.hypot(*(p1 - p0)))
        if abs(b) < 1e-12 or chord < 1e-12:
            k = max(1, int(math.ceil(chord / step)))
            out.append(np.linspace(p0, p1, k, endpoint=False))
            continue
        theta = 4.0 * math.atan(b)
        r = chord / (2.0 * abs(math.sin(theta / 2.0)))
        mid = (p0 + p1) / 2.0
        h = math.sqrt(max(r * r - (chord / 2.0) ** 2, 0.0))
        nrm = np.array([-(p1 - p0)[1], (p1 - p0)[0]]) / chord
        sgn = 1.0 if theta > 0 else -1.0
        if abs(theta) > math.pi:
            sgn = -sgn
        c = mid + nrm * h * sgn
        a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
        k = max(2, int(math.ceil(abs(theta) * r / step)))
        ang = np.linspace(a0, a0 + theta, k, endpoint=False)
        out.append(c + r * np.column_stack([np.cos(ang), np.sin(ang)]))
    return np.vstack(out)


def signed_area(xy):
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def main(names):
    config = load_config()
    options = sheets.settings(config)
    report = {}
    for name in names:
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        base = rs["rotation"]
        plan_area = {p["piece_id"]: p["area_mm2"] for p in plan["pieces"]}
        plan_holes = {p["piece_id"]: p["holes"] for p in plan["pieces"]}
        placements = {pl["piece_id"]: (sh["sheet"], pl) for sh in plan["sheets"] for pl in sh["placements"]}
        files = sorted(work.glob("sheet_*.dxf"))
        print("=" * 110)
        print(f"SET {name}: {len(files)} sheet DXF(s) in {work}")
        set_rep = {"sheets": [], "problems": []}
        problems = set_rep["problems"]
        kerf_angles = []        # (sheet, piece, length, deg off nearest sheet axis)
        for f in files:
            sheet_no = int(f.stem.split("_")[1])
            doc = ezdxf.readfile(str(f))
            msp = doc.modelspace()
            cam = {}
            pattern = []
            frames = {}
            for e in msp:
                layer = str(e.dxf.layer)
                if e.dxftype() == "LWPOLYLINE":
                    verts = np.asarray([(x, y, b) for x, y, b in e.get_points("xyb")], float)
                    if layer.startswith("CAM__"):
                        cam.setdefault(layer, []).append((bool(e.closed), verts))
                    elif layer.startswith("SHEET__"):
                        frames[layer] = verts
                elif e.dxftype() == "LINE" and layer.startswith("PATTERN_"):
                    pattern.append((layer, np.array([e.dxf.start.x, e.dxf.start.y]),
                                    np.array([e.dxf.end.x, e.dxf.end.y])))
            # frames
            for lname, verts in frames.items():
                mn, mx = verts[:, :2].min(axis=0), verts[:, :2].max(axis=0)
                print(f"  {f.name} {lname}: {mn.round(3)} .. {mx.round(3)}")
            # pieces
            expected = [pid for pid in plan["files"][sheet_no - 1]["pieces"]] if plan.get("files") else []
            layer_of = {nesting._layer("CAM", pid): pid for pid in placements}
            pieces = {}
            for layer, loops in cam.items():
                pid = layer_of.get(layer, layer)
                closed_all = all(c for c, _v in loops)
                rings = [sample(v) for _c, v in loops]
                areas = [abs(signed_area(r)) for r in rings]
                oi = int(np.argmax(areas))
                outer = rings[oi]
                holes = [r for i, r in enumerate(rings) if i != oi]
                poly = Polygon(outer, holes)
                valid = poly.is_valid
                if not valid:
                    poly = poly.buffer(0.0)
                windings = [("CCW" if signed_area(r) > 0 else "CW") for r in rings]
                pieces[pid] = dict(poly=poly, closed=closed_all, valid=valid, loops=len(loops),
                                   windings=windings, raw_loops=loops)
                minx, miny, maxx, maxy = poly.bounds
                inside = (minx >= USABLE[0] - TOL and miny >= USABLE[1] - TOL
                          and maxx <= USABLE[2] + TOL and maxy <= USABLE[3] + TOL)
                margin = min(minx - USABLE[0], miny - USABLE[1], USABLE[2] - maxx, USABLE[3] - maxy)
                a_dxf = poly.area
                a_plan = plan_area.get(pid, float("nan"))
                sh_pl = placements.get(pid)
                size_ok = ""
                if sh_pl:
                    pl = sh_pl[1]
                    exp_min = np.asarray(pl["offset_mm"])
                    exp_max = exp_min + np.array([pl["width_mm"], pl["length_mm"]])
                    dev = max(abs(minx - exp_min[0]), abs(miny - exp_min[1]), abs(maxx - exp_max[0]), abs(maxy - exp_max[1]))
                    size_ok = f"bbox vs placement dev {dev:.3f} mm"
                    if sh_pl[0] != sheet_no:
                        problems.append(f"{f.name}: {pid} is in this file but plan put it on sheet {sh_pl[0]}")
                print(f"  {f.name} {pid:<8} loops {len(loops)} (plan holes {plan_holes.get(pid)}) closed={closed_all} valid={valid} "
                      f"winding={windings} bbox [{minx:.2f},{miny:.2f}]-[{maxx:.2f},{maxy:.2f}] inside_usable={inside} "
                      f"margin {margin:.3f} mm  area dxf {a_dxf:.1f} vs plan {a_plan:.1f} (diff {a_dxf - a_plan:+.2f} mm2, "
                      f"{(a_dxf - a_plan) / a_plan * 100 if a_plan else float('nan'):+.4f}%)  {size_ok}")
                if not closed_all:
                    problems.append(f"{f.name}: {pid} has an unclosed CAM polyline")
                if not inside:
                    problems.append(f"{f.name}: {pid} outside usable envelope by {-margin:.3f} mm")
                if not valid:
                    problems.append(f"{f.name}: {pid} polygon invalid as written")
                if len(loops) != 1 + plan_holes.get(pid, 0):
                    problems.append(f"{f.name}: {pid} has {len(loops)} loops but plan says {1 + plan_holes.get(pid, 0)}")
            missing = [pid for pid in expected if pid not in pieces]
            extra = [pid for pid in pieces if pid not in expected]
            if missing or extra:
                problems.append(f"{f.name}: pieces missing {missing} extra {extra}")
            # pairwise clearance
            ids = sorted(pieces)
            min_clear = float("inf")
            for a, b in itertools.combinations(ids, 2):
                pa, pb = pieces[a]["poly"], pieces[b]["poly"]
                d = pa.distance(pb)
                ov = pa.intersects(pb)
                min_clear = min(min_clear, d)
                flag = ""
                if ov:
                    flag = " OVERLAP"
                    problems.append(f"{f.name}: {a} and {b} OVERLAP (intersection area {pa.intersection(pb).area:.2f})")
                elif d < SPACING - TOL:
                    flag = " TOO CLOSE"
                    problems.append(f"{f.name}: {a} and {b} only {d:.3f} mm apart")
                print(f"  {f.name} clearance {a} - {b}: {d:.3f} mm{flag}")
            # pattern lines
            angs = []
            outside_piece = 0
            total_len = 0.0
            for layer, s, t in pattern:
                v = t - s
                L = float(np.hypot(*v))
                total_len += L
                if L < 1e-9:
                    continue
                angs.append(math.degrees(math.atan2(v[1], v[0])) % 180.0)
                pid_layer = layer.split("__", 1)[1] if "__" in layer else ""
                pid = layer_of.get("CAM__" + pid_layer)
                if pid in pieces and not pieces[pid]["poly"].buffer(0.02).covers(LineString([s, t])):
                    outside_piece += 1
            if angs:
                a = np.array(angs)
                hist = {k: int(v) for k, v in zip(*np.unique(a.round(2), return_counts=True))}
                print(f"  {f.name} pattern lines: {len(pattern)} total {total_len:.0f} mm; angle (deg mod 180) "
                      f"min {a.min():.4f} max {a.max():.4f}; distinct(rounded 0.01): {dict(list(hist.items())[:8])}"
                      f"{' ...' if len(hist) > 8 else ''}; lines not inside their piece: {outside_piece}")
                if outside_piece:
                    problems.append(f"{f.name}: {outside_piece} pattern lines not inside their piece")
            else:
                print(f"  {f.name} pattern lines: 0")
            # kerf edges in the sheet frame
            for pid, info in pieces.items():
                if pid not in placements:
                    continue
                pl = placements[pid][1]
                piece = rs["pieces"][pid]
                outer_l, holes_l, panel_poly = rs["panels"][piece.panel_id]
                placement = nesting.Placement(piece_id=pid, panel_id=piece.panel_id, sheet_index=sheet_no - 1,
                                              rotation_deg=int(pl["rotation_deg"]),
                                              offset=np.asarray(pl["offset_mm"], float),
                                              origin=np.asarray(pl["origin_mm"], float))
                rings = [sheets.sample_loop(outer_l, 1.0)[0]] + [sheets.sample_loop(h, 1.0)[0] for h in holes_l]
                boundary = unary_union([LineString(np.vstack([placement.apply(r, base), placement.apply(r, base)[:1]]))
                                        for r in rings])
                shapely.prepare(boundary)
                for _closed, verts in info["raw_loops"]:
                    n = len(verts)
                    for i in range(n):
                        if abs(verts[i, 2]) > 1e-12:
                            continue
                        p0 = verts[i, :2]; p1 = verts[(i + 1) % n, :2]
                        L = float(np.hypot(*(p1 - p0)))
                        if L < 1e-9:
                            continue
                        mid = (p0 + p1) / 2.0
                        if boundary.distance(Point(mid)) <= ON_BOUNDARY_MM:
                            continue
                        d = (p1 - p0) / L
                        off = min(math.degrees(math.asin(min(1.0, abs(d[0])))),     # off +Y (along boat)
                                  math.degrees(math.asin(min(1.0, abs(d[1])))))     # off +X (across boat)
                        which = "along(+Y)" if abs(d[0]) < abs(d[1]) else "across(+X)"
                        kerf_angles.append((f.name, pid, L, off, which))
            set_rep["sheets"].append({"file": f.name, "pieces": ids, "min_clearance_mm": None if min_clear == float("inf") else round(min_clear, 3)})
        if kerf_angles:
            worst = max(kerf_angles, key=lambda k: k[3])
            n_al = sum(1 for k in kerf_angles if k[4].startswith("along"))
            print(f"  kerf edges found in DXFs: {len(kerf_angles)} ({n_al} along, {len(kerf_angles) - n_al} across); "
                  f"worst angle off sheet axis {worst[3]:.2e} deg ({worst[0]} {worst[1]} len {worst[2]:.1f}); "
                  f"edges > 0.01 deg: {[k for k in kerf_angles if k[3] > 0.01]}")
            print("  kerf edge list (file, piece, length mm, deg off, which):")
            for k in sorted(kerf_angles, key=lambda k: (k[0], k[1], -k[2])):
                print(f"    {k[0]} {k[1]:<8} {k[2]:8.1f} mm  {k[3]:.2e} deg  {k[4]}")
        else:
            print("  kerf edges found in DXFs: 0")
        set_rep["kerf_edges"] = [list(k) for k in kerf_angles]
        print(f"  PROBLEMS ({len(problems)}):")
        for p in problems:
            print("    !", p)
        report[name] = set_rep
    (OUT / "verify_dxf.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or list(SETS))
