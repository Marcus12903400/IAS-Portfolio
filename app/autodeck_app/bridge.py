"""Glue to the AutoDeck2 engine: run the stages, and turn a run into overlay
geometry -- in the world frame (drawn over the mesh) and in the flat panel
layout (as in outline.dxf)."""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy.spatial import cKDTree

from . import settings

Log = Callable[[str], None]


def _engine():
    """Late import so a missing engine is reported by the server, not at import time."""

    settings.ensure_engine_on_path()
    import autodeck2  # noqa: F401
    from autodeck2 import autofit, config, engine, ingest, layout, pipeline, teak, v1compat
    return autofit, config, engine, ingest, layout, pipeline, teak, v1compat


# ---------------------------------------------------------------------------
# runs

def list_runs() -> list[dict[str, Any]]:
    runs = []
    if not settings.RUNS_DIR.is_dir():
        return runs
    for run_dir in sorted(settings.RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta_path = run_dir / "run.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runs.append({
            "run_id": run_dir.name, "run_dir": str(run_dir), "input_name": meta.get("input_name"),
            "input_path": meta.get("input_path"), "input_exists": Path(str(meta.get("input_path", ""))).is_file(),
            "status": meta.get("status"), "units": meta.get("units"), "layout_mode": meta.get("layout_mode"),
            "panels": len(meta.get("panels", [])), "teak": bool((meta.get("teak") or {}).get("enabled")),
            "pattern": ((meta.get("teak") or {}).get("pattern") or ("teak" if (meta.get("teak") or {}).get("enabled") else None)),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(meta_path.stat().st_mtime)),
            "files": run_files(run_dir),
        })
    return runs


def run_files(run_dir: Path) -> dict[str, bool]:
    names = ["outline.3dm", "outline.dxf", "auto_cam.3dm", "final_auto.dxf", "final.dxf",
             "outline_report.md", "autofit_report.md", "final_report.md", "calibration_report.md",
             # seams_previous.json is the copy the seam optimiser takes before it
             # replaces anything, so it has to be listed or the user cannot get
             # their hand-placed seams back.  The numbered copies are the older
             # ones stepped aside by later presses, and seams_hand.json is the
             # first hand-placed set, kept for good -- same reason for all of
             # them: an unlisted backup is a backup nobody knows they have.
             "run.json", "sheet_report.md", "seams.json", "seams_previous.json", "seams_hand.json"]
    files = {name: (run_dir / name).is_file() for name in names}
    for path in sorted(run_dir.glob("seams_previous_*.json")):
        files[path.name] = True
    # Sheet DXFs are numbered and there can be any number of them.
    for path in sorted(run_dir.glob("sheet_*.dxf")):
        files[path.name] = True
    return files


# ---------------------------------------------------------------------------
# jobs

def pattern_overrides(pattern: str, size_mm: float | None) -> dict[str, float] | None:
    """One 'size' number from the page -> the engine's pattern dimensions.
    teak: spacing on centre; diamond: long diagonal (short = half, the stock
    2:1 stitch); hex: across the flats."""

    if size_mm is None or size_mm <= 0:
        return None
    if pattern == "teak":
        return {"teak_spacing_mm": float(size_mm)}
    if pattern == "diamond":
        return {"diamond_long_diagonal_mm": float(size_mm), "diamond_short_diagonal_mm": float(size_mm) / 2.0}
    if pattern == "hex":
        return {"hex_across_flats_mm": float(size_mm)}
    return None


def job_outline(scan_path: Path, units: str | None, layout_mode: str, pattern: str, pattern_size: float | None, log: Log) -> dict[str, Any]:
    _autofit, config_mod, _engine_mod, _ingest, _layout, pipeline, _teak, _v1 = _engine()
    config = config_mod.load_config()
    run_id = pipeline.make_run_id(scan_path) if hasattr(pipeline, "make_run_id") else None
    run_dir = settings.RUNS_DIR / run_id if run_id else None
    log("Engine: scan -> panels -> raw curves (a new scan takes minutes; cached afterwards)")
    summary = pipeline.run_outline(scan_path, config, units=units, layout_mode=layout_mode, pattern=pattern,
                                   pattern_overrides=pattern_overrides(pattern, pattern_size),
                                   run_dir=run_dir, progress=log)
    log(f"Outline written: {summary['outline']['outline_3dm']}")
    return {"run_id": Path(summary["run_dir"]).name, "run_dir": summary["run_dir"], "status": summary["status"],
            "warnings": summary.get("warnings", []), "panels": summary.get("panels", {})}


def job_autofit(run_dir: Path, log: Log) -> dict[str, Any]:
    autofit, config_mod, *_rest = _engine()
    config = config_mod.load_config()
    report = autofit.fit_run(run_dir, config, progress=log)
    lines = []
    for panel in report["panels"]:
        for loop in panel["loops"]:
            lines.append(f"panel {panel['panel_id']} {loop['kind']}: {loop['line_count']} lines + {loop['arc_count']} arcs, "
                         f"{loop['corner_count']} corners, max dev {loop['max_deviation_mm']:.1f} mm"
                         + (f", {len(loop['flagged'])} flagged" if loop["flagged"] else ""))
    for line in lines:
        log(line)
    log(f"Auto-fit status: {report['status']}")
    return {"status": report["status"], "final_auto_dxf": bool(report.get("final_auto_dxf")),
            "missing_panels": report.get("missing_panels", []), "flagged_total": report.get("flagged_total", 0), "lines": lines}


def job_ingest(run_dir: Path, drawing: Path, log: Log) -> dict[str, Any]:
    _autofit, config_mod, _engine_mod, ingest, *_rest = _engine()
    config = config_mod.load_config()
    log(f"Ingesting {drawing.name}")
    try:
        result = ingest.ingest_run(run_dir, drawing, config)
    except ingest.IngestError as exc:
        raise RuntimeError(str(exc)) from exc
    for rejection in result.rejections:
        log(f"rejected: {rejection}")
    for pid, panel in result.panels.items():
        for problem in panel.problems:
            log(f"panel {pid}: {problem}")
        for loop in panel.loops_report:
            s = loop["stats"]
            log(f"panel {pid} {loop['kind']}: {s['primitive_count']} primitives ({s['line_count']}L/{s['arc_count']}A), "
                f"{s['tangent_failure_count']} tangent failures, {s['intentional_corner_count']} corners")
    log(f"Ingest status: {result.status}")
    return {"status": result.status, "final_dxf": bool(result.final_dxf), "rejections": result.rejections}


# ---------------------------------------------------------------------------
# sheets: seams, nesting, per-sheet DXF


def _sheet_modules():
    settings.ensure_engine_on_path()
    from autodeck2 import config as config_mod, seamplace, sheetjob, sheets
    return config_mod, sheetjob, sheets, seamplace


def _sheet_config(overrides: dict[str, Any]) -> dict[str, Any]:
    config_mod, _sheetjob, _sheets, _seamplace = _sheet_modules()
    config = config_mod.load_config()
    if overrides:
        config = {**config, "sheets": {**(config.get("sheets") or {}), **overrides}}
    return config


# How finely a seam is chopped up before being lifted onto the 3D model.  A deck
# is not flat, so a seam drawn as one straight line in the layout is a curve over
# the mesh; 20 mm is the same spacing the fitted CAM outline is drawn at, and on
# the crown of a cockpit sole it is well inside a tenth of a millimetre of sag.
SEAM_WORLD_STEP_MM = 20.0


def seam_direction_deg(seam: Any) -> float:
    return math.degrees(math.atan2(seam.y2 - seam.y1, seam.x2 - seam.x1)) % 180.0


def _seam_payload(seam: Any, snap: dict[str, Any] | None) -> dict[str, Any]:
    """One seam as the page sees it: where it will be cut, where the user put
    it, how it was placed, and what the corrector did to it.

    Coordinates are NOT rounded here.  The page hands this straight back on the
    next edit, and `raw` is what every future correction is recomputed from, so
    rounding it would let a seam creep by a hundredth of a millimetre per edit.
    JSON round-trips a double exactly in both Python and the browser.
    """

    payload = seam.to_dict()
    payload["length_mm"] = round(float(math.hypot(seam.x2 - seam.x1, seam.y2 - seam.y1)), 2)
    payload["direction_deg"] = round(seam_direction_deg(seam), 3)
    payload.update(
        snap_applied=bool(snap["applied"]) if snap else False,
        snap_kind=snap["kind"] if snap else "",
        snap_note=snap["note"] if snap else "",
        snap_reference=snap["reference_label"] if snap else "",
        snap_angle_deg=snap["angle_change_deg"] if snap else 0.0,
        snap_moved_mm=snap["moved_mm"] if snap else 0.0,
    )
    return payload


def _boat_payload(frame: Any, warnings: list[str]) -> dict[str, Any]:
    """The resolved boat frame, for a flat view that has to draw itself bow up.

    Everything the seam tab needs to orient itself is here and nothing is
    invented: with no axis, `axis` is None and the view is expected to stay as it
    is and say why, and with an unsure bow, `bow_confidence` says so rather than
    the page asserting which end is which.
    """

    payload = frame.to_dict()
    payload["axis_deg"] = (None if frame.axis is None
                           else round(math.degrees(math.atan2(frame.axis[1], frame.axis[0])) % 180.0, 3))
    bow = frame.bow_direction
    payload["bow_deg"] = (None if bow is None
                          else round(math.degrees(math.atan2(bow[1], bow[0])) % 360.0, 3))
    payload["warnings"] = list(warnings)
    return payload


