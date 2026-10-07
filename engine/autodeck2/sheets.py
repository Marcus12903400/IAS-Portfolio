"""Seams, sheet nesting and per-sheet DXF export.

Decking comes on 40 x 80 inch sheets and the material is directional -- Reflex
TruGrain -- so the 80 inch dimension must run along the length of the boat.  A
deck panel is usually far bigger than one sheet, so the user draws seams where
the joins would look best, each piece is separated from its neighbours by
6 mm, and the resulting pieces are nested onto as many sheets as it takes.

Frames
------
Everything here works in the PLACED panel frame -- the same millimetre frame as
`final_auto.dxf` and the flat view, so a seam the user draws on the flat view
needs no conversion.

The boat's longitudinal axis comes from the run's stored pattern frame
(`run.json` -> `teak.frame.longitudinal_axis`).  That axis is expressed in the
boat-plan frame, which differs from the placed frame only by each panel's
`nest_offset` translation -- and a translation does not change a direction, so
the stored axis is directly usable here.  `sheet_transform` rotates the whole
job so that axis points along +Y, after which the sheet is simply
1016 mm in X by 2032 mm in Y and nesting is axis aligned.

Arc fidelity
------------
The whole point of AutoDeck's fitter is emitting clean LINE/ARC geometry for
VCarve, so splitting must not silently degrade arcs into dense polylines.
Loops are sampled with a record of which original segment produced each point;
after the boolean, runs of points that came from one original arc are refitted
to a single bulge, and only the edges the seam kerf introduced become straight
lines.  `max_arc_error_mm` in the result reports how far the rebuilt arcs sit
from the sampled points, so a regression is visible rather than silent.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

MM_PER_INCH = 25.4

# --------------------------------------------------------------------------
# defaults (all overridable from config["sheets"])

DEFAULTS: dict[str, Any] = {
    # Physical sheet, and the largest part we are willing to cut from one.
    "sheet_width_mm": 40.0 * MM_PER_INCH,          # 1016.0  across the boat
    "sheet_length_mm": 80.0 * MM_PER_INCH,         # 2032.0  along the boat (grain)
    "max_part_width_mm": 39.0 * MM_PER_INCH,       #  990.6
    "max_part_length_mm": 79.0 * MM_PER_INCH,      # 2006.6
    # Gap left between two pieces that meet at a seam, split evenly either side.
    "seam_gap_mm": 6.0,
    # Minimum clear distance between two nested pieces on a sheet.
    "part_spacing_mm": 20.0,
    # Sampling/rebuild tolerances.
    "sample_step_mm": 1.0,
    "arc_rebuild_tolerance_mm": 0.05,
    "min_piece_area_mm2": 400.0,                   # discard slivers a seam shaves off
    # Nesting search granularity.
    "nest_step_mm": 5.0,
    "allow_180_rotation": True,
    # How hard the automatic seam finder tries to make the pieces LOOK right:
    # a port and a starboard piece that visibly match, pieces that nearly fill
    # their own bounding box, and cuts that run off the console walls instead
    # of 40 mm to one side of them.  0.0 is pure waste, which is how the button
    # behaved before this setting existed; 1.0 will trade a lot of material for
    # tidy pieces.  It can never add a sheet and can never put a piece outside
    # the envelope -- both of those are ordered ahead of it and are not for
    # sale at any weight.  `seamplan` is where it is spent.
    "seam_tidiness_weight": 0.35,
    # Grain direction. None = use the boat axis detected for the pattern frame.
    # A number overrides it: the angle in degrees, in the placed frame, of the
    # direction that must run along the 80 inch sheet dimension.  The override
    # matters because axis detection can be unsure (it reports a confidence),
    # and cutting a directional material against the grain ruins the sheet.
    "grain_angle_deg": None,
}

# Everything that decides how a roughly drawn seam is straightened lives in
# `seamsnap`, but it is the same job and the user sets it in the same place, so
# the snap keys join the sheet keys here: one config["sheets"] block overrides
# the sheet size, the seam gap AND the snapping, and `settings()` resolves the
# lot in one call.  seamsnap deliberately does not import this module at import
# time (see the note beside its own imports), so this direction of the
# dependency is the only one, and the import order cannot decide the outcome.
from . import seamsnap as _seamsnap  # noqa: E402  (must follow DEFAULTS)

DEFAULTS.update(_seamsnap.SNAP_DEFAULTS)


def settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    merged.update((config or {}).get("sheets") or {})
    if merged["max_part_width_mm"] > merged["sheet_width_mm"]:
        raise ValueError("sheets.max_part_width_mm exceeds sheet_width_mm")
    if merged["max_part_length_mm"] > merged["sheet_length_mm"]:
        raise ValueError("sheets.max_part_length_mm exceeds sheet_length_mm")
    # A seam cuts by having its kerf SUBTRACTED from the panel, so a gap of
    # zero subtracts a zero-width rectangle and the panel comes back in one
    # piece.  Every seam on the job then silently does nothing: `split_panel`
    # returns the whole panel, the optimiser searches an arrangement that can
    # never be cut, and the user gets a 2058 x 3994 mm "piece" with no
    # explanation.  Measured on the cached boat -- at 6 mm the deck cuts into
    # ten pieces, at 1e-12 mm into eleven, and at exactly 0 into one.  A
    # negative gap is the same failure with a sign on it.  There is no useful
    # zero-gap job (two pieces that touch are one piece), so this is refused
    # here, once, rather than defended against in five places downstream.
    if float(merged["seam_gap_mm"]) <= 0.0:
        raise ValueError(
            f"sheets.seam_gap_mm must be greater than zero (got {merged['seam_gap_mm']}); "
            "a seam with no gap removes no material and so does not cut the panel at all"
        )
    weight = float(merged["seam_tidiness_weight"])
    if not 0.0 <= weight <= 1.0:
        raise ValueError(
            f"sheets.seam_tidiness_weight must be between 0.0 and 1.0 (got {weight})"
        )
    merged["seam_tidiness_weight"] = weight
    return merged


# --------------------------------------------------------------------------
# loops: the (x, y, bulge) representation the DXFs use


@dataclass
class Loop:
    """One closed CAM loop, as DXF LWPOLYLINE (x, y, bulge) vertices."""

    vertices: np.ndarray                    # (N, 3) x, y, bulge

    @property
    def xy(self) -> np.ndarray:
        return np.asarray(self.vertices, dtype=float)[:, :2]

    @property
    def bulges(self) -> np.ndarray:
        return np.asarray(self.vertices, dtype=float)[:, 2]

    def transformed(self, rotation: np.ndarray, offset: np.ndarray, flip: bool = False) -> "Loop":
        """Rigid transform. Bulge is invariant under rotation and translation;
        a 180 degree turn is a rotation, so bulges are never negated here.
        `flip` is reserved for mirroring, which decking may NOT use."""

        if flip:
            raise ValueError("mirroring a directional-grain part is not permitted")
        xy = self.xy @ np.asarray(rotation, dtype=float).T + np.asarray(offset, dtype=float)
        return Loop(np.column_stack([xy, self.bulges]))


def bulge_to_arc(p0: np.ndarray, p1: np.ndarray, bulge: float) -> tuple[np.ndarray, float, float, float]:
    """Centre, radius, start angle and signed sweep for one bulged segment."""

    chord = p1 - p0
    length = float(np.hypot(*chord))
    theta = 4.0 * math.atan(bulge)
    radius = length / (2.0 * abs(math.sin(theta / 2.0)))
    mid = (p0 + p1) / 2.0
    height = math.sqrt(max(radius * radius - (length / 2.0) ** 2, 0.0))
    normal = np.array([-chord[1], chord[0]]) / length
    sign = 1.0 if theta > 0 else -1.0
    if abs(theta) > math.pi:
        sign = -sign
    centre = mid + normal * height * sign
    start = math.atan2(p0[1] - centre[1], p0[0] - centre[0])
    return centre, radius, start, theta


def sample_loop(loop: Loop, step_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """Dense points around a loop plus, for each point, the index of the
    original segment it came from.  The provenance is what lets arcs be
    rebuilt after a boolean operation instead of being flattened."""

    xy = loop.xy
    bulges = loop.bulges
    count = len(xy)
    points: list[np.ndarray] = []
    source: list[np.ndarray] = []
    for i in range(count):
        p0 = xy[i]
        p1 = xy[(i + 1) % count]
        bulge = float(bulges[i])
        chord = float(np.hypot(*(p1 - p0)))
        if abs(bulge) < 1e-12 or chord < 1e-12:
            n = max(1, int(math.ceil(chord / step_mm)))
            seg = np.linspace(p0, p1, n, endpoint=False)
        else:
            centre, radius, start, theta = bulge_to_arc(p0, p1, bulge)
            n = max(2, int(math.ceil(abs(theta) * radius / step_mm)))
            angles = np.linspace(start, start + theta, n, endpoint=False)
            seg = centre + radius * np.column_stack([np.cos(angles), np.sin(angles)])
        points.append(seg)
        source.append(np.full(len(seg), i, dtype=np.int64))
    return np.vstack(points), np.concatenate(source)


def _bulge_through(p0: np.ndarray, mid: np.ndarray, p1: np.ndarray) -> float:
    """Bulge of the arc from p0 to p1 passing through mid.

    With sagitta s and chord c, 2s/c = tan(theta/4), which is exactly the DXF
    bulge.  The sign has to match `bulge_to_arc`, which puts the centre along
    +normal for a positive bulge -- so the arc bows the OTHER way, along
    -normal, and the sagitta measured along +normal is negative when the bulge
    is positive.  Hence the leading minus; getting this backwards puts every
    rebuilt arc on the wrong side of its chord.
    """

    chord = p1 - p0
    length = float(np.hypot(*chord))
    if length < 1e-12:
        return 0.0
    normal = np.array([-chord[1], chord[0]]) / length
    sagitta = float(np.dot(mid - (p0 + p1) / 2.0, normal))
    return -2.0 * sagitta / length


def rebuild_loop(points: np.ndarray, segment_source: np.ndarray, original: Loop,
                 tolerance_mm: float) -> tuple[Loop, float]:
    """Turn a ring of points back into (x, y, bulge) vertices.

    Provenance is per SEGMENT, not per vertex: `segment_source[i]` is the
    original loop segment that produced the edge from `points[i]` to
    `points[i+1]`, or -1 for an edge the seam kerf introduced.

    Per-vertex provenance does not work here.  A straight kerf cut arrives from
    shapely as a single edge with only two vertices, and both of them sit ON
    the original boundary at the points where the cut meets it -- so both would
    snap to boundary provenance, the seam edge would get no group of its own,
    and the ring's two ends would merge straight through it, closing the piece
    with a diagonal across the cut.

    Runs of segments sharing an arc provenance collapse into one bulge;
    everything else becomes a line.  Returns the loop and the worst deviation
    between a rebuilt arc and the points it replaced.
    """

    n = len(points)
    if n < 3:
        return Loop(np.zeros((0, 3))), 0.0
    original_bulges = original.bulges

    # Only segments from the SAME original segment may be grouped. Introduced
    # edges (-1) each stand alone: where two seams cross, a piece's ring has two
    # kerf edges meeting at a corner, and grouping them would collapse that
    # corner and cut the piece short.
    groups: list[tuple[int, list[int]]] = []
    for index in range(n):
        src = int(segment_source[index])
        if groups and groups[-1][0] == src and src >= 0:
            groups[-1][1].append(index)
        else:
            groups.append((src, [index]))
    # The ring's start usually falls in the middle of one original run.
    if len(groups) > 1 and groups[0][0] == groups[-1][0] and groups[0][0] >= 0:
        groups[0] = (groups[0][0], groups[-1][1] + groups[0][1])
        groups.pop()

    vertices: list[list[float]] = []
    worst = 0.0
    for src, segments in groups:
        start_point = points[segments[0]]
        end_point = points[(segments[-1] + 1) % n]
        is_arc = 0 <= src < len(original_bulges) and abs(float(original_bulges[src])) > 1e-12
        if not is_arc or len(segments) < 3:
            vertices.append([start_point[0], start_point[1], 0.0])
            continue
        mid_point = points[segments[len(segments) // 2]]
        bulge = _bulge_through(start_point, mid_point, end_point)
        if abs(bulge) < 1e-9:
            vertices.append([start_point[0], start_point[1], 0.0])
            continue
        centre, radius, _s, _t = bulge_to_arc(start_point, end_point, bulge)
        covered = points[[s for s in segments]]
        deviation = float(np.abs(np.linalg.norm(covered - centre, axis=1) - radius).max())
        if deviation > tolerance_mm:
            # Do not fake an arc through points that are not on one; keep the
            # sampled polyline rather than cut the wrong shape.
            for index in segments:
                vertices.append([points[index][0], points[index][1], 0.0])
            continue
        worst = max(worst, deviation)
        vertices.append([start_point[0], start_point[1], bulge])
    return Loop(np.asarray(vertices, dtype=float)), worst


def loop_polygon(outer: Loop, holes: Sequence[Loop], step_mm: float) -> Polygon:
    shell, _src = sample_loop(outer, step_mm)
    rings = []
    for hole in holes:
        ring, _s = sample_loop(hole, step_mm)
        if len(ring) >= 3:
            rings.append(ring)
    polygon = Polygon(shell, rings)
    if not polygon.is_valid:
        polygon = polygon.buffer(0.0)
    return polygon


# --------------------------------------------------------------------------
# seams


_FALSE_WORDS = frozenset({"false", "no", "off", "0", ""})
_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})


def _as_flag(value: Any, default: bool = True) -> bool:
    """A yes/no field out of JSON, reading the words the way a person means them.

    `bool("false")` is True, and every JSON encoder that stringifies its values
    -- a hand-written curl, an older client, a form post -- can put that word
    here.  Taking it at face value inverts the answer, so the words are read as
    words and anything genuinely unrecognisable falls back to `default` rather
    than to whichever way Python happens to lean.
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _FALSE_WORDS:
            return False
        if word in _TRUE_WORDS:
            return True
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


