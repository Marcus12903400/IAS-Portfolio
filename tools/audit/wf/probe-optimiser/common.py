"""Shared helpers for the optimiser probe.

Everything here works on SCRATCH COPIES of the cached runs, staged exactly the
way engine/tests/test_seamplan.py::make_run stages them (final_auto.dxf +
run.json copied, seams.json written).  Nothing here ever touches
D:/AutoDeck/engine/outputs/runs.

Run any probe with:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
      .venv/Scripts/python.exe <scratch>/wf/probe-optimiser/<script>.py
"""

from __future__ import annotations

import json
import math
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import LineString, Polygon

from autodeck2 import seamplan, seamsnap, sheetjob, sheets
from autodeck2.config import load_config

SCRATCH = Path(r"C:/Users/marcu/AppData/Local/Temp/claude/D--AutoDeck/"
               r"a545dbfc-03b6-4f2a-8e9f-1656326b95cf/scratchpad/wf/probe-optimiser")
RUNS = Path(r"D:/AutoDeck/engine/outputs/runs")
AXIS_RUN = RUNS / "21kwcockpit-1-20260901-180939"
NO_AXIS_RUN = RUNS / "21kwcockpit-1-20260901-172519"

# Copied verbatim from engine/tests/test_seamplan.py HAND_PLACED.
HAND_PLACED = [
    {"seam_id": "s1", "x1": 110.61724090576172, "y1": -1005.9564819335938,
     "x2": 86.17920684814453, "y2": -453.04376220703125, "panel_id": None},
    {"seam_id": "s2", "x1": 9.80996036529541, "y1": 451.1668701171875,
     "x2": -23.79237174987793, "y2": 1004.07958984375, "panel_id": None},
    {"seam_id": "s3", "x1": -1474.8062744140625, "y1": 374.7977600097656,
     "x2": 40.35771179199219, "y2": 484.76934814453125, "panel_id": None},
    {"seam_id": "s4", "x1": 1836.56005859375, "y1": 344.2501525878906,
     "x2": 1469.9881591796875, "y2": 328.97625732421875, "panel_id": None},
]


def stage(name: str, source: Path = AXIS_RUN, seams: Any = HAND_PLACED) -> Path:
    """A fresh scratch copy of `source` (final_auto.dxf + run.json) named `name`.

    seams=None  -> no seams.json at all
    seams=[]    -> {"seams": []}
    """

    work = SCRATCH / "runs" / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for file_name in ("final_auto.dxf", "run.json"):
        shutil.copyfile(source / file_name, work / file_name)
    if seams is not None:
        (work / "seams.json").write_text(json.dumps({"seams": seams}, indent=2), encoding="utf-8")
    assert str(work).startswith(str(SCRATCH)), work
    return work


def config_with(**overrides: Any) -> dict[str, Any]:
    config = load_config()
    return {**config, "sheets": {**(config.get("sheets") or {}), **overrides}}


def jsonable(value: Any) -> Any:
    if isinstance(value, sheets.Seam):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return value


def dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(jsonable(payload), indent=1), encoding="utf-8")


def timed_optimise(work: Path, config: dict[str, Any], budget: float,
                   label: str) -> tuple[dict[str, Any], list[str], float]:
    """optimise() with a progress log captured and the wall time measured."""

    messages: list[str] = []
    started = time.monotonic()
    result = seamplan.optimise(work, config, progress=messages.append, time_budget_s=budget)
    wall = time.monotonic() - started
    print(f"[{label}] wall {wall:.1f}s  status={result['status']}  "
          f"evaluated={result['report']['candidates_evaluated']}  "
          f"confirmed={result['report']['candidates_confirmed']}  "
          f"budget_exhausted={result['report']['budget_exhausted']}  "
          f"elapsed_s(reported)={result['report']['elapsed_s']}")
    return result, messages, wall


