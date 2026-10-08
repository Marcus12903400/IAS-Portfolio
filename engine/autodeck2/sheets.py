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
import os
import re
import tempfile
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
    # ...and how NARROW a cut piece may be and still be worth handling.  Area
    # alone let a 1156 x 5.6 mm toothpick through (6473 mm2) and it went on to
    # the sheet as a part nobody can route.  The floor has to stay under the
    # 25-40 mm width of the reference boat's gunwale-strip panels, which the
    # optimiser legitimately cuts into pieces of that same width, so 15 mm:
    # it removes every sliver the audit measured (5-10 mm wide) and keeps every
    # real part.  Settable in config["sheets"] for a shop that wants it wider.
    "min_piece_width_mm": 15.0,
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
    # A step of zero is not "as fine as possible", it is a hang or a division
    # by zero: `sample_loop` divides by it and the nester's position grid is
    # built by repeated addition of `nest_step_mm`, which never terminates at
    # zero.  Both were reachable from a config file with a 0 in it, and both
    # took every /api/sheets call down with them, so both are refused here --
    # the one place every caller already goes through.
    for key in ("sample_step_mm", "nest_step_mm"):
        if float(merged[key]) <= 0.0:
            raise ValueError(f"sheets.{key} must be greater than zero (got {merged[key]})")
    # Everything else numeric must at least be a real, finite number: a NaN or
    # an infinity in the config reaches the geometry as a number no later rule
    # can reject and comes back out as the bare token NaN in the result JSON.
    for key, value in merged.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(float(value)):
            raise ValueError(f"sheets.{key} must be a finite number (got {value})")
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
                 tolerance_mm: float,
                 fallbacks: list[int] | None = None) -> tuple[Loop, float]:
    """Turn a ring of points back into (x, y, bulge) vertices.

    Provenance is per SEGMENT, not per vertex: `segment_source[i]` is the
    original loop segment that produced the edge from `points[i]` to
    `points[i+1]`, or -1 for an edge the seam kerf introduced.  The ids are
    interpreted against `original.bulges`, so a caller handing in several
    source loops must offset their segment ids into one combined table -- see
    `_rebuild_ring`, which is every in-tree caller.

    Per-vertex provenance does not work here.  A straight kerf cut arrives from
    shapely as a single edge with only two vertices, and both of them sit ON
    the original boundary at the points where the cut meets it -- so both would
    snap to boundary provenance, the seam edge would get no group of its own,
    and the ring's two ends would merge straight through it, closing the piece
    with a diagonal across the cut.

    Runs of segments sharing an arc provenance collapse into one bulge;
    everything else becomes a line.  Returns the loop and the worst deviation
    between a rebuilt arc and the points it replaced.  When `fallbacks` is
    given, one entry per arc run that FAILED its tolerance is appended, so the
    caller can report flattening the rebuilt arc count alone cannot see.
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
            if fallbacks is not None:
                fallbacks.append(len(segments))
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

    @property
    def chord_bound(self) -> bool:
        """Does this seam cut ONLY the chord that was clicked?

        A seam placed with the direction-first tool (any `mode`) is a chord of
        the panel: the hover showed one specific stretch of the line, broken
        around the console, and the click said "join THERE".  Extending such a
        seam across the whole panel -- the legacy behaviour, still right for a
        free two-point drag on an old run -- split decks in half when the user
        had asked for one 450 mm join beside the console.  So a moded seam cuts
        its stored chord plus a little reach past the outline, and only a mode-
        less legacy seam keeps the whole-line extension.
        """

        return bool(self.mode)


def read_seams(run_dir: Path) -> list[Seam]:
    """The run's seam list, or [] when the file is missing or unreadable.

    A crash mid-write used to leave a 0-byte seams.json, and then EVERY reader
    failed on the JSONDecodeError -- the Sheets tab on open, the optimiser
    before it could take its backup.  Now the broken file is kept as a .bak
    (only when one is not already there, so repeated failures do not eat the
    best copy), the run reads as "no seams", and the user can still work.

    utf-8-sig because a file edited by hand in Windows Notepad can carry a BOM,
    and `json.loads` refuses one.
    """

    path = Path(run_dir) / "seams.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        backup = path.with_name("seams.json.bak")
        try:
            if not backup.is_file() and path.stat().st_size > 0:
                backup.write_bytes(path.read_bytes())
        except OSError:
            pass
        return []
    if not isinstance(payload, dict):
        return []
    seams = []
    for item in payload.get("seams") or []:
        try:
            seams.append(Seam.from_dict(item))
        except (ValueError, TypeError, KeyError):
            continue
    return seams


def write_seams(run_dir: Path, seams: Sequence[Seam]) -> Path:
    """Replace the run's seam list.

    Written through a temp file and `os.replace` when Windows allows it, and
    IN PLACE when it does not.  The trade was measured on this platform: with
    three threads reading the file while it is rewritten four thousand times,
    `Path.replace` onto the live name fails 3921 times out of 4000, because
    Windows will not rename over a file another handle has open and the seam
    hover opens the file on every pointer move.  A writer that almost always
    fails loses the user's edit, which is the one thing this file holds.

    So the temp file is tried FIRST -- a crash between creating it and
    replacing leaves the old file intact, which fixes the truncate-then-write
    window that used to leave a 0-byte seams.json -- and the in-place write is
    the fallback that keeps the save itself reliable.

    The one reader that really can arrive mid-write is the seam hover, which
    runs on every pointer move; `bridge.seam_references` catches the parse error
    and keeps its previous list for that one frame.  Everything else that reads
    this file -- Export and the seam search -- is held off by the page while a
    re-plan is in flight, which is the same guard that stops them cutting the
    previous seam set.
    """

    path = Path(run_dir) / "seams.json"
    text = json.dumps({"seams": [s.to_dict() for s in seams]}, indent=2)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".seams-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp:
            temp.write(text)
        os.replace(temp_name, str(path))
    except OSError:
        # Windows refused the rename because a reader held the file open.  The
        # save must not fail: write in place, exactly as this function always
        # did, and accept the torn-read window the hover already tolerates.
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------
# splitting

# The shortest run of seam inside a panel that counts as a join rather than a
# nick in the edge of the part.  The same number `seamplace` puts under every
# chord it offers, stated here through `seamsnap` (which `sheets` already
# imports for its defaults) so the hover, the click and the cut cannot disagree
# about what a seam is.
MIN_CHORD_MM = _seamsnap.MIN_SEAM_LENGTH_MM


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
    # How many arc runs the ring rebuild refitted as polylines because the
    # three-point fit failed its tolerance.  Zero on a healthy split; a number
    # that climbs means arcs are quietly being flattened, which is exactly the
    # regression `max_arc_error_mm` cannot see (it only counts arcs that PASSED).
    arc_fallbacks: int = 0

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

    HOW MUCH OF THE LINE IS CUT depends on how the seam was placed.  A seam
    with a placement `mode` (the direction tool, the optimiser) is bound to
    the CHORD that was clicked: the hover preview showed one stretch of the
    line, broken around the console, and the chord is what gets kerfed, plus
    one seam gap of reach past the outline so the cut crosses the boundary
    cleanly.  A legacy free seam (mode "") keeps the old whole-line
    extension, so runs made before the direction tool behave exactly as they
    did.
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
    #
    # A seam BOUND to this panel is now held to the same standard as the hover
    # that placed it: its chord has to cross real material for at least
    # MIN_CHORD_MM (the same floor `seamplace.seam_through` puts under a
    # previewed chord), so a seam grazing a corner shaves nothing instead of
    # nicking a sliver off it.
    relevant: list[Seam] = []
    for seam in seams:
        if seam.panel_id == panel_id:
            chord = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
            try:
                crosses = panel.intersection(chord).length >= MIN_CHORD_MM
            except (ValueError, TypeError):
                continue
            if crosses:
                relevant.append(seam)
            else:
                warnings.append(
                    f"panel {panel_id}: seam {seam.seam_id or '?'} crosses no material on this panel, "
                    "so it cut nothing -- move it onto the deck or remove it")
        elif seam.panel_id is None:
            drawn = LineString([(seam.x1, seam.y1), (seam.x2, seam.y2)])
            if drawn.intersects(panel):
                relevant.append(seam)
    if not relevant:
        piece = Piece(f"P{panel_id}", panel_id, outer, list(holes), float(panel.area))
        return [piece], warnings

    reach = float(np.hypot(*(np.asarray(panel.bounds[2:]) - np.asarray(panel.bounds[:2])))) + 10.0
    half_gap = float(options["seam_gap_mm"]) / 2.0
    seam_names = ", ".join(str(s.seam_id) or "?" for s in relevant)
    kerfs = []
    for seam in relevant:
        # A moded seam keeps the chord it was given; a legacy free seam cuts
        # the whole line.  The one-gap reach on the chord is enough to cross
        # the outline because the hover that produced the chord trimmed it to
        # the settled line's own crossings of that outline.
        line = seam.extended(float(options["seam_gap_mm"]) if seam.chord_bound else reach)
        kerf = line.buffer(half_gap, cap_style=2, join_style=2)
        if not kerf.intersects(panel):
            warnings.append(
                f"panel {panel_id}: seam {seam.seam_id or '?'} crosses no material on this panel, "
                "so it cut nothing -- move it onto the deck or remove it")
            continue
        kerfs.append(kerf)
    if not kerfs:
        piece = Piece(f"P{panel_id}", panel_id, outer, list(holes), float(panel.area))
        return [piece], warnings
    remainder = panel.difference(unary_union(kerfs))
    if remainder.is_empty:
        return [], [f"panel {panel_id}: the seams removed the whole panel"]

    parts = list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder]
    min_area = float(options["min_piece_area_mm2"])
    min_width = float(options.get("min_piece_width_mm", 0.0))
    kept: list[Polygon] = []
    dropped_area = dropped_width = 0
    for part in parts:
        if part.area < min_area:
            dropped_area += 1
            continue
        if min_width > 0.0 and _narrower_than(part, min_width):
            dropped_width += 1
            continue
        kept.append(part)
    if dropped_area > 0:
        warnings.append(f"panel {panel_id}: dropped {dropped_area} sliver piece(s) below "
                        f"{min_area:.0f} mm2 (seams: {seam_names})")
    if dropped_width > 0:
        warnings.append(f"panel {panel_id}: dropped {dropped_width} sliver piece(s) narrower than "
                        f"{min_width:.0f} mm (seams: {seam_names})")

    # Provenance for arc rebuilding: every sampled point of every original
    # loop, tagged with the loop it belongs to and the segment within it.
    sources: list[tuple[Loop, np.ndarray, np.ndarray]] = []
    for loop in [outer, *holes]:
        pts, src = sample_loop(loop, step)
        sources.append((loop, pts, src))

    pieces: list[Piece] = []
    tolerance = float(options["arc_rebuild_tolerance_mm"])
    skipped = 0
    fallbacks = 0
    for part in sorted(kept, key=lambda g: (-g.area, g.bounds)):
        # CCW outer, CW holes -- the convention ingest.order_loops enforces for
        # every other loop this codebase emits.
        part = orient(part, 1.0)
        outer_loop, worst, ring_fallbacks = _rebuild_ring(
            np.asarray(part.exterior.coords)[:-1], sources, tolerance, step)
        fallbacks += ring_fallbacks
        hole_loops = []
        for ring in part.interiors:
            rebuilt, err, ring_fallbacks = _rebuild_ring(
                np.asarray(ring.coords)[:-1], sources, tolerance, step)
            fallbacks += ring_fallbacks
            # A rebuilt ring counts as area if it has three straight vertices
            # OR is a two-vertex arc pair -- which is what a circle rebuilds
            # to, and dropping it silently lost the 207 mm round cut-out out
            # of every cut piece that contained it.
            if len(rebuilt.vertices) >= 3 or _encloses_area(rebuilt.vertices):
                hole_loops.append(rebuilt)
                worst = max(worst, err)
            elif len(rebuilt.vertices):
                warnings.append(
                    f"panel {panel_id}: a cut-out was too small to rebuild on piece "
                    f"P{panel_id}-{len(pieces) + 1} and was left out")
        if not (len(outer_loop.vertices) >= 3 or _encloses_area(outer_loop.vertices)):
            skipped += 1
            warnings.append(f"panel {panel_id}: piece {len(pieces) + 1} degenerated; skipped")
            continue
        pieces.append(Piece(
            # Numbered from the KEPT list: a skipped part used to eat an id, so
            # "P1-3 needs a seam" pointed at a different piece after every edit.
            piece_id=f"P{panel_id}-{len(pieces) + 1}", panel_id=panel_id, outer=outer_loop,
            holes=hole_loops, area_mm2=float(part.area), from_seam=True, max_arc_error_mm=worst,
            arc_fallbacks=fallbacks,
        ))
    return pieces, warnings


def _narrower_than(part: Polygon, limit_mm: float) -> bool:
    """Is this part narrower, at its narrowest, than `limit_mm`?

    The shorter side of the minimum rotated rectangle -- the smallest envelope
    the part fits in at any turn.  A 1156 x 5.6 mm toothpick measures 5.6 mm
    however long it is, while its AREA (6473 mm2) was always enough to pass the
    old test.  The gunwale strips survive because they are 25 mm and up, well
    over the floor.
    """

    import shapely

    try:
        envelope = shapely.minimum_rotated_rectangle(part)
        width = min(envelope.bounds[2] - envelope.bounds[0], envelope.bounds[3] - envelope.bounds[1])
    except (ValueError, TypeError):
        return False
    return width < limit_mm


def _rebuild_ring(ring: np.ndarray, sources: Sequence[tuple[Loop, np.ndarray, np.ndarray]],
                  tolerance_mm: float, step_mm: float) -> tuple[Loop, float, int]:
    """Attach provenance to a boolean-output ring, then rebuild its arcs.

    Provenance is decided per SEGMENT, from each segment's MIDPOINT: an edge
    whose middle lies on the original boundary came from it, while the middle
    of a seam-kerf edge is out in open space and is marked -1.  Testing the
    midpoint rather than the endpoints is what distinguishes the cut from the
    boundary it cuts -- a kerf edge's two endpoints both sit on the original
    boundary and would otherwise look like part of it.

    Segment ids are namespaced by (loop, segment): every original loop -- the
    outer boundary AND every cut-out -- contributes its own numbered segments
    to one combined table, and a run of ring edges only collapses into an arc
    when it came from one segment of one loop.  The previous behaviour kept a
    single "dominant" loop per ring and flattened every edge from any other
    loop into 1 mm straight vertices, which is how a cut piece's console walls
    arrived in VCarve as thousands of nodes while its own outline kept its
    arcs.

    An edge whose midpoint lands on an original loop's own VERTEX (the first
    sample of a segment) is treated as introduced.  That edge is the junction
    chord where the kerf meets the boundary, and attributing it to either of
    the two segments meeting there starts the arc run one sample into its
    neighbour -- the three-point fit then fails its tolerance and the whole
    fillet comes back as a polyline.  Marking the chord introduced keeps every
    surviving run strictly inside one true arc.
    """

    from scipy.spatial import cKDTree

    if len(ring) < 3:
        return Loop(np.zeros((0, 3))), 0.0, 0
    all_points = np.vstack([pts for _loop, pts, _src in sources])
    owner = np.concatenate([np.full(len(pts), i, dtype=np.int64)
                            for i, (_l, pts, _s) in enumerate(sources)])
    all_src = np.concatenate([src for _loop, _pts, src in sources])

    # Where each segment's own samples begin, in the concatenated arrays: the
    # (loop, segment) pair changes value exactly at an original vertex.
    starts_run = np.empty(len(all_points), dtype=bool)
    starts_run[0] = True
    starts_run[1:] = (owner[1:] != owner[:-1]) | (all_src[1:] != all_src[:-1])

    midpoints = 0.5 * (ring + np.roll(ring, -1, axis=0))
    tree = cKDTree(all_points)
    distance, nearest = tree.query(midpoints, k=1, workers=-1)
    snap = max(step_mm * 0.75, 1e-6)
    on_original = distance <= snap
    # Combined table: loop index offsets each loop's segment ids past the ends
    # of the loops before it, so (loop 1, seg 3) and (loop 2, seg 3) are
    # different sources and an arc run can never span two loops.
    loop_starts = np.cumsum([0] + [len(s[0].vertices) for s in sources])
    loop_of = np.where(on_original, owner[nearest], -1)
    seg_of = np.where(on_original, all_src[nearest], -1)
    junction = on_original & starts_run[nearest]
    segment_source = np.where(junction, -1,
                              np.where(loop_of >= 0, loop_starts[np.clip(loop_of, 0, None)]
                                       + np.clip(seg_of, 0, None), -1))
    combined = Loop(np.vstack([s[0].vertices for s in sources]))
    fallbacks: list[int] = []
    loop, worst = rebuild_loop(ring, segment_source, combined, tolerance_mm,
                               fallbacks=fallbacks)
    return loop, worst, len(fallbacks)


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
    another part.  So a two-vertex loop counts provided at least one end really
    is bowed: two straight vertices are a line and enclose nothing, but ONE
    bowed end is a D shape and bounds area the same as a full circle does.
    """

    count = len(vertices)
    if count >= 3:
        return True
    if count != 2:
        return False
    bulges = np.abs(np.asarray(vertices, dtype=float)[:, 2])
    chord = float(np.hypot(*(vertices[1][:2] - vertices[0][:2])))
    return bool(bulges.max() > 1e-9 and chord > 1e-9)


