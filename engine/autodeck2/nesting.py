"""Packing cut pieces onto 40 x 80 inch sheets, and writing one DXF per sheet.

Grain is the whole constraint.  Reflex TruGrain is directional, so the 80 inch
(2032 mm) sheet dimension has to run along the length of the boat, and a part
may only be turned by 180 degrees -- never 90, which would run the grain
across the boat, and never mirrored.  180 degrees costs nothing visually and
is what lets an L-shaped piece tuck into its neighbour's notch, which is where
the material saving actually comes from.

`sheets.sheet_transform` has already rotated everything so +Y is along the
boat, so packing here is plain axis-aligned rectangle-and-polygon work.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

from .sheets import Loop, Piece, sample_loop


@dataclass
class Placement:
    """Where one piece sits on one sheet."""

    piece_id: str
    panel_id: int
    sheet_index: int
    rotation_deg: int                       # 0 or 180 only
    offset: np.ndarray                      # applied after rotation, sheet mm
    origin: np.ndarray                      # bbox min of the rotated piece, subtracted first
    width_mm: float = 0.0
    length_mm: float = 0.0

    def matrix(self, base: np.ndarray) -> np.ndarray:
        radians = math.radians(self.rotation_deg)
        cos, sin = math.cos(radians), math.sin(radians)
        return np.array([[cos, -sin], [sin, cos]]) @ base

    def apply(self, xy: np.ndarray, base: np.ndarray) -> np.ndarray:
        """Placed-frame points -> sheet coordinates."""

        return np.asarray(xy, dtype=float) @ self.matrix(base).T - self.origin + self.offset

    def to_dict(self) -> dict[str, Any]:
        return {"piece_id": self.piece_id, "panel_id": self.panel_id,
                "sheet_index": self.sheet_index, "rotation_deg": self.rotation_deg,
                "offset_mm": [round(float(v), 3) for v in self.offset],
                "origin_mm": [round(float(v), 3) for v in self.origin],
                "width_mm": round(self.width_mm, 1), "length_mm": round(self.length_mm, 1)}


@dataclass
class Sheet:
    index: int
    placements: list[Placement] = field(default_factory=list)
    used_area_mm2: float = 0.0

    def utilisation(self, options: dict[str, Any]) -> float:
        area = float(options["sheet_width_mm"]) * float(options["sheet_length_mm"])
        return self.used_area_mm2 / area if area else 0.0


def _rotation(degrees: int) -> np.ndarray:
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    return np.array([[cos, -sin], [sin, cos]])


def _piece_polygon(piece: Piece, matrix: np.ndarray, step_mm: float) -> Polygon:
    shell, _s = sample_loop(piece.outer, step_mm)
    rings = []
    for hole in piece.holes:
        ring, _h = sample_loop(hole, step_mm)
        if len(ring) >= 3:
            rings.append(ring @ matrix.T)
    polygon = Polygon(shell @ matrix.T, rings)
    return polygon if polygon.is_valid else polygon.buffer(0.0)


def nest(pieces: Sequence[Piece], base_rotation: np.ndarray,
         options: dict[str, Any]) -> tuple[list[Sheet], dict[str, Any], list[str]]:
    """Bottom-left first fit with polygon collision.

    Pieces go down largest first, each at the lowest then leftmost position
    where it stays inside the usable area and keeps `part_spacing_mm` clear of
    everything already placed.  Deterministic; good for the handful of parts a
    deck yields.  It is a first-fit heuristic, not a no-fit-polygon optimiser,
    and does not claim optimality.
    """

    step = float(options["sample_step_mm"])
    spacing = float(options["part_spacing_mm"])
    grid = float(options["nest_step_mm"])
    usable_w = float(options["max_part_width_mm"])
    usable_l = float(options["max_part_length_mm"])
    margin_x = (float(options["sheet_width_mm"]) - usable_w) / 2.0
    margin_y = (float(options["sheet_length_mm"]) - usable_l) / 2.0
    angles: tuple[int, ...] = (0, 180) if options.get("allow_180_rotation", True) else (0,)

    warnings: list[str] = []
    prepared: list[tuple[Piece, dict[int, tuple[Polygon, float, float]]]] = []
    piece_origins: dict[str, dict[int, np.ndarray]] = {}
    unplaced: list[str] = []

    for piece in pieces:
        variants: dict[int, tuple[Polygon, float, float]] = {}
        origins: dict[int, np.ndarray] = {}
        for angle in angles:
            polygon = _piece_polygon(piece, _rotation(angle) @ base_rotation, step)
            minx, miny, maxx, maxy = polygon.bounds
            origins[angle] = np.array([minx, miny], dtype=float)
            variants[angle] = (affinity.translate(polygon, -minx, -miny), maxx - minx, maxy - miny)
        piece_origins[piece.piece_id] = origins
        if all(w > usable_w or l > usable_l for _p, w, l in variants.values()):
            unplaced.append(piece.piece_id)
            warnings.append(
                f"piece {piece.piece_id} is {variants[0][1]:.0f} x {variants[0][2]:.0f} mm and does not fit "
                f"the {usable_w:.1f} x {usable_l:.1f} mm usable area in any allowed rotation; add a seam"
            )
            continue
        prepared.append((piece, variants))

    order = sorted(prepared, key=lambda item: (-item[0].area_mm2, item[0].piece_id))
    sheets: list[Sheet] = []
    occupancy: list[Any] = []

    for piece, variants in order:
        target: Sheet | None = None
        spot: tuple[int, float, float] | None = None
        for sheet in sheets:
            spot = _find_spot(variants, occupancy[sheet.index], usable_w, usable_l, grid, angles)
            if spot is not None:
                target = sheet
                break
        if target is None:
            target = Sheet(index=len(sheets))
            sheets.append(target)
            occupancy.append(None)
            spot = _find_spot(variants, None, usable_w, usable_l, grid, angles)
        if spot is None:
            unplaced.append(piece.piece_id)
            warnings.append(f"piece {piece.piece_id} could not be placed even on an empty sheet")
            continue
        _commit(target, occupancy, piece, variants, piece_origins[piece.piece_id],
                spot, margin_x, margin_y, spacing)

    summary = {
        "sheet_count": len(sheets),
        "piece_count": sum(len(s.placements) for s in sheets),
        "unplaced_piece_ids": unplaced,
        "sheet_width_mm": float(options["sheet_width_mm"]),
        "sheet_length_mm": float(options["sheet_length_mm"]),
        "usable_width_mm": usable_w,
        "usable_length_mm": usable_l,
        "part_spacing_mm": spacing,
        "seam_gap_mm": float(options["seam_gap_mm"]),
        "rotations_allowed_deg": list(angles),
        "utilisation": [round(s.utilisation(options), 4) for s in sheets],
    }
    return sheets, summary, warnings


def _find_spot(variants: dict[int, tuple[Polygon, float, float]], occupied: Any,
               usable_w: float, usable_l: float, grid: float,
               angles: Iterable[int]) -> tuple[int, float, float] | None:
    """Lowest-then-leftmost feasible (angle, x, y) for this piece, or None.

    `occupied` is the union of everything already on the sheet, already grown
    by the minimum part spacing, so a simple intersection test enforces the
    clearance.
    """

    blocked = prep(occupied) if occupied is not None else None
    best: tuple[int, float, float] | None = None
    for angle in angles:
        polygon, width, length = variants[angle]
        if width > usable_w + 1e-9 or length > usable_l + 1e-9:
            continue
        y = 0.0
        while y <= usable_l - length + 1e-9:
            if best is not None and y > best[2]:
                break
            x = 0.0
            while x <= usable_w - width + 1e-9:
                if best is not None and (y, x) >= (best[2], best[1]):
                    break
                candidate = affinity.translate(polygon, x, y)
                if blocked is None or not blocked.intersects(candidate):
                    if best is None or (y, x) < (best[2], best[1]):
                        best = (angle, x, y)
                    break
                x += grid
            y += grid
    return best


def _commit(sheet: Sheet, occupancy: list[Any], piece: Piece,
            variants: dict[int, tuple[Polygon, float, float]],
            origins: dict[int, np.ndarray], spot: tuple[int, float, float],
            margin_x: float, margin_y: float, spacing: float) -> None:
    angle, x, y = spot
    polygon, width, length = variants[angle]
    placed = affinity.translate(polygon, x, y)
    sheet.placements.append(Placement(
        piece_id=piece.piece_id, panel_id=piece.panel_id, sheet_index=sheet.index,
        rotation_deg=angle, offset=np.array([x + margin_x, y + margin_y], dtype=float),
        origin=origins[angle], width_mm=width, length_mm=length,
    ))
    sheet.used_area_mm2 += float(placed.area)
    clear = placed.buffer(spacing, join_style=2)
    occupancy[sheet.index] = clear if occupancy[sheet.index] is None else \
        unary_union([occupancy[sheet.index], clear])


# --------------------------------------------------------------------------
# export


_UNSAFE_LAYER = re.compile(r"[^A-Za-z0-9_]")


def _layer(prefix: str, piece_id: str) -> str:
    """DXF layer names cannot contain ':' (and we avoid anything else exotic),
    matching the CAM_USER__PANEL_n convention already used by ingest.py."""

    return f"{prefix}__{_UNSAFE_LAYER.sub('_', piece_id)}"


def place_loop(loop: Loop, placement: Placement, base_rotation: np.ndarray) -> Loop:
    """A loop moved onto its sheet.

    The bulge is carried through unchanged: bulge is invariant under rotation
    and translation, and mirroring -- the one transform that would negate it --
    is never applied to a directional-grain part.
    """

    return Loop(np.column_stack([placement.apply(loop.xy, base_rotation), loop.bulges]))


def write_sheet_dxfs(run_dir: Path, sheets: Sequence[Sheet], pieces: dict[str, Piece],
                     base_rotation: np.ndarray,
                     pattern: dict[int, list[np.ndarray]], pattern_kind: str | None,
                     options: dict[str, Any]) -> list[dict[str, Any]]:
    """One DXF per sheet, in sheet coordinates with the sheet corner at the
    origin -- the frame the CNC expects.

    Each sheet carries the cut profiles on `CAM__<piece>` layers, the pattern
    grooves clipped to each piece on `PATTERN_<KIND>__<piece>`, and the sheet
    outline plus usable area on reference layers so the operator can see the
    margin.  Every file is re-read after writing and the polylines checked
    closed, the same round-trip ingest.py does.
    """

    import ezdxf

    step = float(options["sample_step_mm"])
    written: list[dict[str, Any]] = []
    for sheet in sheets:
        doc = ezdxf.new("R2010", setup=True)
        doc.header["$INSUNITS"] = 4                     # millimetres
        msp = doc.modelspace()

        outline = _layer("SHEET", "OUTLINE")
        doc.layers.add(outline).rgb = (120, 120, 120)
        msp.add_lwpolyline(
            [(0.0, 0.0), (options["sheet_width_mm"], 0.0),
             (options["sheet_width_mm"], options["sheet_length_mm"]), (0.0, options["sheet_length_mm"])],
            format="xy", close=True, dxfattribs={"layer": outline})
        usable = _layer("SHEET", "USABLE")
        doc.layers.add(usable).rgb = (90, 90, 90)
        mx = (options["sheet_width_mm"] - options["max_part_width_mm"]) / 2.0
        my = (options["sheet_length_mm"] - options["max_part_length_mm"]) / 2.0
        msp.add_lwpolyline(
            [(mx, my), (mx + options["max_part_width_mm"], my),
             (mx + options["max_part_width_mm"], my + options["max_part_length_mm"]),
             (mx, my + options["max_part_length_mm"])],
            format="xy", close=True, dxfattribs={"layer": usable})

        counts: dict[str, int] = {}
        for placement in sheet.placements:
            piece = pieces[placement.piece_id]
            cam = _layer("CAM", placement.piece_id)
            if cam not in doc.layers:
                doc.layers.add(cam)
            for loop in [piece.outer, *piece.holes]:
                moved = place_loop(loop, placement, base_rotation)
                msp.add_lwpolyline(
                    [(float(x), float(y), float(b)) for x, y, b in moved.vertices],
                    format="xyb", close=True, dxfattribs={"layer": cam})
                counts[cam] = counts.get(cam, 0) + 1

            grooves = pattern.get(piece.panel_id) or []
            if not grooves:
                continue
            shell, _s = sample_loop(piece.outer, step)
            boundary = Polygon(placement.apply(shell, base_rotation))
            if not boundary.is_valid:
                boundary = boundary.buffer(0.0)
            holes = []
            for hole in piece.holes:
                ring, _h = sample_loop(hole, step)
                candidate = Polygon(placement.apply(ring, base_rotation))
                holes.append(candidate if candidate.is_valid else candidate.buffer(0.0))
            if holes:
                boundary = boundary.difference(unary_union(holes))
            player = _layer(f"PATTERN_{(pattern_kind or 'TEAK').upper()}", placement.piece_id)
            if player not in doc.layers:
                doc.layers.add(player).rgb = (150, 100, 40)
            for segment in grooves:
                clipped = LineString(placement.apply(segment, base_rotation)).intersection(boundary)
                for part in (clipped.geoms if isinstance(clipped, MultiLineString) else [clipped]):
                    if part.is_empty or part.geom_type != "LineString":
                        continue
                    coords = np.asarray(part.coords)
                    msp.add_line((float(coords[0][0]), float(coords[0][1])),
                                 (float(coords[-1][0]), float(coords[-1][1])),
                                 dxfattribs={"layer": player})
                    counts[player] = counts.get(player, 0) + 1

        path = Path(run_dir) / f"sheet_{sheet.index + 1:02d}.dxf"
        doc.saveas(path)

        reread = ezdxf.readfile(str(path))
        polylines = [p for p in reread.modelspace().query("LWPOLYLINE")
                     if str(p.dxf.layer).startswith("CAM__")]
        written.append({
            "sheet": sheet.index + 1,
            "path": str(path),
            "name": path.name,
            "units": reread.header.get("$INSUNITS"),
            "pieces": [p.piece_id for p in sheet.placements],
            "cam_polylines": len(polylines),
            "all_closed": all(p.closed for p in polylines),
            "pattern_lines": len(reread.modelspace().query("LINE")),
            "utilisation": round(sheet.utilisation(options), 4),
            "layer_counts": counts,
        })
    return written
