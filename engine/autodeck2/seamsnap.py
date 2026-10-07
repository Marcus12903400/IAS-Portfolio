"""Straightening a roughly drawn seam onto the direction the boat gives it.

The user drags a seam across the flat view with a mouse, on a deck that is two
metres of hand-scanned boat, and then the cut has to look like it was drawn
against a straightedge.  Those two things do not meet on their own.

The boat's own axis is what makes them meet, and here it is the MASTER
reference rather than a fallback.  It is one single direction, computed once
for the run, and it is already doing this same job in two other places: it
oriented the teak pattern lines, and it orients the sheet, because Reflex
TruGrain is directional and the 80 inch sheet dimension has to run bow to
stern.  In the user's own words:

    "the same tecnique we used to fide the derection of the pattern to follow
     the dection of the boat is the same way we orient the parts to be inline
     with the sheet ... we can use this to make sure the verticle long seams
     are plumb or straight with the center of the boat and the teaklines so we
     dont get any pie shape things and then the horizontle short seams are
     always going to be 90 degrees or perfectly side to side with the boat over
     ruling the in line with other features we tried to set up previously."

So a seam normally gets one of exactly two directions:

    ALONG-BOAT   the axis itself -- the long seams, exactly parallel to the
                 centreline and therefore exactly parallel to the plank lines.
    ACROSS-BOAT  exactly ninety degrees to it -- the short seams, "perfectly
                 side to side with the boat".

Why the axis outranks the fitted geometry
-----------------------------------------
A long seam that fans a degree or two open against the planks running through
it is the "pie shape thing" the user is describing, and it is the defect being
designed out.  A console edge two degrees off square is not a reason to cut a
seam two degrees off square: that edge is a scanned line off a hand-built boat,
while the planks are dead straight, run the whole length of the deck and cross
the seam at right angles.  The eye reads the planks, so the planks win.  The
same argument applies to the material: every piece is nested with the grain
along the boat, so a seam that is square to the boat is also square to the
sheet, and two pieces either side of it meet with their grain in line.

Rule order in `snap_seam`
-------------------------
1. AXIS CAPTURE.  If the drawn direction is within `seam_axis_snap_deg`
   (default 20 degrees, modulo 180) of either master direction, the seam is
   swung about its own drawn midpoint until it lies EXACTLY along that master.
   This overrules every feature snap -- collinear, tangent, perpendicular and
   parallel candidates are not even considered for that seam.  The capture
   angle is deliberately much wider than the five degree feature tolerance,
   because the user says these seams are ALWAYS square; five degrees would let
   a sloppily dragged line stay crooked.  The two masters are ninety degrees
   apart and the capture is twenty, so at most one of them can ever claim a
   seam and there is nothing to arbitrate.
2. POSITION REFINEMENT, angle preserving.  Having squared the seam, if it now
   sits within `seam_snap_offset_mm` of a straight reference whose direction
   matches the snapped direction to within half a degree, the seam is
   translated PERPENDICULAR TO ITSELF onto that reference's infinite line.  It
   is never rotated again.  That is what lets a squared seam also land exactly
   on a console edge that happens to be square -- the best of both worlds --
   without letting an edge that is not square pull the seam off the boat.
3. FEATURE SNAPPING, the fallback, for genuinely diagonal seams only: the ones
   outside the axis capture on BOTH masters.  A seam deliberately dragged at
   forty-five degrees across a corner still gets the old collinear / tangent /
   perpendicular / parallel treatment at the old five degree tolerance.
4. NO AXIS.  When the run has no stored pattern frame and no manual grain
   angle, steps 1 and 2 are skipped, feature snapping is used on its own, and
   `NO_AXIS_WARNING` comes back on every result.  There is no sensible default
   direction to invent for a boat, so none is invented silently.

`seam_axis_priority` False restores the old order -- features first, the axis
only where nothing was near enough -- for the case where the axis itself is
suspect.

That decision is then applied over and over until the seam stops moving, which
is what makes the corrector a projection instead of merely an improvement.
One pass is not enough: squaring a seam to the boat can bring a fitted edge
within reach that the drawing was never within reach of, and turning a seam
parallel to one edge can leave it lying along another.  Stopping after one pass
would hand back a seam that the next click would move again -- the drift this
module promises not to have.

Frames
------
Everything here is in the PLACED frame -- the millimetre frame of
`final_auto.dxf`, of the flat view and of `sheets.Seam` -- so a seam the user
drew on the flat view needs no conversion either coming in or going out.  The
axis is a direction, and the placed frame differs from the boat-plan frame the
axis was stored in only by a translation, so it carries over untouched.

What a snap is not allowed to do
--------------------------------
A corrector that moves a seam somewhere the user did not draw it is worse than
no corrector at all, because the user cannot see the fitted edge it locked onto
and will not understand what happened.  These rules keep that from happening:

* a feature snap is rejected outright if it would move either endpoint more
  than `seam_snap_max_move_mm`, and so is a refinement translation (the axis
  ROTATION is exempt, and deliberately: it pivots about the point the user
  drew the seam through, so the seam stays where it was put, and the boat
  direction is not hidden geometry -- it is the planks the user is looking at);
* a reference has to be within `seam_snap_reach_mm` of the drawn segment, so a
  parallel edge on the far side of the boat cannot reach across and grab it;
* snapping is idempotent -- a seam already square, or already sitting on an
  edge, comes back untouched, byte for byte, so repeated edits in the UI cannot
  drift.

Scoring (feature snapping only)
-------------------------------
The feature rules are all evaluated for every reference and the cheapest wins,
cost being the angle used up plus the distance moved, each as a fraction of its
own tolerance, plus a small penalty ordering the kinds
collinear < tangent < perpendicular < parallel.  The penalty only decides
near-ties, and it decides them in favour of the more specific correction:
landing ON the console edge beats merely running parallel to it.

This module is deliberately pure -- no file IO, no config loading -- so the
whole corrector can be reasoned about and tested from numbers alone.  The
caller resolves the axis with `sheetjob.resolve_axis`, which is the same call
`plan` and `preview` make, so overriding the grain angle re-squares every seam
as well as re-orienting every sheet.  It leans on `sheets.bulge_to_arc` for the
DXF bulge maths rather than repeating it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence

import numpy as np

if TYPE_CHECKING:                       # pragma: no cover -- annotations only
    from .sheets import Loop

# `sheets` is imported inside the two functions that need its bulge maths, not
# at the top, and that is not a style preference.  `sheets.DEFAULTS` absorbs
# `SNAP_DEFAULTS` (below) so that one config["sheets"] block can override every
# snap option along with the sheet sizes -- which makes sheets depend on
# seamsnap.  Importing sheets from here as well would close the cycle, and then
# whichever of the two modules happened to be imported first would decide
# whether the merge worked.  Nothing in this module touches sheets at import
# time, so breaking the cycle on this side costs one dict lookup per reference
# build and buys an import order that cannot go wrong.

# --------------------------------------------------------------------------
# defaults (merged into the sheets settings by the caller)

SNAP_DEFAULTS: dict[str, Any] = {
    "seam_snap_enabled": True,
    # How far off a master direction a seam may be drawn and still be taken as
    # meant to be square. Much wider than the feature tolerance below because
    # the user's rule is that these seams are ALWAYS square -- a line dragged
    # fifteen degrees out was still meant to be square, it was just dragged
    # badly, and nothing in a deck wants to be fifteen degrees off the boat.
    "seam_axis_snap_deg": 20.0,
    # The boat wins over nearby fitted edges. False restores feature-first
    # snapping, for a run whose detected axis is not trusted.
    "seam_axis_priority": True,
    # The user's own number, and it now governs FEATURE snapping only: anything
    # less than five degrees off a real edge was meant to be on that edge.
    "seam_snap_angle_deg": 5.0,
    # How far sideways a seam may be shifted to land on the edge, and how far
    # away the edge itself may be before it stops being "nearby".
    "seam_snap_offset_mm": 25.0,
    "seam_snap_reach_mm": 300.0,
    # Scan noise leaves short slivers of segment behind; a 10 mm stub is not a
    # straightedge and a 3 mm radius is not a fillet, so neither is offered.
    "seam_snap_min_ref_length_mm": 15.0,
    "seam_snap_min_ref_radius_mm": 10.0,
    # The hard guard. No snap may teleport a seam this far, whatever it costs.
    "seam_snap_max_move_mm": 60.0,
    "seam_snap_use_axis": True,
}

# A seam this short is a stray click, not a drawing; correcting its angle would
# swing the endpoints around wildly for no gain.
MIN_SEAM_LENGTH_MM = 5.0

# The two master directions, named the way the fabricator says them out loud.
# The notes are built by putting a verb in front of these, so they have to read
# as English mid-sentence: "lined up with the boat and the teak lines",
# "squared across the boat".
ALONG_LABEL = "the boat and the teak lines"
ACROSS_LABEL = "across the boat"

# Said once per seam when there is no direction to square to. The user is being
# told what did NOT happen, and what to do about it, because a run with no
# pattern frame still snaps to edges and would otherwise look like it worked.
NO_AXIS_WARNING = (
    "no boat direction is stored for this run (was a pattern selected?), so seams could not be "
    "squared to the boat or to the teak lines -- they were only lined up with nearby fitted "
    "edges. Set the grain angle and snap again."
)

# How close to the snapped direction a reference has to be before the seam is
# allowed to slide onto it. Half a degree is "that edge is square too", not
# "that edge is nearly square": at five degrees a 300 mm seam translated onto
# the edge would still stand 13 mm off it at one end, which is the wedge the
# whole module exists to remove.
_REFINE_ANGLE_DEG = 0.5

# ...and how far either END of the seam is then allowed to stand off that edge.
# The angle on its own is not enough, because the gap it leaves grows with the
# seam: at the half-degree limit a 2000 mm seam ends up 8.7 mm off the edge at
# both ends, and the note says "moved 5 mm onto panel 1 cut-out edge" about it.
# The fabricator reads that as "the seam follows that edge", so it has to. One
# millimetre is under the DXF's own hundredth-of-a-millimetre grid times a
# hundred, and well under the 6 mm the seam gap allows for -- close enough that
# the join really does run with the edge along its whole length.
_REFINE_MAX_GAP_MM = 1.0

# Below these the correction is not a correction at all, and reporting one
# would make the UI say something happened when nothing did.
#
# Neither number is a tolerance the user would recognise -- 1e-6 mm is a
# nanometre, and the DXF is written to a hundredth of a millimetre -- they are
# the float noise floor, and they have to sit above it or a seam that is
# already exactly right comes back "corrected".  Snapping a snapped seam
# re-derives its direction from the corrected endpoints, and `acos` throws away
# half its digits next to a dot product of one: measured over three thousand
# random seams, a seam that had NOT moved at all (worst endpoint change
# 5e-13 mm) still read as 1.2e-6 degrees out.  So the degree floor is the acos
# noise, and the millimetre floor is the real test of whether anything happened.
_NO_CHANGE_MM = 1e-6
_NO_CHANGE_DEG = 1e-5

# The widest axis capture that can mean anything. The two masters are ninety
# degrees apart, so no seam is ever further than forty-five from the nearer of
# them; at forty-five every seam is already claimed and anything above it is
# just the same setting spelled louder.
MAX_AXIS_CAPTURE_DEG = 45.0

_EPS = 1e-12

# The two kinds the boat axis produces. They are exempt from the move guard and
# they end a round of feature snapping, so they are named once here.
_AXIS_KINDS = ("along-boat", "across-boat")

# How many times the correction may be re-applied before it is called settled.
# Two rounds is the most that has ever been needed in practice -- square the
# seam, then let it settle onto an edge -- and four leaves room for a chain
# nobody has thought of. It is a stop, not a design: if some pathological pair
# of references ever pushed a seam back and forth, this ends the loop, and the
# seam is left at a position that is still a legal correction of the drawing.
_MAX_ROUNDS = 4

# Cheapest first: the more specific the snap, the more it deserves a near-tie.
# The boat axis is not in here -- it no longer competes on cost with anything,
# it decides first and on its own.
_KIND_PENALTY = {
    "collinear": 0.00,
    "tangent": 0.05,
    "perpendicular": 0.10,
    "parallel": 0.15,
}


# --------------------------------------------------------------------------
# small geometry helpers
#
# These are spelled out rather than pulled from shapely because every one of
# them runs inside the candidate loop, and because a seam corrector that can be
# read top to bottom is a seam corrector that can be trusted.


def _cross(u: np.ndarray, v: np.ndarray) -> float:
    """2D scalar cross product. numpy 2 no longer accepts two-vectors in
    `np.cross`, and the sign convention matters in several places below."""

    return float(u[0] * v[1] - u[1] * v[0])


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=float)
    norm = float(math.hypot(vector[0], vector[1]))
    if norm < _EPS:
        return np.zeros(2)
    return vector / norm


def _direction_angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    """Angle between two directions, ignoring which way round each points.

    A seam has no head and no tail, so a line drawn right-to-left along an edge
    is zero degrees off it, not a hundred and eighty.
    """

    dot = abs(float(np.clip(float(np.dot(a, b)), -1.0, 1.0)))
    return math.degrees(math.acos(dot))


def _oriented(unit: np.ndarray, along: np.ndarray) -> np.ndarray:
    """`unit` turned to point the same way the user drew, so the corrected
    endpoints stay in the order the user put them in.

    The turn is a plain negation, so the result is bit-for-bit `unit` or its
    exact opposite -- which is what makes two seams snapped to the same master
    exactly parallel rather than nearly parallel.
    """

    return unit if float(np.dot(unit, along)) >= 0.0 else -unit


def _point_segment_distance(point: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    span = b - a
    denom = float(np.dot(span, span))
    if denom < _EPS:
        return float(math.hypot(*(point - a)))
    t = float(np.clip(float(np.dot(point - a, span)) / denom, 0.0, 1.0))
    return float(math.hypot(*(point - (a + t * span))))


def _runs_alongside(p: np.ndarray, q: np.ndarray, reference: Reference) -> bool:
    """Do this seam and this reference lie SIDE BY SIDE rather than end to end?

    Measured by projecting both onto the reference's own direction and asking
    whether the two ranges overlap.  Only the along-direction matters: how far
    apart they are sideways is what the snap is about to change.

    It exists to stop one seam being slid onto another one.  A previously drawn
    seam is offered as a reference so that a new seam can CONTINUE it -- the
    join carrying on across the gap between two panels, which has to come out as
    one straight line -- and for that the two lie end to end and this is False.
    Two seams that overlap along their own direction are two separate joins
    across the same stretch of deck, and sliding one onto the other does not
    tidy anything up: it deletes the strip of deck between them.  Measured on
    the real boat, two seams placed twenty-four millimetres apart -- inside the
    twenty-five millimetre `seam_snap_offset_mm` -- came out exactly on top of
    each other, and the hover preview had drawn them apart right up to the
    click, because the preview does not know about seams that already exist.

    Fitted edges are exempt and stay exempt: running a seam ALONG a console edge
    is the whole point of the refinement, and a console edge is not a cut.
    """

    unit = reference.unit
    if float(math.hypot(*unit)) < 0.5:
        return False
    own = sorted((float(np.dot(p - reference.p0, unit)), float(np.dot(q - reference.p0, unit))))
    other = sorted((0.0, float(np.dot(reference.p1 - reference.p0, unit))))
    return min(own[1], other[1]) - max(own[0], other[0]) > _EPS


def _segments_cross(a0: np.ndarray, a1: np.ndarray, b0: np.ndarray, b1: np.ndarray) -> bool:
    d1 = _cross(a1 - a0, b0 - a0)
    d2 = _cross(a1 - a0, b1 - a0)
    d3 = _cross(b1 - b0, a0 - b0)
    d4 = _cross(b1 - b0, a1 - b0)
    return ((d1 > 0.0) != (d2 > 0.0)) and ((d3 > 0.0) != (d4 > 0.0))


def _segment_distance(a0: np.ndarray, a1: np.ndarray, b0: np.ndarray, b1: np.ndarray) -> float:
    """Closest approach of two finite segments -- the "is that edge nearby?"
    test.  Touching segments fall out of the endpoint minimum as ~0, so only a
    genuine crossing needs the special case."""

    if _segments_cross(a0, a1, b0, b1):
        return 0.0
    return min(
        _point_segment_distance(a0, b0, b1),
        _point_segment_distance(a1, b0, b1),
        _point_segment_distance(b0, a0, a1),
        _point_segment_distance(b1, a0, a1),
    )


def _line_intersection(p: np.ndarray, u: np.ndarray, q: np.ndarray, v: np.ndarray) -> np.ndarray | None:
    """Where the infinite line p + s*u meets q + t*v, or None if they never do."""

    denom = _cross(u, v)
    if abs(denom) < 1e-9:
        return None
    return p + u * (_cross(q - p, v) / denom)


def _foot_on_line(point: np.ndarray, origin: np.ndarray, unit: np.ndarray) -> np.ndarray:
    return origin + unit * float(np.dot(point - origin, unit))


def _wrap_pi(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _set_direction(p: np.ndarray, q: np.ndarray, pivot: np.ndarray,
                   drawn_unit: np.ndarray, unit: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Swing the drawn segment about `pivot` until it runs exactly along `unit`.

    Each endpoint keeps the distance it had along the DRAWN direction, so the
    seam comes out exactly as long as the user drew it and the pivot -- the
    point the user cared about, a corner, a tangency, or the middle of the
    stroke -- does not move at all.  Projecting the endpoints onto the new line
    instead would shave a fraction of a percent off the length for no reason.
    """

    return (pivot + unit * float(np.dot(p - pivot, drawn_unit)),
            pivot + unit * float(np.dot(q - pivot, drawn_unit)))


