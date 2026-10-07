"""Placing a seam by choosing its direction first, and trimming it to the part.

The old seam tool asked the user to drag two points and then straightened
whatever came out.  This is the other half of the same idea, and it is what the
user actually asked for:

    "make the seam feature easier to use like insead of drawing the 2 points
     you can select wether it is a vertical, horizontal or diagonal (select
     angle) and then it shows you the seam as you hover over the boat auto
     detecting the edges (not the pattern) andf then you click to lock it in."

So the direction is decided BEFORE the seam is placed, which makes it exact by
construction rather than corrected afterwards.  There is nothing left to
straighten: a seam placed "across the boat" already is, to the last bit.

    vertical    = ALONG the boat, bow to stern.  The long seams, exactly
                  parallel to the centreline and therefore to the teak lines.
    horizontal  = ACROSS the boat, side to side.  The short seams, exactly
                  ninety degrees to the axis.
    diagonal    = a chosen angle, measured off the boat centreline, so 0 is the
                  same as vertical and 90 the same as horizontal.

Those are the user's words, and on the seam tab -- which is drawn bow up -- a
long seam really does read vertical on screen and a short one horizontal.  The
direction always comes from the same `sheetjob.resolve_axis` call the cut uses,
manual grain override included, so what is hovered and what is cut cannot
disagree.

Trimming to the part
--------------------
While the pointer moves, the user has to see the seam that WOULD be placed, not
an endless line across the screen: it starts at one edge of the panel, ends at
the other, and is broken around the console so it never draws across a cut-out.
That is what `seam_through` returns, and the edges it uses are ONLY the fitted
CAM outline -- never the teak or pattern lines, which the user was explicit
about ("auto detecting the edges (not the pattern)").

Why this is a module of its own rather than more of `seamsnap`
-------------------------------------------------------------
`seamsnap` corrects a line that already exists and is deliberately free of
shapely -- every one of its helpers runs inside a candidate loop and is spelled
out so the corrector can be read top to bottom.  This is the opposite job:
deciding where a line goes in the first place, by clipping it against real
polygons with holes in them, which is exactly what shapely is for.  Keeping
them apart keeps the corrector small and keeps the polygon work in one place
that the app can cache.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from . import seamsnap
from .sheets import Loop, classify_loops, loop_polygon

# A chord shorter than this is the line grazing a corner, not a cut.  It is the
# same floor `seamsnap` puts under a drawn seam, and for the same reason: five
# millimetres of seam is not a join, it is a nick in the edge of the part.
MIN_CHORD_MM = seamsnap.MIN_SEAM_LENGTH_MM

# How far past the part the trial line is drawn before it is clipped.  The
# clip only ever shortens it, so this needs to be comfortably longer than the
# part and nothing more.
_REACH_MARGIN_MM = 10.0

MODES = ("along", "across", "angle")


def direction_for(axis: Sequence[float] | None, mode: str,
                  angle_deg: float | None = None) -> np.ndarray | None:
    """The unit direction one of the three placement modes means, or None when
    the run has no boat axis to measure them from.

    `mode` "angle" is degrees off the centreline -- one number, no ambiguity --
    and 0 and 90 are made to return the masters THEMSELVES rather than a
    rotation that happens to land on them.  `cos(radians(90))` is 6.1e-17, not
    zero, so going through the trigonometry would leave a seam placed at exactly
    ninety degrees a rounding error off square, which is the pie shape the whole
    feature exists to prevent.

    The angle is measured ANTI-CLOCKWISE off the centreline, the way a
    protractor reads and the way `direction_degrees` reports it back, so a seam
    the user asked for at 30 degrees really is drawn 30 degrees anti-clockwise
    of the boat and the seam list's "30 deg off the centreline" is true.  The
    turn is deliberately NOT taken from `masters[1]`: `across` points clockwise,
    because it has to agree bit for bit with the first row of the sheet
    transform, and sweeping towards it produced the mirror image -- 30 typed in
    came out at 150, leaning the wrong way over the planks, with every label on
    the page still saying 30.

    A seam is a line and not an arrow, so 150 and -30 are the same diagonal and
    one number in 0..180 still covers every diagonal there is.
    """

    masters = seamsnap.master_directions(axis)
    if masters is None:
        return None
    along, across = masters
    if mode == "along":
        return along
    if mode == "across":
        return across
    if mode != "angle":
        return None
    turn = float(angle_deg or 0.0) % 180.0
    if turn == 0.0:
        return along
    if turn == 90.0:
        return across
    radians = math.radians(turn)
    # The anti-clockwise quarter turn of the boat axis: the same line as
    # `across` and the opposite arrow, built here rather than negated from it so
    # that this reads as what it is -- a rotation of `along` by +turn.
    left = np.array([-along[1], along[0]])
    # Built from the boat's own axis rather than from an absolute angle, so a
    # diagonal turns with the boat exactly as the other two modes do.
    return _unit(along * math.cos(radians) + left * math.sin(radians))


def direction_degrees(direction: Sequence[float] | None) -> float:
    """A direction as an angle in the placed frame, folded onto 0..180 -- what
    the page shows the user, and undirected because a seam is."""

    if direction is None:
        return 0.0
    unit = np.asarray(direction, dtype=float)[:2]
    return float(math.degrees(math.atan2(unit[1], unit[0])) % 180.0)


def panel_polygons(loops_by_panel: Mapping[int, Sequence[Loop]],
                   options: Mapping[str, Any] | None = None) -> dict[int, Polygon]:
    """Each panel's fitted outline as a polygon with its cut-outs as holes.

    Built once and cached by the caller: this is the expensive half of a hover
    (a full deck panel samples to thousands of points at the 1 mm step), and
    rebuilding it on every pointer move would make the tool feel broken.
    """

    step = float((options or {}).get("sample_step_mm", 1.0))
    polygons: dict[int, Polygon] = {}
    for panel_id, loops in loops_by_panel.items():
        outer, holes = classify_loops(list(loops), step)
        if outer is None:
            continue
        polygon = loop_polygon(outer, holes, step)
        if not polygon.is_empty:
            polygons[int(panel_id)] = polygon
    return polygons


def seam_through(point_xy: Sequence[float], direction_unit: Sequence[float],
                 panels: Mapping[int, Any], options: Mapping[str, Any] | None = None,
                 panel_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """The seam that would be placed through `point_xy` in `direction_unit`,
    trimmed to the part.

    Returns one entry per chord -- panel_id, both endpoints and the length in
    millimetres -- ordered along the direction, so a line that crosses a console
    comes back as the two pieces either side of it and never as one line drawn
    over the cut-out.  Off the part entirely, the answer is an empty list, which
    is what tells the page there is nothing to place here.

    `panels` may be the loops-per-panel mapping `sheets.read_fitted_dxf`
    returns, or the polygons `panel_polygons` built from it earlier; the second
    form is the one a hover should use, because it has already paid for the
    sampling.  `panel_ids` narrows the answer to particular panels, which is how
    a seam already bound to one panel is drawn.

    Everything here is in the placed frame, the frame of `final_auto.dxf` and of
    `sheets.Seam`, so a segment can be stored as a seam with no conversion.
    """

    polygons = _as_polygons(panels, options)
    unit = _unit(np.asarray(direction_unit, dtype=float).ravel()[:2])
    point = np.asarray(point_xy, dtype=float).ravel()[:2]
    if unit is None or not np.isfinite(point).all():
        return []
    wanted = None if panel_ids is None else {int(p) for p in panel_ids}

    segments: list[dict[str, Any]] = []
    for panel_id in sorted(polygons):
        if wanted is not None and panel_id not in wanted:
            continue
        polygon = polygons[panel_id]
        if polygon.is_empty:
            continue
        reach = _reach(polygon, point)
        if reach <= 0.0:
            continue
        trial = LineString([point - unit * reach, point + unit * reach])
        for start, end in _straight_parts(polygon.intersection(trial)):
            # Every chord is reported pointing the way the user chose, so two
            # seams placed the same way are stored the same way round.
            if float(np.dot(end - start, unit)) < 0.0:
                start, end = end, start
            length = float(math.hypot(*(end - start)))
            if length < MIN_CHORD_MM:
                continue
            segments.append({
                "panel_id": panel_id,
                "x1": float(start[0]), "y1": float(start[1]),
                "x2": float(end[0]), "y2": float(end[1]),
                "length_mm": length,
                "along_mm": float(np.dot((start + end) / 2.0 - point, unit)),
            })
    segments.sort(key=lambda s: (s["along_mm"], s["panel_id"]))
    return segments


def panels_crossed(x1: float, y1: float, x2: float, y2: float,
                   panels: Mapping[int, Any], options: Mapping[str, Any] | None = None,
                   panel_id: int | None = None) -> list[int]:
    """The panels a drawn seam actually cuts.

    The same test `sheets.split_panel` applies: a seam bound to a panel cuts
    that panel, and a free seam cuts every panel its DRAWN segment touches --
    not every panel its extension would reach, which on a nested layout is all
    of them.
    """

    polygons = _as_polygons(panels, options)
    if panel_id is not None:
        return [int(panel_id)] if int(panel_id) in polygons else []
    drawn = LineString([(x1, y1), (x2, y2)])
    return [pid for pid in sorted(polygons) if drawn.intersects(polygons[pid])]


# --------------------------------------------------------------------------
# helpers


def _unit(vector: np.ndarray) -> np.ndarray | None:
    length = float(math.hypot(*vector)) if vector.size >= 2 else 0.0
    if not math.isfinite(length) or length < 1e-12:
        return None
    return vector[:2] / length


def _reach(polygon: Polygon, point: np.ndarray) -> float:
    """Half the length of a trial line guaranteed to cross the whole polygon:
    the furthest bounding-box corner, plus a margin."""

    min_x, min_y, max_x, max_y = polygon.bounds
    corners = np.array([[min_x, min_y], [min_x, max_y], [max_x, min_y], [max_x, max_y]])
    return float(np.linalg.norm(corners - point, axis=1).max()) + _REACH_MARGIN_MM


def _straight_parts(geometry: Any) -> list[tuple[np.ndarray, np.ndarray]]:
    """The chords in whatever shapely handed back.

    Clipping a straight line with a polygon can return a LineString, a
    MultiLineString (one piece either side of the console), a
    GeometryCollection when the line also grazes a vertex, a bare Point for a
    line that only touches, or nothing at all.  Each LineString is a connected
    piece of one straight line, so its first and last coordinates are its ends
    even when the clip left extra vertices in between, where the line ran
    through a point on the boundary.  Points are dropped: touching a corner is
    not a seam.
    """

    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        coords = np.asarray(geometry.coords, dtype=float)
        if len(coords) < 2:
            return []
        return [(coords[0][:2], coords[-1][:2])]
    parts: list[tuple[np.ndarray, np.ndarray]] = []
    for piece in getattr(geometry, "geoms", ()):
        parts.extend(_straight_parts(piece))
    return parts


def _as_polygons(panels: Mapping[int, Any],
                 options: Mapping[str, Any] | None) -> dict[int, Polygon]:
    """Accept either loops-per-panel or already-built polygons."""

    if not panels:
        return {}
    sample = next(iter(panels.values()))
    if isinstance(sample, BaseGeometry):
        return {int(pid): geometry for pid, geometry in panels.items()}
    return panel_polygons(panels, options)