@dataclass
class Seam:
    """A straight seam the user placed, in the placed frame.

    `panel_id` of None means "wherever it crosses", which is what a user
    dragging a line across the whole flat layout means.  The segment is
    extended past the panel before cutting so a roughly drawn line still
    separates the panel cleanly -- the user was told to draw seams "roughly".

    `x1..y2` are the seam AS IT WILL BE CUT: straightened to the boat.  The
    other three fields are what makes that repeatable rather than a one-way
    edit of the user's drawing.

    `raw` is the line exactly as the user put it down, and every straightening
    is recomputed from `raw` -- never from an already-straightened line.  Snap a
    snapped seam and it must not creep: correcting from the corrected line would
    compound whatever the last correction did, and a hundred re-plans would walk
    the seam off the deck.  None means the seam predates this field, in which
    case its current coordinates ARE the drawing.

    `mode` records what the user ASKED FOR rather than only what they got:

        "along"  bow to stern, exactly parallel to the centreline and the planks
        "across" exactly ninety degrees to it, side to side
        "angle"  `angle_deg` degrees off the centreline
        ""       dragged as two free points, direction taken from the drawing

    Storing the intent matters because the boat's axis can move afterwards --
    the user sets a manual grain angle, or re-runs the pattern.  A seam placed
    "across the boat" then has to stay exactly across the boat, so its direction
    is recomputed from whatever axis the job is now using, instead of being
    frozen at the coordinates it happened to be given the first time.

    `snap` is the per-seam opt out: False leaves the seam exactly where it was
    put, for the one join the fabricator wants to place by eye.
    """

    seam_id: str
    x1: float
    y1: float
    x2: float
    y2: float
    panel_id: int | None = None
    snap: bool = True
    raw: tuple[float, float, float, float] | None = None
    mode: str = ""
    angle_deg: float | None = None

    @property
    def drawn(self) -> tuple[float, float, float, float]:
        """The line the correction starts from: `raw` when the seam has one,
        and its own coordinates when it does not (an older seams.json, or a
        seam that has never been through the corrector)."""

        if self.raw is None:
            return float(self.x1), float(self.y1), float(self.x2), float(self.y2)
        x1, y1, x2, y2 = self.raw
        return float(x1), float(y1), float(x2), float(y2)

    def to_dict(self) -> dict[str, Any]:
        return {"seam_id": self.seam_id, "x1": self.x1, "y1": self.y1,
                "x2": self.x2, "y2": self.y2, "panel_id": self.panel_id,
                "snap": bool(self.snap),
                "raw": None if self.raw is None else [float(v) for v in self.raw],
                "mode": self.mode, "angle_deg": self.angle_deg}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Seam":
        """Tolerant of every seams.json ever written: the four new fields are
        all optional, and a file from before they existed reads back as a
        snapping, free-direction seam whose drawing is its own geometry.

        Tolerant, but not credulous: `snap` goes through `_as_flag` rather than
        `bool()`, because the string "false" is a true Python value and reading
        it as "yes, straighten this seam" would do the exact opposite of what
        the caller asked for -- silently, to the one seam the fabricator wanted
        left where they put it.
        """

        raw = payload.get("raw")
        if raw is not None:
            values = [float(v) for v in raw]
            if len(values) != 4:
                raise ValueError("seam raw must be [x1, y1, x2, y2]")
            raw = (values[0], values[1], values[2], values[3])
        mode = str(payload.get("mode") or "")
        if mode not in ("", "along", "across", "angle"):
            raise ValueError(f"unknown seam mode {mode!r}; expected along, across, angle or blank")
        angle = payload.get("angle_deg")
        return cls(
            seam_id=str(payload.get("seam_id") or ""),
            x1=float(payload["x1"]), y1=float(payload["y1"]),
            x2=float(payload["x2"]), y2=float(payload["y2"]),
            panel_id=None if payload.get("panel_id") in (None, "") else int(payload["panel_id"]),
            snap=_as_flag(payload.get("snap"), default=True),
            raw=raw, mode=mode,
            angle_deg=None if angle in (None, "") else float(angle),
        )

    def moved_to(self, x1: float, y1: float, x2: float, y2: float) -> "Seam":
        """The same seam at new endpoints, with everything that says where it
        came from carried across unchanged."""

        return Seam(seam_id=self.seam_id, x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2),
                    panel_id=self.panel_id, snap=self.snap, raw=self.drawn,
                    mode=self.mode, angle_deg=self.angle_deg)

    def extended(self, distance_mm: float) -> LineString:
        p0 = np.array([self.x1, self.y1], dtype=float)
        p1 = np.array([self.x2, self.y2], dtype=float)
        direction = p1 - p0
        length = float(np.hypot(*direction))
        if length < 1e-9:
            return LineString([p0, p1])
        unit = direction / length
        return LineString([p0 - unit * distance_mm, p1 + unit * distance_mm])