def _mm(value: float) -> str:
    """Millimetres the way a fabricator reads them: whole numbers, except that
    a move smaller than one millimetre must not print as "0 mm", which reads as
    "nothing happened"."""

    return f"{value:.0f}" if value >= 0.95 else f"{value:.2f}"


# --------------------------------------------------------------------------
# references: the geometry a seam is allowed to line up with


@dataclass(frozen=True, eq=False)
class Reference:
    """One piece of existing geometry a seam may be snapped onto.

    `kind` is one of:
      "line"  a straight segment of a fitted loop
      "arc"   a bulged segment of a fitted loop (a fillet, a round cut-out)
      "axis"  a direction only -- along the boat, or across it
      "seam"  a seam the user already drew (and that has itself been snapped)

    `p0`/`p1` are the real endpoints of the segment for line/seam, the arc's
    two end vertices for arc, and two points defining the direction for axis
    (an axis has no position, so its endpoints are only ever used for its
    direction).  `angles` is (start, start + sweep) in radians, signed the way
    `sheets.bulge_to_arc` reports it, so a clockwise arc keeps its direction.

    eq is off because numpy arrays do not compare to a single truth value and a
    generated __eq__ would raise the moment anything put a Reference in a set.
    """

    kind: str
    label: str
    panel_id: int | None
    p0: np.ndarray
    p1: np.ndarray
    centre: np.ndarray | None = None
    radius: float | None = None
    angles: tuple[float, float] | None = None

    # ---- straight geometry (line, seam, axis)

    @property
    def unit(self) -> np.ndarray:
        """Unit direction of the segment (for an arc, of its chord)."""

        return _unit(self.p1 - self.p0)

    @property
    def length_mm(self) -> float:
        return float(math.hypot(*(self.p1 - self.p0)))

    @property
    def midpoint(self) -> np.ndarray:
        return (self.p0 + self.p1) / 2.0

    def offset_of(self, point: np.ndarray) -> float:
        """Perpendicular distance from `point` to the INFINITE line through the
        reference -- how far sideways the seam would have to move to land on
        it, ignoring where along the edge it sits."""

        return abs(_cross(self.unit, point - self.p0))

    def project(self, point: np.ndarray) -> np.ndarray:
        """`point` dropped perpendicularly onto the infinite line."""

        return _foot_on_line(point, self.p0, self.unit)

    def distance_to_segment(self, point: np.ndarray) -> float:
        return _point_segment_distance(point, self.p0, self.p1)

    def distance_to(self, p: np.ndarray, q: np.ndarray) -> float:
        """Closest approach between the drawn seam and this reference's own
        extent -- the reach test.  An axis has no extent, so it is always
        in reach."""

        if self.kind == "axis":
            return 0.0
        return _segment_distance(p, q, self.p0, self.p1)

    # ---- arc geometry

    @property
    def sweep(self) -> float:
        if self.angles is None:
            return 0.0
        return self.angles[1] - self.angles[0]

    def angle_parameter(self, angle: float) -> float:
        """Where `angle` falls along the arc: 0 at `p0`, 1 at `p1`.

        Measured from the arc's MIDDLE rather than its start, because wrapping
        relative to the start sends a point just outside the start end round to
        the far side of the circle -- which would read as "miles past the end"
        instead of "just short of it" and defeat the slack below.
        """

        sweep = self.sweep
        if self.angles is None or abs(sweep) < 1e-9:
            return 0.0
        middle = self.angles[0] + sweep / 2.0
        return 0.5 + _wrap_pi(angle - middle) / sweep

    def point_at_parameter(self, t: float) -> np.ndarray:
        angle = self.angles[0] + self.sweep * t
        return self.centre + self.radius * np.array([math.cos(angle), math.sin(angle)])

    def tangent_at_parameter(self, t: float) -> np.ndarray:
        """Unit tangent, pointing the way the arc is swept."""

        angle = self.angles[0] + self.sweep * t
        turn = 1.0 if self.sweep >= 0.0 else -1.0
        return np.array([-math.sin(angle), math.cos(angle)]) * turn