def panel_shapes(view: "RunView", options: dict[str, Any]) -> tuple[dict[int, Any], dict[int, Any]]:
    """The run's fitted loops and the shapely polygons built from them, cached
    on the view.

    The polygons are the expensive half of a seam hover -- a full deck panel
    samples to thousands of points at the 1 mm step -- and a hover happens on
    every pointer move.  Rebuilding them per call would make the tool feel
    broken, so they are built once and kept until the fitted DXF changes on disk
    (a re-run of auto-fit, or an ingest), which the modification time catches.
    """

    _config_mod, sheetjob, sheets_mod, seamplace = _sheet_modules()
    source = sheetjob.source_dxf(view.run_dir)
    if source is None:
        return {}, {}
    key = (str(source), source.stat().st_mtime_ns, round(float(options.get("sample_step_mm", 1.0)), 6))
    cached = view.sheet_cache.get("shapes")
    if cached is None or cached[0] != key:
        loops = sheets_mod.read_fitted_dxf(source)[0]
        cached = (key, loops, seamplace.panel_polygons(loops, options))
        view.sheet_cache["shapes"] = cached
    return cached[1], cached[2]


def seam_world_polylines(view: "RunView", seams: Any, options: dict[str, Any]) -> list[dict[str, Any]]:
    """Each seam drawn ON THE DECK, so the 3D view can be used to check it.

    A seam is stored as a straight line in the flat layout, but the cut runs
    across a curved deck, so the line has to be clipped to the part, chopped up
    and lifted through the same smooth surface fit the CAM overlay uses.

    HOW MUCH of the line is drawn follows how the seam will be CUT (see
    `sheets.Seam.chord_bound`): a seam placed with a direction tool is its own
    chord -- the stretch that was hovered and clicked, broken around the
    console -- and only that chord goes on the deck.  A legacy free seam is cut
    as the whole line, so the whole line is drawn, chords of the panel it
    crosses, holes taken out.  Drawing one thing and cutting another is exactly
    the defect this halves of the pipeline used to have.
    """

    _config_mod, _sheetjob, _sheets_mod, seamplace = _sheet_modules()
    _loops, polygons = panel_shapes(view, options)
    out: list[dict[str, Any]] = []
    for seam in seams:
        unit = np.array([seam.x2 - seam.x1, seam.y2 - seam.y1], dtype=float)
        length = float(np.hypot(*unit))
        if length < 1e-9 or not polygons:
            out.append({"seam_id": seam.seam_id, "world": []})
            continue
        if seam.chord_bound:
            pid = seam.panel_id
            if pid is None or pid not in view.placements or pid not in view.lifters:
                out.append({"seam_id": seam.seam_id, "world": []})
                continue
            ends = np.array([[seam.x1, seam.y1], [seam.x2, seam.y2]])
            dense = _densify(ends, SEAM_WORLD_STEP_MM)
            out.append({"seam_id": seam.seam_id,
                        "world": [_rounded(lift_proud(view, pid, _unplace(view.placements[pid], dense)))]})
            continue
        crossed = seamplace.panels_crossed(seam.x1, seam.y1, seam.x2, seam.y2,
                                           polygons, options, seam.panel_id)
        middle = np.array([(seam.x1 + seam.x2) / 2.0, (seam.y1 + seam.y2) / 2.0])
        pieces: list[list[list[float]]] = []
        for segment in seamplace.seam_through(middle, unit / length, polygons, options,
                                              panel_ids=crossed):
            pid = int(segment["panel_id"])
            if pid not in view.placements or pid not in view.lifters:
                continue
            ends = np.array([[segment["x1"], segment["y1"]], [segment["x2"], segment["y2"]]])
            dense = _densify(ends, SEAM_WORLD_STEP_MM)
            pieces.append(_rounded(lift_proud(view, pid, _unplace(view.placements[pid], dense))))
        out.append({"seam_id": seam.seam_id, "world": pieces})
    return out


# How a seam with no id of its own is named on the way in: the next free
# "s<number>".  Naming by LIST POSITION (the old s{i + 1}) is what let
# remove-then-place produce two seams called s4 -- the survivor kept its old
# name and the newcomer was given the same one by index -- and everything that
# merges replies by id then silently conflated them.
_S_ID = re.compile(r"^s(\d+)$")


