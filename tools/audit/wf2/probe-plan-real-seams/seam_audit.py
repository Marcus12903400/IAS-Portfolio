"""Per seam, in the placed frame, using the AS-CUT seams plan() reported:

  1. drawn chord vs what the extended cut removed (per panel it touched):
     raw drawn length, as-cut chord length, chord length inside the panel,
     total length of the EXTENDED line inside the panel and how many chords
     it makes -- a seam is flagged EXTENDED when the cut is > 1 mm longer than
     the chord in the panel.
  2. kerf / sliver accounting per panel (replicating sheets.split_panel's own
     steps): panel area, kerf area removed, pieces kept, slivers dropped.
  3. straight kerf edges of every production piece vs the boat axis
     (teak.frame.longitudinal_axis straight from run.json AND the resolved
     frame): expect < 0.01 deg off along/across.

Run:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app;<scratch>" \
      .venv/Scripts/python.exe <scratch>/seam_audit.py [A B C D]
"""
import json
import math
import sys

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from common import SETS, OUT, copy_dir, load_json, resplit
from autodeck2 import seamsnap, sheets
from autodeck2.config import load_config

ON_BOUNDARY_MM = 0.75


def chords(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom.length] if geom.length > 0.01 else []
    out = []
    for g in getattr(geom, "geoms", []):
        out += chords(g)
    return out