def _loop_references(loop: Loop, panel_id: int, label: str,
                     min_length_mm: float, min_radius_mm: float) -> list[Reference]:
    """Every segment of one fitted loop that is worth lining a seam up with."""

    from .sheets import bulge_to_arc

    xy = loop.xy
    bulges = loop.bulges
    count = len(xy)
    references: list[Reference] = []
    for index in range(count):
        p0 = xy[index]
        p1 = xy[(index + 1) % count]
        bulge = float(bulges[index])
        chord = float(math.hypot(*(p1 - p0)))
        if abs(bulge) < 1e-12:
            if chord >= min_length_mm:
                references.append(Reference("line", label, panel_id, p0, p1))
            continue
        if chord < _EPS:
            continue
        centre, radius, start, sweep = bulge_to_arc(p0, p1, bulge)
        if radius >= min_radius_mm:
            references.append(Reference("arc", label, panel_id, p0, p1,
                                        centre=centre, radius=float(radius),
                                        angles=(float(start), float(start + sweep))))
    return references


def references_from_loops(loops_by_panel: Mapping[int, Sequence[Loop]], options: dict[str, Any],
                          hole_ids: Mapping[int, Sequence[int]] | None = None) -> list[Reference]:
    """Every fitted edge in the job, as something a seam can be snapped onto.

    `loops_by_panel` is what `sheets.read_fitted_dxf` returns, and `hole_ids`
    says which loops of each panel are cut-outs.  When it is not given the
    largest-area loop of each panel is taken as the outer boundary, exactly the
    rule `sheets.classify_loops` uses for the cut itself.

    The labels matter more than they look: the console the user talks about IS
    a hole loop, so "panel 1 cut-out edge" is the phrase that will tell them
    what their seam locked onto.
    """

    from .sheets import classify_loops

    min_length = float(options["seam_snap_min_ref_length_mm"])
    min_radius = float(options["seam_snap_min_ref_radius_mm"])
    step = float(options.get("sample_step_mm", 1.0))

    references: list[Reference] = []
    for panel_id in sorted(loops_by_panel):
        loops = list(loops_by_panel[panel_id])
        if not loops:
            continue
        if hole_ids is not None:
            holes = {int(i) for i in (hole_ids.get(panel_id) or ())}
        else:
            _outer, hole_loops = classify_loops(loops, step)
            holes = {i for i, loop in enumerate(loops) if any(loop is hole for hole in hole_loops)}
        for index, loop in enumerate(loops):
            kind = "cut-out edge" if index in holes else "outer edge"
            references.extend(_loop_references(
                loop, panel_id, f"panel {panel_id} {kind}", min_length, min_radius))
    return references