def summarise_metrics(metrics: dict[str, Any] | None) -> str:
    if metrics is None:
        return "None"
    shape = metrics.get("shape") or {}
    return (f"status={metrics['status']} sheets={metrics['sheet_count']} pieces={metrics['piece_count']} "
            f"seams={metrics['seam_count']} oversize={metrics['oversize']} waste%={metrics['waste_percent']} "
            f"cost%={metrics['cost_percent']} penalty={metrics['tidiness_penalty_percent']} "
            f"shape={shape} util={[round(u, 4) for u in metrics['utilisation']]}")


def panel_polygons(run_dir: Path, step: float) -> tuple[dict[int, Polygon], dict[int, Polygon],
                                                        dict[int, list[Polygon]]]:
    """(material polygon with holes, outer-only polygon, hole polygons) per panel,
    from the same source DXF production cuts, sampled at the production step."""

    source = sheetjob.source_dxf(run_dir)
    loops, _pattern, _kind = sheets.read_fitted_dxf(source)
    material: dict[int, Polygon] = {}
    outer_only: dict[int, Polygon] = {}
    holes_of: dict[int, list[Polygon]] = {}
    for panel_id, panel_loops in loops.items():
        outer, holes = sheets.classify_loops(panel_loops, step)
        if outer is None:
            continue
        material[panel_id] = sheets.loop_polygon(outer, list(holes), step)
        outer_only[panel_id] = sheets.loop_polygon(outer, [], step)
        holes_of[panel_id] = [sheets.loop_polygon(hole, [], step) for hole in holes]
    return material, outer_only, holes_of


def masters_for(run_dir: Path, options: dict[str, Any]):
    frame, _warnings = sheetjob.resolve_frame(run_dir, options)
    return frame, seamsnap.master_directions(frame.axis)


def angle_between_deg(u: np.ndarray, v: np.ndarray) -> float:
    cosine = abs(float(np.dot(u, v)))
    return math.degrees(math.acos(min(1.0, cosine)))


def chord_report(seam: sheets.Seam, material: Polygon, outer_only: Polygon,
                 holes: list[Polygon], masters, rotation: np.ndarray) -> dict[str, Any]:
    """Everything about where one seam's INFINITE line meets its panel."""

    p = np.array([seam.x1, seam.y1], dtype=float)
    q = np.array([seam.x2, seam.y2], dtype=float)
    span = q - p
    length = float(np.hypot(*span))
    unit = span / length
    along, across = masters
    min_x, min_y, max_x, max_y = outer_only.bounds
    reach = float(math.hypot(max_x - min_x, max_y - min_y)) + 50.0
    centre = (p + q) / 2.0
    line = LineString([centre - unit * reach, centre + unit * reach])

    material_hit = material.intersection(line)
    outer_hit = outer_only.intersection(line)
    material_parts = list(getattr(material_hit, "geoms", [material_hit])) if not material_hit.is_empty else []
    outer_parts = list(getattr(outer_hit, "geoms", [outer_hit])) if not outer_hit.is_empty else []

    def extent(parts):
        distances = []
        for part in parts:
            for point in np.asarray(getattr(part, "coords", []), dtype=float):
                distances.append(float(np.dot(point[:2] - centre, unit)))
        return (min(distances), max(distances)) if distances else (None, None)

    o_lo, o_hi = extent(outer_parts)
    full_span = None if o_lo is None else o_hi - o_lo
    hole_hits = [index for index, hole in enumerate(holes) if hole.intersects(line)]
    drawn = LineString([p, q])
    # In the sheet frame: the cut's offset and where it sits across the panel's extent.
    sheet_pq = np.array([p, q]) @ rotation.T
    sheet_outer = np.asarray(outer_only.exterior.coords) @ rotation.T
    direction = "along" if angle_between_deg(unit, along) < angle_between_deg(unit, across) else "across"
    if direction == "along":
        offset = float(sheet_pq[:, 0].mean())
        lo, hi = float(sheet_outer[:, 0].min()), float(sheet_outer[:, 0].max())
    else:
        offset = float(sheet_pq[:, 1].mean())
        lo, hi = float(sheet_outer[:, 1].min()), float(sheet_outer[:, 1].max())

    # The longest material chord available in this direction on this panel,
    # for context ("is this cut nicking a corner").
    best_chord = 0.0
    sweep_lo, sweep_hi = lo + 1.0, hi - 1.0
    normal = across if direction == "along" else along
    unit_master = along if direction == "along" else across
    for value in np.linspace(sweep_lo, sweep_hi, 160):
        probe = LineString([normal * value - unit_master * reach, normal * value + unit_master * reach])
        best_chord = max(best_chord, float(material.intersection(probe).length))

    return {
        "seam_id": seam.seam_id,
        "panel_id": seam.panel_id,
        "mode": seam.mode,
        "geometric_direction": direction,
        "endpoints": [round(float(v), 2) for v in (seam.x1, seam.y1, seam.x2, seam.y2)],
        "seam_length_mm": round(length, 2),
        "angle_to_along_deg": angle_between_deg(unit, along),
        "angle_to_across_deg": angle_between_deg(unit, across),
        "material_chord_mm": round(float(material_hit.length), 2),
        "material_segments": len(material_parts),
        "outer_span_edge_to_edge_mm": None if full_span is None else round(full_span, 2),
        "seam_minus_outer_span_mm": None if full_span is None else round(length - full_span, 3),
        "endpoint_dist_to_outer_boundary_mm": [round(float(outer_only.exterior.distance(LineString([p, p]).centroid)), 3),
                                              round(float(outer_only.exterior.distance(LineString([q, q]).centroid)), 3)],
        "line_crosses_cutouts": hole_hits,
        "drawn_seam_intersects_material": bool(drawn.intersects(material)),
        "sheet_frame_offset_mm": round(offset, 2),
        "panel_extent_in_that_axis_mm": [round(lo, 2), round(hi, 2)],
        "offset_fraction_across_panel": round((offset - lo) / (hi - lo), 4) if hi > lo else None,
        "best_material_chord_in_this_direction_mm": round(best_chord, 2),
        "chord_as_fraction_of_best": round(float(material_hit.length) / best_chord, 4) if best_chord else None,
    }