def _fresh_seam_ids(seams: list[dict[str, Any]]) -> None:
    """Give every id-less seam in the posted list a unique id, in place.

    The number is the next free one past the highest `s<n>` already in the
    list, so a seam removed and a seam placed in the same breath can never end
    up sharing a name, and a reply merged by id can never overwrite one seam
    with another.
    """

    highest = 0
    for seam in seams:
        match = _S_ID.match(str(seam.get("seam_id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    for seam in seams:
        if not seam.get("seam_id"):
            highest += 1
            seam["seam_id"] = f"s{highest}"


def sheet_preview(run_dir: Path, overrides: dict[str, Any],
                  seams: list[dict[str, Any]] | None = None, save: bool = False,
                  view: "RunView | None" = None) -> dict[str, Any]:
    """Seams plus the nested sheet layout as drawable rings; writes no DXFs.

    `view` is the loaded run, and it is optional because the sheet workflow has
    to keep working before one is open: with it, every seam also comes back
    lifted onto the 3D model under `seams_world`, and without it that key is
    simply absent rather than the whole call failing.
    """

    _config_mod, sheetjob, sheets_mod, _seamplace = _sheet_modules()
    config = _sheet_config(overrides)
    if seams is not None:
        _fresh_seam_ids(seams)
        parsed = [sheets_mod.Seam.from_dict(s) for s in seams]
        if save:
            # The edit lands on disk BEFORE anything is planned.  A re-plan can
            # refuse (no fitted geometry, a setting that cannot be cut with),
            # and "removing seams must always work" cannot depend on the plan's
            # good humour: the removal -- or any edit -- is stored first, and
            # if the plan then succeeds the corrected list overwrites it a
            # moment later with exactly the same seams as cut.
            sheets_mod.write_seams(run_dir, parsed)
    else:
        parsed = sheets_mod.read_seams(run_dir)
    resolved = sheets_mod.settings(config)

    frame, axis_warnings = sheetjob.resolve_frame(run_dir, resolved)
    if sheetjob.source_dxf(run_dir) is None:
        if save:
            sheets_mod.write_seams(run_dir, parsed)
        result = {"available": False,
                  "reason": "run auto-fit (or ingest a drawing) first -- sheets are cut from the fitted outline",
                  "seams": [_seam_payload(s, None) for s in parsed], "settings": resolved,
                  "boat": _boat_payload(frame, axis_warnings)}
        if view is not None:
            # The flat view still needs the panel layout to draw itself bow up
            # and un-nested, fitted geometry or not (audit F48).
            result["panels"] = _panel_layout(view)
        return result

    result = sheetjob.preview(run_dir, config, seams=parsed)
    # The seams AS CUT, straightened once by sheetjob and reported here so the
    # page and the router are looking at the same lines.  These are what gets
    # saved: each carries the user's own drawing in `raw`, so re-opening the run
    # re-derives exactly the same correction rather than compounding it.
    cut = [sheets_mod.Seam.from_dict(item) for item in result["seams"]]
    if save:
        sheets_mod.write_seams(run_dir, cut)
    result["available"] = True
    result["seams"] = [_seam_payload(seam, snap)
                       for seam, snap in zip(cut, result.get("seam_snaps") or [None] * len(cut))]
    result["settings"] = resolved
    result["boat"] = _boat_payload(frame, axis_warnings)
    if view is not None:
        result["seams_world"] = seam_world_polylines(view, cut, resolved)
        result["panels"] = _panel_layout(view)
    return result


def _panel_layout(view: "RunView") -> list[dict[str, Any]]:
    """Where each panel sits in the layout, and where it sits on the BOAT.

    The flat view nests the panels for cutting, which is the wrong picture for
    laying seams out -- the fabricator needs to see the deck as it is on the
    boat.  Nest mode only ever translates a panel, so the boat-plan position is
    the placed position minus that translation, and handing over the offset is
    all the view needs to switch between the two.
    """

    panels: list[dict[str, Any]] = []
    for pid in sorted(view.placements):
        placement = view.placements[pid]
        outline = placement.placed_outline
        bbox = (None if outline is None or not len(outline)
                else [outline.min(axis=0).round(2).tolist(), outline.max(axis=0).round(2).tolist()])
        panels.append({
            "panel_id": pid,
            "role": view.result.panels[pid].role if pid in view.result.panels else None,
            "nest_offset_mm": np.round(np.asarray(placement.nest_offset, dtype=float), 4).tolist(),
            "bbox_placed_mm": bbox,
        })
    return panels


def hover_context(view: "RunView", overrides: dict[str, Any]) -> tuple[dict[str, Any], Any, list[str]]:
    """The resolved sheet settings and boat frame for a hover, cached.

    Both are re-derived from files -- config.yaml for one, run.json for the
    other -- and a hover happens on every pointer move, so reading them each
    time was a couple of milliseconds spent re-answering a question whose answer
    only changes when a stage re-runs.  The cache key covers the overrides the
    page sent and run.json's modification time, so a manual grain angle or a new
    outline is picked up on the very next hover.
    """

    _config_mod, sheetjob, sheets_mod, _seamplace = _sheet_modules()
    meta = view.run_dir / "run.json"
    key = (json.dumps(overrides or {}, sort_keys=True, default=str),
           meta.stat().st_mtime_ns if meta.is_file() else 0)
    cached = view.sheet_cache.get("context")
    if cached is None or cached[0] != key:
        options = sheets_mod.settings(_sheet_config(overrides))
        frame, warnings = sheetjob.resolve_frame(view.run_dir, options)
        cached = (key, options, frame, warnings)
        view.sheet_cache["context"] = cached
    return cached[1], cached[2], cached[3]


def seam_hover(view: "RunView", overrides: dict[str, Any], mode: str,
               angle_deg: float | None = None, point_flat: Any = None,
               point_world: Any = None, with_world: bool = True) -> dict[str, Any]:
    """The seam that WOULD be placed under the pointer, trimmed to the part.

    This is the whole of the new seam tool's feel: choose a direction, move the
    pointer, and watch the join snap from edge to edge of the panel, broken
    around the console, with its length on show so the piece size can be judged
    before anything is committed.  It has to answer in a few milliseconds, which
    is why the panel polygons are cached on the view.

    The direction comes from the same `resolve_axis` the cut uses, so a seam
    placed by hovering needs no straightening afterwards -- it IS the master
    direction, to the last bit.

    The POSITION is settled here too, for the same reason.  The corrector is
    still allowed to slide a placed seam sideways onto a fitted edge that runs
    the same way, and it should: landing exactly on a console edge is worth
    having.  But it used to do that after the click, so the line jumped up to
    two centimetres away from the one the preview had drawn.  The same slide is
    applied to the hovered line, so what is on screen is what gets cut.
    """

    _config_mod, _sheetjob, _sheets_mod, seamplace = _sheet_modules()
    options, frame, axis_warnings = hover_context(view, overrides)
    unit = seamplace.direction_for(frame.axis, mode, angle_deg)
    if unit is None:
        return {"segments": [], "world": [], "direction_deg": None,
                "boat": _boat_payload(frame, axis_warnings),
                "reason": ("this run has no boat direction, so there is no along or across the boat "
                           "to place a seam on -- set the grain angle first")}

    off_deck = {"segments": [], "world": [],
                "direction_deg": round(seamplace.direction_degrees(unit), 3),
                "boat": _boat_payload(frame, axis_warnings),
                "reason": "that is not on the deck"}

    if point_flat is None:
        picks = pick_flat(view, np.asarray(point_world, dtype=float).reshape(1, -1))
        if not picks or picks[0]["panel_id"] is None:
            return off_deck
        point = np.array([picks[0]["x"], picks[0]["y"]], dtype=float)
    else:
        point = np.asarray(point_flat, dtype=float).ravel()[:2]

    _loops, polygons = panel_shapes(view, options)
    segments = seamplace.seam_through(point, unit, polygons, options)
    if not segments:
        # Both branches answer the same way off the deck. The flat one used to
        # return an empty list with no reason at all, which contradicted the
        # documented contract and left the page with nothing to say.
        return dict(off_deck, point_flat=[round(float(point[0]), 2), round(float(point[1]), 2)])

    # The settle pool is exactly what `sheetjob.apply_snap` will offer the
    # clicked chord: the boat's two directions, the fitted edges, and the seams
    # already on the deck (which are stored in their corrected positions).
    # Settling against anything else is how the preview and the cut came to
    # disagree by a fraction of a millimetre.
    from autodeck2 import seamsnap
    references = (seamsnap.axis_references(frame.axis)
                  + edge_references(view, options) + seam_references(view, options))
    segments = _settled(view, options, segments, unit, mode, angle_deg, frame.axis,
                        point, references)
    # The endpoints are NOT rounded: the page stores whichever chord the user
    # clicks as the seam's `raw`, and rounding it to a hundredth of a millimetre
    # would knock it off the master direction just enough that the corrector
    # then reports having "squared" a seam that was already square.  Only the
    # length is rounded, because that one is read by a human.
    payload = [{"panel_id": s["panel_id"], "x1": s["x1"], "y1": s["y1"],
                "x2": s["x2"], "y2": s["y2"],
                "length_mm": round(s["length_mm"], 1)} for s in segments]

    world: list[list[list[float]]] = []
    if with_world:
        for segment in segments:
            pid = int(segment["panel_id"])
            if pid not in view.placements or pid not in view.lifters:
                # One world polyline per segment, same order -- the documented
                # contract. A panel with no lifter has no line to draw on the
                # deck, and skipping it outright would slide every later
                # polyline onto the wrong segment.
                world.append([])
                continue
            ends = np.array([[segment["x1"], segment["y1"]], [segment["x2"], segment["y2"]]])
            dense = _densify(ends, SEAM_WORLD_STEP_MM)
            world.append(_rounded(lift_proud(view, pid, _unplace(view.placements[pid], dense))))

    return {"segments": payload, "world": world,
            "direction_deg": round(seamplace.direction_degrees(unit), 3),
            "point_flat": [round(float(point[0]), 2), round(float(point[1]), 2)],
            "boat": _boat_payload(frame, axis_warnings)}


def edge_references(view: "RunView", options: dict[str, Any]) -> list[Any]:
    """The fitted edges a seam may be lined up with, cached on the view.

    Built from the same loops the cut is made from, by the same call
    `sheetjob.apply_snap` makes, so a hover and a placement cannot disagree
    about what is nearby.  Sampling every loop is far too slow to repeat on each
    pointer move, hence the cache; it is keyed on the fitted DXF's modification
    time exactly as `panel_shapes` is.
    """

    _config_mod, sheetjob, _sheets_mod, _seamplace = _sheet_modules()
    from autodeck2 import seamsnap

    source = sheetjob.source_dxf(view.run_dir)
    if source is None:
        return []
    key = (str(source), source.stat().st_mtime_ns,
           round(float(options.get("seam_snap_min_ref_length_mm", 15.0)), 6),
           round(float(options.get("seam_snap_min_ref_radius_mm", 10.0)), 6),
           round(float(options.get("sample_step_mm", 1.0)), 6))
    cached = view.sheet_cache.get("edges")
    if cached is None or cached[0] != key:
        loops, _polygons = panel_shapes(view, options)
        cached = (key, seamsnap.references_from_loops(loops, options))
        view.sheet_cache["edges"] = cached
    return cached[1]


def seam_references(view: "RunView", options: dict[str, Any]) -> list[Any]:
    """The seams already on this run, as references, exactly as
    `sheetjob.apply_snap` offers them to the seam being placed.

    The hover has to see these or the preview is not the truth.  A new seam that
    CONTINUES one already on the deck is slid onto its line when it is placed --
    that is what makes a join carry straight on across the gap between two
    panels -- and while the preview knew only about fitted edges, the line
    visibly jumped by up to `seam_snap_offset_mm` at the click.

    They are read from seams.json rather than taken from the request, because
    that file is what `apply_snap` will itself read, and it holds the CORRECTED
    endpoints: `sheet_preview(save=True)` writes them back after every edit. So
    the pool the preview uses and the pool the click uses are the same list.

    Cheap enough to key on the file's modification time and rebuild: a seam list
    is a few hundred bytes of JSON and a Reference is two endpoints -- there is
    no sampling here, which is the only expensive part of `edge_references`.
    """

    _config_mod, _sheetjob, sheets_mod, _seamplace = _sheet_modules()
    from autodeck2 import seamsnap

    path = view.run_dir / "seams.json"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return []
    min_length = float(options.get("seam_snap_min_ref_length_mm", 15.0))
    key = (stamp, round(min_length, 6))
    cached = view.sheet_cache.get("seamrefs")
    if cached is None or cached[0] != key:
        built = []
        try:
            stored = sheets_mod.read_seams(view.run_dir)
        except (ValueError, OSError):
            # A hover must never fail on the seam file, and this is the one
            # reader that can genuinely arrive in the middle of a write: the
            # pointer is moving twenty-five times a second while a re-plan
            # rewrites seams.json.  `sheets.write_seams` explains why that write
            # is not atomic.  Keeping the previous list for one frame is exactly
            # right -- it is the list that was there a moment ago, and the next
            # pointer move rebuilds it.
            return cached[1] if cached else []
        for index, seam in enumerate(stored):
            p0 = np.array([seam.x1, seam.y1], dtype=float)
            p1 = np.array([seam.x2, seam.y2], dtype=float)
            if float(np.hypot(*(p1 - p0))) < min_length:
                continue
            built.append(seamsnap.Reference("seam", f"seam {seam.seam_id or index + 1}",
                                            seam.panel_id, p0, p1))
        cached = (key, built)
        view.sheet_cache["seamrefs"] = cached
    return cached[1]


def _settled(view: "RunView", options: dict[str, Any],
             segments: list[dict[str, Any]], unit: np.ndarray,
             mode: str, angle_deg: float | None, axis: Any,
             point: np.ndarray, references: list[Any]) -> list[dict[str, Any]]:
    """Each hovered chord slid onto a fitted edge that runs the same way, if one
    is near enough -- the corrector's rule 2, applied before the click instead of
    after it.

    The settle IS the correction now, not an approximation of it: each chord is
    wrapped in a real `sheets.Seam` with its mode and panel and pushed through
    `sheetjob.apply_snap` against the same reference pool the click will use,
    so the line the user commits is bit for bit the line that gets cut.  The
    two used to be two slightly different slidings of the same chord, and the
    difference (up to 0.87 mm) is what "the note says it moved although the
    preview had settled" was.

    A chord that did move is then re-trimmed to its panel, because the ends of
    the old chord were the old line's crossings of the outline and the new line
    crosses it somewhere else.  Of the chords the re-trim offers, the one kept
    is the one whose stretch BRACKETS THE POINTER -- not the one whose middle
    is nearest the slid middle.  Sweeping along the boat beside the console,
    the slid middle sits under the console where there is no chord at all, and
    nearest-the-middle used to answer with the far side of it: the preview
    vanished for a hundred millimetres exactly where the user was aiming.
    """

    _config_mod, sheetjob, _sheets_mod, seamplace = _sheet_modules()

    if not bool(options.get("seam_snap_enabled", True)):
        return segments
    _loops, polygons = panel_shapes(view, options)

    settled: list[dict[str, Any]] = []
    for segment in segments:
        raw = (segment["x1"], segment["y1"], segment["x2"], segment["y2"])
        hover = _sheets_mod.Seam("hover", *raw, panel_id=int(segment["panel_id"]),
                                 snap=True, raw=raw, mode=mode, angle_deg=angle_deg)
        try:
            (cut,), (_snap,), _w = sheetjob.apply_snap([hover], _loops, axis, options,
                                                       references=list(references))
        except (ValueError, TypeError):
            settled.append(segment)
            continue
        moved = max(abs(cut.x1 - raw[0]), abs(cut.y1 - raw[1]),
                    abs(cut.x2 - raw[2]), abs(cut.y2 - raw[3]))
        if moved <= 1e-6:
            settled.append(segment)
            continue
        middle = np.array([(cut.x1 + cut.x2) / 2.0, (cut.y1 + cut.y2) / 2.0])
        panel_id = int(segment["panel_id"])
        again = seamplace.seam_through(middle, unit, polygons, options, panel_ids=[panel_id])
        if again:
            settled.append(_chord_under_pointer(again, point, unit, middle))
        else:
            # The slide took the line off the part altogether, which only a
            # reference right at the edge could do. Keep what was previewed.
            settled.append(segment)
    return settled


def _chord_under_pointer(chords: list[dict[str, Any]], point: np.ndarray,
                         unit: np.ndarray, anchor: np.ndarray) -> dict[str, Any]:
    """The chord whose stretch along the line contains the pointer's foot.

    `anchor` is any point on the settled line; each chord's parameter range is
    measured from it along `unit`, and the pointer's own parameter has to fall
    inside one of them (with a couple of millimetres of grace for the click
    being at the very end of a chord).  Nothing brackets it -- the pointer is
    over the console -- and the nearest chord by parameter distance stands in,
    which is the old behaviour for that one honest case.
    """

    where = float(np.dot(point - anchor, unit))
    best: dict[str, Any] | None = None
    best_gap = float("inf")
    for chord in chords:
        start = float(np.dot(np.array([chord["x1"], chord["y1"]]) - anchor, unit))
        end = float(np.dot(np.array([chord["x2"], chord["y2"]]) - anchor, unit))
        low, high = (start, end) if start <= end else (end, start)
        if low - 2.0 <= where <= high + 2.0:
            return chord
        gap = low - where if where < low else where - high
        if gap < best_gap:
            best, best_gap = chord, gap
    return best if best is not None else chords[0]


def job_sheets(run_dir: Path, overrides: dict[str, Any], log: Log) -> dict[str, Any]:
    _config_mod, sheetjob, _sheets, _seamplace = _sheet_modules()
    result = sheetjob.plan(run_dir, _sheet_config(overrides), progress=log)
    for warning in result.get("warnings", []):
        log(f"warning: {warning}")
    for entry in result.get("files", []):
        log(f"{entry['name']}: {len(entry['pieces'])} piece(s), "
            f"{entry['utilisation'] * 100:.0f}% used, closed polylines={entry['all_closed']}")
    log(f"Sheets: {result['summary']['sheet_count']} for {result['piece_count']} piece(s) "
        f"-- status {result['status']}")
    return {"status": result["status"], "sheet_count": result["summary"]["sheet_count"],
            "piece_count": result["piece_count"],
            "files": [entry["name"] for entry in result.get("files", [])],
            "oversize": result.get("oversize", [])}


# How long the "work out the best seams" button is allowed to search for.  A
# minute and a half is about as long as anyone will watch a progress log without
# deciding the program has hung, and on the boats measured so far the search has
# run out of arrangements to try well before it runs out of time.
OPTIMISE_BUDGET_S = 90.0

# The copy taken before the optimiser replaces the user's seams.  It is a file
# name and not a directory of versions on purpose: one obvious thing to open
# when the answer is wrong, listed with every other file the run produced.
SEAMS_BACKUP = "seams_previous.json"

# The FIRST hand-placed seam set, kept under a name the optimiser never rotates
# away, so the fabricator's original work survives any number of presses.  It
# is written once, the first time the button is pressed on a run that already
# has seams, and never overwritten after that.
SEAMS_HAND = "seams_hand.json"


def _keep_older_backup(backup: Path, log: Log) -> None:
    """Step an existing seams_previous.json aside before it is replaced.

    Numbered from 1 upwards, oldest number first, so the names read in the order
    they were made.  Nothing here can fail the press: if the rename cannot be
    done the button still runs, and the user still has an undo for the seams
    they are about to lose -- only the older copy goes.
    """

    if not backup.is_file():
        return
    for index in range(1, 100):
        older = backup.with_name(f"{backup.stem}_{index}{backup.suffix}")
        if older.exists():
            continue
        try:
            backup.replace(older)
            log(f"The previous copy was kept as {older.name}.")
        except OSError as error:
            log(f"warning: could not keep the older seam backup ({error}); it will be replaced")
        return


def job_optimise_seams(run_dir: Path, overrides: dict[str, Any], log: Log,
                       time_budget_s: float = OPTIMISE_BUDGET_S) -> dict[str, Any]:
    """Work out where the seams should go, and put them there.

    Two rules keep this from being something the user regrets pressing:

      * the seams that are already on the run are COPIED to `seams_previous.json`
        before anything is written, and the log says so while it is happening,
        so a hand-placed set is never gone;
      * nothing is written at all unless the arrangement found is actually
        better than what is there.  The optimiser reports its own result
        honestly, including when it loses, and losing has to mean "leave the
        fabricator's work alone" rather than "overwrite it with something
        worse".

    The seams go through `sheet_preview(save=True)` -- the same path the seam
    tab writes with -- so an optimised seam is stored exactly like a drawn one:
    straightened once, with the chosen line kept in `raw`, and re-planned from
    there ever after.
    """

    _config_mod, _sheetjob, sheets_mod, _seamplace = _sheet_modules()
    from autodeck2 import seamplan

    config = _sheet_config(overrides)
    existing = sheets_mod.read_seams(run_dir)
    log(f"Working out the best seam positions. This run has {len(existing)} seam(s) now; "
        f"searching for up to {float(time_budget_s):.0f} seconds.")

    # The backup's CONTENT is decided now, before the search starts, though the
    # file itself is only written once the answer is about to be saved.  The
    # search runs for a minute and a half, and the seams it read at the start
    # used to be copied at the end -- so a set the user changed mid-search never
    # made it into the backup, and an undo restored three of four seams with the
    # fourth gone for good.  Holding the pressed bytes also means a refusal or
    # a no-improvement press touches nothing on disk at all.
    source = Path(run_dir) / "seams.json"
    backup = Path(run_dir) / SEAMS_BACKUP
    hand = Path(run_dir) / SEAMS_HAND
    pressed_at = source.stat().st_mtime_ns if source.is_file() else None
    pressed_bytes = source.read_bytes() if source.is_file() else None

    result = seamplan.optimise(run_dir, config, progress=log, time_budget_s=float(time_budget_s))
    report = result["report"]
    for warning in report.get("warnings") or []:
        log(f"warning: {warning}")

    payload: dict[str, Any] = {
        "status": result["status"],
        "reason": result["reason"],
        "improved": bool(report["improved"]),
        "seams_written": 0,
        "seams_replaced": 0,
        "backup": None,
        "before": report["before"],
        "after": report["after"],
        "panels": report["panels"],
        "candidates_evaluated": report["candidates_evaluated"],
        "candidates_confirmed": report["candidates_confirmed"],
        "elapsed_s": report["elapsed_s"],
        "budget_exhausted": report["budget_exhausted"],
        "search_note": report["search_note"],
    }

    if not result["seams"] or not report["improved"]:
        # `improved` says the search found something better; it must not go on
        # saying so once nothing has been written, because the page puts the
        # headline "0 seams . 1 sheet . 38% waste" up off exactly that flag and
        # the fabricator would go and cut a run that still has every seam it
        # had. The one case where the two come apart is a winner with no seams
        # at all: this button will not erase a seam set, so it says so instead.
        payload["improved"] = False
        log(result["reason"])
        if result["seams"] == [] and report["improved"]:
            payload["reason"] = (
                "the best arrangement found needs no seams at all on this deck. This button will "
                f"not delete seams, so the {len(existing)} you have are still there -- remove them "
                "yourself if you agree.")
            log(payload["reason"])
        log(f"Nothing was changed -- the {len(existing)} seam(s) already on this run are untouched.")
        return payload

    # The seams the search read were the ones on disk when it started.  If the
    # file has changed since -- an edit from another tab, a save that raced the
    # job -- then overwriting it would throw away work the search never saw,
    # so the answer is dropped instead and the newer seams stand.
    if pressed_at is not None and source.is_file() and source.stat().st_mtime_ns != pressed_at:
        payload["improved"] = False
        payload["reason"] = ("the seams on this run changed while the search was running, so its "
                             "answer was thrown away rather than write over them. Press the button "
                             "again if you still want it.")
        log(payload["reason"])
        return payload

    # The write is happening, so now is the moment to commit the backup: the
    # numbered rotation first, then the copy of exactly what was pressed, then
    # the once-only hand copy.  Nothing here can fail the write -- if a rename
    # or copy fails, the log says so and the optimiser's seams still land.
    _keep_older_backup(backup, log)
    if pressed_bytes is not None:
        backup.write_bytes(pressed_bytes)
    else:
        backup.write_text(json.dumps({"seams": []}, indent=2), encoding="utf-8")
    log(f"Copied the {len(existing)} seam(s) that were here to {SEAMS_BACKUP} -- "
        "download it from the file list to get them back.")
    if existing and not hand.is_file():
        # The first hand-placed set, kept for good: however many times the
        # button is pressed, this one name always holds the work that was on
        # the deck before any automatic layout touched it.
        hand.write_bytes(pressed_bytes)
        log(f"Kept a copy of these as {SEAMS_HAND} -- it stays even if you press the button again.")

    saved = sheet_preview(run_dir, overrides,
                          seams=[seam.to_dict() for seam in result["seams"]], save=True)
    payload["seams_written"] = len(saved.get("seams") or [])
    payload["seams_replaced"] = len(existing)
    payload["backup"] = SEAMS_BACKUP

    before, after = report["before"], report["after"]
    log(f"Replaced {len(existing)} seam(s) with {payload['seams_written']}: "
        f"{len(before['oversize'])} piece(s) too big -> {len(after['oversize'])}, "
        f"{before['sheet_count']} sheet(s) -> {after['sheet_count']}, "
        f"{before['waste_percent']:.1f}% waste -> {after['waste_percent']:.1f}%.")
    log(f"{report['candidates_evaluated']} arrangement(s) tried in "
        f"{report['elapsed_s']:.0f}s; {report['search_note']}.")
    return payload


# ---------------------------------------------------------------------------
# a loaded run and its overlays

@dataclass
class RunView:
    run_dir: Path
    meta: dict[str, Any]
    result: Any                       # autodeck2 EngineResult
    placements: dict[int, Any]        # panel id -> PanelPlacement
    teak_lines: Any                   # dict[pid, list[(p0, p1)]] in the placed frame, or None
    mm_per_unit: float
    pattern_info: dict[str, Any] = field(default_factory=dict)
    flatten: dict[int, dict[str, Any]] = field(default_factory=dict)
    lifters: dict[int, Callable[[np.ndarray], np.ndarray]] = field(default_factory=dict)
    # World -> flat picking, built on first use: a run that is only ever viewed
    # never pays for it, and building one costs an AABB tree per panel.
    locators: dict[int, Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]] = field(default_factory=dict)
    # Fitted loops and their shapely polygons, kept between seam hovers -- see
    # panel_shapes.  Keyed by the fitted DXF's modification time, so an auto-fit
    # or an ingest invalidates it without anyone having to remember to.
    sheet_cache: dict[str, Any] = field(default_factory=dict)


def load_run(run_dir: Path, log: Log) -> RunView:
    _autofit, config_mod, engine_mod, _ingest, layout, pipeline, teak, v1compat = _engine()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    input_path = Path(meta["input_path"])
    if not input_path.is_file():
        raise FileNotFoundError(f"the scan this run was made from is not at {input_path}")
    config = config_mod.load_config()
    log("Loading developed panels (engine cache)")
    result = engine_mod.run_engine(input_path, config, units=meta.get("units"), progress=log, debug_dir=run_dir / "debug")
    placements, _warnings = layout.compute_layout(pipeline.panel_sources(result), config, meta.get("layout_mode") or "nest")
    pattern_lines = None
    pattern_info: dict[str, Any] = {}
    stored = meta.get("teak") or {}
    if stored.get("enabled"):
        pattern = str(stored.get("pattern") or "teak")
        log({"teak": "Teak lines", "diamond": "Diamond stitch", "hex": "Hexagons"}.get(pattern, pattern))
        pattern_settings = dict(engine_mod.merged_v1_config(config)["pattern"])
        pattern_settings.update(stored.get("dimensions_mm") or {})   # the run keeps its own sizes
        if hasattr(teak, "generate_pattern"):
            pattern_lines, pattern_info = teak.generate_pattern(result, placements, pattern_settings, pattern)
        else:
            pattern_lines, pattern_info = teak.generate_teak(result, placements, pattern_settings)
    mm_per_unit = float(v1compat.unit_scale_to_mm(str(meta.get("units") or result.units)))
    view = RunView(run_dir, meta, result, placements, pattern_lines, mm_per_unit, pattern_info)
    for pid, panel in result.panels.items():
        view.lifters[pid] = _make_lifter(panel.development)
        try:
            view.flatten[pid] = engine_mod.flatten_quality(panel)
        except Exception:  # noqa: BLE001 -- quality info is advisory
            view.flatten[pid] = {"strategy": panel.strategy, "status": panel.status}
    return view


_LIFT_NEIGHBOURS = 24            # development vertices averaged per query point
_LIFT_CHUNK = 20000              # query points per batch, so the (N,k,3) working arrays stay small


def _vertex_normals(xyz: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Area-weighted vertex normals of the development mesh.

    Used only to give the fitted plane's normal a SIGN.  A plane fit knows which
    way the surface tilts but not which side of it is the deck and which is the
    water, and the answer has to be the same for every point of a curve or the
    overlay would flip from proud to buried halfway along.  The mesh itself
    knows: the scan's triangles are consistently wound, so their own normals all
    point out of the deck (measured on the cached run, the mean face normal of
    every panel is within 18 degrees of world +Z).  Taking the sign from the
    nearest mesh vertex is therefore reading the answer off the geometry rather
    than guessing it from an assumed up direction, which would be wrong the
    moment a scan arrives on its side.
    """

    normals = np.zeros_like(xyz)
    if not len(faces):
        return normals
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    face = np.cross(b - a, c - a)            # length is twice the area: the weight
    for column in range(3):
        np.add.at(normals, faces[:, column], face)
    length = np.linalg.norm(normals, axis=1)
    return normals / np.maximum(length, 1e-12)[:, None]


def _make_lifter(development: Any) -> Callable[..., Any]:
    """uv (panel frame, mm) -> world xyz (mm), by fitting a small plane to the
    surface around each query point -- a degree-1 moving least squares fit.

    Why not just read the triangle the point lands in, the way _triangle_lifter
    below does?  Because the development mesh *is* the scan's own mesh: roughly
    5 mm triangles carrying the photogrammetry's few-tenths-of-a-millimetre
    noise.  Reading one triangle at a time copies that noise straight into every
    line we draw over the model, and on a 3 mm chord a half-millimetre wobble in
    the surface normal direction is about 37 degrees of direction change.  That
    is exactly the complaint: the curve is smooth in VCarve but ragged on the 3D
    model.  Measured on run 21kwcockpit-1, panel 1's outer loop -- the DXF curve
    turns 0.94 deg (p90) from one 3 mm chord to the next, but the triangle lift
    of that same curve turns 26.0 deg (p90) and 85.7 deg (p99).

    Fitting a plane through the ~24 nearest development vertices averages the
    noise out while still following the deck.  The same loop turns 0.96 deg (p90)
    after the fit, and its points move a median of 0.01 mm (p95 2.6 mm) from
    where the triangle lift put them, so the drawn line still lies on the
    surface.  The important part is that this smooths the *surface*, not the
    curve: unlike a moving average along the polyline it cannot round off a
    genuine corner, because the corner lives in the DXF and the DXF is untouched
    (max turn stays 118-180 deg wherever the fitted outline really corners).
    Results barely moved for k between 12 and 96 because the bandwidth h below
    adapts to the local vertex spacing.  Cost is about 70 ms for 3600 query
    points on a 194k-vertex panel, which is why load_run can afford it eagerly.

    With `with_normals=True` the same fit also hands back the unit surface
    normal at each point, which costs one cross product: the plane's own tilt is
    already in the solution's second and third rows (the du and dv derivatives
    of x, y and z), so the normal is very nearly free.  `overlays` uses it to
    float the drawn lines a hair off the deck -- see OVERLAY_PROUD_MM.
    """

    uv = np.asarray(development.uv_mm, dtype=float)[:, :2]
    xyz = np.asarray(development.mesh.base_vertices_mm, dtype=float)
    faces = np.asarray(development.mesh.faces, dtype=int)
    if not len(uv):
        def empty(points, with_normals: bool = False):
            return (np.empty((0, 3)), np.empty((0, 3))) if with_normals else np.empty((0, 3))
        return empty
    tree = cKDTree(uv)
    k = min(_LIFT_NEIGHBOURS, len(uv))
    mesh_normals = _vertex_normals(xyz, faces) if len(faces) else None

    def lift(points: np.ndarray, with_normals: bool = False) -> Any:
        pts = np.asarray(points, dtype=float)
        if not pts.size:
            return (np.empty((0, 3)), np.empty((0, 3))) if with_normals else np.empty((0, 3))
        pts = pts.reshape(len(pts), -1)[:, :2]
        out = np.empty((len(pts), 3))
        normals = np.zeros((len(pts), 3)) if with_normals else None
        for start in range(0, len(pts), _LIFT_CHUNK):
            batch = pts[start:start + _LIFT_CHUNK]
            d, idx = tree.query(batch, k=k)
            d = np.asarray(d, dtype=float).reshape(len(batch), k)
            idx = np.asarray(idx).reshape(len(batch), k)
            if k < 3:
                # A degenerate development (a couple of vertices) has no plane to
                # fit; the nearest vertex is the only honest answer.
                out[start:start + len(batch)] = xyz[idx[:, 0]]
                if normals is not None and mesh_normals is not None:
                    normals[start:start + len(batch)] = mesh_normals[idx[:, 0]]
                continue
            # Adaptive bandwidth: the furthest neighbour sets the support radius,
            # so dense and sparse parts of the mesh get the same effective fit.
            h = np.maximum(d[:, -1][:, None], 1e-6)
            w = (1.0 - np.clip(d / h, 0.0, 1.0) ** 2) ** 2 + 1e-6   # smooth to ~zero at the rim
            du = uv[idx] - batch[:, None, :]                        # neighbours relative to the query
            design = np.concatenate([np.ones((len(batch), k, 1)), du], axis=2)   # (N,k,3): 1, du, dv
            weighted = design * w[:, :, None]
            normal = weighted.transpose(0, 2, 1) @ design           # (N,3,3)
            rhs = weighted.transpose(0, 2, 1) @ xyz[idx]            # (N,3,3): columns x, y, z
            # A ridge so a degenerate neighbourhood (all neighbours collinear in
            # uv, or all on top of each other) cannot make the solve singular.
            normal[:, 0, 0] += 1e-9
            normal[:, 1, 1] += 1e-6
            normal[:, 2, 2] += 1e-6
            # Row 0 of the solution is the constant term, i.e. the fitted surface
            # point at du = 0 -- which is the query point itself.  Rows 1 and 2
            # are d(xyz)/du and d(xyz)/dv: the two surface tangents.
            solution = np.linalg.solve(normal, rhs)
            out[start:start + len(batch)] = solution[:, 0, :]
            if normals is None:
                continue
            fitted = np.cross(solution[:, 1, :], solution[:, 2, :])
            length = np.linalg.norm(fitted, axis=1)
            usable = length > 1e-12
            fitted[usable] /= length[usable][:, None]
            if mesh_normals is not None:
                # The fit gives an axis, not a side; the mesh's own winding gives
                # the side.  Flipping to agree with the nearest vertex normal is
                # what keeps a whole curve on one side of the deck.
                reference = mesh_normals[idx[:, 0]]
                fitted[~usable] = reference[~usable]
                flip = np.einsum("ij,ij->i", fitted, reference) < 0.0
                fitted[flip] *= -1.0
            normals[start:start + len(batch)] = fitted
        return (out, normals) if with_normals else out

    return lift


def _triangle_lifter(development: Any) -> Callable[[np.ndarray], np.ndarray]:
    """The old lift: uv (panel frame, mm) -> world xyz (mm) by barycentric
    interpolation inside the single best development triangle.  Kept as the
    unsmoothed reference the overlay regression test measures _make_lifter
    against; nothing in the app draws with it."""

    uv = np.asarray(development.uv_mm, dtype=float)[:, :2]
    xyz = np.asarray(development.mesh.base_vertices_mm, dtype=float)
    faces = np.asarray(development.mesh.faces, dtype=int)
    tri = uv[faces]                                   # (M,3,2)
    tree = cKDTree(tri.mean(axis=1))
    k = min(12, len(faces))

    def lift(points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=float)[:, :2]
        if not len(pts):
            return np.empty((0, 3))
        _d, idx = tree.query(pts, k=k)
        idx = np.asarray(idx).reshape(len(pts), -1)
        cand = tri[idx]                               # (N,k,3,2)
        v0 = cand[:, :, 1] - cand[:, :, 0]; v1 = cand[:, :, 2] - cand[:, :, 0]; v2 = pts[:, None, :] - cand[:, :, 0]
        d00 = (v0 * v0).sum(-1); d01 = (v0 * v1).sum(-1); d11 = (v1 * v1).sum(-1)
        d20 = (v2 * v0).sum(-1); d21 = (v2 * v1).sum(-1)
        den = d00 * d11 - d01 * d01
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        b = (d11 * d20 - d01 * d21) / den; c = (d00 * d21 - d01 * d20) / den; a = 1.0 - b - c
        inside = (a >= -1e-3) & (b >= -1e-3) & (c >= -1e-3)
        score = np.where(inside, 1.0, np.minimum(np.minimum(a, b), c))
        best = np.argmax(score, axis=1)
        rows = np.arange(len(pts))
        bary = np.stack([a, b, c], axis=-1)[rows, best]
        bary = np.clip(bary, 0.0, 1.0)
        bary /= np.maximum(bary.sum(axis=1, keepdims=True), 1e-12)
        corners = xyz[faces[idx[rows, best]]]         # (N,3,3)
        return (corners * bary[:, :, None]).sum(axis=1)

    return lift


def _closest_on_triangles(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Barycentric (u, v, w) of the closest point of each triangle (a, b, c) to
    the matching point p -- Ericson's region test, vectorised over any leading
    shape.  Only the igl-less fallback in _make_locator uses it, but it has to be
    the true point-to-triangle distance and not a plane projection, or a query
    just off the edge of the deck picks the wrong panel."""

    ab = b - a; ac = c - a
    d1 = ((p - a) * ab).sum(-1); d2 = ((p - a) * ac).sum(-1)
    d3 = ((p - b) * ab).sum(-1); d4 = ((p - b) * ac).sum(-1)
    d5 = ((p - c) * ab).sum(-1); d6 = ((p - c) * ac).sum(-1)
    va = d3 * d6 - d5 * d4; vb = d5 * d2 - d1 * d6; vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-20, 1e-20, denom)

    def safe(num, den):
        return np.clip(num / np.where(np.abs(den) < 1e-20, 1e-20, den), 0.0, 1.0)

    # Interior first, then override with each edge and vertex region in turn.
    v = vb / denom; w = vc / denom
    on_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    t = safe(d4 - d3, (d4 - d3) + (d5 - d6))
    v = np.where(on_bc, 1.0 - t, v); w = np.where(on_bc, t, w)
    on_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    t = safe(d2, d2 - d6)
    v = np.where(on_ac, 0.0, v); w = np.where(on_ac, t, w)
    on_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    t = safe(d1, d1 - d3)
    v = np.where(on_ab, t, v); w = np.where(on_ab, 0.0, w)
    at_c = (d6 >= 0) & (d5 <= d6)
    v = np.where(at_c, 0.0, v); w = np.where(at_c, 1.0, w)
    at_b = (d3 >= 0) & (d4 <= d3)
    v = np.where(at_b, 1.0, v); w = np.where(at_b, 0.0, w)
    at_a = (d1 <= 0) & (d2 <= 0)
    v = np.where(at_a, 0.0, v); w = np.where(at_a, 0.0, w)
    return np.stack([1.0 - v - w, v, w], axis=-1)


def _make_locator(development: Any) -> Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """world xyz (mm) -> (uv in this panel's frame (mm), distance in mm from the
    query point to this panel's surface).

    The inverse of _make_lifter, and what the 3D seam tool needs: the user
    clicks the model, three.js hands back a world point, and the seam has to be
    stored in the flat layout.  The answer is the closest point of the closest
    triangle, so a click that lands just off a panel still reports how far off it
    was -- which is how pick_flat decides whether the click was on the deck at
    all.  No smoothing here: picking wants the real surface, and a fraction of a
    millimetre of scan noise is irrelevant to where a seam goes.

    libigl does the search when it is importable, because it is exact and the
    AABB tree it builds can be kept between clicks (igl.point_mesh_squared_distance
    rebuilds one every call -- about 170 ms per panel on a full-resolution scan,
    which the user would feel).  Without libigl the app still has to pick, so
    there is a nearest-triangle-centroid fallback; it uses the true
    point-to-triangle distance for the candidates it does look at."""

    uv = np.asarray(development.uv_mm, dtype=float)[:, :2]
    xyz = np.ascontiguousarray(np.asarray(development.mesh.base_vertices_mm, dtype=float))
    faces = np.ascontiguousarray(np.asarray(development.mesh.faces, dtype=np.int32))
    try:
        import igl
    except Exception:  # noqa: BLE001 -- the app must still pick without libigl
        igl = None
    aabb = None
    if igl is not None and len(faces):
        try:
            aabb = igl.AABB()
            aabb.init(xyz, faces)
        except Exception:  # noqa: BLE001 -- an igl without the reusable tree still has the helper
            aabb = None
    centroid_tree = None if igl is not None else cKDTree(xyz[faces].mean(axis=1))
    k = min(16, len(faces))

    def locate(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        query = np.asarray(points, dtype=float)
        if not query.size or not len(faces):
            return np.empty((0, 2)), np.empty((0,))
        query = np.ascontiguousarray(query.reshape(len(query), -1)[:, :3])
        if igl is not None:
            if aabb is not None:
                sq_dist, face_index, closest = aabb.squared_distance(xyz, faces, query)
            else:
                sq_dist, face_index, closest = igl.point_mesh_squared_distance(query, xyz, faces)
            face_index = np.asarray(face_index, dtype=int)
            corners = xyz[faces[face_index]]                      # (N,3,3)
            bary = _closest_on_triangles(np.asarray(closest), corners[:, 0], corners[:, 1], corners[:, 2])
            dist = np.sqrt(np.maximum(np.asarray(sq_dist, dtype=float), 0.0))
        else:
            _d, idx = centroid_tree.query(query, k=k)
            idx = np.asarray(idx).reshape(len(query), k)
            cand = xyz[faces[idx]]                                # (N,k,3,3)
            bary_all = _closest_on_triangles(query[:, None, :], cand[:, :, 0], cand[:, :, 1], cand[:, :, 2])
            hit = (cand * bary_all[:, :, :, None]).sum(axis=2)    # (N,k,3)
            gap = np.linalg.norm(hit - query[:, None, :], axis=2)
            best = np.argmin(gap, axis=1)
            rows = np.arange(len(query))
            face_index = idx[rows, best]
            bary = bary_all[rows, best]
            dist = gap[rows, best]
        return (uv[faces[face_index]] * bary[:, :, None]).sum(axis=1), dist

    return locate


def _unplace(placement: Any, xy: np.ndarray) -> np.ndarray:
    xy = np.asarray(xy, dtype=float)[:, :2]
    return (xy - placement.translation) @ placement.rotation.T + placement.uv_centroid


def _densify(points: np.ndarray, spacing: float) -> np.ndarray:
    pts = np.asarray(points, dtype=float)[:, :2]
    if len(pts) < 2:
        return pts
    out = [pts[:1]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(math.ceil(float(np.linalg.norm(b - a)) / spacing)))
        out.append(np.linspace(a, b, n + 1)[1:])
    return np.vstack(out)


def _rounded(points: np.ndarray, decimals: int = 2) -> list[list[float]]:
    """Trim the JSON, but not so hard that it bends the line.  This used to
    round to 0.1 mm, which on the old 3 mm chords was a quantisation of the same
    order as the chord's own rise: measured on panel 1's outer loop it lifted the
    median turn angle of the *flat* curve from 0.02 deg to 1.12 deg, i.e. the
    preview was visibly rougher than the DXF purely from rounding.  0.01 mm is
    still ten times finer than any router can hold."""

    return np.round(np.asarray(points, dtype=float), decimals).tolist()


_CAM_RE = re.compile(r"(?:^|::)(AUTO_CAM|USER_CAM)::PANEL_(\d+)$")
_DXF_PANEL_RE = re.compile(r"__PANEL_(\d+)$")


def _sample_bulge_polyline(points_xyb: list, closed: bool, sag_mm: float = 0.05, max_step_mm: float = 25.0) -> np.ndarray:
    """Sample an LWPOLYLINE (x, y, bulge) exactly as CAD interprets it, spending
    points only where the geometry curves.

    A straight run gets its two end vertices and nothing in between; an arc gets
    just enough chords that it never departs from the true arc by more than
    sag_mm (sagitta of a chord subtending delta is r * (1 - cos(delta / 2)), so
    delta = 2 * acos(1 - sag / r)), with a further cap so no chord is longer than
    max_step_mm on a very large radius.  The flat view draws these points
    straight through to the screen, so sag_mm is literally how round an arc looks
    there -- 0.05 mm is well under a screen pixel at any sane zoom.

    sag_mm is the whole visual budget and max_step_mm is only a backstop, which
    is worth being clear about because the cap looks like the tighter number and
    is not.  The chord count is the max of the two rules, so whichever asks for
    more chords wins, and they cross where a chord of max_step_mm has exactly
    sag_mm of sag: at r = max_step^2 / (8 * sag), i.e. 1562 mm for 25 and 0.05.
    Below that radius the sagitta rule is already the stricter one and the cap
    changes nothing; above it the cap only makes chords shorter than the budget
    needs.  Measured over all 239 bulge arcs in the cached runs' final_auto.dxf
    files (radii 8 mm to 9.9 m, sweeps up to 207 deg), the worst sagitta actually
    produced is 0.0500 mm at a 25 mm cap, at a 12 mm cap and with no cap at all;
    only the point count moves -- 2719 arc points uncapped, 3100 at 25 mm, 3965
    at 12 mm.  End to end that is 0.055 mm between the drawn polyline and ezdxf's
    own expansion of the same file, and the flat view is fit-to-window with no
    zoom (a 4.4 x 3.5 m layout at about 0.4 px/mm), so it is 0.02 of a pixel.
    Tightening the cap to 12 mm was measured and buys nothing on screen for 28%
    more points, so 25 mm stays.

    This replaced a fixed 3 mm step, which turned a 3 m straight edge into 1000
    identical-direction points and one real panel loop into 3632 points.  The
    waste was not only payload: on 3 mm chords the 0.01 mm output rounding in
    _rounded was itself worth up to a degree of direction jitter per vertex.

    The arc centre and direction resolution below is deliberately untouched -- it
    was verified against what VCarve actually cuts and is not the thing that was
    wrong."""

    n = len(points_xyb)
    out: list[np.ndarray] = []
    sag = max(float(sag_mm), 1e-6)
    last = n if closed else n - 1
    for i in range(last):
        x1, y1, b = points_xyb[i]
        x2, y2, _ = points_xyb[(i + 1) % n]
        p1 = np.array([x1, y1]); p2 = np.array([x2, y2])
        chord = p2 - p1; c = float(np.linalg.norm(chord))
        if abs(b) < 1e-12 or c < 1e-12:
            # Straight: emit the start vertex only.  The next iteration emits the
            # next vertex, and the close-the-ring append below emits the last.
            out.append(p1[None, :])
            continue
        theta = 4.0 * math.atan(b)
        r = c / (2.0 * abs(math.sin(theta / 2.0)))
        mid = (p1 + p2) / 2.0
        h = math.sqrt(max(r * r - (c / 2.0) ** 2, 0.0))
        nrm = np.array([-chord[1], chord[0]]) / c
        center = mid + nrm * (h if theta > 0 else -h) * (1.0 if abs(theta) <= math.pi else -1.0)
        a0 = math.atan2(p1[1] - center[1], p1[0] - center[0])
        if np.linalg.norm(center + r * np.array([math.cos(a0 + theta), math.sin(a0 + theta)]) - p2) > 0.01:
            center = mid - nrm * (h if theta > 0 else -h) * (1.0 if abs(theta) <= math.pi else -1.0)
            a0 = math.atan2(p1[1] - center[1], p1[0] - center[0])
        delta = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - sag / r)))
        k = int(math.ceil(abs(theta) / delta)) if delta > 1e-12 else 2
        k = max(k, int(math.ceil(abs(theta) * r / max_step_mm)))
        k = min(max(k, 2), 720)
        angles = np.linspace(a0, a0 + theta, k, endpoint=False)
        arc = center + r * np.column_stack([np.cos(angles), np.sin(angles)])
        arc[0] = p1              # the vertex is the DXF's, not the reconstruction's
        out.append(arc)
    if not closed:
        out.append(np.array([points_xyb[-1][:2]], dtype=float))
    else:
        out.append(np.array([points_xyb[0][:2]], dtype=float))   # close the displayed ring
    return np.vstack(out)