# --------------------------------------------------------------------------
# the boat's two master directions


def master_directions(axis: Sequence[float] | None) -> tuple[np.ndarray, np.ndarray] | None:
    """(along the boat, across the boat) as exact unit vectors, or None.

    `axis` is the run's longitudinal axis after `sheetjob.resolve_axis` has had
    its say -- the same direction the teak pattern was drawn along and the same
    direction the 80 inch sheet dimension runs, so a seam squared against it is
    squared against everything else the job is aligned to.

    "Across" is built by swapping the components and negating one, which is a
    quarter turn with no trigonometry and therefore no rounding in it at all:
    the two masters are exactly, bit-for-bit, ninety degrees apart, and two
    seams snapped to the same master are exactly parallel.  Going through
    cos/sin of an angle would leave them a rounding error apart, and a rounding
    error is how a pie shape starts.

    The turn is CLOCKWISE -- (x, y) -> (y, -x) -- and that is not arbitrary: it
    makes `across` the very row `sheets.sheet_transform` puts first, so the
    frame a band is MEASURED in and the vectors a cut is BUILT from are the same
    right angle to the last bit.  The anti-clockwise turn would be a reflection
    of the sheet frame, and reflecting a directional-grain part is exactly what
    this program refuses to do.

    Note that this makes `across` point the opposite way from the arrow the
    pattern stage stored as `teak.frame.transverse_axis`.  For a seam that is
    nothing at all -- a seam is a line, not an arrow, and everything here works
    modulo 180 degrees.  It matters in one place only, `seamplace.direction_for`,
    which has to sweep a diagonal the way a protractor does; that function turns
    anti-clockwise for itself rather than borrowing this vector's sense.

    Returns None when the run has no detected axis and none was set by hand;
    there is no sensible default direction to invent for a boat.
    """

    if axis is None:
        return None
    values = np.asarray(axis, dtype=float).ravel()
    if values.size < 2:
        return None
    along = _unit(values[:2])
    if float(math.hypot(*along)) < 0.5:
        return None
    across = np.array([along[1], -along[0]])
    return along, across


