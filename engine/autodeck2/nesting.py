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
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry
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


def _sampled_rings(piece: Piece, step_mm: float) -> tuple[np.ndarray, list[np.ndarray]]:
    """The piece's loops as dense point rings.

    Both allowed rotations are these same points through a different matrix,
    and walking a 4000 point outline is not free, so it is done once per piece
    rather than once per rotation.
    """

    shell, _s = sample_loop(piece.outer, step_mm)
    rings = []
    for hole in piece.holes:
        ring, _h = sample_loop(hole, step_mm)
        if len(ring) >= 3:
            rings.append(ring)
    return shell, rings


def _piece_polygon(shell: np.ndarray, holes: Sequence[np.ndarray],
                   matrix: np.ndarray) -> BaseGeometry:
    """The piece at one rotation, repaired if the fitted outline was invalid.

    `buffer(0.0)` may hand back a MultiPolygon rather than a Polygon, and that
    is not an edge case worth ignoring: a deck outline that pinches -- an
    hourglass sole, a coaming that closes back on itself -- repairs into two
    lobes joined at a point, and `sheets.split_panel` passes such an outline
    through untouched when the panel has no seams on it.  Everything the nester
    does with this geometry (translate, intersects, bounds, area) is defined for
    a multi-part geometry too, so it is carried as one rather than being thrown
    away or forced back into a single ring.
    """

    polygon = Polygon(shell @ matrix.T, [ring @ matrix.T for ring in holes])
    return polygon if polygon.is_valid else polygon.buffer(0.0)


def _parts(geometry: BaseGeometry) -> list[Polygon]:
    """The polygon parts of a geometry, so a repaired outline that came back in
    two lobes is walked in full instead of raising on `.exterior`."""

    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    return [part for part in getattr(geometry, "geoms", ()) if isinstance(part, Polygon)]


# --------------------------------------------------------------------------
# the candidate piece, ready to be tried at a position
#
# How many sample points each successive filtering pass in `_find_spot` uses.
# The first pass runs over every x position in a row, so it has to be tiny; the
# later passes only ever see the handful of positions that survived the one
# before, so they can afford to be thorough.
_PROBE_LEVELS = (8, 48, 256)

# How far inside the piece an interior sample point has to sit before it is
# trusted.  A point exactly on the edge would be a coin toss once it and the
# outline have been rounded to their own nearest doubles at some offset, and a
# coin toss here would move a part on the sheet.  A micron is millions of times
# the rounding error at these coordinates and costs nothing in coverage.
_PROBE_INSET_MM = 1e-6


@dataclass
class _Variant:
    """One piece at one allowed rotation, shifted so its bbox corner is (0, 0).

    `probes` is the piece's own sample points -- one set per pass, smallest
    first -- and `bounds` is the polygon's real extent rather than the nominal
    width and length, so the bounding-box rejection in `_find_spot` is exact.
    """

    polygon: BaseGeometry
    width: float
    length: float
    bounds: tuple[float, float, float, float]
    probes: tuple[np.ndarray, ...] = ()


def _spread(points: np.ndarray, count: int) -> np.ndarray:
    """`count` of `points`, taken evenly across the array so that a small set
    still covers the whole shape instead of bunching up in one corner."""

    if len(points) <= count:
        return points
    index = np.unique(np.linspace(0, len(points) - 1, count).round().astype(int))
    return points[index]