# The fitted DXFs name their CAM loops CAM_USER__PANEL_n (the convention
# `ingest.py` writes and every fitted run follows), and other things can share
# the __PANEL_n suffix -- so the family prefix is part of the contract.
_CAM_LAYER = re.compile(r"^CAM.*__PANEL_(\d+)$")


def read_fitted_dxf(path: Path,
                    warnings: list[str] | None = None) -> tuple[dict[int, list[Loop]], dict[int, list[np.ndarray]], str | None]:
    """CAM loops and pattern groove lines per panel from final_auto.dxf /
    final.dxf.  Bulges are read through unchanged: this is exactly the
    geometry VCarve would otherwise have received.

    Only CLOSED CAM-layer polylines become loops, and anything skipped for
    being open, unclosed or on a foreign layer is reported through `warnings`
    when the caller passes a list: a file whose cut-out quietly goes missing
    because its layer was named differently is a wrong cut file, and the only
    thing worse than dropping it is dropping it silently.
    """

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
            if not _CAM_LAYER.match(layer):
                if warnings is not None:
                    warnings.append(
                        f"{Path(path).name}: skipped polyline on layer {layer!r} -- only CAM layers "
                        "are cut")
                continue
            if not bool(entity.closed):
                if warnings is not None:
                    warnings.append(
                        f"{Path(path).name}: skipped an OPEN polyline on {layer} -- a cut loop "
                        "has to close")
                continue
            vertices = np.asarray([(x, y, b) for x, y, b in entity.get_points("xyb")], dtype=float)
            if _encloses_area(vertices):
                loops.setdefault(pid, []).append(Loop(vertices))
            elif warnings is not None:
                warnings.append(
                    f"{Path(path).name}: skipped a two-point line on {layer} that encloses no area")
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


# How much float noise a piece may carry past the envelope and still count as
# fitting.  The nester's own grid test already allows 1e-9; the report has to
# agree with it or a piece measures "too big" here that the nester placed
# happily -- an exact 990.6 x 2006.6 mm rectangle on a 3.68 degree axis came
# out 1.4e-13 mm over purely from the rotation's trigonometry.
ENVELOPE_TOLERANCE_MM = 1e-6


def oversize_report(pieces: Sequence[Piece], rotation: np.ndarray,
                    options: dict[str, Any]) -> list[dict[str, Any]]:
    """Pieces that still will not fit a sheet, and by how much, so the user
    knows where another seam is needed.

    Sizes are carried at ONE decimal and printed from that same number, so the
    same piece cannot read "616 x 2413" in one line and "617 x 2413" in the
    next (which is what rounding twice used to produce).
    """

    limit_w = float(options["max_part_width_mm"])
    limit_l = float(options["max_part_length_mm"])
    step = float(options["sample_step_mm"])
    report = []
    for piece in pieces:
        width, length = oriented_extent(piece, rotation, step)
        if width <= limit_w + ENVELOPE_TOLERANCE_MM and length <= limit_l + ENVELOPE_TOLERANCE_MM:
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