def axis_references(grain_direction: Sequence[float] | None,
                    origin: Sequence[float] | None = None) -> list[Reference]:
    """The boat's own two directions, as labelled References.

    These carry no position -- an axis is a direction and nothing else -- so
    they exist for two reasons: they name the master a seam was snapped to in
    words the fabricator uses ("squared across the boat"), and they let a
    caller that already builds one reference list hand the axis to `snap_seam`
    in that list instead of passing it separately.

    Returns nothing when the run has no axis, exactly as `master_directions`
    does.
    """

    masters = master_directions(grain_direction)
    if masters is None:
        return []
    along, across = masters
    start = np.zeros(2) if origin is None else np.asarray(origin, dtype=float)[:2]
    # The 1000 mm length is arbitrary: an axis reference is a direction, and
    # `distance_to` never measures against its endpoints.
    return [
        Reference("axis", ALONG_LABEL, None, start, start + along * 1000.0),
        Reference("axis", ACROSS_LABEL, None, start, start + across * 1000.0),
    ]


def axis_from_references(references: Sequence[Reference]) -> np.ndarray | None:
    """The along-boat direction carried by an axis Reference in the list.

    The across-boat one is recognised by its label and turned back, so it does
    not matter which of the pair the caller kept: both describe the same single
    boat axis, and both give the same pair of masters back.
    """

    for reference in references:
        if reference.kind != "axis":
            continue
        unit = reference.unit
        if float(math.hypot(*unit)) < 0.5:
            continue
        if reference.label == ACROSS_LABEL:
            # across = (along[1], -along[0]), so turning it back is the same
            # quarter turn the other way.
            return np.array([-unit[1], unit[0]])
        return unit
    return None


# --------------------------------------------------------------------------
# the correction itself


@dataclass
class SnapResult:
    """What the corrector did to one seam, and what to tell the user about it.

    `kind` is "along-boat" or "across-boat" when the seam was squared to the
    boat, one of the feature kinds when it was lined up with fitted geometry,
    and "" when it was left as drawn.  `reference_label` names the thing the
    seam ended up ON: the master for a plain axis snap, and the fitted edge for
    an axis snap that was then slid onto one, because that edge is what the
    fabricator will be looking at.
    """

    x1: float
    y1: float
    x2: float
    y2: float
    applied: bool
    kind: str                  # along-boat | across-boat | collinear |
                               # tangent | perpendicular | parallel | ""
    reference_label: str
    angle_change_deg: float
    moved_mm: float            # the larger of the two endpoint displacements
    note: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2,
            "applied": self.applied, "kind": self.kind,
            "reference_label": self.reference_label,
            "angle_change_deg": round(self.angle_change_deg, 3),
            "moved_mm": round(self.moved_mm, 3),
            "note": self.note,
            "warnings": list(self.warnings),
        }


@dataclass
class _Candidate:
    """One possible correction, before the scoring picks between them."""

    kind: str
    reference: Reference
    q0: np.ndarray
    q1: np.ndarray


def _candidates(p: np.ndarray, q: np.ndarray, drawn: np.ndarray,
                references: Sequence[Reference], options: dict[str, Any]) -> Iterator[_Candidate]:
    """Every correction a FITTED reference could justify, acceptance tests
    applied but scoring and the move guard still to come.

    Axis references are skipped here: the boat direction is decided before any
    of this runs (or, with `seam_axis_priority` off, after it), never as one
    more candidate competing on cost.
    """

    angle_limit = float(options["seam_snap_angle_deg"])
    offset_limit = float(options["seam_snap_offset_mm"])
    reach_limit = float(options["seam_snap_reach_mm"])
    middle = (p + q) / 2.0

    for reference in references:
        if reference.kind == "axis":
            continue

        if reference.kind in ("line", "seam"):
            unit = reference.unit
            if float(math.hypot(*unit)) < 0.5:
                continue
            angle = _direction_angle_deg(drawn, unit)
            in_reach = reference.distance_to(p, q) <= reach_limit

            # A seam already on the deck may be CONTINUED but never merged into:
            # see `_runs_alongside`. The parallel rule below only turns the seam,
            # so it stays available either way.
            alongside = reference.kind == "seam" and _runs_alongside(p, q, reference)

            if angle <= angle_limit and in_reach:
                if reference.offset_of(middle) <= offset_limit and not alongside:
                    # Rule 1, the headline case: the drawn line is a continuation
                    # of this edge, so drop both ends straight onto it.
                    #
                    # It is the MIDDLE of the stroke that decides, not both ends.
                    # Asking whether the whole drawn line is already near the
                    # edge is the wrong question: a metre of seam four degrees
                    # out has its middle sitting on the edge and its ends 40 mm
                    # away, which used to read as "not the same line", get the
                    # weaker parallel treatment -- and then, because the ends
                    # had come in, land collinear on the NEXT click. The same
                    # drawing must not answer differently the second time it is
                    # snapped. Asking about the middle also cannot do that: a
                    # rotation about the middle leaves the middle exactly where
                    # it was, so whichever branch a seam takes it takes again.
                    yield _Candidate("collinear", reference,
                                     reference.project(p), reference.project(q))
                else:
                    # Rule 4: too far sideways to be the same line, but the user
                    # clearly meant it to run with the edge. This is the fallback
                    # for rule 1 and not a rival to it -- landing ON the edge
                    # always moves further than merely turning to face the same
                    # way, so offering both would let the weaker correction win
                    # every single time on cost.
                    a, b = _set_direction(p, q, middle, drawn, _oriented(unit, drawn))
                    yield _Candidate("parallel", reference, a, b)

            # Rule 3: square to the edge, turned about the point where the two
            # actually meet so that meeting point does not shift.
            if abs(90.0 - angle) <= angle_limit:
                cross = _line_intersection(p, drawn, reference.p0, unit)
                if (cross is not None
                        and reference.distance_to_segment(cross) <= offset_limit
                        and _point_segment_distance(cross, p, q) <= reach_limit):
                    normal = _oriented(np.array([-unit[1], unit[0]]), drawn)
                    a, b = _set_direction(p, q, cross, drawn, normal)
                    yield _Candidate("perpendicular", reference, a, b)

        elif reference.kind == "arc":
            candidate = _tangent_candidate(p, q, drawn, reference, angle_limit, offset_limit, reach_limit)
            if candidate is not None:
                yield candidate