def main(names):
    config = load_config()
    options = sheets.settings(config)
    half_gap = float(options["seam_gap_mm"]) / 2.0
    min_area = float(options["min_piece_area_mm2"])
    audit = {}
    for name in names:
        work = copy_dir(name)
        plan = load_json(OUT / f"{name}_plan.json")
        rs = resplit(work, plan, options)
        run = load_json(work / "run.json")
        stored_axis = np.asarray(run["teak"]["frame"]["longitudinal_axis"], float)
        axis = rs["frame"].axis
        along, across = seamsnap.master_directions(axis)
        print("=" * 110)
        print(f"SET {name}: axis resolved {axis} stored {stored_axis} same={np.allclose(axis, stored_axis, atol=0)}")
        rows = []
        print("-- 1) drawn chord vs extended cut --")
        print(f"  {'seam':<16}{'mode':<7}{'bound':<6}{'panel':<6}{'raw_len':>8}{'cut_chord':>10}{'in_panel':>9}"
              f"{'EXT_cut':>9}{'n':>3}  chords")
        for seam in rs["seams"]:
            raw = seam.drawn
            raw_len = math.hypot(raw[2] - raw[0], raw[3] - raw[1])
            chord = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
            touched = 0
            for pid, (outer, holes, poly) in rs["panels"].items():
                relevant = seam.panel_id == pid or (seam.panel_id is None and chord.intersects(poly))
                if not relevant:
                    continue
                touched += 1
                reach = float(np.hypot(*(np.asarray(poly.bounds[2:]) - np.asarray(poly.bounds[:2])))) + 10.0
                ext = seam.extended(reach).intersection(poly)
                ch = chords(ext)
                cut_len = sum(ch)
                in_panel = chord.intersection(poly).length
                flag = "EXTENDED" if cut_len > in_panel + 1.0 else ""
                multi = " MULTI-CHORD" if len(ch) > 1 else ""
                print(f"  {seam.seam_id:<16}{seam.mode or '-':<7}{str(seam.panel_id):<6}{pid:<6}{raw_len:8.1f}{chord.length:10.1f}"
                      f"{in_panel:9.1f}{cut_len:9.1f}{len(ch):3d}  {[round(c, 1) for c in ch]} {flag}{multi}")
                rows.append(dict(seam=seam.seam_id, mode=seam.mode, bound=seam.panel_id, panel=pid,
                                 raw_len=round(raw_len, 1), chord_len=round(chord.length, 1),
                                 in_panel=round(in_panel, 1), cut_len=round(cut_len, 1), chords=[round(c, 1) for c in ch],
                                 extended=bool(flag)))
            if touched == 0:
                print(f"  {seam.seam_id:<16}{seam.mode or '-':<7}{str(seam.panel_id):<6} touches NO panel (raw len {raw_len:.1f})")
                rows.append(dict(seam=seam.seam_id, mode=seam.mode, bound=seam.panel_id, panel=None, raw_len=round(raw_len, 1)))
        ext_rows = [r for r in rows if r.get("extended")]
        print(f"  => {len(ext_rows)} of {len([r for r in rows if r.get('panel') is not None])} seam/panel cuts are longer than the chord shown")

        print("-- 2) kerf / sliver accounting per panel --")
        acct = {}
        total_panel = total_kerf = total_kept = total_dropped = 0.0
        for pid, (outer, holes, poly) in rs["panels"].items():
            relevant = []
            for seam in rs["seams"]:
                if seam.panel_id == pid:
                    relevant.append(seam)
                elif seam.panel_id is None and LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)]).intersects(poly):
                    relevant.append(seam)
            if not relevant:
                print(f"  panel {pid}: area {poly.area:.0f} mm2, no seams -> whole")
                acct[pid] = dict(area=poly.area, kerf=0.0, kept=poly.area, dropped=0.0, n_dropped=0)
                total_panel += poly.area; total_kept += poly.area
                continue
            reach = float(np.hypot(*(np.asarray(poly.bounds[2:]) - np.asarray(poly.bounds[:2])))) + 10.0
            kerfs = unary_union([s.extended(reach).buffer(half_gap, cap_style=2, join_style=2) for s in relevant])
            kerf_area = poly.intersection(kerfs).area
            rem = poly.difference(kerfs)
            parts = list(rem.geoms) if isinstance(rem, MultiPolygon) else ([rem] if not rem.is_empty else [])
            kept = [p for p in parts if p.area >= min_area]
            dropped = [p for p in parts if p.area < min_area]
            kerf_len = poly.intersection(unary_union([s.extended(reach) for s in relevant])).length
            print(f"  panel {pid}: area {poly.area:.0f}  kerf removed {kerf_area:.0f} mm2 (cut length in panel {kerf_len:.0f} mm x 6 = {kerf_len * 6:.0f})  "
                  f"parts {len(parts)} kept {len(kept)} ({sum(p.area for p in kept):.0f} mm2)  dropped {len(dropped)} "
                  f"({sum(p.area for p in dropped):.1f} mm2; areas {[round(p.area, 1) for p in dropped]}; bboxes "
                  f"{[tuple(round(v, 0) for v in p.bounds) for p in dropped]})")
            acct[pid] = dict(area=poly.area, kerf=kerf_area, kept=sum(p.area for p in kept), dropped=sum(p.area for p in dropped), n_dropped=len(dropped))
            total_panel += poly.area; total_kerf += kerf_area; total_kept += sum(p.area for p in kept); total_dropped += sum(p.area for p in dropped)
        plan_sum = sum(p["area_mm2"] for p in plan["pieces"])
        print(f"  TOTAL panel area {total_panel:.0f}; kerf {total_kerf:.0f} ({total_kerf / total_panel * 100:.3f}%); kept {total_kept:.0f}; "
              f"slivers dropped {total_dropped:.1f} ({total_dropped / total_panel * 100:.4f}%); plan pieces sum {plan_sum:.0f} "
              f"(kept - plan {total_kept - plan_sum:+.1f}); vanished total {total_panel - plan_sum:.0f} mm2")

        print("-- 3) kerf-edge angles vs boat axis (placed frame) --")
        edges = []
        for pid, piece in rs["pieces"].items():
            outer, holes, poly = rs["panels"][piece.panel_id]
            boundary = poly.boundary
            for loop in [piece.outer, *piece.holes]:
                xy = loop.xy; b = loop.bulges; n = len(xy)
                for i in range(n):
                    if abs(b[i]) > 1e-12:
                        continue
                    p0 = xy[i]; p1 = xy[(i + 1) % n]
                    L = float(np.hypot(*(p1 - p0)))
                    if L < 1e-9:
                        continue
                    mid = (p0 + p1) / 2.0
                    if boundary.distance(Point(mid)) <= ON_BOUNDARY_MM:
                        continue
                    d = (p1 - p0) / L
                    a_al = seamsnap._direction_angle_deg(d, along)
                    a_ac = seamsnap._direction_angle_deg(d, across)
                    a_st = min(seamsnap._direction_angle_deg(d, stored_axis / np.hypot(*stored_axis)),
                               seamsnap._direction_angle_deg(d, np.array([-stored_axis[1], stored_axis[0]]) / np.hypot(*stored_axis)))
                    edges.append((pid, L, min(a_al, a_ac), "along" if a_al < a_ac else "across", a_st))
        if edges:
            worst = max(edges, key=lambda e: e[2])
            print(f"  kerf edges {len(edges)}; worst off along/across {worst[2]:.2e} deg ({worst[0]} len {worst[1]:.1f}); "
                  f"worst vs stored run.json axis {max(e[4] for e in edges):.2e} deg; edges > 0.01 deg: {[e for e in edges if e[2] > 0.01]}")
            short = [e for e in edges if e[1] < 15.0]
            print(f"  kerf edges shorter than 15 mm: {len(short)} {[(e[0], round(e[1], 1)) for e in short][:20]}")
            per_piece = {}
            for e in edges:
                per_piece.setdefault(e[0], []).append(round(e[1], 1))
            print(f"  kerf edges per piece: {per_piece}")
        audit[name] = dict(rows=rows, accounting=acct, kerf_edges=[list(e) for e in edges])
    (OUT / "seam_audit.json").write_text(json.dumps(audit, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or list(SETS))