def read_final_dxf(path: Path) -> tuple[dict[int, list[np.ndarray]], dict[int, list[np.ndarray]]]:
    """The exact content of a final DXF: CAM loops (arcs reconstructed from
    bulges, the way VCarve will) and pattern lines, per panel."""

    import ezdxf

    doc = ezdxf.readfile(str(path))
    loops: dict[int, list[np.ndarray]] = {}
    pattern: dict[int, list[np.ndarray]] = {}
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer)
        match = _DXF_PANEL_RE.search(layer)
        if not match:
            continue
        pid = int(match.group(1))
        if entity.dxftype() == "LWPOLYLINE":
            pts = _sample_bulge_polyline(list(entity.get_points("xyb")), bool(entity.closed))
            (pattern if layer.startswith("PATTERN_") else loops).setdefault(pid, []).append(pts)
        elif entity.dxftype() == "LINE" and layer.startswith("PATTERN_"):
            s, e = entity.dxf.start, entity.dxf.end
            pattern.setdefault(pid, []).append(np.array([[s.x, s.y], [e.x, e.y]], dtype=float))
    return loops, pattern


def _layer_name(layer: Any) -> str:
    full = getattr(layer, "FullPath", None)
    return str(full) if full else str(layer.Name)


def _sample_curve(geometry: Any) -> list[np.ndarray]:
    """Points along a rhino3dm curve (xy), splitting poly curves into pieces."""

    import rhino3dm

    if isinstance(geometry, rhino3dm.PolyCurve):
        pieces = []
        for k in range(geometry.SegmentCount):
            pieces.extend(_sample_curve(geometry.SegmentCurve(k)))
        return pieces
    if isinstance(geometry, rhino3dm.PolylineCurve):
        return [np.array([[geometry.Point(i).X, geometry.Point(i).Y] for i in range(geometry.PointCount)])]
    if isinstance(geometry, rhino3dm.LineCurve):
        return [np.array([[geometry.PointAtStart.X, geometry.PointAtStart.Y], [geometry.PointAtEnd.X, geometry.PointAtEnd.Y]])]
    if not isinstance(geometry, rhino3dm.Curve):
        return []
    nurbs = geometry.ToNurbsCurve() if not isinstance(geometry, rhino3dm.NurbsCurve) else geometry
    if nurbs is None:
        return []
    domain = nurbs.Domain
    if nurbs.IsLinear():
        count = 2
    else:
        # Length-based, not a flat count: 48 points was far too few for a 3 m
        # sweep and absurdly many for a 6 mm fillet.  rhino3dm has no curve
        # length, so a coarse chord sum is the estimate -- it only sets the
        # density, so being a few percent short is harmless.
        probe = np.linspace(domain.T0, domain.T1, 33)
        coarse = np.array([[nurbs.PointAt(float(t)).X, nurbs.PointAt(float(t)).Y] for t in probe])
        length = float(np.linalg.norm(np.diff(coarse, axis=0), axis=1).sum())
        count = int(min(512, max(16, math.ceil(length / 8.0) + 1)))
    ts = np.linspace(domain.T0, domain.T1, count)
    return [np.array([[nurbs.PointAt(float(t)).X, nurbs.PointAt(float(t)).Y] for t in ts])]