def _tangent_candidate(p: np.ndarray, q: np.ndarray, drawn: np.ndarray, reference: Reference,
                       angle_limit: float, offset_limit: float, reach_limit: float) -> _Candidate | None:
    """Rule 2: make the seam graze the fillet instead of nicking it.

    The touching point is where the perpendicular from the arc's centre meets
    the drawn line -- the closest point of the circle to what the user drew.
    While that point is on the arc the tangent there is parallel to the drawn
    line by construction, so the correction is a pure sideways shift of at most
    `seam_snap_offset_mm` and the angle test costs nothing.  It starts to bite
    at the ends: a touching point that has run just past the arc is pulled back
    to the arc's own end vertex, and then the tangent there really is at an
    angle to the drawing, which is exactly when the user should be asked to
    aim better rather than have the seam swung round for them.
    """

    centre = reference.centre
    radius = float(reference.radius or 0.0)
    if centre is None or radius < _EPS:
        return None
    foot = _foot_on_line(centre, p, drawn)
    normal = foot - centre
    gap = float(math.hypot(*normal))
    if gap < _EPS:
        # The seam runs through the centre of the arc; there is no side of it
        # to be tangent on, and no small correction that would make one.
        return None
    if abs(gap - radius) > offset_limit:
        return None

    parameter = reference.angle_parameter(math.atan2(normal[1], normal[0]))
    # The slack is a length along the arc, converted to the arc's own parameter,
    # so a big fillet tolerates the same few millimetres of overshoot as a small
    # one rather than the same angle.
    slack = offset_limit / max(radius * abs(reference.sweep), _EPS)
    if parameter < -slack or parameter > 1.0 + slack:
        return None

    touch_parameter = float(np.clip(parameter, 0.0, 1.0))
    touch = reference.point_at_parameter(touch_parameter)
    tangent = _oriented(reference.tangent_at_parameter(touch_parameter), drawn)
    if _direction_angle_deg(drawn, tangent) > angle_limit:
        return None
    if _point_segment_distance(touch, p, q) > reach_limit:
        return None
    a, b = _set_direction(p, q, touch, drawn, tangent)
    return _Candidate("tangent", reference, a, b)


def _note(kind: str, reference: Reference, angle_deg: float, moved_mm: float,
          refined: Reference | None = None, shift_mm: float = 0.0) -> str:
    """One short sentence for the user, who is holding a router, not a mouse."""

    label = reference.label
    if kind in ("along-boat", "across-boat"):
        # "lined up with the boat and the teak lines", "squared across the boat".
        verb = "lined up with" if kind == "along-boat" else "squared"
        if refined is not None:
            return f"{verb} {label}, then moved {_mm(shift_mm)} mm onto {refined.label}"
        if angle_deg < 0.05:
            return f"{verb} {label}"
        return f"{verb} {label} (was {angle_deg:.1f} deg off)"
    if kind == "collinear":
        if angle_deg < 0.1:
            return f"moved {_mm(moved_mm)} mm onto {label}"
        return f"straightened {angle_deg:.1f} deg onto {label}"
    if kind == "tangent":
        return f"made tangent to {label} (r {float(reference.radius or 0.0):.0f} mm)"
    if kind == "perpendicular":
        return f"squared to {label} ({angle_deg:.1f} deg)"
    if kind == "parallel":
        return f"made parallel to {label} ({angle_deg:.1f} deg)"
    return ""


def _unchanged(p: np.ndarray, q: np.ndarray) -> SnapResult:
    """The seam exactly as it was drawn.

    Handing back the original numbers rather than the arithmetically identical
    ones is what keeps repeated UI edits from walking a seam a last bit at a
    time.
    """

    return SnapResult(x1=float(p[0]), y1=float(p[1]), x2=float(q[0]), y2=float(q[1]),
                      applied=False, kind="", reference_label="",
                      angle_change_deg=0.0, moved_mm=0.0, note="")


def _refine_position(a: np.ndarray, b: np.ndarray, unit: np.ndarray,
                     references: Sequence[Reference],
                     options: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, Reference, float] | None:
    """Slide an already-squared seam sideways onto a fitted edge that is square
    too, without touching its angle.

    This is what makes the axis rule better than a compromise instead of worse
    than one.  A seam squared across the boat that was drawn 4 mm off the
    console's own edge should sit ON that edge -- the user was aiming at it --
    but only because the edge itself turned out to be square within half a
    degree.  If it is not square, the seam stays where the boat put it and the
    edge is ignored: that mismatch is the boat's, not the seam's.

    The move is a translation along the seam's own normal, so the direction
    survives untouched, and the seam's MIDDLE is what lands on the reference
    line -- the symmetric choice, and the one that keeps the drawn stroke
    centred where the user put it.

    Only straight references are offered.  An arc has no infinite line to sit
    on, and pulling a squared seam onto a fillet's tangent would move it to
    wherever that tangency happens to fall, which is not what the user drew.

    The half-degree window is measured against the seam's own LENGTH as well,
    because a note that says "moved onto panel 1 cut-out edge" has to be true
    end to end and not merely at the middle.  See `_REFINE_MAX_GAP_MM`.
    """

    offset_limit = float(options["seam_snap_offset_mm"])
    reach_limit = float(options["seam_snap_reach_mm"])
    max_move = float(options["seam_snap_max_move_mm"])

    normal = np.array([-unit[1], unit[0]])
    middle = (a + b) / 2.0
    half_length = float(math.hypot(*(b - a))) / 2.0
    # The angle at which this seam's ends would stand _REFINE_MAX_GAP_MM off the
    # edge. On anything shorter than 2 * _REFINE_MAX_GAP_MM / tan(0.5 deg) --
    # about 230 mm -- the half degree is the tighter of the two and this does
    # nothing; on a two metre seam it is ten times tighter.
    gap_limit_deg = (math.degrees(math.atan2(_REFINE_MAX_GAP_MM, half_length))
                     if half_length > _EPS else _REFINE_ANGLE_DEG)
    angle_limit = min(_REFINE_ANGLE_DEG, gap_limit_deg)

    best: tuple[float, float, Reference] | None = None
    for reference in references:
        if reference.kind not in ("line", "seam"):
            continue
        ref_unit = reference.unit
        if float(math.hypot(*ref_unit)) < 0.5:
            continue
        if _direction_angle_deg(unit, ref_unit) > angle_limit:
            continue
        if reference.kind == "seam" and _runs_alongside(a, b, reference):
            # Two joins across the same stretch of deck. Merging them would
            # delete the strip between -- see `_runs_alongside`.
            continue
        if reference.distance_to(a, b) > reach_limit:
            continue
        denom = _cross(ref_unit, normal)
        if abs(denom) < 0.5:
            # Unreachable while the directions agree to half a degree (|denom|
            # is then within 1e-4 of 1), but a divide is a divide.
            continue
        shift = -_cross(ref_unit, middle - reference.p0) / denom
        distance = abs(shift)
        if distance > offset_limit or distance > max_move:
            continue
        if best is None or distance < best[0]:
            best = (distance, shift, reference)

    if best is None:
        return None
    distance, shift, reference = best
    move = normal * shift
    return a + move, b + move, reference, distance


