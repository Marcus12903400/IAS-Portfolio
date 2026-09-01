"""Run-level orchestration for the sheet workflow.

    fitted CAM loops  ->  apply seams  ->  check nothing is still oversize
                      ->  nest on 40 x 80 in sheets  ->  one DXF per sheet

Reads the run's `final.dxf` when an ingested hand drawing exists (the user's
own geometry wins) and `final_auto.dxf` otherwise, so the sheets always follow
whichever outline the run actually settled on.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from . import nesting, sheets as sheets_mod
from .sheets import Piece, Seam

Progress = Callable[[str], None]


def _silent(_message: str) -> None:
    return None


def source_dxf(run_dir: Path) -> Path | None:
    """The fitted geometry to cut: an ingested drawing beats the auto fit."""

    for name in ("final.dxf", "final_auto.dxf"):
        candidate = Path(run_dir) / name
        if candidate.is_file():
            return candidate
    return None


def boat_axis(run_dir: Path) -> tuple[np.ndarray | None, float]:
    """The boat's longitudinal axis and the confidence it was detected with.

    Stored by the pattern stage in run.json.  It is expressed in the boat-plan
    frame, which differs from the placed frame only by a translation, so it is
    directly usable for orienting the grain.
    """

    meta_path = Path(run_dir) / "run.json"
    if not meta_path.is_file():
        return None, 0.0
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    frame = ((meta.get("teak") or {}).get("frame") or {})
    axis = frame.get("longitudinal_axis")
    if not axis:
        return None, 0.0
    return np.asarray(axis, dtype=float)[:2], float(frame.get("axis_confidence", 0.0))


def resolve_axis(run_dir: Path, options: dict[str, Any]) -> tuple[np.ndarray | None, float, list[str]]:
    """The grain direction this job will actually use, and what to warn about.

    Both `plan` and `preview` go through here.  They used to resolve the axis
    separately, and `preview` ignored the manual override -- so the placements
    were computed in the override frame while the picture was drawn in the
    detected frame, and pieces appeared outside the sheet.  That bit exactly
    when the user was told to override, i.e. when detection was unsure.
    """

    axis, confidence = boat_axis(run_dir)
    warnings: list[str] = []
    override = options.get("grain_angle_deg")
    if override is not None:
        radians = math.radians(float(override))
        detected = axis
        axis = np.array([math.cos(radians), math.sin(radians)])
        warnings.append(f"grain direction set manually to {float(override):.1f} deg "
                        "(overriding the detected boat axis)")
        if detected is not None:
            # The pattern grooves were generated from the DETECTED axis and are
            # already baked into the fitted DXF. If the manual grain disagrees,
            # the planks on the finished deck will not run with the material
            # grain -- which is the whole reason the sheet has a direction.
            detected_deg = math.degrees(math.atan2(detected[1], detected[0]))
            difference = abs((float(override) - detected_deg + 90.0) % 180.0 - 90.0)
            if difference > 3.0:
                warnings.append(
                    f"the manual grain angle differs from the pattern's boat axis by "
                    f"{difference:.1f} deg ({detected_deg:.1f} deg). The pattern grooves are "
                    "already fixed in the fitted DXF, so the planks and the material grain "
                    "will not line up. Re-run the outline with the correct axis, or clear the "
                    "manual angle."
                )
        return axis, 1.0, warnings
    if axis is None:
        warnings.append(
            "no boat axis stored for this run (was a pattern selected?); the grain direction "
            "falls back to the panel frame's +Y and is probably wrong -- set the grain angle "
            "before cutting"
        )
    elif confidence < 0.5:
        warnings.append(
            f"boat axis confidence is only {confidence:.2f}; the grain direction may be wrong. "
            "Check the sheet preview and set the grain angle manually if it looks off -- cutting "
            "a directional material across the grain ruins the sheet."
        )
    return axis, confidence, warnings


def plan(run_dir: Path, config: dict[str, Any], seams: Sequence[Seam] | None = None,
         progress: Progress | None = None, write_files: bool = True) -> dict[str, Any]:
    """Split by seams, nest, and (optionally) write the per-sheet DXFs."""

    progress = progress or _silent
    run_dir = Path(run_dir)
    options = sheets_mod.settings(config)
    source = source_dxf(run_dir)
    if source is None:
        raise FileNotFoundError(
            f"{run_dir.name} has no final_auto.dxf or final.dxf; run auto-fit (or ingest) first"
        )

    seam_list = list(seams) if seams is not None else sheets_mod.read_seams(run_dir)
    progress(f"Reading fitted geometry from {source.name}")
    loops, pattern, pattern_kind = sheets_mod.read_fitted_dxf(source)
    if not loops:
        raise ValueError(f"{source.name} contains no CAM loops")

    axis, confidence, warnings = resolve_axis(run_dir, options)
    rotation = sheets_mod.sheet_transform(axis)

    step = float(options["sample_step_mm"])
    progress(f"Applying {len(seam_list)} seam(s) to {len(loops)} panel(s)")
    pieces: list[Piece] = []
    for panel_id in sorted(loops):
        outer, holes = sheets_mod.classify_loops(loops[panel_id], step)
        if outer is None:
            continue
        panel_pieces, panel_warnings = sheets_mod.split_panel(
            panel_id, outer, holes, seam_list, options
        )
        pieces.extend(panel_pieces)
        warnings.extend(panel_warnings)

    oversize = sheets_mod.oversize_report(pieces, rotation, options)
    for item in oversize:
        warnings.append(
            f"piece {item['piece_id']} is {item['width_mm']:.0f} x {item['length_mm']:.0f} mm, "
            f"over by {item['over_width_mm']:.0f} x {item['over_length_mm']:.0f} mm -- {item['hint']}"
        )

    progress(f"Nesting {len(pieces)} piece(s) on "
             f"{options['sheet_width_mm']:.0f} x {options['sheet_length_mm']:.0f} mm sheets")
    sheet_list, summary, nest_warnings = nesting.nest(pieces, rotation, options)
    warnings.extend(nest_warnings)

    by_id = {piece.piece_id: piece for piece in pieces}
    files: list[dict[str, Any]] = []
    if write_files and sheet_list:
        progress(f"Writing {len(sheet_list)} sheet DXF(s)")
        files = nesting.write_sheet_dxfs(
            run_dir, sheet_list, by_id, rotation, pattern, pattern_kind, options
        )

    worst_arc = max((p.max_arc_error_mm for p in pieces), default=0.0)
    status = "OK"
    if oversize or summary["unplaced_piece_ids"]:
        status = "NEEDS_SEAMS"
    elif not sheet_list:
        status = "EMPTY"

    result: dict[str, Any] = {
        "status": status,
        "source_dxf": source.name,
        "seam_count": len(seam_list),
        "piece_count": len(pieces),
        "oversize": oversize,
        "summary": summary,
        "files": files,
        "warnings": warnings,
        "max_arc_rebuild_error_mm": round(worst_arc, 4),
        "boat_axis": None if axis is None else [round(float(v), 6) for v in axis],
        "boat_axis_confidence": round(confidence, 4),
        "pieces": [
            {"piece_id": p.piece_id, "panel_id": p.panel_id,
             "area_mm2": round(p.area_mm2, 1), "from_seam": p.from_seam,
             "holes": len(p.holes)}
            for p in pieces
        ],
        "sheets": [
            {"sheet": s.index + 1,
             "utilisation": round(s.utilisation(options), 4),
             "placements": [pl.to_dict() for pl in s.placements]}
            for s in sheet_list
        ],
    }
    if write_files:
        (run_dir / "sheets.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        _write_report(run_dir / "sheet_report.md", result, options)
    return result


def preview(run_dir: Path, config: dict[str, Any], seams: Sequence[Seam] | None = None) -> dict[str, Any]:
    """Sheet layout as drawable polylines, for the browser -- no files written."""

    run_dir = Path(run_dir)
    options = sheets_mod.settings(config)
    result = plan(run_dir, config, seams=seams, write_files=False)

    source = source_dxf(run_dir)
    loops, _pattern, _kind = sheets_mod.read_fitted_dxf(source) if source else ({}, {}, None)
    # Same resolution plan() used, override included -- drawing the rings in a
    # different frame from the one the placements were computed in put pieces
    # outside the sheet.
    axis, _confidence, _warnings = resolve_axis(run_dir, options)
    rotation = sheets_mod.sheet_transform(axis)
    step = float(options["sample_step_mm"])

    seam_list = list(seams) if seams is not None else sheets_mod.read_seams(run_dir)
    pieces: dict[str, Piece] = {}
    for panel_id in sorted(loops):
        outer, holes = sheets_mod.classify_loops(loops[panel_id], step)
        if outer is None:
            continue
        panel_pieces, _w = sheets_mod.split_panel(panel_id, outer, holes, seam_list, options)
        for piece in panel_pieces:
            pieces[piece.piece_id] = piece

    drawn: list[dict[str, Any]] = []
    for sheet_index, sheet in enumerate(result["sheets"]):
        rings: list[dict[str, Any]] = []
        for entry in sheet["placements"]:
            piece = pieces.get(entry["piece_id"])
            if piece is None:
                continue
            placement = nesting.Placement(
                piece_id=entry["piece_id"], panel_id=entry["panel_id"], sheet_index=sheet_index,
                rotation_deg=int(entry["rotation_deg"]),
                offset=np.asarray(entry["offset_mm"], dtype=float),
                origin=np.asarray(entry["origin_mm"], dtype=float),
            )
            for loop in [piece.outer, *piece.holes]:
                # Coarser than the geometry step: this is for drawing on screen,
                # where 4 mm chords are already sub-pixel on a 2 m sheet.
                points, _s = sheets_mod.sample_loop(loop, max(step, 4.0))
                moved = placement.apply(points, rotation)
                rings.append({
                    "piece_id": entry["piece_id"], "panel_id": entry["panel_id"],
                    "rotation_deg": entry["rotation_deg"],
                    "points": np.round(np.vstack([moved, moved[:1]]), 2).tolist(),
                })
        drawn.append({"sheet": sheet["sheet"], "utilisation": sheet["utilisation"], "rings": rings})

    result["preview"] = {
        "sheet_width_mm": options["sheet_width_mm"],
        "sheet_length_mm": options["sheet_length_mm"],
        "usable_width_mm": options["max_part_width_mm"],
        "usable_length_mm": options["max_part_length_mm"],
        "sheets": drawn,
    }
    # plan(write_files=False) leaves `files` empty, so a preview would report no
    # sheet DXFs even when they are sitting on disk -- and the page builds its
    # download links from this list, so they never appeared.
    result["files"] = exported_sheets(run_dir)
    return result


def exported_sheets(run_dir: Path) -> list[dict[str, Any]]:
    """Sheet DXFs already written for this run, newest export first."""

    written = json.loads((Path(run_dir) / "sheets.json").read_text(encoding="utf-8")) \
        if (Path(run_dir) / "sheets.json").is_file() else {}
    by_name = {entry.get("name"): entry for entry in (written.get("files") or [])}
    files: list[dict[str, Any]] = []
    for path in sorted(Path(run_dir).glob("sheet_*.dxf")):
        entry = dict(by_name.get(path.name) or {})
        entry.setdefault("name", path.name)
        entry.setdefault("sheet", len(files) + 1)
        entry.setdefault("pieces", [])
        entry.setdefault("utilisation", 0.0)
        entry["exists"] = True
        files.append(entry)
    return files


def _write_report(path: Path, result: dict[str, Any], options: dict[str, Any]) -> None:
    summary = result["summary"]
    lines = [
        "# AutoDeck sheet layout", "",
        f"- Source geometry: `{result['source_dxf']}`",
        f"- Seams applied: {result['seam_count']}  ->  {result['piece_count']} piece(s)",
        f"- Sheet: {options['sheet_width_mm']:.1f} x {options['sheet_length_mm']:.1f} mm "
        f"({options['sheet_width_mm'] / 25.4:.0f} x {options['sheet_length_mm'] / 25.4:.0f} in), "
        f"usable {options['max_part_width_mm']:.1f} x {options['max_part_length_mm']:.1f} mm",
        f"- Seam gap: {options['seam_gap_mm']:.1f} mm; minimum spacing between pieces: "
        f"{options['part_spacing_mm']:.1f} mm",
        f"- Rotation allowed: {', '.join(str(a) + ' deg' for a in summary['rotations_allowed_deg'])} "
        "(the grain runs along the 80 in sheet axis, so 90 deg and mirroring are never used)",
        f"- Sheets needed: **{summary['sheet_count']}**",
        f"- Status: **{result['status']}**",
        "",
    ]
    if result["max_arc_rebuild_error_mm"] > 0:
        lines += [f"Arcs surviving the seam cut were refitted to within "
                  f"{result['max_arc_rebuild_error_mm']:.4f} mm of the original curve.", ""]
    if result["sheets"]:
        lines += ["## Sheets", "", "| sheet | pieces | utilisation |", "|---|---|---:|"]
        for sheet in result["sheets"]:
            names = ", ".join(p["piece_id"] for p in sheet["placements"]) or "-"
            lines.append(f"| {sheet['sheet']} | {names} | {sheet['utilisation'] * 100:.1f}% |")
        lines.append("")
        lines += ["## Placements", "",
                  "| sheet | piece | panel | rotation | size mm | position mm |", "|---|---|---|---:|---|---|"]
        for sheet in result["sheets"]:
            for p in sheet["placements"]:
                lines.append(
                    f"| {sheet['sheet']} | {p['piece_id']} | {p['panel_id']} | {p['rotation_deg']} deg | "
                    f"{p['width_mm']:.0f} x {p['length_mm']:.0f} | "
                    f"({p['offset_mm'][0]:.0f}, {p['offset_mm'][1]:.0f}) |")
        lines.append("")
    if result["oversize"]:
        lines += ["## Still too big for a sheet", "",
                  "These need another seam before they can be cut:", ""]
        for item in result["oversize"]:
            lines.append(f"- **{item['piece_id']}** {item['width_mm']:.0f} x {item['length_mm']:.0f} mm "
                         f"(over by {item['over_width_mm']:.0f} x {item['over_length_mm']:.0f} mm) -- {item['hint']}")
        lines.append("")
    if result["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in result["warnings"]] + [""]
    lines += ["Manufacturing approval stays TEST_ONLY until a human has physically verified a cut.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