def read_cam_layers(path: Path) -> tuple[dict[str, dict[int, list[np.ndarray]]], dict[str, list[np.ndarray]]]:
    """AUTO_CAM / USER_CAM polylines per panel and AUTO_CORNERS / USER_CORNERS points from a 3dm."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(path))
    curves: dict[str, dict[int, list[np.ndarray]]] = {"AUTO_CAM": {}, "USER_CAM": {}}
    corners: dict[str, list[np.ndarray]] = {"AUTO_CORNERS": [], "USER_CORNERS": []}
    if model is None:
        return curves, corners
    layers = list(model.Layers)
    for obj in model.Objects:
        index = obj.Attributes.LayerIndex
        if index < 0 or index >= len(layers):
            continue
        name = _layer_name(layers[index])
        geometry = obj.Geometry
        short = name.split("::")[-1]
        if short in corners and isinstance(geometry, rhino3dm.Point):
            corners[short].append(np.array([geometry.Location.X, geometry.Location.Y]))
            continue
        match = _CAM_RE.search(name)
        if not match:
            continue
        family, pid = match.group(1), int(match.group(2))
        for piece in _sample_curve(geometry):
            if len(piece) >= 2:
                curves[family].setdefault(pid, []).append(piece)
    return curves, corners


# How far off the deck a drawn overlay floats in the 3D view.
#
# This is a RENDERING offset and nothing else: the flat layer, every DXF and
# every seam coordinate are untouched, so nothing that gets cut moves by a
# micron.  It exists because the fitted CAM outline is a fair curve while the
# scan's meshed border is ragged, so 24 to 66 percent of every fitted loop lands
# just OFF the meshed footprint (measured on the cached runs: median 1.18 mm
# out, worst 8.32 mm).  The plane fit extrapolates correctly out there, but
# app.js draws the overlays depth-tested against a decimated preview mesh, so a
# line sitting exactly on the surface reads as dipping in and out of the deck
# near the panel edges -- which is the user's original "it is not smooth"
# complaint showing up in a second guise.
#
# 1.5 mm is chosen to clear that: it is bigger than the ~1 mm the decimated
# preview mesh itself wanders from the full-resolution development, and small
# enough that at the ~0.4 px/mm the flat view draws at, and at any normal 3D
# camera distance, the line still reads as being ON the deck rather than
# hovering above it.
OVERLAY_PROUD_MM = 1.5


def lift_proud(view: "RunView", pid: int, uv_points: Any,
               proud_mm: float = OVERLAY_PROUD_MM) -> np.ndarray:
    """uv (panel frame, mm) -> world xyz (mm), floated `proud_mm` out along the
    surface normal so the drawn line never intersects the deck it describes.

    Every world overlay goes through here, and only world overlays do: the flat
    layer is built straight from the same uv coordinates and is never touched by
    this.
    """

    xyz, normals = view.lifters[pid](uv_points, with_normals=True)
    if not len(xyz):
        return xyz
    return xyz + normals * float(proud_mm)


def overlays(view: RunView, log: Log) -> dict[str, Any]:
    """Every drawable layer of a run: `world` polylines (mm, over the mesh) and `flat` polylines (placed layout)."""

    result = view.result
    layers: list[dict[str, Any]] = []

    def add(layer_id: str, label: str, color: str, world: list, flat: list, kind: str = "lines", default_on: bool = True, **extra: Any) -> None:
        if not world and not flat:
            return
        layers.append({"id": layer_id, "label": label, "color": color, "kind": kind, "on": default_on,
                       "world": world, "flat": flat, "count": max(len(world), len(flat)), **extra})

    def lifted(pid: int, placed_xy: np.ndarray, spacing: float) -> list[list[float]]:
        dense = _densify(placed_xy, spacing)
        return _rounded(lift_proud(view, pid, _unplace(view.placements[pid], dense)))

    for family, layer_id, label, color, on in (
        ("OUTER", "raw_outer", "Raw outline (wall line)", "#ff3b30", True),
        ("OBSTACLE", "raw_obstacles", "Raw obstacles", "#ff9500", True),
        ("FEATURE", "raw_features", "Seams / hatches / nonskid", "#ffd60a", True),
        ("HINT", "raw_hints", "Hints (low confidence)", "#8e8e93", False),
    ):
        world = []; flat = []
        for pid in sorted(result.panels):
            for curve in result.curves_for(pid, family):
                # Lifted off the surface, exactly like every other world overlay
                # -- these four used their raw measured xyz, which is ON the
                # deck by definition, so the preview mesh swallowed them: nearly
                # half the length of the red wall line was inside the model and
                # simply invisible with x-ray off.  Re-deriving the point
                # through the panel's lifter is what "proud" needs (it wants uv,
                # and returns the surface normal with it), and it is the same
                # call the ROBUST layer below has always made.
                world.append(_rounded(lift_proud(view, pid, curve.flat_points_mm))
                             if pid in view.lifters else _rounded(curve.world_points_mm))
                flat.append(_rounded(view.placements[pid].apply(curve.flat_points_mm)))
        for curve in result.unassigned():
            # An unassigned curve belongs to no panel, so there is no unroll to
            # lift it through; its measured points are all there is.
            if curve.family == family:
                world.append(_rounded(curve.world_points_mm))
        add(layer_id, label, color, world, flat, default_on=on)

    world = []; flat = []
    for pid in sorted(result.panels):
        for curve in result.curves_for(pid, "ROBUST"):
            flat.append(_rounded(view.placements[pid].apply(curve.flat_points_mm)))
            world.append(_rounded(lift_proud(view, pid, curve.flat_points_mm)))
    add("robust", "Robust reference (de-noised)", "#34c759", world, flat, default_on=False)

    if view.teak_lines:
        pattern = str(view.pattern_info.get("pattern") or "teak")
        dims = view.pattern_info.get("dimensions_mm") or {}
        label, color = {
            "teak": (f"Teak lines ({dims.get('teak_spacing_mm', 63.5):g} mm)", "#c68a3c"),
            "diamond": (f"Diamond stitch ({dims.get('diamond_long_diagonal_mm', 152.4):g} × {dims.get('diamond_short_diagonal_mm', 76.2):g} mm)", "#2fd6c3"),
            "hex": (f"Hexagons ({dims.get('hex_across_flats_mm', 152.4):g} mm)", "#ff6482"),
        }.get(pattern, ("Pattern", "#c68a3c"))
        world = []; flat = []
        for pid, lines in view.teak_lines.items():
            if pid not in view.placements:
                continue
            for p0, p1 in lines:
                seg = np.array([p0[:2], p1[:2]], dtype=float)
                flat.append(_rounded(seg))
                world.append(lifted(pid, seg, 40.0))
        add("pattern", label, color, world, flat)

    for filename, family, layer_id, label, color in (
        ("auto_cam.3dm", "AUTO_CAM", "auto_cam", "Auto-fit CAM outline", "#0a84ff"),
        ("outline.3dm", "USER_CAM", "user_cam", "Your drawn CAM outline", "#bf5af2"),
    ):
        path = view.run_dir / filename
        if not path.is_file():
            continue
        try:
            curves, corners = read_cam_layers(path)
        except Exception as exc:  # noqa: BLE001
            log(f"could not read {filename}: {exc}")
            continue
        world = []; flat = []
        for pid, pieces in curves[family].items():
            if pid not in view.placements:
                continue
            for piece in pieces:
                flat.append(_rounded(piece))
                world.append(lifted(pid, piece, 20.0))
        add(layer_id, label, color, world, flat)
        corner_key = "AUTO_CORNERS" if family == "AUTO_CAM" else "USER_CORNERS"
        points = corners.get(corner_key) or []
        if points:
            xy = np.vstack(points)
            world_pts = []
            for q in xy:
                pid = _panel_at(view, q)
                if pid is not None:
                    world_pts.append(_rounded(lift_proud(view, pid, _unplace(view.placements[pid], q[None, :])))[0])
            add(layer_id + "_corners", label.replace("CAM outline", "corners"), color, world_pts, _rounded(xy), kind="points")

    # the exact files VCarve receives, reconstructed from the DXFs themselves
    for filename, layer_id, label, color in (
        ("final_auto.dxf", "final_auto_file", "final_auto.dxf — exact VCarve file", "#00e676"),
        ("final.dxf", "final_file", "final.dxf — exact VCarve file (your drawing)", "#ff9f0a"),
    ):
        path = view.run_dir / filename
        if not path.is_file():
            continue
        try:
            loops, pattern_lines = read_final_dxf(path)
        except Exception as exc:  # noqa: BLE001
            log(f"could not read {filename}: {exc}")
            continue
        world = []; flat = []
        for pid, pieces in loops.items():
            if pid not in view.placements:
                continue
            for piece in pieces:
                flat.append(_rounded(piece))
                world.append(lifted(pid, piece, 20.0))
        for pid, lines in pattern_lines.items():
            if pid not in view.placements:
                continue
            for seg in lines:
                flat.append(_rounded(seg))
                world.append(lifted(pid, seg, 40.0))
        add(layer_id, label, color, world, flat)

    panels = []
    for pid in sorted(result.panels):
        placement = view.placements[pid]
        outline = placement.placed_outline
        bbox = None
        if outline is not None and len(outline):
            bbox = [outline.min(axis=0).round(1).tolist(), outline.max(axis=0).round(1).tolist()]
        panels.append({"id": pid, "role": result.panels[pid].role, "bbox_flat": bbox,
                       "world_centroid": np.round(np.asarray(result.panels[pid].world_centroid_mm, dtype=float), 1).tolist(),
                       "flatten": view.flatten.get(pid, {})})
    return {"run_id": view.run_dir.name, "mm_per_unit": view.mm_per_unit, "units": view.meta.get("units"),
            "layout_mode": view.meta.get("layout_mode"), "layers": layers, "panels": panels, "files": run_files(view.run_dir),
            "pattern": view.pattern_info.get("pattern") if view.pattern_info else None}


# A click further than this from every panel is not a click on the deck.  Half a
# panel's own thickness would be too tight -- three.js hands back the point on
# the *preview* mesh, which is decimated to ~250k faces and so sits a
# millimetre or two off the full-resolution development in places.
PICK_MAX_MM = 50.0


def locator_for(view: RunView, pid: int) -> Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """The panel's world -> uv locator, built the first time something picks."""

    locator = view.locators.get(pid)
    if locator is None:
        locator = _make_locator(view.result.panels[pid].development)
        view.locators[pid] = locator
    return locator