@dataclass
class _Step:
    """One round's correction: where the seam goes, and what it locked onto.

    `landed` and `shift_mm` are only filled in by the axis rule, and say that
    the squared seam was then slid sideways onto a fitted edge.
    """

    kind: str
    reference: Reference
    a: np.ndarray
    b: np.ndarray
    landed: Reference | None = None
    shift_mm: float = 0.0


def _axis_step(p: np.ndarray, q: np.ndarray, drawn: np.ndarray,
               masters: tuple[np.ndarray, np.ndarray], references: Sequence[Reference],
               options: dict[str, Any]) -> _Step | None:
    """Rules 1 and 2: square the seam to the boat, then let it settle onto a
    square edge if there is one.

    Returns None only when NEITHER master claimed the seam -- i.e. it really is
    a diagonal, and feature snapping should have it.  A seam that IS claimed but
    is already exactly where the boat wants it comes back as a step that goes
    nowhere, not as None, so that a seam already square can never then be
    grabbed and rotated off square by a nearby edge.
    """

    # The masters are ninety degrees apart, so a seam is never more than
    # forty-five degrees from the NEARER of them; a capture wider than that
    # cannot mean "reach further", it can only mean "always square up". Taking
    # it at face value used to hand the seam to whichever master was listed
    # first, so typing 60 into the settings box quietly turned an almost-across
    # seam into an along-boat one and said "lined up with the boat" about it.
    capture = min(float(options.get("seam_axis_snap_deg", SNAP_DEFAULTS["seam_axis_snap_deg"])),
                  MAX_AXIS_CAPTURE_DEG)
    along, across = masters
    middle = (p + q) / 2.0

    # Nearest master wins, and only then is the capture consulted. Sorting by
    # the angle keeps the choice a fact about the seam rather than about the
    # order the two directions happen to be written in.
    options_by_angle = sorted(
        (("along-boat", along, ALONG_LABEL), ("across-boat", across, ACROSS_LABEL)),
        key=lambda item: _direction_angle_deg(drawn, item[1]))
    kind, master, label = options_by_angle[0]
    if _direction_angle_deg(drawn, master) > capture:
        return None
    unit = _oriented(master, drawn)
    a, b = _set_direction(p, q, middle, drawn, unit)

    landed: Reference | None = None
    shift = 0.0
    refined = _refine_position(a, b, unit, references, options)
    if refined is not None:
        a, b, landed, shift = refined
    return _Step(kind, Reference("axis", label, None, middle, middle + master),
                 a, b, landed, shift)


def _feature_step(p: np.ndarray, q: np.ndarray, drawn: np.ndarray,
                  references: Sequence[Reference], options: dict[str, Any]) -> _Step | None:
    """Rule 3: the old corrector, for seams the boat axis did not claim.

    Returns None when nothing came close enough.  A seam already sitting on the
    reference that wins comes back as a step that goes nowhere, which is how
    the caller tells "nothing was near it" apart from "it is already right".
    """

    # Both tolerances are user-settable and both may legitimately be set to
    # zero -- "do not turn a seam at all", "do not move it at all". Zero is a
    # meaningful answer to ask for and a fatal one to divide by, and a snap
    # option must never be able to turn a seam edit into a 500, so the divisor
    # is floored. Anything that survived `_candidates` at a zero tolerance is
    # already exactly on the reference, so its share of the cost is zero either
    # way and the floor changes no ordering.
    angle_limit = max(float(options["seam_snap_angle_deg"]), _EPS)
    offset_limit = max(float(options["seam_snap_offset_mm"]), _EPS)
    max_move = float(options["seam_snap_max_move_mm"])

    best: _Candidate | None = None
    best_cost = math.inf
    for candidate in _candidates(p, q, drawn, references, options):
        moved = max(float(math.hypot(*(candidate.q0 - p))), float(math.hypot(*(candidate.q1 - q))))
        if moved > max_move:
            continue
        angle = _direction_angle_deg(drawn, _unit(candidate.q1 - candidate.q0))
        cost = angle / angle_limit + moved / offset_limit + _KIND_PENALTY[candidate.kind]
        # Strictly cheaper, so ties go to the reference that came first and the
        # same drawing always produces the same cut.
        if cost < best_cost:
            best, best_cost = candidate, cost

    if best is None:
        return None
    return _Step(best.kind, best.reference, best.q0, best.q1)


