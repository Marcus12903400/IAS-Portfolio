"""Run-level orchestration for the sheet workflow.

    fitted CAM loops  ->  apply seams  ->  check nothing is still oversize
                      ->  nest on 40 x 80 in sheets  ->  one DXF per sheet

Reads the run's `final.dxf` when an ingested hand drawing exists (the user's
own geometry wins) and `final_auto.dxf` otherwise, so the sheets always follow
whichever outline the run actually settled on.

This is also the ONE place a seam is straightened, in `snapped_seams`, and that
is deliberate: straightening needs the boat axis after the manual grain
override has had its say, and it needs the fitted loops, and this module
already resolves both for the cut.  Both `plan` and `preview` go through it, so
the seam the user is shown in the preview is the seam that gets cut.  This file
has been bitten once by those two resolving the axis separately -- the pieces
were placed in one frame and drawn in another -- and a seam corrector split the
same way would show a straight join and cut a crooked one.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from . import nesting, seamplace, seamsnap, sheets as sheets_mod
from .seamsnap import SnapResult
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


# How much of the detected bow's confidence has to survive being projected onto
# a manually set grain axis before the bow is still worth stating.  A quarter is
# well under the half the page treats as "sure", so a projection weak enough to
# reach this floor was never going to be reported as certain anyway; what the
# floor stops is the seam tab turning the deck end for end on the strength of a
# number that has become arithmetic noise.
_BOW_CARRY_FLOOR = 0.25


@dataclass(frozen=True)
class BoatFrame:
    """Which way the boat runs, and which end of it is the bow.

    `axis` is the longitudinal direction in the placed frame -- the direction
    the teak lines were drawn along, the direction the 80 inch sheet dimension
    runs, and the direction the long seams are squared to.  It is a direction
    and not a heading: `bow_sign` says which way along it the bow is (+1 or -1),
    so the bow points along `axis * bow_sign`.  `bow_sign` 0 means the run does
    not know, and nothing may guess -- the seam tab draws itself bow up, and
    drawing it stern up would have the fabricator laying out the whole deck
    back to front.
    """

    axis: np.ndarray | None
    confidence: float
    bow_sign: int
    bow_confidence: float

    @property
    def bow_direction(self) -> np.ndarray | None:
        """The unit vector pointing at the bow, or None when it is not known."""

        if self.axis is None or self.bow_sign == 0:
            return None
        return np.asarray(self.axis, dtype=float)[:2] * float(self.bow_sign)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": None if self.axis is None else [round(float(v), 9) for v in self.axis[:2]],
            "confidence": round(float(self.confidence), 4),
            "bow_sign": int(self.bow_sign),
            "bow_confidence": round(float(self.bow_confidence), 4),
            "bow_direction": (None if self.bow_direction is None
                              else [round(float(v), 9) for v in self.bow_direction]),
        }


def boat_axis(run_dir: Path) -> BoatFrame:
    """The boat's longitudinal axis, which end is the bow, and how sure the
    detector was of each.

    All four numbers are stored by the pattern stage in run.json under
    `teak.frame`; engine-v1's `patterns.BoatFrame` works the bow out from
    cross-boat width slices -- the narrow end is the bow -- so nothing here has
    to re-derive it.  The frame is expressed in the boat-plan frame, which
    differs from the placed frame only by each panel's nest translation, and a
    translation does not change a direction, so it is directly usable here.
    """

    meta_path = Path(run_dir) / "run.json"
    if not meta_path.is_file():
        return BoatFrame(None, 0.0, 0, 0.0)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    frame = ((meta.get("teak") or {}).get("frame") or {})
    axis = frame.get("longitudinal_axis")
    if not axis:
        return BoatFrame(None, 0.0, 0, 0.0)
    sign = int(np.sign(float(frame.get("bow_sign", 0) or 0)))
    return BoatFrame(np.asarray(axis, dtype=float)[:2],
                     float(frame.get("axis_confidence", 0.0)),
                     sign, float(frame.get("bow_confidence", 0.0)) if sign else 0.0)


def resolve_frame(run_dir: Path, options: dict[str, Any]) -> tuple[BoatFrame, list[str]]:
    """The boat frame this job will actually use, and what to warn about.

    Everything that needs a direction goes through here: the sheet rotation, the
    seam corrector, and the bow-up orientation of the seam tab.  One resolution
    per job means the manual grain override reaches all three or none of them.
    """

    detected = boat_axis(run_dir)
    axis, confidence = detected.axis, detected.confidence
    bow_sign, bow_confidence = detected.bow_sign, detected.bow_confidence
    warnings: list[str] = []
    override = options.get("grain_angle_deg")
    if override is not None:
        radians = math.radians(float(override))
        axis = np.array([math.cos(radians), math.sin(radians)])
        warnings.append(f"grain direction set manually to {float(override):.1f} deg "
                        "(overriding the detected boat axis)")
        # The override gives a line, not a heading, so the bow has to be carried
        # over from the detection: whichever way along the new axis still points
        # at the end the detector called the bow.  Without a detection there is
        # no bow at all, and the seam tab has to say so rather than pick an end.
        #
        # The projection is also how much bow there is LEFT to carry over, and
        # the confidence has to follow it down.  Turn the grain square to the
        # detected axis and the dot product is zero: the sign is then decided by
        # the last bit of a rounding error, and a two hundredth of a degree
        # nudge turns the whole deck end for end.  Keeping the detector's own
        # 0.82 there would have the seam tab state, with confidence, a bow it no
        # longer knows anything about -- so the confidence is scaled by the
        # projection, and below _BOW_CARRY_FLOOR there is no bow at all.
        bow = detected.bow_direction
        if bow is None:
            bow_sign, bow_confidence = 0, 0.0
        else:
            projection = float(np.dot(axis, bow))
            carried = bow_confidence * abs(projection)
            if carried < _BOW_CARRY_FLOOR:
                bow_sign, bow_confidence = 0, 0.0
                warnings.append(
                    "the manual grain angle is nearly square to the detected boat axis, so which end "
                    "is the bow no longer follows from it; the seam view is left unturned"
                )
            else:
                bow_sign = 1 if projection >= 0.0 else -1
                bow_confidence = carried
        if detected.axis is not None:
            # The pattern grooves were generated from the DETECTED axis and are
            # already baked into the fitted DXF. If the manual grain disagrees,
            # the planks on the finished deck will not run with the material
            # grain -- which is the whole reason the sheet has a direction.
            detected_deg = math.degrees(math.atan2(detected.axis[1], detected.axis[0]))
            difference = abs((float(override) - detected_deg + 90.0) % 180.0 - 90.0)
            if difference > 3.0:
                warnings.append(
                    f"the manual grain angle differs from the pattern's boat axis by "
                    f"{difference:.1f} deg ({detected_deg:.1f} deg). The pattern grooves are "
                    "already fixed in the fitted DXF, so the planks and the material grain "
                    "will not line up. Re-run the outline with the correct axis, or clear the "
                    "manual angle."
                )
        return BoatFrame(axis, 1.0, bow_sign, bow_confidence), warnings
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
    if axis is not None and bow_sign and bow_confidence < 0.5:
        warnings.append(
            f"which end of this boat is the bow was only {bow_confidence:.2f} sure, so the "
            "seam view may be drawn stern up. Check the shape before laying seams out by eye."
        )
    return BoatFrame(axis, confidence, bow_sign, bow_confidence), warnings


def resolve_axis(run_dir: Path, options: dict[str, Any]) -> tuple[np.ndarray | None, float, list[str]]:
    """The grain direction this job will actually use, and what to warn about.

    Both `plan` and `preview` go through here -- and so does the seam corrector,
    which is why the manual grain override re-squares every seam as well as
    re-orienting every sheet.  They used to resolve the axis separately, and
    `preview` ignored the override, so the placements were computed in the
    override frame while the picture was drawn in the detected frame and pieces
    appeared outside the sheet.  That bit exactly when the user was told to
    override, i.e. when detection was unsure.
    """

    frame, warnings = resolve_frame(run_dir, options)
    return frame.axis, frame.confidence, warnings


# --------------------------------------------------------------------------
# straightening the seams -- the one place it happens


def snapped_seams(run_dir: Path, config: dict[str, Any],
                  seams: Sequence[Seam] | None = None
                  ) -> tuple[list[Seam], list[SnapResult], list[str]]:
    """The user's seams as they will actually be cut, what was done to each,
    and what to tell them about it.

    This is the single entry point for seam straightening.  It resolves the boat
    frame the same way the cut does, reads the fitted loops the cut is made
    from, applies each seam's stored placement mode against the CURRENT axis,
    and then hands the lot to `seamsnap`.

    `seams` defaults to the run's own seams.json.  Every returned Seam carries
    the drawing it came from in `raw`, so calling this again on its own output
    gives the same answer: the correction always starts from what the user put
    down, never from the last correction's result.
    """

    run_dir = Path(run_dir)
    options = sheets_mod.settings(config)
    seam_list = list(seams) if seams is not None else sheets_mod.read_seams(run_dir)
    frame, warnings = resolve_frame(run_dir, options)
    source = source_dxf(run_dir)
    loops = sheets_mod.read_fitted_dxf(source)[0] if source is not None else {}
    snapped, results, snap_warnings = apply_snap(seam_list, loops, frame.axis, options)
    return snapped, results, warnings + snap_warnings


def apply_snap(seams: Sequence[Seam], loops: dict[int, list[Any]], axis: np.ndarray | None,
               options: dict[str, Any],
               references: list[Any] | None = None) -> tuple[list[Seam], list[SnapResult], list[str]]:
    """Straighten a seam list against an axis and a set of fitted loops.

    Split out from `snapped_seams` so that `plan`, which has already read the
    DXF and resolved the frame for the cut, straightens the seams against
    exactly those and not against a second reading of the same files.

    `references` may carry a pool the caller already built (the app caches one
    per fitted DXF for the hover, and the hover's settle has to be the SAME
    correction the click gets or the line jumps between them).  It defaults to
    being built here from `loops`.

    The reference pool is built here rather than handed to `seamsnap.snap_seams`
    because of the per-seam opt out: a seam with `snap` False is left exactly
    where the user placed it, but it is still a real join on the deck, so the
    seams drawn after it must be able to line up with WHERE IT IS -- not with
    where the corrector would have put it.

    Each seam is offered only the references on the panel it cuts.  The pool
    spans the whole nested layout, where panels that are metres apart on the
    boat sit side by side, and without that restriction a join on one panel
    could slide onto a stub of edge or seam on another (audit A6).
    """

    if references is None:
        references = seamsnap.axis_references(axis) + seamsnap.references_from_loops(loops, options)
    enabled = bool(options.get("seam_snap_enabled", True))
    min_ref = float(options["seam_snap_min_ref_length_mm"])

    pool = list(references)
    out: list[Seam] = []
    results: list[SnapResult] = []
    warnings: list[str] = []
    for index, seam in enumerate(seams):
        drawn = seam.drawn
        if not enabled or not seam.snap:
            # Placed by eye, and left exactly there: no correction, and no
            # re-aiming from the mode either, because "leave this one alone" has
            # to keep meaning that when the grain angle changes later.
            result = SnapResult(*drawn, applied=False, kind="", reference_label="",
                                angle_change_deg=0.0, moved_mm=0.0, note="")
        else:
            aimed, mode_warning = _aimed(seam, axis)
            if mode_warning and mode_warning not in warnings:
                warnings.append(mode_warning)
            # A seam placed with the direction-first tool arrives here already
            # aimed exactly where the user asked, so the corrector is told not to
            # turn it -- it may only slide it sideways onto an edge that shares
            # that direction. Without the lock a diagonal typed as ten degrees
            # sat inside the twenty degree axis capture and was swung all the way
            # onto the centreline, while the note and the seam list both went on
            # saying ten. The lock is dropped when the mode could not be applied
            # at all -- no axis to measure it from -- because then the direction
            # is only whatever happened to be drawn, and correcting that is the
            # whole point of the corrector.
            locked = bool(seam.mode) and not mode_warning
            result = _restated(seam, drawn,
                               seamsnap.snap_seam(*aimed, pool, options, axis=axis,
                                                  direction_locked=locked,
                                                  panel_ids=([int(seam.panel_id)]
                                                             if seam.panel_id is not None else None)))
        for warning in result.warnings:
            if warning not in warnings:
                warnings.append(warning)
        results.append(result)
        out.append(seam.moved_to(result.x1, result.y1, result.x2, result.y2))
        p0 = np.array([result.x1, result.y1], dtype=float)
        p1 = np.array([result.x2, result.y2], dtype=float)
        if float(np.hypot(*(p1 - p0))) >= min_ref:
            pool.append(seamsnap.Reference("seam", f"seam {seam.seam_id or index + 1}",
                                           seam.panel_id, p0, p1))
    return out, results, warnings


def _restated(seam: Seam, drawn: tuple[float, float, float, float],
              result: SnapResult) -> SnapResult:
    """The correction retold against the line the USER drew.

    `seamsnap` is handed the seam already turned onto its placement mode, so it
    honestly reports "nothing to do" for a seam that the mode had just swung
    twenty-five degrees -- which would leave the user staring at a seam that
    moved with no explanation of why.  Everything the fabricator is told is
    measured from their own drawing instead: how far it turned, how far it
    moved, and which of the two things did it.

    The three names borrowed from `seamsnap` under an underscore are borrowed on
    purpose: the undirected angle between two directions and the two float noise
    floors have exactly one correct definition each, and a second copy of them
    here is a second thing to keep in step.
    """

    if not seam.mode:
        return result
    p = np.array(drawn[:2], dtype=float)
    q = np.array(drawn[2:], dtype=float)
    a = np.array([result.x1, result.y1], dtype=float)
    b = np.array([result.x2, result.y2], dtype=float)
    span, moved_span = q - p, b - a
    if float(np.hypot(*span)) < 1e-9 or float(np.hypot(*moved_span)) < 1e-9:
        return result
    angle = seamsnap._direction_angle_deg(span / np.hypot(*span),
                                          moved_span / np.hypot(*moved_span))
    moved = max(float(np.hypot(*(a - p))), float(np.hypot(*(b - q))))
    if moved <= seamsnap._NO_CHANGE_MM and angle <= seamsnap._NO_CHANGE_DEG:
        return result                       # the mode asked for what was already there
    result.angle_change_deg = angle
    result.moved_mm = moved
    # The corrector's own note already names the fitted edge the seam settled
    # onto, and that is the more useful sentence, so it is kept whenever it says
    # more than the mode does.
    if not result.applied or result.reference_label in (seamsnap.ALONG_LABEL, seamsnap.ACROSS_LABEL):
        result.applied = True
        result.kind = _MODE_KINDS[seam.mode]
        result.reference_label = _MODE_LABELS.get(seam.mode) or _mode_words(seam)
        result.note = _mode_note(seam, angle)
    return result


_MODE_KINDS = {"along": "along-boat", "across": "across-boat", "angle": "angle-to-boat"}
_MODE_LABELS = {"along": seamsnap.ALONG_LABEL, "across": seamsnap.ACROSS_LABEL}


def _mode_note(seam: Seam, angle_deg: float) -> str:
    """One short sentence, in the same voice `seamsnap` uses."""

    if seam.mode == "along":
        sentence = f"lined up with {seamsnap.ALONG_LABEL}"
    elif seam.mode == "across":
        sentence = f"squared {seamsnap.ACROSS_LABEL}"
    else:
        sentence = f"set to {float(seam.angle_deg or 0.0):g} deg off the boat"
    if angle_deg < 0.05:
        return sentence
    return f"{sentence} (was {angle_deg:.1f} deg off)"


def _aimed(seam: Seam, axis: np.ndarray | None) -> tuple[tuple[float, float, float, float], str]:
    """The drawn seam turned onto the direction its placement mode asks for.

    A seam placed "across the boat" has to STAY exactly across the boat when the
    axis moves under it -- when the user sets a manual grain angle, say -- so its
    direction is recomputed here from the current axis rather than being frozen
    at whatever coordinates it was first given.  The seam is swung about its own
    midpoint and keeps its length, so it stays where it was put; only its
    direction changes, and for the usual case where the axis has not moved
    nothing changes at all.
    """

    x1, y1, x2, y2 = seam.drawn
    if not seam.mode:
        return (x1, y1, x2, y2), ""
    unit = seamplace.direction_for(axis, seam.mode, seam.angle_deg)
    if unit is None:
        return (x1, y1, x2, y2), (
            f"seam {seam.seam_id or '?'} was placed {_mode_words(seam)}, but this run has no boat "
            "direction to measure that against, so it was left at the angle it was drawn. "
            "Set the grain angle."
        )
    p = np.array([x1, y1], dtype=float)
    q = np.array([x2, y2], dtype=float)
    half = float(np.hypot(*(q - p))) / 2.0
    if half < 1e-9:
        return (x1, y1, x2, y2), ""
    if float(np.dot(q - p, unit)) < 0.0:
        unit = -unit                        # keep the end the user started from first
    middle = (p + q) / 2.0
    start, end = middle - unit * half, middle + unit * half
    return (float(start[0]), float(start[1]), float(end[0]), float(end[1])), ""


def _mode_words(seam: Seam) -> str:
    if seam.mode == "along":
        return "along the boat"
    if seam.mode == "across":
        return "across the boat"
    return f"at {float(seam.angle_deg or 0.0):.0f} deg off the boat"


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
    warnings: list[str] = []
    progress(f"Reading fitted geometry from {source.name}")
    loops, pattern, pattern_kind = sheets_mod.read_fitted_dxf(source, warnings)
    if not loops:
        raise ValueError(f"{source.name} contains no CAM loops")

    frame, frame_warnings = resolve_frame(run_dir, options)
    warnings.extend(frame_warnings)
    axis, confidence = frame.axis, frame.confidence
    rotation = sheets_mod.sheet_transform(axis)

    # Straighten before splitting, against the loops just read and the frame
    # just resolved, so the cut is made from the corrected seams and the preview
    # is drawn from the same corrected seams.
    seam_list, snap_results, snap_warnings = apply_snap(seam_list, loops, axis, options)
    warnings.extend(snap_warnings)

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
            f"piece {item['piece_id']} is {item['width_mm']:.1f} x {item['length_mm']:.1f} mm, "
            f"over by {item['over_width_mm']:.1f} x {item['over_length_mm']:.1f} mm -- {item['hint']}"
        )

    progress(f"Nesting {len(pieces)} piece(s) on "
             f"{options['sheet_width_mm']:.0f} x {options['sheet_length_mm']:.0f} mm sheets")
    sheet_list, summary, nest_warnings = nesting.nest(pieces, rotation, options)
    warnings.extend(nest_warnings)

    by_id = {piece.piece_id: piece for piece in pieces}
    files: list[dict[str, Any]] = []
    if write_files and sheet_list:
        progress(f"Writing {len(sheet_list)} sheet DXF(s)")
        # The export measures every piece again as it writes it and refuses any
        # that will not fit -- see `nesting.write_sheet_dxfs`. Its refusals go
        # into the job log and into `warnings`, which is what puts them in
        # sheet_report.md, because a file that is short a part has to say so
        # everywhere the user looks.
        files = nesting.write_sheet_dxfs(
            run_dir, sheet_list, by_id, rotation, pattern, pattern_kind, options,
            progress=progress, warnings=warnings,
        )
        _remove_stale_sheet_dxfs(run_dir, files, progress)

    refused = [item for entry in files for item in (entry.get("refused") or [])]
    worst_arc = max((p.max_arc_error_mm for p in pieces), default=0.0)
    status = "OK"
    if oversize or summary["unplaced_piece_ids"] or refused:
        status = "NEEDS_SEAMS"
    elif not sheet_list:
        status = "EMPTY"

    result: dict[str, Any] = {
        "status": status,
        "source_dxf": source.name,
        "seam_count": len(seam_list),
        "piece_count": len(pieces),
        "oversize": oversize,
        # Pieces the export itself refused to write, measured as the geometry
        # that was about to go into the DXF. Normally empty and normally a
        # repeat of `oversize`; when the two disagree it is this one that
        # describes what is actually in the files.
        "refused": refused,
        "summary": summary,
        "files": files,
        "warnings": warnings,
        "max_arc_rebuild_error_mm": round(worst_arc, 4),
        # How many arc runs the rebuild refitted as polylines.  Zero on a
        # healthy split; `max_arc_rebuild_error_mm` only counts the arcs that
        # PASSED, so this is the only number that can see flattening.
        "arc_fallbacks": int(sum(p.arc_fallbacks for p in pieces)),
        "boat_axis": None if axis is None else [round(float(v), 6) for v in axis],
        "boat_axis_confidence": round(confidence, 4),
        "boat_frame": frame.to_dict(),
        # The seams AS CUT, each still carrying the drawing it came from, and
        # what the corrector did to it. `preview` re-splits from exactly these,
        # which is what makes the picture and the cut the same geometry.
        "seams": [seam.to_dict() for seam in seam_list],
        "seam_snaps": [snap.to_dict() for snap in snap_results],
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
    # A refused piece never made it into the file, so it must not be counted as
    # using the sheet either: "sheet 3, 62% used" with a part missing reads as
    # a job that fits when it does not.  Its placement row keeps the id (the
    # report still has to name it) but carries "(not written)".
    if refused:
        blocked = {item["piece_id"] for item in refused}
        sheet_area = float(options["sheet_width_mm"]) * float(options["sheet_length_mm"])
        for sheet_row in result["sheets"]:
            missing = [p for p in sheet_row["placements"] if p["piece_id"] in blocked]
            if not missing:
                continue
            lost = sum(by_piece_area(by_id, p["piece_id"]) for p in missing)
            sheet_row["utilisation"] = round(
                max(0.0, sheet_row["utilisation"] - lost / sheet_area), 4)
            for p in sheet_row["placements"]:
                if p["piece_id"] in blocked:
                    p["not_written"] = True
    if write_files:
        (run_dir / "sheets.json").write_text(json.dumps(result, indent=2),
                                             encoding="utf-8", newline="\n")
        _write_report(run_dir / "sheet_report.md", result, options)
    return result


def by_piece_area(by_id: dict[str, Piece], piece_id: str) -> float:
    piece = by_id.get(piece_id)
    return float(piece.area_mm2) if piece is not None else 0.0


def _remove_stale_sheet_dxfs(run_dir: Path, files: Sequence[dict[str, Any]],
                             progress: Progress | None = None) -> None:
    """Delete every sheet DXF this export did not write.

    The files are numbered, and a re-export that needs FEWER sheets used to
    leave the old higher-numbered ones sitting beside the new set with working
    download links -- "sheet 4 (0 pieces)" offered next to a two-sheet job, and
    nothing anywhere said it was from an older, bigger layout.  They are
    deleted instead, so what is on disk is always exactly the last export.
    """

    import re as _re

    say = progress or _silent
    written = {entry.get("name") for entry in files}
    pattern = _re.compile(r"^sheet_\d{2,3}\.dxf$")
    for path in sorted(Path(run_dir).glob("sheet_*.dxf")):
        if path.name in written or not pattern.match(path.name):
            continue
        try:
            path.unlink()
            say(f"Removed {path.name} -- it was left over from a bigger layout")
        except OSError as error:
            say(f"warning: could not remove the stale {path.name} ({error})")


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

    # The STRAIGHTENED seams plan() just cut with, not the raw ones the caller
    # passed: splitting from the drawing again here would draw one set of pieces
    # and cut another, which is the same class of bug as resolving the axis
    # twice. `plan` reports them in full precision, so the split is identical.
    seam_list = [Seam.from_dict(item) for item in result["seams"]]
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
            # `hole` is what tells the drawing a ring apart from a part.  A piece
            # with a cut-out emits two rings carrying the same piece id, and
            # drawn identically they read as two parts nested one inside the
            # other: the sheet picture showed nine shapes for eight pieces and
            # invited the fabricator to go looking for a part that does not
            # exist.  A hole is a hole and has to be drawn as one.
            for index, loop in enumerate([piece.outer, *piece.holes]):
                # Coarser than the geometry step: this is for drawing on screen,
                # where 4 mm chords are already sub-pixel on a 2 m sheet.
                points, _s = sheets_mod.sample_loop(loop, max(step, 4.0))
                moved = placement.apply(points, rotation)
                rings.append({
                    "piece_id": entry["piece_id"], "panel_id": entry["panel_id"],
                    "rotation_deg": entry["rotation_deg"], "hole": index > 0,
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
    """Sheet DXFs already written for this run, newest export first.

    An entry is marked `stale` when the seams on disk have changed since the
    export was made -- the file is from an older, different layout, and the
    page says so beside its download link instead of offering it as though it
    matched the seams on screen.
    """

    run_dir = Path(run_dir)
    written = json.loads((run_dir / "sheets.json").read_text(encoding="utf-8")) \
        if (run_dir / "sheets.json").is_file() else {}
    by_name = {entry.get("name"): entry for entry in (written.get("files") or [])}
    exported_seam_ids = [str(s.get("seam_id"))
                         for s in (written.get("seams") or [])]
    live_seam_ids = [str(s.seam_id) for s in sheets_mod.read_seams(run_dir)]
    stale = exported_seam_ids != live_seam_ids
    files: list[dict[str, Any]] = []
    for path in sorted(Path(run_dir).glob("sheet_*.dxf")):
        entry = dict(by_name.get(path.name) or {})
        entry.setdefault("name", path.name)
        entry.setdefault("sheet", len(files) + 1)
        entry.setdefault("pieces", [])
        entry.setdefault("utilisation", 0.0)
        entry["exists"] = True
        entry["stale"] = bool(stale or not by_name.get(path.name))
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
            names = ", ".join(
                p["piece_id"] + (" *(not written)*" if p.get("not_written") else "")
                for p in sheet["placements"]) or "-"
            lines.append(f"| {sheet['sheet']} | {names} | {sheet['utilisation'] * 100:.1f}% |")
        lines.append("")
        lines += ["## Placements", "",
                  "| sheet | piece | panel | rotation | size mm | position mm |", "|---|---|---|---:|---|---|"]
        for sheet in result["sheets"]:
            for p in sheet["placements"]:
                marker = " *(not written)*" if p.get("not_written") else ""
                lines.append(
                    f"| {sheet['sheet']} | {p['piece_id']}{marker} | {p['panel_id']} | {p['rotation_deg']} deg | "
                    f"{p['width_mm']:.0f} x {p['length_mm']:.0f} | "
                    f"({p['offset_mm'][0]:.0f}, {p['offset_mm'][1]:.0f}) |")
        lines.append("")
    if result["oversize"]:
        lines += ["## Still too big for a sheet", "",
                  "These need another seam before they can be cut:", ""]
        for item in result["oversize"]:
            lines.append(f"- **{item['piece_id']}** {item['width_mm']:.1f} x {item['length_mm']:.1f} mm "
                         f"(over by {item['over_width_mm']:.1f} x {item['over_length_mm']:.1f} mm) -- {item['hint']}")
        lines.append("")
    if result.get("refused"):
        lines += ["## Left out of the sheet files", "",
                  "The export measured these pieces as it was writing them, found they could not "
                  "be cut, and did NOT put them in the DXF. The sheet files above are short these "
                  "parts on purpose:", ""]
        for item in result["refused"]:
            lines.append(f"- **{item['piece_id']}** on sheet {item['sheet']} -- {item['reason']}")
        lines.append("")
    if result["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in result["warnings"]] + [""]
    lines += ["Manufacturing approval stays TEST_ONLY until a human has physically verified a cut.", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