def warm_picking(view: RunView) -> None:
    """Build every panel's world -> uv search tree up front.

    Picking is lazy by default, which is right for a run that is only ever
    looked at.  It is wrong the moment the seam tool is in use: the tree for a
    194k-vertex deck panel takes about a second to build, and the seam tool
    picks on every pointer move, so the first move over the boat would stall.
    The server calls this while opening a run, where a second is expected.
    """

    for pid in sorted(view.result.panels):
        if pid in view.placements:
            locator_for(view, pid)


def pick_flat(view: RunView, world_points: Any) -> list[dict[str, Any]]:
    """World xyz (mm) -> the same points in the placed (flat layout) frame, which
    is the frame seams and the flat view are drawn in.  Every panel is asked and
    the nearest surface wins, because panels overlap in world z and only the
    distance can say which deck the user actually clicked.  panel_id is None when
    the nearest panel is further away than PICK_MAX_MM, so the page can say "that
    is not on the deck" instead of quietly snapping the seam somewhere wrong.
    distance_mm is -1 when the run has no panels to pick at all."""

    query = np.asarray(world_points, dtype=float)
    if not query.size:
        return []
    query = query.reshape(len(query), -1)[:, :3]
    best_dist = np.full(len(query), np.inf)
    best_xy = np.zeros((len(query), 2))
    best_pid = np.full(len(query), -1, dtype=int)
    for pid in sorted(view.result.panels):
        if pid not in view.placements:
            continue
        uv, dist = locator_for(view, pid)(query)
        if not len(uv):
            continue
        closer = dist < best_dist
        best_dist[closer] = dist[closer]
        best_xy[closer] = view.placements[pid].apply(uv)[closer]
        best_pid[closer] = pid
    picks = []
    for i in range(len(query)):
        found = best_pid[i] >= 0
        picks.append({"panel_id": int(best_pid[i]) if found and best_dist[i] <= PICK_MAX_MM else None,
                      "x": round(float(best_xy[i][0]), 2), "y": round(float(best_xy[i][1]), 2),
                      "distance_mm": round(float(best_dist[i]), 2) if found else -1.0})
    return picks


def _panel_at(view: RunView, q: np.ndarray) -> int | None:
    best = None; best_d = float("inf")
    for pid, placement in view.placements.items():
        outline = placement.placed_outline
        if outline is None or not len(outline):
            continue
        d = float(np.min(np.linalg.norm(outline - q, axis=1)))
        if d < best_d:
            best, best_d = pid, d
    return best