def snap_seam(x1: float, y1: float, x2: float, y2: float,
              references: Sequence[Reference], options: dict[str, Any],
              axis: Sequence[float] | None = None,
              direction_locked: bool = False) -> SnapResult:
    """Correct one roughly drawn seam: square it to the boat, or failing that
    line it up with the geometry it was aiming at.

    `axis` is the run's longitudinal direction as `sheetjob.resolve_axis`
    returns it, manual grain override included.  It may instead be carried in
    `references` as the pair `axis_references` builds; passing it here wins.
    Without either, the seam is only ever lined up with fitted edges and every
    result carries `NO_AXIS_WARNING`.

    `direction_locked` says the seam's direction is already exactly what the
    user asked for and NOTHING here may turn it.  That is the case for every
    seam placed with the direction-first tool: `sheetjob._aimed` has just built
    it from the current axis, so it is along the boat, square across it, or at
    the diagonal that was typed, to the last bit.  Turning it again can only
    make it wrong, and it did: a diagonal placed at ten degrees off the boat
    was inside the twenty degree axis capture, so it was swung all the way onto
    the centreline while the seam list went on calling it ten degrees.  The
    seam may still be SLID sideways onto a fitted edge that shares its
    direction, which is a position change and not a direction change.

    Returns the seam unchanged -- `applied` False, empty note -- if nothing
    came close enough, if the correction would have moved an endpoint further
    than `seam_snap_max_move_mm`, or if the seam is already exactly where the
    winning rule wants it.

    The correction is applied round by round until it stops moving, and that
    is what makes it idempotent rather than nearly idempotent.  One pass is not
    enough on its own: squaring a seam to the boat can bring a fitted edge
    within reach that was not within reach of the drawing, and turning a seam
    parallel to one edge can leave it lying along another.  Snapping once and
    stopping would then hand back a seam that the NEXT click would move again,
    which is the drift the whole module promises not to have.  Each round takes
    the smallest correction available from where the seam now is, so the answer
    is a fixed point: feed it back in and nothing happens.
    """

    p = np.array([x1, y1], dtype=float)
    q = np.array([x2, y2], dtype=float)
    if not bool(options.get("seam_snap_enabled", True)):
        return _unchanged(p, q)
    if float(math.hypot(*(q - p))) < MIN_SEAM_LENGTH_MM:
        return _unchanged(p, q)

    references = list(references)
    warnings: list[str] = []
    masters: tuple[np.ndarray, np.ndarray] | None = None
    if bool(options.get("seam_snap_use_axis", True)):
        masters = master_directions(axis if axis is not None else axis_from_references(references))
        if masters is None:
            # Never invent a direction for a boat -- say that there isn't one.
            warnings.append(NO_AXIS_WARNING)

    drawn = _unit(q - p)
    if direction_locked:
        return _locked(p, q, drawn, references, options, warnings)

    axis_first = bool(options.get("seam_axis_priority", SNAP_DEFAULTS["seam_axis_priority"]))
    max_move = float(options["seam_snap_max_move_mm"])

    # What the move guard measures against. It starts at the drawing and is
    # moved on by an axis snap, because squaring to the boat is exempt from it:
    # the guard is there to stop a seam being dragged onto geometry the user
    # cannot see, and the boat direction is not hidden geometry -- it is the
    # planks the user is looking at. Squaring pivots about the middle of the
    # stroke, so the seam stays where it was put; only its angle changes, and
    # on a two metre seam the angle the user asked for is worth more than the
    # 60 mm the ends have to swing to get it.
    anchor_p, anchor_q = p, q
    current_p, current_q = p, q
    winner: _Step | None = None
    for _round in range(_MAX_ROUNDS):
        here = _unit(current_q - current_p)
        step: _Step | None = None
        if masters is not None and axis_first:
            step = _axis_step(current_p, current_q, here, masters, references, options)
        if step is None:
            step = _feature_step(current_p, current_q, here, references, options)
        if step is None and masters is not None and not axis_first:
            # Feature-first mode: the boat still squares up whatever no edge wanted.
            step = _axis_step(current_p, current_q, here, masters, references, options)
        if step is None:
            break
        if max(float(math.hypot(*(step.a - current_p))),
               float(math.hypot(*(step.b - current_q)))) <= _NO_CHANGE_MM:
            # The winning rule wants the seam exactly where it already is.
            break
        if step.kind in _AXIS_KINDS:
            anchor_p, anchor_q = step.a, step.b
        elif max(float(math.hypot(*(step.a - anchor_p))),
                 float(math.hypot(*(step.b - anchor_q)))) > max_move:
            break
        current_p, current_q = step.a, step.b
        winner = step

    # The user is told what happened to the line they drew, not what the last
    # round did to the round before it.
    angle = _direction_angle_deg(drawn, _unit(current_q - current_p))
    moved = max(float(math.hypot(*(current_p - p))), float(math.hypot(*(current_q - q))))
    if winner is None or (moved <= _NO_CHANGE_MM and angle <= _NO_CHANGE_DEG):
        result = _unchanged(p, q)
        result.warnings = warnings
        return result

    result = SnapResult(
        x1=float(current_p[0]), y1=float(current_p[1]),
        x2=float(current_q[0]), y2=float(current_q[1]),
        applied=True, kind=winner.kind,
        reference_label=(winner.landed.label if winner.landed is not None
                         else winner.reference.label),
        angle_change_deg=angle, moved_mm=moved,
        note=_note(winner.kind, winner.reference, angle, moved, winner.landed, winner.shift_mm),
    )
    result.warnings = warnings
    return result


def _locked(p: np.ndarray, q: np.ndarray, drawn: np.ndarray,
            references: Sequence[Reference], options: dict[str, Any],
            warnings: list[str]) -> SnapResult:
    """The whole correction for a seam whose direction may not be touched.

    Rule 2 and nothing else: if a straight fitted edge shares this seam's
    direction to within half a degree and sits within reach, the seam slides
    perpendicular to itself onto that edge's line.  Its angle is exactly what
    it arrived with, whatever happens.

    One shift settles it -- the seam does not rotate, so no second round can
    bring a reference into range that the first did not have -- and sliding it
    onto an edge it is already on moves it nowhere, which is idempotence for
    free.
    """

    refined = _refine_position(p, q, drawn, references, options)
    if refined is None:
        result = _unchanged(p, q)
        result.warnings = warnings
        return result
    a, b, reference, shift = refined
    moved = max(float(math.hypot(*(a - p))), float(math.hypot(*(b - q))))
    if moved <= _NO_CHANGE_MM:
        result = _unchanged(p, q)
        result.warnings = warnings
        return result
    result = SnapResult(
        x1=float(a[0]), y1=float(a[1]), x2=float(b[0]), y2=float(b[1]),
        applied=True, kind="collinear", reference_label=reference.label,
        angle_change_deg=0.0, moved_mm=moved,
        note=f"moved {_mm(shift)} mm onto {reference.label}",
    )
    result.warnings = warnings
    return result


def _endpoints(seam: Any) -> tuple[float, float, float, float]:
    """Endpoints of a `sheets.Seam` or of a bare (x1, y1, x2, y2)."""

    if hasattr(seam, "x1"):
        return float(seam.x1), float(seam.y1), float(seam.x2), float(seam.y2)
    x1, y1, x2, y2 = seam
    return float(x1), float(y1), float(x2), float(y2)


def snap_seams(seams: Sequence[Any], references: Sequence[Reference], options: dict[str, Any],
               axis: Sequence[float] | None = None) -> list[SnapResult]:
    """Correct a whole list of seams, in the order they were drawn.

    Each seam is fed back in as a reference for the ones after it, using its
    CORRECTED position.  With the boat axis in charge that matters less than it
    used to -- every long seam is snapped to the same master, so they come out
    exactly parallel whether they can see each other or not -- but it is what
    lets a second seam drawn a few millimetres off the first slide onto it
    exactly, because the first is now square within half a degree by
    construction.
    """

    min_length = float(options["seam_snap_min_ref_length_mm"])
    pool = list(references)
    results: list[SnapResult] = []
    for index, seam in enumerate(seams):
        x1, y1, x2, y2 = _endpoints(seam)
        result = snap_seam(x1, y1, x2, y2, pool, options, axis=axis)
        results.append(result)
        p0 = np.array([result.x1, result.y1], dtype=float)
        p1 = np.array([result.x2, result.y2], dtype=float)
        if float(math.hypot(*(p1 - p0))) >= min_length:
            seam_id = str(getattr(seam, "seam_id", "") or index + 1)
            pool.append(Reference("seam", f"seam {seam_id}",
                                  getattr(seam, "panel_id", None), p0, p1))
    return results