def read_seams(run_dir: Path) -> list[Seam]:
    path = Path(run_dir) / "seams.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Seam.from_dict(item) for item in payload.get("seams", [])]


def write_seams(run_dir: Path, seams: Sequence[Seam]) -> Path:
    """Replace the run's seam list.

    Written IN PLACE, deliberately, and not through the write-a-temp-file-then-
    rename dance that would make it atomic.  Measured on this platform: with
    three threads reading the file while it is rewritten four thousand times,
    `Path.replace` onto the live name fails 3921 times out of 4000, because
    Windows will not rename over a file another handle has open and Python does
    not open for reading with delete sharing.  Trading a reader that sometimes
    sees half a file for a WRITER that almost always fails is much the worse
    bargain: the write is the user's seam edit, and losing it loses their work.

    The one reader that really can arrive mid-write is the seam hover, which
    runs on every pointer move; `bridge.seam_references` catches the parse error
    and keeps its previous list for that one frame.  Everything else that reads
    this file -- Export and the seam search -- is held off by the page while a
    re-plan is in flight, which is the same guard that stops them cutting the
    previous seam set.
    """

    path = Path(run_dir) / "seams.json"
    path.write_text(json.dumps({"seams": [s.to_dict() for s in seams]}, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# splitting


@dataclass
class Piece:
    """One cuttable piece after seams have been applied."""

    piece_id: str
    panel_id: int
    outer: Loop
    holes: list[Loop] = field(default_factory=list)
    area_mm2: float = 0.0
    from_seam: bool = False
    max_arc_error_mm: float = 0.0

    def polygon(self, step_mm: float) -> Polygon:
        return loop_polygon(self.outer, self.holes, step_mm)


def split_panel(panel_id: int, outer: Loop, holes: Sequence[Loop], seams: Sequence[Seam],
                options: dict[str, Any]) -> tuple[list[Piece], list[str]]:
    """Cut one panel's fitted loops with the seams, leaving a `seam_gap_mm`
    gap between neighbouring pieces.

    The gap is produced by subtracting a kerf -- each seam line buffered by
    half the gap with flat ends -- from the panel polygon.  That gives the
    asymmetry the job needs for free: the original fitted outer boundary is
    untouched, and only the new seam edges are inset.
    """

    step = float(options["sample_step_mm"])
    warnings: list[str] = []
    panel = loop_polygon(outer, holes, step)
    if panel.is_empty:
        return [], [f"panel {panel_id}: empty after sampling; skipped"]

    # A seam with panel_id None applies "wherever it crosses" -- but that has to
    # mean where the user's DRAWN line crosses, not where the extended one does.
    # The extension below reaches a whole bbox diagonal in both directions, so
    # without this test a seam drawn over one panel silently slices another
    # panel metres away.
    relevant: list[Seam] = []
    for seam in seams:
        if seam.panel_id == panel_id:
            relevant.append(seam)
        elif seam.panel_id is None:
            drawn = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
            if drawn.intersects(panel):
                relevant.append(seam)
    if not relevant:
        piece = Piece(f"P{panel_id}", panel_id, outer, list(holes), float(panel.area))
        return [piece], warnings

    reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
    half_gap = float(options["seam_gap_mm"]) / 2.0
    kerfs = [s.extended(reach).buffer(half_gap, cap_style=2, join_style=2) for s in relevant]
    remainder = panel.difference(unary_union(kerfs))
    if remainder.is_empty:
        return [], [f"panel {panel_id}: the seams removed the whole panel"]

    parts = list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder]
    parts = [p for p in parts if p.area >= float(options["min_piece_area_mm2"])]
    dropped = (1 if isinstance(remainder, Polygon) else len(remainder.geoms)) - len(parts)
    if dropped > 0:
        warnings.append(f"panel {panel_id}: dropped {dropped} sliver piece(s) below "
                        f"{options['min_piece_area_mm2']:.0f} mm2")

    # Provenance for arc rebuilding: every sampled point of every original
    # loop, tagged with the loop it belongs to and the segment within it.
    sources: list[tuple[Loop, np.ndarray, np.ndarray]] = []
    for loop in [outer, *holes]:
        pts, src = sample_loop(loop, step)
        sources.append((loop, pts, src))

    pieces: list[Piece] = []
    tolerance = float(options["arc_rebuild_tolerance_mm"])
    for index, part in enumerate(sorted(parts, key=lambda g: (-g.area, g.bounds))):
        # CCW outer, CW holes -- the convention ingest.order_loops enforces for
        # every other loop this codebase emits.
        part = orient(part, 1.0)
        outer_loop, worst = _rebuild_ring(np.asarray(part.exterior.coords)[:-1], sources, tolerance, step)
        hole_loops = []
        for ring in part.interiors:
            rebuilt, err = _rebuild_ring(np.asarray(ring.coords)[:-1], sources, tolerance, step)
            if len(rebuilt.vertices) >= 3:
                hole_loops.append(rebuilt)
                worst = max(worst, err)
        if len(outer_loop.vertices) < 3:
            warnings.append(f"panel {panel_id}: piece {index + 1} degenerated; skipped")
            continue
        pieces.append(Piece(
            piece_id=f"P{panel_id}-{index + 1}", panel_id=panel_id, outer=outer_loop,
            holes=hole_loops, area_mm2=float(part.area), from_seam=True, max_arc_error_mm=worst,
        ))
    return pieces, warnings


def _rebuild_ring(ring: np.ndarray, sources: Sequence[tuple[Loop, np.ndarray, np.ndarray]],
                  tolerance_mm: float, step_mm: float) -> tuple[Loop, float]:
    """Attach provenance to a boolean-output ring, then rebuild its arcs.

    Provenance is decided per SEGMENT, from each segment's MIDPOINT: an edge
    whose middle lies on the original boundary came from it, while the middle
    of a seam-kerf edge is out in open space and is marked -1.  Testing the
    midpoint rather than the endpoints is what distinguishes the cut from the
    boundary it cuts -- a kerf edge's two endpoints both sit on the original
    boundary and would otherwise look like part of it.
    """

    from scipy.spatial import cKDTree

    if len(ring) < 3:
        return Loop(np.zeros((0, 3))), 0.0
    all_points = np.vstack([pts for _loop, pts, _src in sources])
    owner = np.concatenate([np.full(len(pts), i, dtype=np.int64)
                            for i, (_l, pts, _s) in enumerate(sources)])
    all_src = np.concatenate([src for _loop, _pts, src in sources])

    midpoints = 0.5 * (ring + np.roll(ring, -1, axis=0))
    tree = cKDTree(all_points)
    distance, nearest = tree.query(midpoints, k=1, workers=-1)
    snap = max(step_mm * 0.75, 1e-6)
    on_original = distance <= snap
    segment_source = np.where(on_original, all_src[nearest], -1)
    loop_of = np.where(on_original, owner[nearest], -1)

    # Segment indices are only meaningful within one original loop, so rebuild
    # against whichever loop supplied most of this ring and treat the rest as
    # introduced edges.
    if not (loop_of >= 0).any():
        return rebuild_loop(ring, np.full(len(ring), -1, dtype=np.int64),
                            Loop(np.zeros((0, 3))), tolerance_mm)
    dominant = int(np.bincount(loop_of[loop_of >= 0], minlength=len(sources)).argmax())
    segment_source = np.where(loop_of == dominant, segment_source, -1)
    return rebuild_loop(ring, segment_source, sources[dominant][0], tolerance_mm)


# --------------------------------------------------------------------------
# grain-aligned sheet frame


def sheet_transform(longitudinal_axis: Sequence[float] | None) -> np.ndarray:
    """Rotation taking placed-frame coordinates into sheet coordinates, where
    +Y is along the boat (the 80 inch sheet dimension and the grain).

    A translation does not change a direction, and nest mode only translates
    panels, so the axis stored for the pattern frame is valid here as-is.
    """

    if longitudinal_axis is None:
        return np.eye(2)
    axis = np.asarray(longitudinal_axis, dtype=float)[:2]
    norm = float(np.hypot(*axis))
    if norm < 1e-9:
        return np.eye(2)
    axis = axis / norm
    # Rotation mapping `axis` onto +Y.
    return np.array([[axis[1], -axis[0]], [axis[0], axis[1]]])


def oriented_extent(piece: Piece, rotation: np.ndarray, step_mm: float) -> tuple[float, float]:
    """Piece size in sheet axes: (across the boat, along the boat)."""

    points, _src = sample_loop(piece.outer, step_mm)
    xy = points @ rotation.T
    return float(xy[:, 0].max() - xy[:, 0].min()), float(xy[:, 1].max() - xy[:, 1].min())


_PANEL_SUFFIX = "__PANEL_"


def _encloses_area(vertices: np.ndarray) -> bool:
    """Does this LWPOLYLINE bound an area, so that it is a part or a cut-out?

    Three straight vertices is the obvious floor, and it used to be the only
    test -- which silently threw away every circle in the file.  A DXF circle
    written as a polyline is TWO vertices carrying bulge +-1, i.e. two
    semicircles, and that is what the fitter emits for a round part and for a
    round cut-out.  Dropping them lost a whole 187 mm circular panel out of the
    cut files with no warning, and left a 207 mm cut-out un-cut in the middle of
    another part.  So a two-vertex loop counts, provided both ends really are
    bowed: two straight vertices are a line and enclose nothing.
    """

    count = len(vertices)
    if count >= 3:
        return True
    if count != 2:
        return False
    bulges = np.abs(np.asarray(vertices, dtype=float)[:, 2])
    chord = float(np.hypot(*(vertices[1][:2] - vertices[0][:2])))
    return bool(bulges.min() > 1e-9 and chord > 1e-9)


def read_fitted_dxf(path: Path) -> tuple[dict[int, list[Loop]], dict[int, list[np.ndarray]], str | None]:
    """CAM loops and pattern groove lines per panel from final_auto.dxf /
    final.dxf.  Bulges are read through unchanged: this is exactly the
    geometry VCarve would otherwise have received."""

    import ezdxf

    doc = ezdxf.readfile(str(path))
    loops: dict[int, list[Loop]] = {}
    pattern: dict[int, list[np.ndarray]] = {}
    kind: str | None = None
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer)
        if _PANEL_SUFFIX not in layer:
            continue
        try:
            pid = int(layer.rsplit(_PANEL_SUFFIX, 1)[1])
        except ValueError:
            continue
        if entity.dxftype() == "LWPOLYLINE" and not layer.startswith("PATTERN_"):
            vertices = np.asarray([(x, y, b) for x, y, b in entity.get_points("xyb")], dtype=float)
            if _encloses_area(vertices):
                loops.setdefault(pid, []).append(Loop(vertices))
        elif entity.dxftype() == "LINE" and layer.startswith("PATTERN_"):
            if kind is None:
                kind = layer.split(_PANEL_SUFFIX)[0].replace("PATTERN_", "").lower()
            start, end = entity.dxf.start, entity.dxf.end
            pattern.setdefault(pid, []).append(np.array([[start.x, start.y], [end.x, end.y]], dtype=float))
    return loops, pattern, kind