def _probes(polygon: BaseGeometry, counts: Sequence[int]) -> tuple[np.ndarray, ...]:
    """Points lying on or inside `polygon`, as one spread-out set per size.

    Ring vertices come first because every polygon has them, and some pieces
    have nowhere else to take points from -- a sliver 6 mm tall leaves no room
    for an interior grid.  Interior points are mixed in on top so that a piece
    big enough to swallow something already placed whole, without its outline
    ever crossing it, is still caught by the cheap test rather than falling
    through to the slow one.

    Every part is walked, not just the first: a repaired self-touching outline
    arrives here as two lobes, and probing only one of them would let the other
    be placed straight through a part already on the sheet.
    """

    rings: list[np.ndarray] = []
    for part in _parts(polygon):
        rings.append(np.asarray(part.exterior.coords, dtype=float)[:-1])
        rings.extend(np.asarray(hole.coords, dtype=float)[:-1] for hole in part.interiors)
    rings = [ring for ring in rings if len(ring)]
    boundary = np.vstack(rings) if rings else np.empty((0, 2), dtype=float)

    # An interior grid sized so the densest pass has a few hundred points to
    # choose from.  The span floor keeps a long thin piece, whose area is tiny
    # next to its length, from asking for a grid of millions of points.
    minx, miny, maxx, maxy = polygon.bounds
    span = max(maxx - minx, maxy - miny, 1e-9)
    step = max(math.sqrt(max(polygon.area, 1e-9) / max(counts)), span / 512.0, 1e-9)
    grid_x = np.arange(minx + step / 2.0, maxx, step)
    grid_y = np.arange(miny + step / 2.0, maxy, step)
    interior = np.empty((0, 2), dtype=float)
    if grid_x.size and grid_y.size:
        mesh_x, mesh_y = (axis.ravel() for axis in np.meshgrid(grid_x, grid_y))
        inside = shapely.contains_xy(polygon, mesh_x, mesh_y)
        # Thin the grid to what the densest pass could use before measuring how
        # far the survivors sit from the edge: the measurement walks the whole
        # outline, which is thousands of points long, so it is worth doing once
        # for a couple of hundred candidates rather than for all of them.  The
        # outline gets an index of its own for the same reason.
        interior = _spread(np.column_stack([mesh_x[inside], mesh_y[inside]]), max(counts))
        if len(interior):
            edge = polygon.boundary
            shapely.prepare(edge)
            hugging = shapely.dwithin(edge, shapely.points(interior[:, 0], interior[:, 1]),
                                      _PROBE_INSET_MM)
            interior = interior[~hugging]

    return tuple(np.vstack([_spread(boundary, count - count // 2),
                            _spread(interior, count // 2)]) for count in counts)


def _grid_steps(limit: float, grid: float) -> np.ndarray:
    """0, grid, 2*grid ... up to `limit`, built by repeated addition.

    Adding rather than multiplying matters: with a fractional nest step the two
    disagree in the last bit or two, and this nester's contract is that it puts
    out the same cut file for the same job every single time.
    """

    values: list[float] = []
    value = 0.0
    while value <= limit + 1e-9:
        values.append(value)
        value += grid
    return np.array(values, dtype=float)


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
    prepared: list[tuple[Piece, dict[int, _Variant]]] = []
    piece_origins: dict[str, dict[int, np.ndarray]] = {}
    unplaced: list[str] = []

    for piece in pieces:
        variants: dict[int, _Variant] = {}
        origins: dict[int, np.ndarray] = {}
        shell, holes = _sampled_rings(piece, step)
        for angle in angles:
            polygon = _piece_polygon(shell, holes, _rotation(angle) @ base_rotation)
            minx, miny, maxx, maxy = polygon.bounds
            origins[angle] = np.array([minx, miny], dtype=float)
            moved = affinity.translate(polygon, -minx, -miny)
            variants[angle] = _Variant(moved, maxx - minx, maxy - miny, moved.bounds)
        piece_origins[piece.piece_id] = origins
        # The same 1e-9 slack `_find_spot` gives the grid search: a piece
        # exactly on the envelope (an exact 990.6 x 2006.6 rectangle on a
        # rotated axis measures a few 1e-13 over, purely from the rotation's
        # trigonometry) used to be refused here while the search would have
        # placed it happily.
        if all(v.width > usable_w + 1e-9 or v.length > usable_l + 1e-9
               for v in variants.values()):
            unplaced.append(piece.piece_id)
            warnings.append(
                f"piece {piece.piece_id} is {variants[0].width:.0f} x {variants[0].length:.0f} mm and does not fit "
                f"the {usable_w:.1f} x {usable_l:.1f} mm usable area in any allowed rotation; add a seam"
            )
            continue
        # Only a piece that will actually be searched for needs sample points.
        for variant in variants.values():
            variant.probes = _probes(variant.polygon, _PROBE_LEVELS)
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


def _find_spot(variants: dict[int, _Variant], occupied: Any,
               usable_w: float, usable_l: float, grid: float,
               angles: Iterable[int]) -> tuple[int, float, float] | None:
    """Lowest-then-leftmost feasible (angle, x, y) for this piece, or None.

    `occupied` is the union of everything already on the sheet, already grown
    by the minimum part spacing, so a simple intersection test enforces the
    clearance.

    Nearly every position on a sheet is blocked, and this is where the whole
    job used to spend its time.  Asking shapely outright costs about 50 us,
    because a fresh translated copy of a 1500-to-4000 point polygon has to be
    built before the intersection test can even start, and a 1016 x 2032 mm
    sheet holds 80000-odd positions per rotation at the default 5 mm step.

    A blocked position is far cheaper to PROVE than a free one: if a single
    point of the piece lands inside the occupied region then the two overlap,
    and that is the end of it.  So `_first_free_x` throws sample points at a
    whole row of positions in one vectorised shapely call, then at the
    survivors again with more points, and only what comes through all of that
    reaches the real polygon test.  The polygon test is still the only thing
    that can declare a position free, so the search lands on exactly the
    position it always did -- the sample points only skip work that was going
    to come back "blocked" anyway.
    """

    # `prep` builds the occupied region's index into the geometry itself, so
    # the point tests in `_first_free_x` get it too without being handed the
    # wrapper.
    blocked = prep(occupied) if occupied is not None else None
    best: tuple[int, float, float] | None = None
    for angle in angles:
        variant = variants[angle]
        if variant.width > usable_w + 1e-9 or variant.length > usable_l + 1e-9:
            continue
        columns = _grid_steps(usable_w - variant.width, grid)
        for y in _grid_steps(usable_l - variant.length, grid):
            if best is not None and y > best[2]:
                break
            # The old inner loop gave up on a row as soon as (y, x) could no
            # longer beat the best spot so far; trimming the row does the same.
            row = columns[columns < best[1]] if best is not None and y == best[2] else columns
            x = _first_free_x(variant, row, float(y), occupied, blocked)
            if x is not None:
                best = (angle, x, float(y))
    return best


def _first_free_x(variant: _Variant, row: np.ndarray, y: float,
                  occupied: Any, blocked: Any) -> float | None:
    """Smallest x in `row` where the piece clears everything already placed."""

    if blocked is None:
        return float(row[0]) if row.size else None

    # Each pass drops every position a sample point catches overlapping the
    # occupied region.  The point is a vertex of, or a point strictly inside,
    # this exact piece, and translating it uses the same addition shapely uses
    # on the outline, so a hit is a guarantee and not an estimate.
    for probes in variant.probes:
        if row.size == 0 or probes.size == 0:
            break
        hit = shapely.intersects_xy(
            occupied,
            (probes[:, 0, None] + row[None, :]).ravel(),
            np.repeat(probes[:, 1] + y, row.size),
        ).reshape(len(probes), row.size)
        row = row[~hit.any(axis=0)]

    minx, miny, maxx, maxy = variant.bounds
    o_minx, o_miny, o_maxx, o_maxy = occupied.bounds
    for x in row:
        # Two shapes whose bounding boxes are clear of each other cannot
        # touch, so this position is free without building anything at all.
        if (x + maxx < o_minx or x + minx > o_maxx
                or y + maxy < o_miny or y + miny > o_maxy):
            return float(x)
        if not blocked.intersects(affinity.translate(variant.polygon, x, y)):
            return float(x)
    return None


def _commit(sheet: Sheet, occupancy: list[Any], piece: Piece,
            variants: dict[int, _Variant],
            origins: dict[int, np.ndarray], spot: tuple[int, float, float],
            margin_x: float, margin_y: float, spacing: float) -> None:
    angle, x, y = spot
    variant = variants[angle]
    placed = affinity.translate(variant.polygon, x, y)
    sheet.placements.append(Placement(
        piece_id=piece.piece_id, panel_id=piece.panel_id, sheet_index=sheet.index,
        rotation_deg=angle, offset=np.array([x + margin_x, y + margin_y], dtype=float),
        origin=origins[angle], width_mm=variant.width, length_mm=variant.length,
    ))
    sheet.used_area_mm2 += float(placed.area)
    # Round joins, not mitre. A mitre buffer at a sharp corner runs far past the
    # nominal offset -- measured at 80.6 mm beyond a round buffer on a 10-degree
    # corner at 20 mm spacing -- which would push pieces apart by much more than
    # the user asked for and can spill the job onto an extra sheet.
    clear = placed.buffer(spacing, join_style=1)
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


# How much a piece may exceed the envelope at the door and still be written.
#
# The nester measures a piece as a POLYLINE sampled at `sample_step_mm`; the
# DXF carries the original arcs, and an arc bows a fraction of a millimetre
# further out than the chord that sampled it -- a sagitta of step^2 / 8r, which
# is 0.03 mm on a 1 mm step around a 5 mm fillet.  So a piece measured here can
# be a hair larger than the piece the nester placed, through nobody's fault.
# A tenth of a millimetre absorbs that and nothing else: the sheet keeps
# 12.7 mm of margin outside the usable area on each side, so a real overrun is
# never anywhere near this small.
EXPORT_TOLERANCE_MM = 0.1


def _placed_bounds(piece: Piece, placement: Placement, base_rotation: np.ndarray,
                   step_mm: float) -> tuple[float, float, float, float]:
    """The box the piece ACTUALLY occupies on the sheet, arcs included.

    Sampled from the placed loop rather than taken from `placement.width_mm`,
    because those came from the nester's own sampling of the piece before it
    was placed and this has to be an independent measurement of the geometry
    about to be written -- otherwise the guard is asking the search whether the
    search was right.
    """

    points, _source = sample_loop(place_loop(piece.outer, placement, base_rotation), step_mm)
    return (float(points[:, 0].min()), float(points[:, 1].min()),
            float(points[:, 0].max()), float(points[:, 1].max()))


def _refuse(piece: Piece, placement: Placement, base_rotation: np.ndarray,
            options: dict[str, Any]) -> dict[str, Any] | None:
    """Why this piece must not be written, or None if it is fine to cut.

    Two ways a part reaches the door uncuttable, and both are checked, because
    they need different things done about them: it can be too BIG for the
    envelope, which needs another seam, or it can be the right size but sitting
    off the usable area, which is a placement fault and needs re-nesting.
    """

    usable_w = float(options["max_part_width_mm"])
    usable_l = float(options["max_part_length_mm"])
    margin_x = (float(options["sheet_width_mm"]) - usable_w) / 2.0
    margin_y = (float(options["sheet_length_mm"]) - usable_l) / 2.0
    min_x, min_y, max_x, max_y = _placed_bounds(piece, placement, base_rotation,
                                                float(options["sample_step_mm"]))
    width, length = max_x - min_x, max_y - min_y
    over_w = width - usable_w
    over_l = length - usable_l
    outside = max(margin_x - min_x, max_x - (margin_x + usable_w),
                  margin_y - min_y, max_y - (margin_y + usable_l))

    if over_w <= EXPORT_TOLERANCE_MM and over_l <= EXPORT_TOLERANCE_MM \
            and outside <= EXPORT_TOLERANCE_MM:
        return None

    if over_w > EXPORT_TOLERANCE_MM or over_l > EXPORT_TOLERANCE_MM:
        needs = ("a seam across the boat" if over_l > EXPORT_TOLERANCE_MM else "")
        if over_w > EXPORT_TOLERANCE_MM:
            needs = (needs + " and along the boat") if needs else "a seam along the boat"
        reason = (f"{width:.1f} x {length:.1f} mm is over the "
                  f"{usable_w:.1f} x {usable_l:.1f} mm envelope by "
                  f"{max(0.0, over_w):.1f} x {max(0.0, over_l):.1f} mm; it needs {needs}")
    else:
        reason = (f"{width:.1f} x {length:.1f} mm was nested {outside:.1f} mm outside the "
                  f"usable area of the sheet, so part of it falls in the trim margin")
    return {
        "piece_id": piece.piece_id, "panel_id": piece.panel_id,
        "sheet": placement.sheet_index + 1,
        "width_mm": round(width, 1), "length_mm": round(length, 1),
        "over_width_mm": round(max(0.0, over_w), 1),
        "over_length_mm": round(max(0.0, over_l), 1),
        "outside_usable_mm": round(max(0.0, outside), 1),
        "reason": reason,
    }


def write_sheet_dxfs(run_dir: Path, sheets: Sequence[Sheet], pieces: dict[str, Piece],
                     base_rotation: np.ndarray,
                     pattern: dict[int, list[np.ndarray]], pattern_kind: str | None,
                     options: dict[str, Any],
                     progress: Any = None,
                     warnings: list[str] | None = None) -> list[dict[str, Any]]:
    """One DXF per sheet, in sheet coordinates with the sheet corner at the
    origin -- the frame the CNC expects.

    Each sheet carries the cut profiles on `CAM__<piece>` layers, the pattern
    grooves clipped to each piece on `PATTERN_<KIND>__<piece>`, and the sheet
    outline plus usable area on reference layers so the operator can see the
    margin.  Every file is re-read after writing and the polylines checked
    closed, the same round-trip ingest.py does.

    The last gate on the envelope
    -----------------------------
    Every piece is measured again HERE, as the geometry about to be written and
    in the sheet frame, against `max_part_width_mm` x `max_part_length_mm`.  A
    piece that is over is REFUSED: its profile and its grooves are left out of
    the file, and the fabricator is told which piece, by how much, and which
    way the seam it still needs has to run -- in the job log through `progress`
    and in the sheet report through `warnings`.

    This repeats work `oversize_report` and the nester have both already done,
    and that is the point.  The user cut a part that did not fit and none of the
    checks upstream had said so, which means one of them can be wrong; a check
    at the door cannot be routed around by any of them being wrong, because the
    only thing it trusts is the polyline it is about to write.  A DXF that
    quietly contains a part nobody can cut is the worst thing this program can
    produce -- the material is bought and the router is running before anyone
    finds out -- and a sheet that is short one part, loudly, is merely annoying.
    """

    import ezdxf

    say = progress if callable(progress) else (lambda _message: None)
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
        refused: list[dict[str, Any]] = []
        for placement in sheet.placements:
            piece = pieces[placement.piece_id]
            problem = _refuse(piece, placement, base_rotation, options)
            if problem is not None:
                refused.append(problem)
                message = (f"REFUSED to write piece {problem['piece_id']} to "
                           f"sheet {problem['sheet']}: {problem['reason']}. That sheet DXF is "
                           "short one part on purpose -- fix the seam and export again.")
                say(message)
                if warnings is not None:
                    warnings.append(message)
                continue
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
        blocked = {item["piece_id"] for item in refused}
        written.append({
            "sheet": sheet.index + 1,
            "path": str(path),
            "name": path.name,
            "units": reread.header.get("$INSUNITS"),
            # What is IN the file, so a caller reading this back is never told
            # about a part the file does not contain.
            "pieces": [p.piece_id for p in sheet.placements if p.piece_id not in blocked],
            "refused": refused,
            "cam_polylines": len(polylines),
            "all_closed": all(p.closed for p in polylines),
            "pattern_lines": len(reread.modelspace().query("LINE")),
            "utilisation": round(sheet.utilisation(options), 4),
            "layer_counts": counts,
        })
    return written
