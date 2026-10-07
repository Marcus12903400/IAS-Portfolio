"""Piece-quality measurements the objective does not see: for each layout found by
the probes (0.35 default, 0.0, 15 s, 8 s, fallback), cut the panels with the
production split and measure every piece's size, the thinnest web of material a
cut leaves beside a cut-out, and necks (erosion test).

Re-run (after probe_01/02/03 have written their JSON):
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe "C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser/probe_06_webs.py"
"""

import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import nearest_points

sys.path.insert(0, str(Path(__file__).parent))
from common import AXIS_RUN, HAND_PLACED, SCRATCH, dump, stage  # noqa: E402
from autodeck2 import sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402

OUT = SCRATCH / "out"


def layouts():
    out = {}
    main = json.loads((OUT / "main_result.json").read_text(encoding="utf-8"))["result"]
    out["w0.35 (default, 60 s)"] = main["seams"]
    tidy = json.loads((OUT / "tidiness.json").read_text(encoding="utf-8"))
    out["w0.0 (60 s)"] = tidy["0.0"]["seams"]
    out["w1.0 (60 s)"] = tidy["1.0"]["seams"]
    short = json.loads((OUT / "short_budgets.json").read_text(encoding="utf-8"))
    for budget in ("15.0", "8.0"):
        out[f"w0.35 budget {budget} s"] = short[budget]["result"]["seams"]
    fallback = json.loads((OUT / "fallback_plan.json").read_text(encoding="utf-8"))
    out["fallback even bands"] = fallback["seams"]
    out["hand placed (before)"] = HAND_PLACED
    return out


def measure(work, seams_payload, config, options):
    step = float(options["sample_step_mm"])
    seams = [sheets.Seam.from_dict(s) for s in seams_payload]
    result = sheetjob.plan(work, config, seams=seams, write_files=False)
    as_cut = [sheets.Seam.from_dict(s) for s in result["seams"]]
    frame, _w = sheetjob.resolve_frame(work, options)
    rotation = sheets.sheet_transform(frame.axis)
    loops, _p, _k = sheets.read_fitted_dxf(sheetjob.source_dxf(work))
    rows = []
    for panel_id in sorted(loops):
        outer, holes = sheets.classify_loops(loops[panel_id], step)
        if outer is None:
            continue
        original = sheets.loop_polygon(outer, list(holes), step)
        original_boundary = original.boundary
        pieces, _pw = sheets.split_panel(panel_id, outer, holes, as_cut, options)
        for piece in pieces:
            poly = piece.polygon(step)
            pts = np.asarray(poly.exterior.coords) @ rotation.T
            width = float(pts[:, 0].max() - pts[:, 0].min())
            length = float(pts[:, 1].max() - pts[:, 1].min())
            webs = []
            for ring in poly.interiors:
                hole_ring = LineString(ring.coords)
                distance = float(hole_ring.distance(poly.exterior))
                a, b = nearest_points(hole_ring, poly.exterior)
                # Is the outer point on a kerf edge (made by a seam) or on the original outline?
                on_original = float(original_boundary.distance(b)) <= 0.75 * step
                webs.append({"web_mm": round(distance, 1), "beside_seam_kerf": not on_original,
                             "at": [round(float(b.x), 1), round(float(b.y), 1)]})
            necks = {}
            for thickness in (10, 20, 30):
                eroded = poly.buffer(-thickness / 2.0)
                parts = 0 if eroded.is_empty else len(getattr(eroded, "geoms", [eroded]))
                necks[f"<{thickness}mm"] = parts
            rows.append({
                "piece_id": piece.piece_id, "panel_id": panel_id, "area_mm2": round(float(poly.area)),
                "size_mm": [round(width, 1), round(length, 1)],
                "holes": len(poly.interiors), "thinnest_web_mm": min((w["web_mm"] for w in webs), default=None),
                "webs": webs, "parts_after_erosion": necks,
            })
    return result, rows


def main():
    config = load_config()
    options = sheets.settings(config)
    work = stage("webs", AXIS_RUN, HAND_PLACED)
    report = {}
    for label, seams_payload in layouts().items():
        result, rows = measure(work, seams_payload, config, options)
        tiny = [r for r in rows if r["area_mm2"] < 40000]
        thin = [r for r in rows if r["thinnest_web_mm"] is not None and r["thinnest_web_mm"] < 40]
        necked = [r for r in rows if r["parts_after_erosion"]["<20mm"] > 1]
        print(f"\n== {label}: status {result['status']}, {len(result['sheets'])} sheets, {result['piece_count']} pieces, "
              f"{result['seam_count']} seams ==")
        print(f"  pieces under 200x200 mm-equivalent (40000 mm2): {len(tiny)}")
        for r in sorted(tiny, key=lambda r: r["area_mm2"]):
            print(f"    {r['piece_id']:<6} {r['area_mm2']:>7} mm2  {r['size_mm'][0]} x {r['size_mm'][1]} mm  necks {r['parts_after_erosion']}")
        print(f"  pieces with a cut-out web thinner than 40 mm: {len(thin)}")
        for r in thin:
            print(f"    {r['piece_id']:<6} {r['size_mm'][0]} x {r['size_mm'][1]} mm  webs {r['webs']}")
        print(f"  pieces that split when eroded 10 mm each side (neck < 20 mm): {len(necked)}")
        for r in necked:
            print(f"    {r['piece_id']:<6} {r['size_mm'][0]} x {r['size_mm'][1]} mm  {r['parts_after_erosion']}")
        report[label] = {"plan": {k: result[k] for k in ("status", "piece_count", "seam_count")},
                         "sheets": len(result["sheets"]), "pieces": rows}
    dump(OUT / "webs.json", report)


if __name__ == "__main__":
    main()