def plan_summary(result: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """The production plan's own numbers in the shape the optimiser reports."""

    placements = {}
    for sheet in result["sheets"]:
        for placement in sheet["placements"]:
            placements[placement["piece_id"]] = {
                "sheet": sheet["sheet"], "width_mm": placement["width_mm"],
                "length_mm": placement["length_mm"], "rotation_deg": placement["rotation_deg"]}
    limit_w = float(options["max_part_width_mm"])
    limit_l = float(options["max_part_length_mm"])
    pieces = []
    for piece in result["pieces"]:
        placed = placements.get(piece["piece_id"])
        pieces.append({
            **piece,
            "placed": placed,
            "inside_envelope": (None if placed is None else
                                bool(placed["width_mm"] <= limit_w and placed["length_mm"] <= limit_l)),
        })
    sheet_area = float(options["sheet_width_mm"]) * float(options["sheet_length_mm"])
    unplaced = set(result["summary"]["unplaced_piece_ids"])
    area = sum(float(p["area_mm2"]) for p in result["pieces"] if p["piece_id"] not in unplaced)
    sheets_n = len(result["sheets"])
    waste = (1.0 - area / (sheets_n * sheet_area)) * 100.0 if sheets_n else 100.0
    return {
        "status": result["status"],
        "sheet_count": sheets_n,
        "piece_count": result["piece_count"],
        "seam_count": result["seam_count"],
        "oversize": [item["piece_id"] for item in result["oversize"]],
        "oversize_detail": result["oversize"],
        "unplaced": sorted(unplaced),
        "refused": result.get("refused"),
        "waste_percent": round(waste, 2),
        "utilisation": [sheet["utilisation"] for sheet in result["sheets"]],
        "pieces": pieces,
        "warnings": result["warnings"],
        "seams_as_cut": result["seams"],
        "seam_snaps": result["seam_snaps"],
    }


def forbidden_words(*texts: str) -> list[str]:
    found = []
    blob = " ".join(t for t in texts if t).lower()
    for word in ("optimal", "optimum", "best possible", "guarantee", "proven", "perfect"):
        if word in blob:
            found.append(word)
    return found