def classify_loops(loops: Sequence[Loop], step_mm: float) -> tuple[Loop | None, list[Loop]]:
    """The largest-area loop is the outer boundary; the rest are cut-outs."""

    if not loops:
        return None, []
    areas = []
    for loop in loops:
        points, _src = sample_loop(loop, step_mm)
        polygon = Polygon(points)
        if not polygon.is_valid:
            polygon = polygon.buffer(0.0)
        areas.append(abs(polygon.area))
    outer_index = int(np.argmax(areas))
    return loops[outer_index], [loop for i, loop in enumerate(loops) if i != outer_index]


def oversize_report(pieces: Sequence[Piece], rotation: np.ndarray,
                    options: dict[str, Any]) -> list[dict[str, Any]]:
    """Pieces that still will not fit a sheet, and by how much, so the user
    knows where another seam is needed."""

    limit_w = float(options["max_part_width_mm"])
    limit_l = float(options["max_part_length_mm"])
    step = float(options["sample_step_mm"])
    report = []
    for piece in pieces:
        width, length = oriented_extent(piece, rotation, step)
        if width <= limit_w and length <= limit_l:
            continue
        report.append({
            "piece_id": piece.piece_id, "panel_id": piece.panel_id,
            "width_mm": round(width, 1), "length_mm": round(length, 1),
            "over_width_mm": round(max(0.0, width - limit_w), 1),
            "over_length_mm": round(max(0.0, length - limit_l), 1),
            "hint": ("needs a seam across the boat" if length > limit_l else "")
                    + (" and along the boat" if width > limit_w and length > limit_l
                       else ("needs a seam along the boat" if width > limit_w else "")),
        })
    return report
