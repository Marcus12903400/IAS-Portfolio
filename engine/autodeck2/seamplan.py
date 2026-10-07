"""Working out where the seams should go, on their own.

    "I also want there to be a button that calculates the best seam positions
     for the lowest waste percentage that fits in the 39"x79" envelope"

    "i want the algorythm to try and make the pices somewhat symetrical. I know
     boats are never going to be a perfect mirror but at least something close
     and I like the pices to be as square/rectangular as possible so that the
     seams are more in line with features like the edges/walls of the center
     console of the boat."

That is one button, and this is what sits behind it.  It chooses a set of seams
for a run, checks the answer against the real cut pipeline, and reports what it
found in the same plain numbers the fabricator already reads off the sheet
report: how many pieces are still too big, how many sheets, how much of the
material is thrown away, and how many joins they will have to live with.

What "best" means here
----------------------
It was the least waste, and the second quote above is why it no longer is.  A
deck that reads as a pair of port and starboard pieces that visibly match looks
MADE; the same deck with the port seam 200 mm further out than the starboard
one looks like a mistake, and it looks like a mistake even when it wastes less
material.  A seam that runs off the console wall reads as intentional; the same
seam 40 mm to one side of it reads as sloppy.  None of that is visible in a
waste percentage, and the fabricator has to hand the finished deck to a
customer.

So two things are still hard requirements, in this order and not for sale at
any price:

    1. no piece outside the 990.6 x 2006.6 mm envelope
    2. the fewest sheets

and then, instead of waste alone, a single blended cost in percentage points:

    cost = waste percent + seam_tidiness_weight x (how untidy the pieces are)

where "untidy" is three measured things -- SYMMETRY about the boat's real
centreline, RECTANGULARITY of the pieces, and ALIGNMENT of each cut to a fitted
edge that runs the same way.  `sheets.DEFAULTS["seam_tidiness_weight"]` is the
one dial: 0.0 is the pure-waste button this used to be, 1.0 will pay about
twenty points of waste for tidy pieces, and the 0.35 default pays a couple of
points, which is what it costs to move a seam onto a console wall on a real
deck.  Because tidiness enters BELOW oversize and sheets in the ordering, no
weight can ever buy an extra sheet or a piece that will not fit, and the tests
assert that at 0.0, 0.35 and 1.0.

The number of joins stays where it always was -- a strict tie-break AFTER the
blended cost, never a weighted term.  A join is a discrete thing somebody has
to cut, hide and trust, and letting a fraction of a point of tidiness buy one
would undo the rule that a panel which already fits a sheet is left whole.

All three tidiness terms are reported next to the waste number, before and
after, so the trade is visible rather than buried in a single score.

What it is allowed to cut
-------------------------
Full-span guillotine cuts, in the two master directions only -- along the boat
and across it.  Three reasons, and none of them is a shortcut:

  * `sheets.split_panel` extends every seam across the whole panel before it
    cuts, so a full-span cut is what actually happens to the material.  A search
    over part-length cuts would be searching for something the cutter cannot
    make.
  * The material is directional.  Reflex TruGrain runs along the boat, so a
    diagonal cut crosses the grain, wastes more of the sheet than a square one,
    and puts a visible join across the planks.
  * The user has already settled the question for the manual tool: long seams
    run with the boat and short ones square across it.  A button that quietly
    used a different rule would be arguing with them.

Diagonal seams stay a manual choice.  The optimiser never proposes one.

The frame the search works in
-----------------------------
Everything is measured in the SHEET frame -- `sheets.sheet_transform` of the
resolved boat axis -- where +Y runs along the boat, the sheet is axis aligned,
and a full-span cut is one number: its offset.  An along-boat cut is a line of
constant X and decides how WIDE the pieces are (limited by
`max_part_width_mm`); an across-boat cut is a line of constant Y and decides how
LONG they are (limited by `max_part_length_mm`).  The winning cuts are turned
back into placed-frame `sheets.Seam` objects before they are returned, because
that is the frame the flat view, `final_auto.dxf` and the cutter all agree on.

Why the envelope test can be trusted
------------------------------------
Every cut runs the whole width or length of the panel, so the cuts divide the
panel into a grid of cells and EVERY piece lies inside exactly one cell.  So if
every cell is inside the 990.6 x 2006.6 mm envelope, no piece can be outside it.
That turns the "does it fit" question -- which needs polygons -- into a
one-dimensional test on band widths, which costs nothing, and it is what lets
the candidate generator throw away the overwhelming majority of cut sets before
any geometry is built.  The real geometry still gets the final word: the winner
is re-cut and re-nested by the production pipeline before any number is
reported.

Where the centreline comes from
-------------------------------
Symmetry needs a mirror line, and it is a stored fact rather than a guess:
`run.json` -> `teak.frame.origin_mm`, the point the pattern stage fitted to the
midpoints of the boat's cross-boat width slices.  In the sheet frame the boat
runs along +Y, so the centreline is a line of constant X and mirroring is one
reflection in that X.  Inferring a centre from a bounding box instead would put
it wherever the cockpit happens to be asymmetric -- on the cached boat, 0.7 mm
out on the big panel and 750 mm out on the port toe rail, which would mirror
that rail onto empty water.

`origin_mm` lives in the BOAT-PLAN frame, and the placed frame differs from it
by each panel's `nest_offset` translation.  A translation does not change a
direction, which is why the stored axis needs no conversion -- but it very much
moves a point, so each panel carries its own `boat_shift_mm` and the symmetry
term is measured with the panels put back where they sit on the boat.  That is
also what makes symmetry work ACROSS panels: on the cached deck the port toe
rail and the starboard toe rail are two separate panels, nested 2.4 m apart,
and their cuts are a mirror pair on the finished boat.

Honesty
-------
This is a bounded heuristic search.  It tries a few hundred arrangements out of
an uncountable number, and the arrangement it returns is the best it FOUND, not
the best that exists.  Nothing here says "optimal", and the report carries the
number of candidates actually evaluated so the user can see how much of a look
it got.  When no arrangement clears the envelope, the result says so plainly,
names the pieces that are still too big, gives their sizes and says which way
each one still needs a seam, rather than returning something that looks like a
success.

Every number reported -- the waste AND all three tidiness terms -- is measured
on the production pipeline's own output: the exact pieces, and the seams AS
CUT after the corrector has had its say, not the cuts the search picked.  The
proxy decides the ORDER things are tried in and is never the thing anyone is
told.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from . import nesting, seamsnap, sheetjob, sheets as sheets_mod
from .sheets import Loop, Piece, Seam

Progress = Callable[[str], None]

# The two directions a cut may run in, spelled the way `sheets.Seam.mode`
# spells them so a chosen cut becomes a seam with no translation table.
ALONG = "along"
ACROSS = "across"

# --------------------------------------------------------------------------
# how the search is bounded
#
# Every number here is a stop, not a discovery.  They are gathered in one place
# because between them they are the whole answer to "how long will the button
# take", and someone tuning it should not have to go looking.

# A band narrower than this is a strip nobody wants to handle, whatever the
# nesting says: it is fragile to cut, awkward to lay, and the joins either side
# of it are two joins where there could have been one.  Cuts that would leave
# one are never offered.
MIN_BAND_MM = 100.0

# The blind sweep, for cuts the reasoned candidates below would miss.  50 mm is
# fine enough that the best position is never far away, and coarse enough that
# the sweep does not swamp the reasoned candidates in the shortlist.
SWEEP_STEP_MM = 50.0

# How close to a master direction a fitted edge has to run before its offset is
# offered as a cut position.  Two degrees, not the corrector's five: this is
# asking "is that edge square to the boat", and an edge two degrees out is not.
EDGE_TOLERANCE_DEG = 2.0

# Bands are measured against the envelope less this margin.  The search cuts a
# polygon sampled at `PROXY_SAMPLE_STEP_MM` while production re-cuts it at
# `sample_step_mm` and rebuilds its arcs, so a piece's measured extent can move
# by a fraction of a millimetre between the two.  Half a millimetre of headroom
# on a 990 mm envelope costs nothing, and it stops the search picking a winner
# that only fits at the resolution it was searched at.
BAND_MARGIN_MM = 0.5

# The proxy geometry.  Sampling at 4 mm instead of the production 1 mm, and
# simplifying the cut pieces by 1 mm before nesting them, makes one candidate
# about fifteen times cheaper to evaluate and -- measured over the whole
# shortlist on the cached runs -- does not change which candidate wins.  It is
# still a proxy, so it never decides what the user is told; see `_confirm`.
PROXY_SAMPLE_STEP_MM = 4.0
PROXY_SIMPLIFY_MM = 1.0
PROXY_NEST_SAMPLE_MM = 8.0

# The nesting grid the search packs on, and it is the one number here that was
# NOT free to choose.  The nester is a bottom-left first fit, so a coarser grid
# does not merely approximate a finer one -- it lands the pieces somewhere else
# entirely, and sometimes somewhere better.  Measured against the production
# 5 mm grid over the fifteen best arrangements on the cached boat: a 40 mm grid
# claimed four sheets for all fifteen when eleven of them really need five, a
# 25 mm grid got fourteen of the fifteen right, and a 10 mm grid got all
# fifteen but costs 700 ms an arrangement instead of 230.  So 25 mm: wrong
# about one arrangement in fifteen, and wrong in the optimistic direction,
# which the exact confirmation then catches.
PROXY_NEST_STEP_MM = 25.0

# A cut whose line crosses less than this much material is nicking the end of a
# part, not joining two of them.  It came from watching the button put a 9 mm
# seam across the end of a toe rail: legal, very slightly better packing, and
# an absurd thing to ask anyone to cut.  A toe rail is only 25 mm wide, though,
# so the floor has to give way on a panel where every possible cut is short --
# hence the half-of-the-longest escape in `_positions`, which guarantees the
# rule can never leave a panel with nowhere to cut.
MIN_CUT_LENGTH_MM = 20.0

# Shortlist sizes, and they are a funnel, each stage cheaper per candidate than
# the one it feeds:
#
#   thousands of feasible cut sets per direction
#     -> MAX_SETS_PER_AXIS per CUT COUNT, by how well their bands pack a sheet
#     -> MAX_SPLIT_CANDIDATES combinations of the two directions, actually cut
#     -> MAX_PANEL_CANDIDATES of those, by how compact the real pieces came out
#     -> nested for real, in the sweep
#
# The per-count quota on the first stage matters: the band score prefers many
# small cuts, and without a quota the two-cut arrangements -- the ones the
# fabricator actually wants -- would be crowded out by four-cut ones before any
# geometry had been looked at.
MAX_SETS_PER_AXIS = 14
MAX_SPLIT_CANDIDATES = 300
MAX_PANEL_CANDIDATES = 80

# How many EXTRA slots each stage gives the tidiest arrangements when tidiness
# is switched on.  The two sieves ahead of the objective are blind to symmetry
# and to fitted edges, so without this the tidy arrangements are thrown away
# BEFORE anything is entitled to compare them against the untidy ones, and the
# weight would do nothing at any setting.
#
# They are EXTRA and not a share of the existing quota, and that distinction
# was measured rather than assumed.  Taking five of the fourteen per-axis slots
# for tidiness cost the cached boat its four-sheet 39.16% arrangement -- the
# one the pure-waste search finds -- because two of the cut sets it is built
# from were in those five slots.  The search then could not choose it at any
# weight, and turning the dial UP made the answer worse on every count at once,
# which is the one behaviour a preference dial must never have.  Adding slots
# instead makes the weight-0 shortlist a strict subset of the weight-1 one, so
# a higher weight can only ever change the answer by preferring something, and
# never by not having been shown it.
TIDY_EXTRA_PER_AXIS = 5
TIDY_EXTRA_PER_PANEL = 24
TIDY_SPLIT_CANDIDATES = 300

# How far down the packing order the tidiness re-score is allowed to look.  A
# wide panel has tens of thousands of feasible cut sets and the tail of that
# list packs badly without being tidy either, so re-scoring all of it would
# cost more than the sieve it is rescuing arrangements from.
TIDY_SIEVE_POOL = 200

# Candidates are shared out between the panels in proportion to their area,
# because the waste is almost entirely decided by the big panel and an equal
# share would spend most of the budget on strips of toe rail.
CANDIDATE_BUDGET = 200
MIN_PANEL_CANDIDATES = 8

# How many cuts beyond the geometric minimum the search will consider in one
# direction.  Two is enough to find the arrangements that pay -- on the cached
# boat the winner uses one extra cut across -- and each further one multiplies
# the shortlist without ever having won.
EXTRA_CUTS = 2

# How many feasible cut sets one direction may enumerate for one cut count.
#
# `_cut_sets` grows every combination that fits, which is C(positions, cuts) in
# the worst case, and that is not a number to leave to chance.  Measured with
# nothing but the defaults: a 2058 mm panel (the cached boat) builds 88 thousand
# sets in a quarter of a second, a 3000 mm one builds 1.1 million in 2.5
# seconds, and a 4000 mm one -- a 13 foot beam, an ordinary boat -- builds
# FORTY-TWO MILLION in 139 seconds and four gigabytes of RAM.  All to keep
# fourteen.  The button sat there for minutes per panel with its progress log
# stuck on one line, whatever time budget had been asked for.
#
# The ceiling is applied by thinning the CANDIDATE POSITIONS rather than by
# truncating the enumeration, because truncating takes the first sets in
# lexicographic order -- every cut crowded up against one end of the panel --
# and that is not a shortlist, it is a bias.  Thinning drops sweep positions
# only, and evenly, so the positions that have a reason to be good (sheet
# filling, even splits, real fitted edges) all survive.  300 thousand is chosen
# so that the cached boat, at 49 positions and four cuts, is not thinned at all
# and its answer is bit for bit what it was.
MAX_CUT_SETS = 300_000

# Share of the time budget the search itself may use.  The rest pays for the
# exact confirmations, which are the only numbers the user is ever shown.
SEARCH_SHARE = 0.7

# A hard ceiling on the number of arrangements evaluated, on top of the clock.
# It is what makes the answer repeatable: a search that stops when the clock
# runs out stops in a different place on a fast machine than on a busy one, and
# the same job would come back with different seams.  Sized so that a full sweep
# of every panel, twice, finishes inside it.
MAX_EVALUATIONS = 260

# And how many more the tidiness climb may add on top -- see `_search` for why
# there are two climbs.  The second one starts from where the first finished,
# so the arrangements the two agree about are cache hits and it costs far less
# than a second search.  The ceiling is a fixed number rather than "whatever
# time is left", for the same reason MAX_EVALUATIONS is: the clock must not be
# allowed to choose the answer.
TIDY_EVALUATIONS = 200

# How many sweeps of the panels the search makes.  The first walks every
# candidate; the second only exists to let a panel react to what the others
# settled on, and in practice it changes nothing on a deck with one big panel.
MAX_SWEEPS = 2

# How many of the best proxy candidates are re-run through the production
# pipeline.  The proxy decides the ORDER, but only these exact runs decide the
# WINNER, so this number is the whole defence against the proxy being wrong.
#
# It was 3, and 3 was measured to be too few.  Widening the shortlist on the
# cached boat -- more candidates, more sweeps, nothing else changed -- made the
# reported answer WORSE: five sheets and 51.3% waste, where the narrower search
# had found four sheets and 39.1%.  The four-sheet arrangement was in the ranked
# list the whole time, at a position the proxy had put outside the top three.
# Confirming twelve found it again (four sheets, 39.07%).  A button that gets
# worse when it is allowed to look harder is a button nobody can trust, and the
# cause was never the search -- it was asking a 25 mm nesting grid to pick the
# finalists.
#
# Twelve exact runs cost about 12 seconds on the cached boat, taking the whole
# job from 15 to 27 seconds against a 90 second budget that was otherwise 83%
# unspent.  That is the right thing to spend it on: an exact number is the only
# kind this module is allowed to report.  It is a fixed count rather than
# "however many fit in the time left" on purpose -- see MAX_EVALUATIONS for why
# the clock must not be allowed to choose the answer.
CONFIRM_COUNT = 12

# How many of the best arrangements ON WASTE ALONE are confirmed as well, on
# top of the best on the blended cost.  Three, because the exact numbers only
# have to be good enough to stop a tidier arrangement being preferred to a
# genuinely cheaper one, and the first of the three is the one that carries the
# guarantee: see the note beside the shortlist in `optimise`.  At weight nought
# these are the same arrangements and the list collapses back to twelve.
CONFIRM_ON_WASTE = 3

# How finely two layouts' waste is compared, in PERCENTAGE POINTS, before the
# tie is handed to the seam count.  It used to be spelled as a number of decimal
# places on the fraction, and the comment claimed it suppressed a hundredth of a
# percent; four places on a fraction is exactly a hundredth of a percent, so it
# preserved the very thing it said it hid, and a gain of 825 mm2 spread over
# four sheets -- a 29 mm square -- could buy an extra join the fabricator has to
# cut, hide and trust.  A tenth of a percentage point is comfortably inside the
# noise of a first-fit nester and comfortably below anything worth a seam.
WASTE_RESOLUTION_PERCENT = 0.1


# --------------------------------------------------------------------------
# how tidy is tidy
#
# Three measured things, each on a 0..1 scale where 1 is what the user asked
# for, and each with a price in PERCENTAGE POINTS OF WASTE that
# `seam_tidiness_weight` then scales.  Working in points of waste is what makes
# the dial mean something the fabricator can reason about: at the 0.35 default,
# a completely unsymmetric arrangement is worth 0.35 x 9 = 3.2 points of waste,
# so a seam moves onto the boat's mirror line if that costs less than three
# points and stays where it is if it costs more.
#
# The three prices are not equal, and the order is the user's own emphasis.
# Symmetry is what they led with and is the thing a customer sees from the
# dock.  Rectangularity is next: it is about handling and nesting as much as
# looks.  Alignment is smallest per unit, but it is the only one of the three
# that is nearly free -- a cut is either on a fitted edge or it is not, and the
# positions that sit on one are already in the candidate list.
SYMMETRY_PENALTY_PERCENT = 9.0
RECTANGULARITY_PENALTY_PERCENT = 7.0
ALIGNMENT_PENALTY_PERCENT = 4.0

# And a fourth, which is here to stop the third undoing the second.
#
# Rectangularity rewards cutting MORE: chop a wandering deck panel into
# narrower and narrower bands and every band fills its own box better than the
# panel did, so area-over-box climbs with every extra cut, for ever.  Measured
# on the cached boat before this term existed: at weight 0.35 the search
# happily took ten seams instead of six -- four extra joins to make, hide and
# trust -- to buy nine points of squareness and a tenth of a point of waste.
# That is not what "as square as possible" meant.
#
# So an extra join has a price too, counted against the FLOOR the geometry
# demands (`_minimum_seams`) rather than against nothing: the joins a panel
# cannot avoid are free, and only the discretionary ones cost.  At 6.0 over a
# scale of 4, each extra join is 1.5 points of waste at full weight, which is
# more than the squareness one extra cut can buy on any panel measured here.
# The seam count also remains a strict tie-break below the blended cost, so
# two arrangements that come out level still take the one with fewer joins.
JOINS_PENALTY_PERCENT = 6.0
JOINS_SCALE = 4.0

# How far a cut's mirror image may be from the nearest real cut before the pair
# stops reading as a pair.  The user's own example is the one to calibrate on:
# "the port seam sits 200 mm further out than the starboard one looks like a
# mistake".  So 200 mm scores zero, and everything between falls off linearly.
# A cut sitting ON the centreline mirrors onto itself, distance nought, and is
# perfectly symmetric -- which is correct and is the reason the nearest-cut
# search includes the cut being measured.
SYMMETRY_SCALE_MM = 200.0

# A cut counts as ON a fitted edge when it is within `seam_snap_offset_mm` of
# one, and that number is not a taste: it is exactly how far sideways the seam
# corrector will slide a seam to land it on an edge, so a cut inside it WILL be
# cut on the edge.  Anything further away gets no alignment credit at all,
# rather than partial credit that falls off with distance.  That is deliberate:
# the user's complaint is that a seam 40 mm off the console wall "reads as
# sloppy -- worse than one nowhere near it", so a near miss is not a partial
# success and must not be rewarded as one.  A graded score would also have the
# search chasing edges it can never quite reach.
#
# The offsets themselves come from `_edge_offsets`, which is already the source
# of the edge-aligned candidate POSITIONS.  Generating them and scoring them
# from the same function is the point: a cut that can sit on a console edge is
# offered, and now also wins.


# --------------------------------------------------------------------------
# the pieces of a plan


@dataclass(frozen=True)
class Cut:
    """One full-span guillotine cut: which way it runs, and where.

    `offset_mm` is measured in the sheet frame -- for an ALONG cut it is the X
    of the line (how far across the boat), for an ACROSS cut the Y (how far
    along it).  One number is the whole cut, which is the point of working in
    this frame.
    """

    direction: str
    offset_mm: float

    @property
    def is_along(self) -> bool:
        return self.direction == ALONG


@dataclass
class _Panel:
    """One panel's fitted geometry, measured once and reused all search long.

    `read_fitted_dxf` costs a third of a second and sampling a deck panel costs
    more, so nothing in here may be rebuilt inside the search loop.
    """

    panel_id: int
    outer: Loop
    holes: list[Loop]
    polygon: Polygon                            # sampled at PROXY_SAMPLE_STEP_MM
    bounds: tuple[float, float, float, float]   # u0, u1, v0, v1 in the sheet frame
    area_mm2: float
    # Where this panel sits on the BOAT rather than on the layout: the sheet
    # frame X of its nest offset.  Subtract it from an along-cut's offset and
    # the cut is expressed in boat-plan coordinates, which is the frame the
    # deck is actually laid in and therefore the only frame in which two
    # panels' cuts can be a mirror pair.  Nought for the primary panel, and for
    # every panel of a run laid out in place rather than nested.
    boat_shift_mm: float = 0.0
    candidates: list[tuple[Cut, ...]] = field(default_factory=list)
    # The arrangements only the tidiness search looks at, kept apart from the
    # list above rather than appended to it.  Appending them would spend the
    # pure-waste climb's evaluation ceiling on arrangements it does not care
    # about, and that climb would then be cut off in a different place at every
    # weight -- which is exactly the "turning the dial up lost the good answer"
    # failure the whole design is arranged to prevent.
    tidy_candidates: list[tuple[Cut, ...]] = field(default_factory=list)
    pieces_cache: dict[tuple[Cut, ...], "_Split"] = field(default_factory=dict)
    length_cache: dict[tuple[str, float], float] = field(default_factory=dict)
    edge_cache: dict[str, tuple[float, ...]] = field(default_factory=dict)

    @property
    def width_mm(self) -> float:
        return self.bounds[1] - self.bounds[0]

    @property
    def length_mm(self) -> float:
        return self.bounds[3] - self.bounds[2]


@dataclass
class _Split:
    """One panel cut one way: the pieces, their sizes, and what fell off."""

    pieces: list[Piece]
    extents: list[tuple[float, float]]          # (across the boat, along the boat)
    dropped: int                                # parts under min_piece_area_mm2
    area_mm2: float


@dataclass(frozen=True)
class _Shape:
    """How tidy one arrangement's pieces are: three numbers on 0..1, best at 1.

    A term is None when the arrangement gives it nothing to measure -- no along
    cuts to be symmetric about, or a run with no stored centreline to be
    symmetric about -- and a term that measured nothing is left out of the
    average rather than scored as a failure.  Scoring "no along cuts" as
    unsymmetric would push the search into adding one just to have something to
    mirror, which is the opposite of what it is for.
    """

    symmetry: float | None
    rectangularity: float | None
    alignment: float | None
    joins: float | None = None

    def penalty_percent(self, weight: float) -> float:
        """What this arrangement's untidiness costs, in points of waste."""

        total = 0.0
        for value, price in ((self.symmetry, SYMMETRY_PENALTY_PERCENT),
                             (self.rectangularity, RECTANGULARITY_PENALTY_PERCENT),
                             (self.alignment, ALIGNMENT_PENALTY_PERCENT),
                             (self.joins, JOINS_PENALTY_PERCENT)):
            if value is not None:
                total += price * (1.0 - float(value))
        return float(weight) * total

    def to_dict(self) -> dict[str, Any]:
        return {
            "symmetry": None if self.symmetry is None else round(float(self.symmetry), 4),
            "rectangularity": (None if self.rectangularity is None
                               else round(float(self.rectangularity), 4)),
            "alignment": None if self.alignment is None else round(float(self.alignment), 4),
            "joins": None if self.joins is None else round(float(self.joins), 4),
        }


@dataclass
class _Layout:
    """One complete arrangement -- a cut set for every panel -- and its score."""

    cuts: dict[int, tuple[Cut, ...]]
    oversize: list[str]
    sheets: int
    waste: float
    seam_count: int
    seam_length_mm: float
    piece_count: int
    shape: _Shape
    tidiness_penalty_percent: float

    @property
    def cost_percent(self) -> float:
        """Waste and untidiness as one number, in percentage points of waste.

        This is the thing being minimised once the envelope and the sheet count
        have been settled, and it is expressed in points of waste on purpose:
        the fabricator can then read `seam_tidiness_weight` as "how much
        material am I willing to spend on this looking right".
        """

        return self.waste * 100.0 + self.tidiness_penalty_percent

    @property
    def key(self) -> tuple:
        """The objective, in the order the user gave it.

        A piece that will not fit is a failure and not a cost, so it sorts first
        and no amount of saved material -- or tidiness -- buys it off.  Then
        sheets, for the same reason: `seam_tidiness_weight` sits INSIDE the
        third term and so cannot reach either of the first two, whatever it is
        set to.  Then the blended cost, then the number of joins, then how much
        joining there is.  The cut positions come last so that two arrangements
        equal on every count still order the same way on every run.
        """

        return (len(self.oversize), self.sheets, _waste_bucket(self.cost_percent),
                self.seam_count, round(self.seam_length_mm, 3), _cuts_key(self.cuts))

    @property
    def waste_key(self) -> tuple:
        """The same objective with tidiness switched off -- what the button
        minimised before this setting existed.

        It is still needed, at every weight, because it is the trajectory the
        search must be sure to walk: see `_search`.
        """

        return (len(self.oversize), self.sheets, _waste_bucket(self.waste * 100.0),
                self.seam_count, round(self.seam_length_mm, 3), _cuts_key(self.cuts))


def _waste_bucket(cost_percent: float) -> int:
    """A cost in whole units of `WASTE_RESOLUTION_PERCENT`, so that two layouts
    within a tenth of a percentage point of each other tie and the seam count
    decides between them.  Both the proxy key and the exact one go through here,
    because a search that ranks on one resolution and reports on another can
    hand back the layout it did not choose.

    It takes the BLENDED cost -- waste plus the tidiness penalty -- rather than
    the waste alone, so a tenth of a point of tidiness is worth exactly as
    little as a tenth of a point of material, which is what it should be worth.
    """

    return int(round(float(cost_percent) / WASTE_RESOLUTION_PERCENT))


def _cuts_key(cuts: dict[int, tuple[Cut, ...]]) -> tuple:
    """A cut set as plain sortable data -- the search's cache key and its final
    tie-break, so the same input always produces the same winner."""

    return tuple((panel_id, tuple((cut.direction, round(cut.offset_mm, 6))
                                  for cut in cuts[panel_id]))
                 for panel_id in sorted(cuts))


# --------------------------------------------------------------------------
# making the pieces look right
#
# One `_Placed` per cut is all three terms' input, and building it in one place
# is what keeps the search and the report measuring the same thing.  The search
# builds them from the `Cut` objects it is trying; the report builds them from
# the seams `sheetjob.plan` actually cut with, AFTER the corrector has slid
# them onto whatever edges it found.  Those are different numbers -- a seam the
# search put 8 mm off a console wall gets cut ON the wall -- and it is the
# second set the user is shown, because that is the deck they will get.


@dataclass(frozen=True)
class _Placed:
    """One cut, ready to be scored: which way, where on the boat, how long."""

    direction: str
    panel_id: int
    offset_mm: float        # in the panel's own sheet frame
    boat_mm: float          # the same cut in boat-plan coordinates
    chord_mm: float         # how much material it actually crosses
    edge_gap_mm: float      # distance to the nearest fitted edge running its way


def _panel_edges(panel: _Panel, direction: str, masters: tuple[np.ndarray, np.ndarray],
                 options: dict[str, Any]) -> tuple[float, ...]:
    """`_edge_offsets` for one panel and direction, worked out once.

    The search asks for these on every candidate it scores and they never
    change, and walking every segment of a deck panel's outline is not free.
    """

    cached = panel.edge_cache.get(direction)
    if cached is None:
        cached = tuple(sorted(_edge_offsets(panel, direction, masters, options)))
        panel.edge_cache[direction] = cached
    return cached


def _placed(panel: _Panel, direction: str, offset_mm: float,
            masters: tuple[np.ndarray, np.ndarray], options: dict[str, Any]) -> _Placed:
    edges = _panel_edges(panel, direction, masters, options)
    gap = min((abs(offset_mm - edge) for edge in edges), default=float("inf"))
    return _Placed(
        direction=direction, panel_id=panel.panel_id, offset_mm=offset_mm,
        boat_mm=offset_mm - panel.boat_shift_mm,
        chord_mm=_chord_mm(panel, direction, offset_mm, masters),
        edge_gap_mm=float(gap),
    )


def _symmetry(placed: Sequence[_Placed], centre_mm: float | None) -> float | None:
    """How well the along-boat cuts mirror each other about the centreline.

    For each along cut, reflect it in the centreline and measure how far that
    reflection lands from the nearest along cut there actually is -- including
    the cut itself, so a cut sitting ON the centreline is its own mirror and
    scores perfectly, which is right: a deck split down the middle is as
    symmetric as a deck can be.

    ACROSS cuts are left out entirely.  A cut square across the boat is
    unchanged by a port-to-starboard mirror, so every arrangement of them is
    already perfectly symmetric and including them would do nothing but dilute
    the term the user actually asked for.

    Weighted by how much material each cut crosses, because a 4 m seam down the
    cockpit sole is what anyone looking at the deck sees and a 60 mm nick
    across the end of a toe rail is not.
    """

    if centre_mm is None:
        return None
    along = [item for item in placed if item.direction == ALONG]
    if not along:
        return None
    positions = [item.boat_mm for item in along]
    total_weight = 0.0
    total = 0.0
    for item in along:
        mirror = 2.0 * centre_mm - item.boat_mm
        distance = min(abs(mirror - other) for other in positions)
        weight = max(item.chord_mm, 1.0)
        total += weight * max(0.0, 1.0 - distance / SYMMETRY_SCALE_MM)
        total_weight += weight
    return total / total_weight if total_weight else None


def _alignment(placed: Sequence[_Placed], options: dict[str, Any]) -> float | None:
    """What share of the cutting runs along an edge that was there anyway.

    A cut within `seam_snap_offset_mm` of a fitted edge running its way scores
    1, because the seam corrector will slide it onto that edge and it will be
    cut there; anything further scores 0.  See the note beside the constants
    for why this is a step and not a slope.

    Weighted by chord length again, so aligning the long seam that runs past
    the console is worth more than aligning a short one that runs past nothing
    much.
    """

    if not placed:
        return None
    reach = float(options["seam_snap_offset_mm"])
    total_weight = 0.0
    total = 0.0
    for item in placed:
        weight = max(item.chord_mm, 1.0)
        total += weight * (1.0 if item.edge_gap_mm <= reach else 0.0)
        total_weight += weight
    return total / total_weight if total_weight else None


def _minimum_seams(panels: Sequence[_Panel], options: dict[str, Any]) -> int:
    """How many joins this deck cannot avoid, whatever anyone prefers.

    The sum over panels of the geometric floor in each direction -- the same
    `_minimum_cuts` the search starts from.  Joins up to this number are not a
    choice and are not charged for; everything above it is discretionary and is
    what the joins term prices.  Counting from nought instead would charge the
    fabricator for a cut the envelope forced on them.
    """

    gap = float(options["seam_gap_mm"])
    total = 0
    for panel in panels:
        for direction in (ALONG, ACROSS):
            lo, hi = _extent(panel, direction)
            total += _minimum_cuts(hi - lo, _usable(direction, options) - BAND_MARGIN_MM, gap)
    return total


def _joins(seam_count: int, floor: int) -> float | None:
    """1 when the job uses no more joins than the geometry demands, falling to
    0 by `JOINS_SCALE` extra ones."""

    extra = max(0, int(seam_count) - max(0, int(floor)))
    return max(0.0, 1.0 - extra / JOINS_SCALE)


def _rectangularity(area_mm2: float, bbox_area_mm2: float) -> float | None:
    """How much of its own bounding box the material fills, over the whole job.

    Summing the areas before dividing is what area-weights it: a 4 m2 piece
    that wanders round three sides of a console drags the number down far
    further than a 0.05 m2 offcut does, which is the user's point -- a big ugly
    piece is a bigger problem than a small one.

    The box is taken in the SHEET frame, not the piece's own best-fit frame,
    because that is the box the material is bought in and the box the nester
    packs against.
    """

    if bbox_area_mm2 <= 0.0 or area_mm2 <= 0.0:
        return None
    return min(1.0, float(area_mm2) / float(bbox_area_mm2))


def _nest_offsets(run_dir: Path) -> dict[int, np.ndarray]:
    """Each panel's nest translation, which is all that separates the placed
    frame from the boat-plan frame the deck is actually laid in.

    Missing or unreadable is not an error: a run laid out in place has no
    offsets at all and nought is exactly right for it.
    """

    meta_path = Path(run_dir) / "run.json"
    if not meta_path.is_file():
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    offsets: dict[int, np.ndarray] = {}
    for entry in meta.get("panels") or []:
        placement = entry.get("placement") or {}
        offset = placement.get("nest_offset_mm")
        if offset is None:
            continue
        try:
            offsets[int(entry["panel_id"])] = np.asarray(offset, dtype=float)[:2]
        except (KeyError, TypeError, ValueError):
            continue
    return offsets


def _centreline(run_dir: Path, rotation: np.ndarray) -> float | None:
    """The boat's centreline as one number: its X in the sheet frame.

    `teak.frame.origin_mm` is the point the pattern stage fitted through the
    midpoints of the boat's cross-boat width slices, so this is a measurement
    of the hull and not an inference from a bounding box.  Row 0 of the sheet
    rotation is the across-boat direction, so projecting the origin onto it is
    the whole conversion.

    None when the run has no stored frame.  The symmetry term then measures
    nothing and says so, rather than falling back on a bounding-box centre --
    an asymmetric cockpit would put that centre somewhere the boat is not, and
    a mirror line in the wrong place is worse than no mirror line at all.
    """

    meta_path = Path(run_dir) / "run.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    origin = ((meta.get("teak") or {}).get("frame") or {}).get("origin_mm")
    if origin is None:
        return None
    try:
        point = np.asarray(origin, dtype=float)[:2]
    except (TypeError, ValueError):
        return None
    if point.shape != (2,) or not np.isfinite(point).all():
        return None
    return float(np.dot(rotation[0], point))


# --------------------------------------------------------------------------
# the entry point


def optimise(run_dir: Path, config: dict[str, Any], progress: Progress | None = None,
             time_budget_s: float = 90.0) -> dict[str, Any]:
    """Choose the seams for a run, and report honestly on what was chosen.

    Returns a dict with the chosen `sheets.Seam` objects under "seams" -- real
    seams, in the placed frame, each with `panel_id` set and `mode` "along" or
    "across", so they can be written straight to seams.json and every existing
    view agrees with the cutter -- and the measured before and after under
    "report".

    The seams are the ones this function CHOSE.  Every number in the report is
    measured by `sheetjob.plan`, the production pipeline, at production
    tolerances: the coarse geometry the search runs on is a proxy, and a proxy
    is never allowed to be the thing the user is told.

    `time_budget_s` bounds the SEARCH.  One exact confirmation always runs after
    it however little time is left, because an unchecked number is worse than a
    slow one.
    """

    started = time.monotonic()
    say = progress or (lambda _message: None)
    run_dir = Path(run_dir)
    options = sheets_mod.settings(config)

    source = sheetjob.source_dxf(run_dir)
    if source is None:
        return _refusal(
            "NO_GEOMETRY",
            f"{run_dir.name} has no fitted outline yet, so there is nothing to lay seams on. "
            "Run auto-fit (or ingest a drawing) first.",
            started, time_budget_s, [])

    frame, warnings = sheetjob.resolve_frame(run_dir, options)
    masters = seamsnap.master_directions(frame.axis)
    if masters is None:
        return _refusal(
            "NO_AXIS",
            "this run has no boat direction stored (was a pattern selected?), and every seam this "
            "button places has to run either along the boat or square across it. Set the grain "
            "angle, or re-run the outline with a pattern, and press the button again.",
            started, time_budget_s, warnings)

    # `sheets.sheet_transform` builds its rows from the same unit axis these
    # masters come from -- row 0 IS across the boat, row 1 IS along it -- so
    # measuring with the matrix and cutting with the vectors cannot disagree.
    # The matrix is used for measuring because it is what `oversize_report` and
    # `nest` are handed in production, and the vectors are used for the seams
    # because they are what `seamsnap` will later compare those seams against.
    rotation = sheets_mod.sheet_transform(frame.axis)

    say(f"Reading fitted geometry from {source.name}")
    loops, _pattern, _kind = sheets_mod.read_fitted_dxf(source)
    if not loops:
        return _refusal("NO_GEOMETRY", f"{source.name} contains no cut outlines.",
                        started, time_budget_s, warnings)

    panels = _read_panels(loops, rotation, options, _nest_offsets(run_dir))
    if not panels:
        return _refusal("NO_GEOMETRY", f"{source.name} contains no usable panel outlines.",
                        started, time_budget_s, warnings)

    # The mirror line the symmetry term is measured against, resolved once.
    # None means this run has no stored boat frame; the term then measures
    # nothing and the other two carry the tidiness weight between them.
    centre_mm = _centreline(run_dir, rotation)
    weight = float(options["seam_tidiness_weight"])
    if centre_mm is None and weight > 0.0:
        warnings.append(
            "this run has no stored centreline, so the automatic seams cannot be made to mirror "
            "each other port and starboard; they are still squared to the boat and still kept "
            "square and on the fitted edges where that is possible"
        )

    # What is on disk now, measured by the real pipeline, so that "better" is a
    # measurement rather than a claim.
    say("Measuring the seams this run already has")
    existing = sheets_mod.read_seams(run_dir)
    before = _confirm(run_dir, config, existing, options, panels, masters, centre_mm)
    exact_cost_s = max(before["elapsed_s"], 0.2)
    say(_headline("Now", before))

    # The clock is read BEFORE the shortlists are built, not after. Building
    # them is the one unbounded thing left in this function -- it cuts every
    # candidate arrangement of every panel for real -- and it used to run
    # outside the budget entirely, so a five second request measured six
    # seconds and an eight second request measured sixteen doing identical
    # work. A panel that runs out of clock keeps whatever shortlist it has and
    # the search carries on with that; a partial shortlist is a smaller search,
    # not a wrong answer, because every arrangement is confirmed exactly later.
    deadline = started + float(time_budget_s)
    search_deadline = min(deadline - 1.5 * exact_cost_s,
                          started + float(time_budget_s) * SEARCH_SHARE)
    _plan_candidates(panels, masters, rotation, options, say, search_deadline, centre_mm)

    search_started = time.monotonic()
    evaluated, ranked, budget_hit = _search(panels, masters, rotation, options,
                                            search_deadline, say, centre_mm,
                                            _minimum_seams(panels, options))
    search_elapsed = time.monotonic() - search_started

    if not ranked:
        return _refusal(
            "NO_LAYOUT",
            "no seam arrangement could be built for this run -- every cut that would fit the "
            "envelope would leave a piece too small to be worth cutting. These seams will have "
            "to be placed by hand.",
            started, time_budget_s, warnings, evaluated=evaluated)

    # The proxy has had its say; from here on only the production pipeline
    # counts.  The best few are re-run rather than only the best, because the
    # proxy is a proxy.
    #
    # The list is led by the best arrangement on WASTE ALONE, whatever the
    # tidiness weight is set to, and that is the second half of the promise
    # that tidiness can never cost a sheet.  The first half is in `_search`,
    # which makes sure that arrangement gets built; this is what makes sure it
    # gets MEASURED, because only a confirmed arrangement can be chosen, and
    # `_exact_key` puts the sheet count ahead of the blended cost.  Between
    # them: the answer at any weight uses no more sheets than the answer at
    # weight nought, as a fact about the code.
    #
    # Leading with it also means it survives the budget running out, since the
    # confirmation loop stops on the clock.
    shortlist: list[_Layout] = []
    seen_cuts: set[tuple] = set()
    for layout in (sorted(ranked, key=lambda item: item.waste_key)[:CONFIRM_ON_WASTE]
                   + ranked[:CONFIRM_COUNT]):
        signature = _cuts_key(layout.cuts)
        if signature in seen_cuts:
            continue
        seen_cuts.add(signature)
        shortlist.append(layout)

    confirmed: list[tuple[tuple, dict[str, Any], list[Seam], _Layout]] = []
    for layout in shortlist:
        if confirmed and time.monotonic() + exact_cost_s > deadline:
            break
        seams = _seams_for(panels, layout, masters)
        say(f"Checking {_layout_words(layout)} with the real cut settings")
        exact = _confirm(run_dir, config, seams, options, panels, masters, centre_mm)
        confirmed.append((_exact_key(exact), exact, seams, layout))
    confirmed.sort(key=lambda item: item[0])
    _key, after, seams, layout = confirmed[0]

    improved = _exact_key(after) < _exact_key(before)
    say(_headline("Best found", after))
    if not improved:
        say("That is no better than the seams this run already has, so nothing needs changing.")

    status = "OK" if not after["oversize"] else "NEEDS_SEAMS"
    return {
        "status": status,
        "reason": _reason(status, after, improved),
        "seams": seams,
        "report": {
            "before": before,
            "after": after,
            "improved": improved,
            "candidates_evaluated": evaluated,
            "candidates_confirmed": len(confirmed),
            "search_is_exhaustive": False,
            "search_note": ("this is the best arrangement the button FOUND inside its time "
                            "budget, not a proof that nothing better exists"),
            "time_budget_s": round(float(time_budget_s), 1),
            "search_elapsed_s": round(search_elapsed, 2),
            "elapsed_s": round(time.monotonic() - started, 2),
            "budget_exhausted": budget_hit,
            "panels": _panel_report(panels, layout),
            "boat_frame": frame.to_dict(),
            # The trade the user got, spelled out rather than buried in the
            # score: what the dial was set to, and what it bought.
            "tidiness_weight": round(weight, 3),
            "tidiness_note": _tidiness_words(before, after, weight),
            "centreline_known": centre_mm is not None,
            "warnings": list(warnings),
        },
    }


def _refusal(status: str, reason: str, started: float, budget: float,
             warnings: Sequence[str], evaluated: int = 0) -> dict[str, Any]:
    """A result that changes nothing and says why, in one sentence a fabricator
    can act on.  It has the same shape as a success, so no caller ever has to
    branch on whether the keys are there."""

    return {
        "status": status,
        "reason": reason,
        "seams": [],
        "report": {
            "before": None, "after": None, "improved": False,
            "candidates_evaluated": evaluated, "candidates_confirmed": 0,
            "search_is_exhaustive": False,
            "search_note": "no search was run",
            "time_budget_s": round(float(budget), 1),
            "search_elapsed_s": 0.0,
            "elapsed_s": round(time.monotonic() - started, 2),
            "budget_exhausted": False,
            "panels": [], "boat_frame": None,
            "tidiness_weight": 0.0, "tidiness_note": "", "centreline_known": False,
            "warnings": list(warnings),
        },
    }


def _reason(status: str, after: dict[str, Any], improved: bool) -> str:
    """One sentence, in the numbers the user already reads off the sheet
    report.

    The failing branch is the one that matters.  "I could not fix this" has to
    be impossible to read as "here is your layout", so it names every piece
    that is still too big, gives its size, says by how much it is over and
    which way the seam it still needs has to run.  A list of piece ids on its
    own is not something a fabricator can act on without going and measuring
    them again.
    """

    if status == "NEEDS_SEAMS":
        count = len(after["oversize"])
        detail = {item["piece_id"]: item for item in after.get("oversize_detail") or []}
        parts = []
        for piece_id in after["oversize"]:
            item = detail.get(piece_id)
            if item is None:
                parts.append(f"{piece_id} (too big to nest)")
                continue
            over = []
            if item.get("over_width_mm"):
                over.append(f"{item['over_width_mm']:.0f} mm too wide, needs a seam ALONG the boat")
            if item.get("over_length_mm"):
                over.append(f"{item['over_length_mm']:.0f} mm too long, needs a seam ACROSS the boat")
            parts.append(f"{piece_id} is {item['width_mm']:.0f} x {item['length_mm']:.0f} mm -- "
                         + " and ".join(over or ["too big to nest"]))
        return (f"THIS LAYOUT CANNOT BE CUT. The button could not find an arrangement that fits, "
                f"and {count} piece(s) are still too big for a "
                f"{after['envelope_mm'][0]:.1f} x {after['envelope_mm'][1]:.1f} mm sheet: "
                + "; ".join(parts)
                + ". Place those seams by hand -- nothing here is ready to export.")
    sentence = (f"best found: {after['sheet_count']} sheet(s), {after['waste_percent']:.1f}% of the "
                f"material wasted, {after['seam_count']} seam(s), and every piece fits the "
                f"{after['envelope_mm'][0]:.1f} x {after['envelope_mm'][1]:.1f} mm envelope.")
    if not improved:
        return sentence + " That is no better than the seams already on this run."
    return sentence


def _headline(prefix: str, metrics: dict[str, Any]) -> str:
    return (f"{prefix}: {metrics['sheet_count']} sheet(s), {metrics['waste_percent']:.1f}% waste, "
            f"{metrics['seam_count']} seam(s), {len(metrics['oversize'])} piece(s) too big, "
            f"{_shape_words(metrics)}")


def _shape_words(metrics: dict[str, Any]) -> str:
    """The three tidiness terms as percentages, or a note that one of them had
    nothing to measure.  Percentages because that is the only kind of number
    this page already asks the fabricator to read."""

    shape = metrics.get("shape") or {}
    bits = []
    for key, label in (("symmetry", "symmetry"), ("rectangularity", "squareness"),
                       ("alignment", "on fitted edges"), ("joins", "no spare joins")):
        value = shape.get(key)
        bits.append(f"{label} n/a" if value is None else f"{label} {float(value) * 100.0:.0f}%")
    return ", ".join(bits)


def _tidiness_words(before: dict[str, Any], after: dict[str, Any], weight: float) -> str:
    """What the tidiness weight is set to and what these pieces measure, in one
    sentence the fabricator can act on.

    It states the two measurements and the dial, and it deliberately does NOT
    claim to know what tidiness cost in material: that would need the same
    search run again at weight nought, and reporting a number this function did
    not measure is the one thing this module will not do.  The tests measure it
    properly, by running both.
    """

    if weight <= 0.0:
        return ("Seam tidiness is switched off (weight 0.00), so this arrangement was chosen on "
                f"waste alone. These pieces measure {_shape_words(after)}.")
    penalty = after.get("tidiness_penalty_percent")
    cost = ("" if penalty is None else
            f" Untidiness was priced at {float(penalty):.1f} points of waste against this "
            "arrangement, which is what any tidier one had to beat.")
    return (f"Seam tidiness weight {weight:.2f}. The seams already on this run measured "
            f"{_shape_words(before)}; these measure {_shape_words(after)}.{cost} "
            "Set the weight to 0.00 for the least material and towards 1.00 for tidier pieces.")


def _layout_words(layout: _Layout) -> str:
    return (f"{layout.seam_count} seam(s) -> {layout.sheets} sheet(s), "
            f"{layout.waste * 100.0:.1f}% waste")


# --------------------------------------------------------------------------
# reading the run


def _read_panels(loops: dict[int, list[Loop]], rotation: np.ndarray,
                 options: dict[str, Any],
                 nest_offsets: dict[int, np.ndarray] | None = None) -> list[_Panel]:
    """Every panel, with the three things the search needs from it: a polygon to
    cut, its extent in the sheet frame, and where it sits on the boat.

    The extent is measured at the PRODUCTION sample step, not the proxy one.
    Band widths are checked against it and production measures the finished
    pieces the same way, so measuring it coarsely here would let the search
    believe a band fits that production then calls too big.

    `nest_offsets` is what puts the panels back on the boat for the symmetry
    term; a run without them (or laid out in place rather than nested) simply
    has every shift at nought, which is correct rather than a fallback.
    """

    production_step = float(options["sample_step_mm"])
    offsets = nest_offsets or {}
    panels: list[_Panel] = []
    for panel_id in sorted(loops):
        outer, holes = sheets_mod.classify_loops(loops[panel_id], production_step)
        if outer is None:
            continue
        polygon = sheets_mod.loop_polygon(outer, list(holes), PROXY_SAMPLE_STEP_MM)
        if polygon.is_empty:
            continue
        points, _source = sheets_mod.sample_loop(outer, production_step)
        sheet_xy = points @ rotation.T
        offset = offsets.get(int(panel_id))
        panels.append(_Panel(
            panel_id=int(panel_id), outer=outer, holes=list(holes), polygon=polygon,
            bounds=(float(sheet_xy[:, 0].min()), float(sheet_xy[:, 0].max()),
                    float(sheet_xy[:, 1].min()), float(sheet_xy[:, 1].max())),
            area_mm2=float(polygon.area),
            boat_shift_mm=0.0 if offset is None else float(np.dot(rotation[0], offset)),
        ))
    return panels


# --------------------------------------------------------------------------
# candidate cuts


def _usable(direction: str, options: dict[str, Any]) -> float:
    return float(options["max_part_width_mm"] if direction == ALONG
                 else options["max_part_length_mm"])


def _extent(panel: _Panel, direction: str) -> tuple[float, float]:
    u0, u1, v0, v1 = panel.bounds
    return (u0, u1) if direction == ALONG else (v0, v1)


def _minimum_cuts(span: float, usable: float, gap: float) -> int:
    """The fewest full-span cuts that can bring `span` inside `usable`.

    With n cuts there are n + 1 bands and n gaps, so the best any arrangement
    can do is (span - n * gap) / (n + 1) per band; requiring that to be within
    the envelope gives n >= (span - usable) / (usable + gap).  This is a floor
    fixed by geometry and not a guess, which is why the search starts here and
    only adds cuts when the envelope is still broken.
    """

    if span <= usable + 1e-9:
        return 0
    return int(math.ceil((span - usable) / (usable + gap) - 1e-9))


def _band_widths(lo: float, hi: float, offsets: Sequence[float], gap: float) -> list[float]:
    """The finished width of each band a set of cuts leaves.

    A cut takes `gap` millimetres of material, half from the piece either side,
    so a band between two cuts is a whole gap narrower than the distance between
    them, while a band that runs out to the panel's own edge only loses the half
    on its cut side.  Getting that asymmetry wrong is how a search convinces
    itself a piece fits when it does not.
    """

    edges = [lo, *offsets, hi]
    widths: list[float] = []
    for index in range(len(edges) - 1):
        width = edges[index + 1] - edges[index]
        if index > 0:
            width -= gap / 2.0
        if index < len(edges) - 2:
            width -= gap / 2.0
        widths.append(width)
    return widths


def _band_efficiency(width: float, usable: float, spacing: float) -> float:
    """How much of the sheet a band of this width would actually use.

    Bands are what get nested, and a sheet holds a whole number of them side by
    side with `part_spacing_mm` between: a 683 mm band fits once across a 990.6
    mm sheet and leaves 300 mm of unusable strip, while a 411 mm band fits twice
    and leaves 148 mm.  This is the cheap stand-in for that, and it is what
    orders the shortlist so the arrangements worth nesting get nested first.  It
    is a ranking and not a prediction -- the real nester interlocks shapes and
    this counts rectangles -- so it never decides anything on its own.
    """

    if width <= 0.0:
        return 0.0
    fits = int((usable + spacing) // (width + spacing))
    if fits < 1:
        return 0.0
    return min(1.0, (fits * width + (fits - 1) * spacing) / usable)


def _edge_offsets(panel: _Panel, direction: str, masters: tuple[np.ndarray, np.ndarray],
                  options: dict[str, Any]) -> list[float]:
    """Offsets of fitted edges that already run in the cut's direction.

    A cut along a console edge or the straight run of a coaming looks
    deliberate, meets the material where there is an edge anyway, and is the cut
    the seam corrector would have slid a hand-drawn seam onto -- so it is worth
    offering even when no other rule would propose it.  Only real straight
    segments count: an arc has no single offset, and a 15 mm stub of scan noise
    is not an edge.
    """

    along, across = masters
    wanted = along if direction == ALONG else across
    normal = across if direction == ALONG else along
    limit = math.cos(math.radians(EDGE_TOLERANCE_DEG))
    minimum = float(options["seam_snap_min_ref_length_mm"])

    offsets: list[float] = []
    for loop in [panel.outer, *panel.holes]:
        xy = loop.xy
        bulges = loop.bulges
        count = len(xy)
        for index in range(count):
            if abs(float(bulges[index])) > 1e-12:
                continue
            p0 = xy[index]
            p1 = xy[(index + 1) % count]
            span = p1 - p0
            length = float(math.hypot(*span))
            if length < minimum:
                continue
            if abs(float(np.dot(span / length, wanted))) < limit:
                continue
            offsets.append(float(np.dot((p0 + p1) / 2.0, normal)))
    return offsets


def _positions(panel: _Panel, direction: str, masters: tuple[np.ndarray, np.ndarray],
               options: dict[str, Any], limit: int | None = None,
               centre_mm: float | None = None) -> list[float]:
    """Every cut position worth trying in one direction, sorted.

    Five sources, and each is here because it has a reason to be a good cut
    rather than because it is a convenient number:

      sheet-filling  a band exactly the usable size, measured in from each end,
                     so the band fills a sheet instead of leaving a strip too
                     narrow for anything;
      even splits    the panel divided into n equal bands, which is what a
                     person would draw and is often right;
      edge-aligned   the offsets of real fitted edges (see `_edge_offsets`);
      mirrored       the centreline itself, and the reflection of every one of
                     the above in it, so that a symmetric answer is always
                     AVAILABLE and not merely preferred;
      a coarse sweep every 50 mm, to catch whatever the other four miss.

    The mirrored source is what makes the symmetry term able to do anything.
    Scans are not symmetric: the console wall shows up as a fitted edge on the
    port side and is 30 mm of noise to starboard, so the edge-aligned source
    offers a port cut with no starboard partner, and the scorer would have had
    to choose between an aligned cut and a symmetric one.  Reflecting the whole
    reasoned set means the partner is on the list whether or not the scan found
    an edge there.  Mirroring is only applied to ALONG cuts: a cut square across
    the boat is unchanged by a port-to-starboard mirror, so reflecting it would
    just be a second name for a position already offered.

    Positions that would leave a sliver against either end of the panel are
    dropped here, once, rather than being filtered again in every combination.

    `limit` caps how many come back, which is how the combination count in
    `_cut_sets` is bounded; only the coarse sweep is thinned.  See `_thinned`.
    """

    lo, hi = _extent(panel, direction)
    usable = _usable(direction, options)
    gap = float(options["seam_gap_mm"])
    span = hi - lo

    values: set[float] = set()

    # Sheet-filling.  The first band runs from the panel edge to the cut and
    # loses only half a gap, so it is exactly usable when the cut sits at
    # lo + usable + gap/2; every band after that loses a half gap at each end.
    step = usable + gap
    position = lo + usable + gap / 2.0
    while position < hi:
        values.add(position)
        position += step
    position = hi - usable - gap / 2.0
    while position > lo:
        values.add(position)
        position -= step

    # Even splits, from the fewest cuts that can work upwards.
    fewest = _minimum_cuts(span, usable - BAND_MARGIN_MM, gap)
    for count in range(max(1, fewest), fewest + EXTRA_CUTS + 1):
        for index in range(count):
            values.add(lo + span * (index + 1) / (count + 1))

    for offset in _panel_edges(panel, direction, masters, options):
        values.add(offset)

    # The mirror, in this panel's own sheet frame.  The centreline is stored in
    # boat-plan coordinates and this panel may have been nested away from where
    # it sits on the boat, so the panel's own shift is added back before
    # reflecting -- reflecting in the un-shifted line would mirror a toe rail
    # about a line two and a half metres from the boat.
    #
    # Two sources, and the second one is not redundant.  Reflecting the
    # positions already offered gives a partner for every cut that had a reason
    # to exist -- an edge, a sheet-filling width -- and that is what makes an
    # ALIGNED pair reachable.  But it can only ever produce pairs straddling
    # positions the other sources happened to propose, and on the cached boat
    # the symmetric pair that actually wins sits at 6.5 +- 485 mm, where no
    # other source has anything: the sweep lattice runs from the panel edge and
    # lands at 476.8 and 526.8, whose mirrors are 486.1 and -463.9, so not one
    # symmetric pair anywhere near it can be formed.  A sweep measured OUT FROM
    # THE CENTRELINE instead of from the panel edge makes every one of them
    # available, at the cost of about forty more positions.
    if direction == ALONG and centre_mm is not None:
        axis = centre_mm + panel.boat_shift_mm
        for value in list(values) + [axis]:
            values.add(2.0 * axis - value)
        values.add(axis)
        step = SWEEP_STEP_MM
        while axis - step > lo or axis + step < hi:
            values.add(axis - step)
            values.add(axis + step)
            step += SWEEP_STEP_MM

    # The sweep is kept apart from the others because it is the only one that
    # can be thinned: the rest are each here for a reason, and there are never
    # many of them.
    reasoned = set(values)
    position = lo + SWEEP_STEP_MM
    while position < hi:
        values.add(position)
        position += SWEEP_STEP_MM

    inside = sorted(value for value in values
                    if lo + MIN_BAND_MM <= value <= hi - MIN_BAND_MM)

    # Drop the positions where the cut barely touches the part.  The floor
    # gives way on a panel whose cuts are all short -- a toe rail is 25 mm wide
    # and still has to be cut somewhere -- so the longest cut always survives
    # and this can never leave a panel with nowhere to cut.
    chords = [_chord_mm(panel, direction, value, masters) for value in inside]
    if not chords:
        return _thinned(inside, reasoned, limit)
    floor = min(MIN_CUT_LENGTH_MM, 0.5 * max(chords))
    kept = [value for value, chord in zip(inside, chords) if chord >= floor]
    return _thinned(kept, reasoned, limit)


def _thinned(positions: Sequence[float], reasoned: set[float], limit: int | None) -> list[float]:
    """At most `limit` positions, keeping every one that had a reason to be
    offered and spreading the survivors of the coarse sweep evenly.

    This is what keeps `_cut_sets` from enumerating tens of millions of
    combinations on a wide panel -- see `MAX_CUT_SETS`.  Taking the sweep
    positions evenly rather than the first `limit` of them is the whole point:
    a shortlist drawn from one end of the panel is not a shortlist.
    """

    if limit is None or limit <= 0 or len(positions) <= limit:
        return list(positions)
    keep = [value for value in positions if value in reasoned]
    sweep = [value for value in positions if value not in reasoned]
    room = limit - len(keep)
    if room <= 0:
        # Even the reasoned positions are more than the ceiling allows, which
        # takes a very large panel; spread those instead and drop the sweep.
        index = np.unique(np.linspace(0, len(keep) - 1, max(limit, 1)).round().astype(int))
        return [keep[i] for i in index]
    if sweep and room < len(sweep):
        index = np.unique(np.linspace(0, len(sweep) - 1, room).round().astype(int))
        sweep = [sweep[i] for i in index]
    return sorted(keep + sweep)


def _position_limit(count: int) -> int | None:
    """How many candidate positions one direction may offer for `count` cuts.

    The enumeration is C(positions, count) at worst, so the ceiling on sets
    turns straight into a ceiling on positions.  None means "no limit needed",
    which is the answer for nought or one cut: there is nothing to combine.
    """

    if count <= 1:
        return None
    size = count
    while math.comb(size + 1, count) <= MAX_CUT_SETS and size < 4096:
        size += 1
    return size


def _cut_sets(panel: _Panel, direction: str, positions: Sequence[float],
              count: int, options: dict[str, Any]) -> list[tuple[float, ...]]:
    """Every set of `count` positions whose bands all fit the envelope.

    Grown one cut at a time so that an impossible prefix is abandoned as soon as
    it is impossible.  Because `positions` is sorted, once the band behind a cut
    is already too wide so is every band behind every later cut, and the whole
    tail can be dropped; without that the combination count on a deck panel runs
    to hundreds of thousands and the button spends its budget enumerating
    arrangements it will never try.
    """

    lo, hi = _extent(panel, direction)
    usable = _usable(direction, options) - BAND_MARGIN_MM
    gap = float(options["seam_gap_mm"])
    results: list[tuple[float, ...]] = []

    if count == 0:
        return [()] if (hi - lo) <= usable else []

    def walk(start: int, chosen: list[float], previous: float) -> None:
        remaining = count - len(chosen)
        if remaining == 0:
            if hi - previous - gap / 2.0 <= usable:
                results.append(tuple(chosen))
            return
        for index in range(start, len(positions)):
            position = positions[index]
            width = position - previous - gap / 2.0 - (gap / 2.0 if chosen else 0.0)
            if width < MIN_BAND_MM:
                continue
            if width > usable:
                break                   # sorted, so every later cut is worse still
            # The cuts still to come can cover at most this much between them;
            # if more panel than that is left, this cut is too far back to be
            # rescued by anything that follows it.
            reach = remaining * usable + (remaining - 1) * gap + gap / 2.0
            if hi - position > reach:
                continue
            chosen.append(position)
            walk(index + 1, chosen, position)
            chosen.pop()

    walk(0, [], lo)
    return results


def _is_mirrored(offsets: Sequence[float], axis: float, tolerance: float = 1.0) -> bool:
    """Do these cuts reflect onto each other in the centreline?

    Cheap arithmetic, and it exists because the tidiness re-score cannot afford
    to look at every feasible cut set on a wide panel.  Sorting by how well a
    set PACKS and re-scoring the best few hundred misses the symmetric ones --
    a symmetric pair on the cached boat packs at 0.69 against a shortlist whose
    top two hundred are all above 0.9 -- so the genuinely mirrored sets are
    pulled out by this test first and re-scored whatever they packed like.
    """

    if not offsets:
        return True
    mirrored = sorted(2.0 * axis - value for value in offsets)
    return all(abs(a - b) <= tolerance for a, b in zip(sorted(offsets), mirrored))


def _set_shape(panel: _Panel, direction: str, combination: Sequence[float],
               masters: tuple[np.ndarray, np.ndarray], options: dict[str, Any],
               centre_mm: float | None) -> float:
    """How tidy one direction's cut set is on its own, 0 to 1, best at 1.

    The two terms a set of parallel cuts can be judged on before anything is
    actually cut: whether they mirror each other, and whether they sit on
    fitted edges.  Rectangularity is not among them because it is a property of
    the PIECES, which needs both directions and a boolean; it comes in later,
    in `_plan_candidates`, where the cutting has been done.
    """

    if not combination:
        return 1.0
    placed = [_placed(panel, direction, offset, masters, options) for offset in combination]
    symmetry = _symmetry(placed, centre_mm)
    alignment = _alignment(placed, options)
    parts = [value for value in (symmetry, alignment) if value is not None]
    return sum(parts) / len(parts) if parts else 1.0


def _ranked_sets(panel: _Panel, direction: str, masters: tuple[np.ndarray, np.ndarray],
                 options: dict[str, Any], needs_cuts: bool,
                 centre_mm: float | None = None
                 ) -> tuple[list[tuple[float, ...]], list[tuple[float, ...]]]:
    """The best cut sets for one direction, grouped by how many cuts they use.

    Returns two lists: the PACKING shortlist, and -- only when there is a
    centreline to be tidy about -- a shorter list of tidy sets that packing
    alone would have thrown away.  They are kept apart all the way through
    `_plan_candidates` for one reason, and it is the reason this whole feature
    can be trusted: at any weight, the shortlist the search actually walks has
    to CONTAIN the shortlist it would have walked at weight nought.  Anything
    less and turning the dial up can lose the good answer instead of ranking it
    below a better one, and the sheet count -- which tidiness is never allowed
    to touch -- goes up.  That is not hypothetical; it is what the first two
    versions of this function did, and it cost the cached boat a whole sheet.

    Sets are ranked by how well their bands would pack the sheet, and a quota is
    kept for each cut count so that the two-cut arrangements are not crowded out
    by the four-cut ones before any geometry has been looked at.  This ordering
    is only a first sieve -- the candidates that survive it are cut for real and
    re-ranked on what came out; see `_plan_candidates`.

    Sets whose bands match to the nearest 25 mm, IN ORDER, are collapsed to the
    best of them: a shortlist of fourteen near-identical cuts is a shortlist of
    one.  In order, because bands of 947, 244, 855 and bands of 855, 947, 244
    are two completely different ways to cut a panel and only their sorted
    widths look alike.

    The tidy list is enumerated from a WIDER set of positions -- the mirrored
    ones `_positions` only offers when there is a centreline -- so it can reach
    arrangements the packing list could not have contained at all, rather than
    merely reordering it.
    """

    lo, hi = _extent(panel, direction)
    usable = _usable(direction, options)
    spacing = float(options["part_spacing_mm"])
    gap = float(options["seam_gap_mm"])
    fewest = _minimum_cuts(hi - lo, usable - BAND_MARGIN_MM, gap)

    # A direction that needs no cut is still offered one -- but only one, and
    # only on a panel that is being cut anyway.  A panel that already fits a
    # sheet is left whole; see `_plan_candidates`.
    if fewest:
        counts = list(range(fewest, fewest + EXTRA_CUTS + 1))
    else:
        counts = [0, 1] if needs_cuts else [0]

    def ranked(positions: Sequence[float], count: int
               ) -> list[tuple[float, tuple[int, ...], tuple[float, ...]]]:
        scored: list[tuple[float, tuple[int, ...], tuple[float, ...]]] = []
        for combination in _cut_sets(panel, direction, positions, count, options):
            widths = _band_widths(lo, hi, combination, gap)
            if any(width < MIN_BAND_MM for width in widths):
                continue
            efficiency = sum(_band_efficiency(w, usable, spacing) for w in widths) / len(widths)
            signature = tuple(int(round(w / 25.0)) for w in widths)
            scored.append((efficiency, signature, combination))
        scored.sort(key=lambda item: (-item[0], item[2]))
        return scored

    # The position list is rebuilt per cut count, because the ceiling on how
    # many combinations may be enumerated is a ceiling on how many positions
    # may be offered, and that tightens fast as the count rises.  Rebuilding is
    # cheap: the only expensive part of `_positions` is the chord length of each
    # candidate cut, and those are cached on the panel.
    kept: list[tuple[float, ...]] = []
    tidy_kept: list[tuple[float, ...]] = []
    for count in counts:
        limit = _position_limit(count)
        scored = ranked(_positions(panel, direction, masters, options, limit), count)

        seen: set[tuple[int, ...]] = set()
        for item in scored:
            if item[1] in seen:
                continue
            seen.add(item[1])
            kept.append(item[2])
            if len(seen) >= MAX_SETS_PER_AXIS:
                break
        if centre_mm is None:
            continue

        # Only a few hundred are re-scored for tidiness: a wide panel has tens
        # of thousands of feasible sets and scoring all of them would cost more
        # than the sieve it is rescuing arrangements from.  Which few hundred
        # matters, though.  Taking the best-packing ones alone misses the
        # symmetric sets entirely -- they pack in the sixties while the top two
        # hundred are all above ninety -- so the genuinely mirrored sets are
        # taken first, whatever they packed like, and the best-packing few
        # hundred are added to them for the sets that are tidy in the other two
        # terms.  Both slices come off a list that is already in a fixed order,
        # so this stays deterministic.
        pool = ranked(_positions(panel, direction, masters, options, limit, centre_mm), count)
        axis = centre_mm + panel.boat_shift_mm
        mirrored = ([item for item in pool if _is_mirrored(item[2], axis)]
                    if direction == ALONG else [])
        tidy = sorted(mirrored[:TIDY_SIEVE_POOL] + pool[:TIDY_SIEVE_POOL], key=lambda item: (
            -_set_shape(panel, direction, item[2], masters, options, centre_mm), item[2]))
        added = 0
        for item in tidy:
            if item[1] in seen:
                continue
            seen.add(item[1])
            tidy_kept.append(item[2])
            added += 1
            if added >= TIDY_EXTRA_PER_AXIS:
                break
    return kept, tidy_kept


def _plan_candidates(panels: Sequence[_Panel], masters: tuple[np.ndarray, np.ndarray],
                     rotation: np.ndarray, options: dict[str, Any], say: Progress,
                     deadline: float | None = None,
                     centre_mm: float | None = None) -> None:
    """Fill in every panel's shortlist of cut sets, best first.

    A panel that already fits a sheet gets exactly one candidate -- no cuts at
    all.  That is a rule and not an optimisation: cutting a part that does not
    need cutting adds a join the fabricator has to make, has to hide and has to
    trust, and no packing gain is worth that.

    Everything else is cut for real and ranked on the result, because guessing
    from band widths turned out to be worth very little.  Measured on the
    cached boat: of a hundred and sixteen arrangements, the four that nested
    onto four sheets instead of five or six were ranked 1st, 2nd, 9th and 10th
    by the total bounding-box area of the pieces they produced -- and scattered
    through the middle of the list by the band score.  So the band score is used
    only to pick which arrangements are worth cutting, and how compact the
    pieces actually came out decides which are worth nesting.

    Total bounding-box area is also, read the other way round, exactly the
    RECTANGULARITY term: the pieces' own area is fixed by the panel, so the
    arrangement with the smallest total box is the one whose pieces fill their
    boxes best.  So this sieve was already ranking on one of the three things
    the user asked for, and it did not have to be replaced -- only told about
    the other two, which is what the reserved share in `_quota` does.

    Nesting slots are shared out by area, because on a deck one panel is the
    cockpit sole and the others are strips of toe rail: an equal share would
    spend most of the budget deciding where to cut the strips.

    `deadline` bounds the whole of this, panel by panel and cut by cut.  A panel
    reached after the clock has gone gets the plainest shortlist there is -- the
    fewest bands that can fit, all the same finished width, in whichever
    direction is over size -- so it is still cut somewhere sensible rather than
    not at all.
    """

    # At weight nought the shortlists must be the ones the pure-waste button
    # always built, to the bit: no mirrored positions offered and no extra
    # slots handed out.  That is what makes "turn tidiness off" mean exactly
    # "the old button" rather than "roughly the old button", and it is what
    # lets the tests measure what tidiness cost by running the same search
    # twice.
    if float(options["seam_tidiness_weight"]) <= 0.0:
        centre_mm = None

    total_area = sum(panel.area_mm2 for panel in panels) or 1.0
    for panel in panels:
        if deadline is not None and time.monotonic() > deadline:
            panel.candidates = _fallback_candidates(panel, options)
            say(f"Panel {panel.panel_id}: out of time to search, using an even split")
            continue
        gap = float(options["seam_gap_mm"])
        needs_cuts = (
            _minimum_cuts(panel.width_mm, _usable(ALONG, options) - BAND_MARGIN_MM, gap) > 0
            or _minimum_cuts(panel.length_mm, _usable(ACROSS, options) - BAND_MARGIN_MM, gap) > 0
        )
        if not needs_cuts:
            panel.candidates = [()]
            say(f"Panel {panel.panel_id} already fits a sheet -- leaving it whole")
            continue

        along_sets, along_tidy = _ranked_sets(panel, ALONG, masters, options,
                                              needs_cuts, centre_mm)
        across_sets, across_tidy = _ranked_sets(panel, ACROSS, masters, options,
                                                needs_cuts, centre_mm)

        def pairs(along: Sequence[tuple[float, ...]],
                  across: Sequence[tuple[float, ...]]) -> list[tuple[Cut, ...]]:
            """Every combination of the two directions, spread across both.

            Both lists are best-first within each cut count, so walking the grid
            diagonally -- by the sum of the two ranks -- spreads the
            arrangements that get cut across both lists instead of taking every
            across set for the single best along set and none for any other.
            """

            grid: list[tuple[int, int, int, tuple[Cut, ...]]] = []
            for a_index, along_cuts in enumerate(along):
                for c_index, across_cuts in enumerate(across):
                    cuts = tuple([Cut(ALONG, offset) for offset in along_cuts]
                                 + [Cut(ACROSS, offset) for offset in across_cuts])
                    grid.append((a_index + c_index, a_index, c_index, cuts))
            grid.sort(key=lambda item: (item[0], item[1], item[2]))
            return [item[3] for item in grid]

        scored: list[tuple[float, int, float, tuple, tuple[Cut, ...], float]] = []
        tidy_scored: list[tuple[float, int, float, tuple, tuple[Cut, ...], float]] = []

        def cut_and_score(cuts_list: Sequence[tuple[Cut, ...]], limit: int,
                          into: list) -> None:
            for cuts in cuts_list[:limit]:
                if deadline is not None and into and time.monotonic() > deadline:
                    break                   # keep what has been cut so far
                split = _split(panel, cuts, masters, rotation, options)
                if split.dropped or not split.pieces:
                    continue                # this cut shaves a sliver off an edge
                compactness = sum(width * length for width, length in split.extents)
                length_mm = sum(_cut_length_mm(panel, cut, masters) for cut in cuts)
                untidy = (0.0 if centre_mm is None else
                          _panel_shape(panel, cuts, split, masters, options,
                                       centre_mm).penalty_percent(1.0))
                into.append((compactness, len(cuts), length_mm,
                             tuple((cut.direction, round(cut.offset_mm, 6)) for cut in cuts),
                             cuts, untidy))

        cut_and_score(pairs(along_sets, across_sets), MAX_SPLIT_CANDIDATES, scored)
        scored.sort(key=lambda item: item[:4])

        share = max(MIN_PANEL_CANDIDATES,
                    int(round(CANDIDATE_BUDGET * panel.area_mm2 / total_area)))
        limit = min(MAX_PANEL_CANDIDATES, share)
        candidates = _quota(scored, limit)
        extras: list[tuple[Cut, ...]] = []

        # And then the tidy extras, ON TOP of the full quota rather than out of
        # it: the arrangements above are exactly the ones a weight-nought search
        # would have looked at, and nothing here may take one of them away. The
        # tidy pool is every arrangement that involves at least one mirrored cut
        # set, plus the ones the compactness quota above could not fit.
        if centre_mm is not None:
            # Every arrangement that uses a tidy cut set in at least one
            # direction, walked ROW BY ROW: each of the best tidy sets against
            # every set in the other direction, before the next tidy set.
            #
            # Two things had to change here and both were measured on the
            # cached boat.  Pairing the two full lists instead of only the tidy
            # ones puts every pair that involves a tidy set behind all four
            # hundred packing-by-packing pairs, and none survives the ceiling.
            # And walking what is left diagonally, by the sum of the two ranks,
            # is a PACKING heuristic -- it spends a fixed budget evenly over two
            # lists that are both ordered by how well they pack.  One of these
            # lists is ordered by how tidy it is instead, and the arrangement
            # that wins here pairs the fifth-tidiest along set with the
            # twenty-ninth-best across set: a rank sum of thirty-two, which the
            # diagonal reaches only after three hundred and seventy-five other
            # pairs.  With either mistake in place, not one symmetric along-cut
            # pair was ever cut, at any weight, and the whole setting did
            # nothing.
            tidy_grid: list[tuple[Cut, ...]] = []
            for along_cuts in along_tidy:
                for across_cuts in list(across_sets) + list(across_tidy):
                    tidy_grid.append(tuple([Cut(ALONG, o) for o in along_cuts]
                                           + [Cut(ACROSS, o) for o in across_cuts]))
            for across_cuts in across_tidy:
                for along_cuts in along_sets:
                    tidy_grid.append(tuple([Cut(ALONG, o) for o in along_cuts]
                                           + [Cut(ACROSS, o) for o in across_cuts]))
            cut_and_score(tidy_grid, TIDY_SPLIT_CANDIDATES, tidy_scored)
            # Ranked by COMPACTNESS, not by tidiness, and that is not a slip.
            # Every arrangement in this pool is already tidy -- it was built
            # from a mirrored or edge-aligned cut set, which is the only way in
            # -- so what is left to choose between them is which will actually
            # nest, and compactness is this module's measured answer to that
            # (see `_plan_candidates`).  Ranking them by tidiness AGAIN picks
            # the twelve tidiest, which on the cached boat were twelve pairings
            # of two good along sets with across sets that need five sheets,
            # while the pairing that fits on four -- and wastes less material
            # than the pure-waste winner -- sat further down the list.
            tidy_scored.sort(key=lambda item: item[:4])
            taken = {_cut_signature(cuts) for cuts in candidates}
            for cuts in _quota(tidy_scored, TIDY_EXTRA_PER_PANEL):
                if _cut_signature(cuts) in taken:
                    continue
                extras.append(cuts)
                taken.add(_cut_signature(cuts))

        # Assigned rather than appended to, so planning the same panels twice --
        # which nothing in the pipeline does, but every test harness eventually
        # will -- cannot quietly double the shortlist.
        panel.tidy_candidates = extras
        panel.candidates = candidates or _fallback_candidates(panel, options)
        extra = (f" and {len(panel.tidy_candidates)} tidier one(s)"
                 if panel.tidy_candidates else "")
        say(f"Panel {panel.panel_id}: {len(panel.candidates)} seam arrangement(s) to try{extra}")


def _even_bands(lo: float, hi: float, count: int, gap: float) -> list[float]:
    """`count` cuts that leave every band the SAME FINISHED width.

    Not the same thing as dividing the span into equal parts, and the
    difference is an envelope bug rather than a nicety.  Cutting at
    lo + span * i / (count + 1) leaves the two outer bands a half gap wider
    than the inner ones -- they only lose kerf on one side -- so the widest
    band comes out at span / (count + 1) - gap / 2 while `_minimum_cuts`
    guarantees only (span - count * gap) / (count + 1).  The gap between those
    two is gap * (count - 1) / (2 * (count + 1)), which is 1.75 mm at seven
    cuts, and a panel sized so that the minimum cut count only just works then
    produces a band 1.25 mm OUTSIDE the envelope after the margin.  Swept over
    every span from 500 to 8000 mm at the default 6 mm gap: the naive split
    overflows on 388 of them, worst case 1.75 mm; this one on none of them.

    Equal finished bands come from B = (span - count * gap) / (count + 1), the
    same B `_minimum_cuts` is built on, so the two now agree by construction
    rather than nearly.
    """

    if count <= 0:
        return []
    band = ((hi - lo) - count * gap) / (count + 1)
    return [lo + band + gap / 2.0 + index * (band + gap) for index in range(count)]


def _fallback_candidates(panel: _Panel, options: dict[str, Any]) -> list[tuple[Cut, ...]]:
    """The plainest arrangement that could work for one panel: the fewest cuts
    the geometry demands, all bands the same finished width, in whichever
    directions are over size.

    It is what a person would draw with a straightedge, and it is what a panel
    gets when the search ran out of clock before reaching it.  Cutting a deck
    into even bands is never the best answer and is almost always a workable
    one, which is the right shape for a fallback -- but "workable" has to
    include fitting the envelope, so the bands are equal AFTER the kerf and not
    before it.  See `_even_bands`.

    It is also, as it happens, the most symmetric arrangement there is on a
    panel the boat runs down the middle of, which is the right accident.
    """

    gap = float(options["seam_gap_mm"])
    cuts: list[Cut] = []
    for direction in (ALONG, ACROSS):
        lo, hi = _extent(panel, direction)
        count = _minimum_cuts(hi - lo, _usable(direction, options) - BAND_MARGIN_MM, gap)
        for offset in _even_bands(lo, hi, count, gap):
            cuts.append(Cut(direction, offset))
    return [tuple(cuts)]


def _quota(scored: Sequence[tuple[float, int, float, tuple, tuple[Cut, ...], float]],
           limit: int) -> list[tuple[Cut, ...]]:
    """`limit` candidates, best first, with every cut count represented.

    Compactness alone is biased towards cutting more: a toe rail lies across the
    sheet frame at an angle, so its bounding box is mostly empty, and chopping
    it into three shrinks the total box area every time.  Taken at face value
    that would put three cuts on a strip that needs one, and the fabricator
    would get two joins they never had to make.  So each cut count keeps a share
    of the slots and the sweep decides between them on the real objective, where
    material removed by a needless kerf counts against it.

    Nothing about tidiness happens here, on purpose: whatever this returns is
    exactly what a weight-nought search would have looked at, and the tidy
    extras are appended AFTER it by `_plan_candidates`.  Keeping the two apart
    is what makes the weight-nought shortlist a subset of every other one.
    """

    by_count: dict[int, list[tuple[Cut, ...]]] = {}
    for item in scored:
        by_count.setdefault(item[1], []).append(item[4])
    if not by_count:
        return []
    per_count = max(1, limit // len(by_count))
    chosen: list[tuple[Cut, ...]] = []
    taken: set[tuple] = set()
    for count in sorted(by_count):
        for cuts in by_count[count][:per_count]:
            chosen.append(cuts)
            taken.add(_cut_signature(cuts))

    # Whatever is left over goes to the best candidates overall, so a panel
    # whose answer really is "cut it four ways" still gets a proper look at it.
    for item in scored:
        if len(chosen) >= limit:
            break
        if _cut_signature(item[4]) in taken:
            continue
        chosen.append(item[4])
        taken.add(_cut_signature(item[4]))
    return chosen[:limit]


def _cut_signature(cuts: Sequence[Cut]) -> tuple:
    return tuple((cut.direction, round(cut.offset_mm, 6)) for cut in cuts)


def _panel_report(panels: Sequence[_Panel], layout: _Layout) -> list[dict[str, Any]]:
    """What was decided for each panel, in words as well as numbers."""

    report: list[dict[str, Any]] = []
    for panel in panels:
        cuts = layout.cuts.get(panel.panel_id, ())
        along_count = sum(1 for cut in cuts if cut.is_along)
        across_count = len(cuts) - along_count
        report.append({
            "panel_id": panel.panel_id,
            "along_cuts": along_count,
            "across_cuts": across_count,
            "note": _cut_words(along_count, across_count),
            "width_mm": round(panel.width_mm, 1),
            "length_mm": round(panel.length_mm, 1),
            "area_mm2": round(panel.area_mm2, 1),
        })
    return report


def _cut_words(along_count: int, across_count: int) -> str:
    parts = []
    if along_count:
        parts.append(f"{along_count} seam{'' if along_count == 1 else 's'} along the boat")
    if across_count:
        parts.append(f"{across_count} seam{'' if across_count == 1 else 's'} across the boat")
    if not parts:
        return "left whole -- it already fits a sheet"
    return " and ".join(parts)


# --------------------------------------------------------------------------
# cutting a panel, cheaply
#
# `sheets.split_panel` is the real thing and it does something this search does
# not need: it rebuilds every surviving arc from the sampled points, which costs
# a kd-tree per ring and about 145 ms on a deck panel.  The search only needs
# outlines to nest, so it does the same boolean -- the same kerf, the same
# minimum area, the same ordering -- and keeps the result as plain polylines.
# That is 10 ms instead of 145, which is the difference between two hundred
# candidates and fifteen.  Nothing the user is shown comes from it; the winner
# goes back through `sheets.split_panel` in `_confirm`.


def _split(panel: _Panel, cuts: tuple[Cut, ...], masters: tuple[np.ndarray, np.ndarray],
           rotation: np.ndarray, options: dict[str, Any]) -> _Split:
    """The pieces one cut set leaves, with each piece's size in sheet axes.

    Cached on the panel: a cut set is evaluated once however many times the
    search puts it in front of a different arrangement of the other panels.
    """

    cached = panel.pieces_cache.get(cuts)
    if cached is not None:
        return cached

    along, across = masters
    gap = float(options["seam_gap_mm"])
    minimum_area = float(options["min_piece_area_mm2"])
    polygon = panel.polygon

    if cuts:
        min_x, min_y, max_x, max_y = polygon.bounds
        reach = float(math.hypot(max_x - min_x, max_y - min_y)) + 10.0
        kerfs = []
        for cut in cuts:
            direction = along if cut.is_along else across
            normal = across if cut.is_along else along
            centre = normal * cut.offset_mm
            kerfs.append(LineString([centre - direction * reach, centre + direction * reach])
                         .buffer(gap / 2.0, cap_style=2, join_style=2))
        remainder = polygon.difference(unary_union(kerfs))
        if remainder.is_empty:
            parts: list[Polygon] = []
        elif isinstance(remainder, MultiPolygon):
            parts = list(remainder.geoms)
        else:
            parts = [remainder]
    elif isinstance(polygon, MultiPolygon):
        # `loop_polygon` repairs a self-touching outline with buffer(0), which
        # can hand back two pieces of panel that only meet at a point.  They are
        # already separate parts, so treat them as such rather than losing one.
        parts = list(polygon.geoms)
    else:
        parts = [polygon]

    kept = [part for part in parts if part.area >= minimum_area]
    # A dropped part is only this cut set's fault when there was a cut.  An
    # uncut panel that already arrives in two pieces is the fitter's business,
    # and refusing every layout because of it would leave the button with
    # nothing to offer.
    dropped = (len(parts) - len(kept)) if cuts else 0

    pieces: list[Piece] = []
    extents: list[tuple[float, float]] = []
    area = 0.0
    for index, part in enumerate(sorted(kept, key=lambda g: (-g.area, g.bounds))):
        shell = np.asarray(part.exterior.coords, dtype=float)[:-1]
        if len(shell) < 3:
            dropped += 1
            continue
        # Measured before simplifying: a millimetre of simplification is
        # invisible to the nester but it is exactly the size of the margin the
        # envelope test is working with, and shrinking a piece before measuring
        # it would be marking your own homework.
        sheet_xy = shell @ rotation.T
        extents.append((float(sheet_xy[:, 0].max() - sheet_xy[:, 0].min()),
                        float(sheet_xy[:, 1].max() - sheet_xy[:, 1].min())))
        simple = part.simplify(PROXY_SIMPLIFY_MM) if PROXY_SIMPLIFY_MM else part
        if simple.is_empty or simple.geom_type != "Polygon":
            simple = part
        pieces.append(Piece(
            piece_id=(f"P{panel.panel_id}" if not cuts and len(kept) == 1
                      else f"P{panel.panel_id}-{index + 1}"),
            panel_id=panel.panel_id,
            outer=_ring_loop(np.asarray(simple.exterior.coords, dtype=float)[:-1]),
            holes=[_ring_loop(np.asarray(ring.coords, dtype=float)[:-1])
                   for ring in simple.interiors
                   if len(np.asarray(ring.coords)) >= 4],
            area_mm2=float(part.area), from_seam=bool(cuts),
        ))
        area += float(part.area)

    split = _Split(pieces=pieces, extents=extents, dropped=dropped, area_mm2=area)
    panel.pieces_cache[cuts] = split
    return split


def _ring_loop(ring: np.ndarray) -> Loop:
    """A ring of points as a bulge-free `sheets.Loop`.

    Straight throughout on purpose: the arcs matter to the cutter and are
    rebuilt by the production split, but to the nester an arc is a run of points
    either way, and not rebuilding them is most of the saving.
    """

    return Loop(np.column_stack([ring, np.zeros(len(ring))]))


def _chord_mm(panel: _Panel, direction: str, offset: float,
              masters: tuple[np.ndarray, np.ndarray]) -> float:
    """How much joining one cut actually makes: the length of the cut line
    inside the panel, not the length of the line drawn on screen.

    A seam drawn right across the layout is still only a join where it crosses
    material, and a cut that clips a corner is a smaller commitment than one
    straight through the middle.  Both the "is this a real join" filter and the
    objective's last tie-break are asking that question, so both ask it here.
    """

    key = (direction, offset)
    cached = panel.length_cache.get(key)
    if cached is not None:
        return cached
    along, across = masters
    unit = along if direction == ALONG else across
    normal = across if direction == ALONG else along
    min_x, min_y, max_x, max_y = panel.polygon.bounds
    reach = float(math.hypot(max_x - min_x, max_y - min_y)) + 10.0
    centre = normal * offset
    line = LineString([centre - unit * reach, centre + unit * reach])
    length = float(panel.polygon.intersection(line).length)
    panel.length_cache[key] = length
    return length


def _cut_length_mm(panel: _Panel, cut: Cut, masters: tuple[np.ndarray, np.ndarray]) -> float:
    return _chord_mm(panel, cut.direction, cut.offset_mm, masters)


def _panel_shape(panel: _Panel, cuts: Sequence[Cut], split: _Split,
                 masters: tuple[np.ndarray, np.ndarray], options: dict[str, Any],
                 centre_mm: float | None) -> _Shape:
    """The three tidiness terms for ONE panel's cut set.

    Used by the sieve in `_plan_candidates`, which is looking at one panel at a
    time.  The whole-job version in `_evaluate` measures the same three things
    over every panel at once, which is the one that decides anything -- a deck
    is symmetric or not as a deck, not panel by panel.
    """

    placed = [_placed(panel, cut.direction, cut.offset_mm, masters, options) for cut in cuts]
    gap = float(options["seam_gap_mm"])
    floor = sum(_minimum_cuts(hi - lo, _usable(direction, options) - BAND_MARGIN_MM, gap)
                for direction, (lo, hi) in ((ALONG, _extent(panel, ALONG)),
                                            (ACROSS, _extent(panel, ACROSS))))
    return _Shape(
        symmetry=_symmetry(placed, centre_mm),
        alignment=_alignment(placed, options),
        rectangularity=_rectangularity(
            split.area_mm2, sum(width * length for width, length in split.extents)),
        joins=_joins(len(cuts), floor),
    )


# --------------------------------------------------------------------------
# the search


def _evaluate(panels: Sequence[_Panel], chosen: dict[int, tuple[Cut, ...]],
              masters: tuple[np.ndarray, np.ndarray], rotation: np.ndarray,
              options: dict[str, Any], nest_options: dict[str, Any],
              centre_mm: float | None = None,
              seam_floor: int = 0) -> _Layout | None:
    """One whole arrangement, cut and nested.

    Returns None for an arrangement that would throw material away -- a cut that
    leaves a part under `min_piece_area_mm2` is a cut that shaves a sliver off
    an edge, and a layout that quietly loses a piece of the deck is not a
    candidate at any waste percentage.

    The three tidiness terms are measured over the WHOLE arrangement rather
    than panel by panel, which is what lets the port toe rail's cut and the
    starboard one's be a mirror pair even though they are two separate panels
    nested two and a half metres apart on the layout.  See the module docstring
    on the boat-plan frame.
    """

    pieces: list[Piece] = []
    oversize: list[str] = []
    placed: list[_Placed] = []
    area = 0.0
    bbox_area = 0.0
    seam_count = 0
    seam_length = 0.0
    usable_w = float(options["max_part_width_mm"])
    usable_l = float(options["max_part_length_mm"])

    for panel in panels:
        cuts = chosen[panel.panel_id]
        split = _split(panel, cuts, masters, rotation, options)
        if split.dropped:
            return None
        pieces.extend(split.pieces)
        area += split.area_mm2
        bbox_area += sum(width * length for width, length in split.extents)
        seam_count += len(cuts)
        for cut in cuts:
            seam_length += _cut_length_mm(panel, cut, masters)
            placed.append(_placed(panel, cut.direction, cut.offset_mm, masters, options))
        for piece, (width, length) in zip(split.pieces, split.extents):
            if width > usable_w + 1e-9 or length > usable_l + 1e-9:
                oversize.append(piece.piece_id)

    if not pieces:
        return None

    _sheets, summary, _warnings = nesting.nest(pieces, rotation, nest_options)
    for piece_id in summary["unplaced_piece_ids"]:
        if piece_id not in oversize:
            oversize.append(piece_id)

    sheet_count = int(summary["sheet_count"])
    sheet_area = float(options["sheet_width_mm"]) * float(options["sheet_length_mm"])
    waste = 1.0 - area / (sheet_count * sheet_area) if sheet_count else 1.0
    shape = _Shape(symmetry=_symmetry(placed, centre_mm),
                   rectangularity=_rectangularity(area, bbox_area),
                   alignment=_alignment(placed, options),
                   joins=_joins(seam_count, seam_floor))
    weight = float(options["seam_tidiness_weight"])
    return _Layout(cuts=dict(chosen), oversize=sorted(oversize), sheets=sheet_count,
                   waste=waste, seam_count=seam_count, seam_length_mm=seam_length,
                   piece_count=len(pieces), shape=shape,
                   tidiness_penalty_percent=shape.penalty_percent(weight))


def _search(panels: Sequence[_Panel], masters: tuple[np.ndarray, np.ndarray],
            rotation: np.ndarray, options: dict[str, Any],
            deadline: float, say: Progress,
            centre_mm: float | None = None,
            seam_floor: int = 0) -> tuple[int, list[_Layout], bool]:
    """Sweep each panel's shortlist in turn, keeping whatever is best so far.

    Waste is GLOBAL -- the nester packs every panel's pieces onto the same
    sheets -- but the piece SHAPES are per panel, so the arrangement is searched
    one panel at a time against the real nester with the other panels held
    where they are.  Panels are swept largest first because the big one decides
    almost everything, and the sweep repeats until a whole pass fails to
    improve anything.

    Inside a panel there is no such separation: which along-cuts pay depends on
    which across-cuts are there (on the cached boat two across-cuts turn a
    five-sheet job into a four-sheet one, but only next to the right pair of
    along-cuts), so a panel's candidates are whole cut sets and the first sweep
    walks all of them.

    Two hills, not one
    ------------------
    A hill climb only ever finds the arrangements ON ITS PATH, and the path is
    decided by the objective.  Climb the blended cost alone and the search can
    walk right past the four-sheet arrangement it would have found on waste --
    measured on the cached boat, that is exactly what happened: at weight 0.35
    it settled on five sheets and 51.2% waste, tidier in every term and worse
    in the only two that are supposed to outrank tidiness.  Nothing in the
    ordering was wrong; the four-sheet arrangement had simply never been built.

    So the sweep is run TWICE, sharing one cache and one pool of results:

      1. the PURE-WASTE climb, over `panel.candidates` only, with the ceiling
         and the sweeps this button has always had.  It sees exactly what it
         would see at weight nought and therefore finds exactly what it would
         find at weight nought -- which is the guarantee the whole feature
         rests on, and the reason the tidy arrangements are kept in a separate
         list rather than appended to this one.  Appended, they would eat this
         climb's evaluation ceiling and cut it off in a different place at
         every weight.

      2. the TIDINESS climb, over both lists, on the blended key, STARTING FROM
         WHERE THE FIRST ONE FINISHED.  Starting from the winner rather than
         from scratch is not an economy, it is the point: a symmetric cut on
         the big panel only pays next to the right cuts on the others, and
         sweeping the big panel first from a cold start judges every symmetric
         arrangement against toe rails cut in the wrong place.  Measured on the
         cached boat -- from a cold start, all twenty-four symmetric
         arrangements it built needed five sheets; from the waste winner, the
         same search finds one on four sheets that also wastes less material.

    Both pools are ranked together on the blended key, which sorts oversize and
    sheets ahead of tidiness, so the answer can be tidier than the pure-waste
    one but can never cost a sheet.

    Returns how many arrangements were actually evaluated, the distinct layouts
    best first, and whether the CLOCK ran out.  Stopping at `MAX_EVALUATIONS` is
    not "out of budget": it happens in the same place every run, which is the
    whole point of it, so it is not reported as a budget failure.
    """

    nest_options = dict(options)
    nest_options["sample_step_mm"] = PROXY_NEST_SAMPLE_MM
    nest_options["nest_step_mm"] = PROXY_NEST_STEP_MM

    order = sorted(panels, key=lambda panel: (-panel.area_mm2, panel.panel_id))
    start = {panel.panel_id: panel.candidates[0] for panel in panels}
    seen: dict[tuple, _Layout | None] = {}
    kept: dict[tuple, _Layout] = {}
    out_of_time = False

    def measure(chosen: dict[int, tuple[Cut, ...]]) -> _Layout | None:
        key = _cuts_key(chosen)
        if key in seen:
            return seen[key]
        layout = _evaluate(panels, chosen, masters, rotation, options, nest_options,
                           centre_mm, seam_floor)
        seen[key] = layout
        if layout is not None:
            kept[key] = layout
        return layout

    def climb(key_of: Callable[[_Layout], tuple], ceiling: int,
              begin: dict[int, tuple[Cut, ...]], tidy: bool) -> _Layout | None:
        nonlocal out_of_time
        current = dict(begin)
        best = measure(current)
        stop = False
        for _sweep in range(MAX_SWEEPS):
            improved_this_sweep = False
            for panel in order:
                choices = (list(panel.candidates) + list(panel.tidy_candidates)
                           if tidy else panel.candidates)
                if len(choices) <= 1:
                    continue
                for candidate in choices:
                    if time.monotonic() > deadline:
                        out_of_time = stop = True
                        break
                    if len(seen) >= ceiling:
                        stop = True
                        break
                    trial = dict(current)
                    trial[panel.panel_id] = candidate
                    layout = measure(trial)
                    if layout is None:
                        continue
                    if best is None or key_of(layout) < key_of(best):
                        best = layout
                        improved_this_sweep = True
                if stop:
                    break
                if best is not None:
                    # Whatever this panel settled on stays put while the next
                    # panel is swept, so each sweep is a walk downhill and never
                    # a swap that undoes the last panel's decision.
                    current = dict(best.cuts)
                    say(f"Tried {len(seen)} arrangement(s); best so far {best.sheets} sheet(s), "
                        f"{best.waste * 100.0:.1f}% waste, {best.seam_count} seam(s)")
                else:
                    say(f"Tried {len(seen)} arrangement(s); none of them can be cut yet")
            if stop or not improved_this_sweep:
                break
        return best

    best = climb(lambda layout: layout.waste_key, MAX_EVALUATIONS, start, False)
    tidy_wanted = (float(options["seam_tidiness_weight"]) > 0.0
                   and any(panel.tidy_candidates for panel in panels))
    # Out of clock is the one reason not to go on; running into the evaluation
    # ceiling is not, because the tidiness climb has a ceiling of its own and
    # the pure-waste answer is already safely in the pool by now.
    if tidy_wanted and not out_of_time:
        say("Looking again for an arrangement that is tidier for the same number of sheets")
        climb(lambda layout: layout.key, MAX_EVALUATIONS + TIDY_EVALUATIONS,
              dict(best.cuts) if best is not None else start, True)

    ranked = sorted(kept.values(), key=lambda layout: layout.key)
    return len(seen), ranked, out_of_time


# --------------------------------------------------------------------------
# turning cuts into seams


def _seams_for(panels: Sequence[_Panel], layout: _Layout,
               masters: tuple[np.ndarray, np.ndarray]) -> list[Seam]:
    """The chosen cuts as real seams in the placed frame.

    Direction comes straight from `seamsnap.master_directions`, which is where
    the corrector's own masters come from, so a seam placed here is EXACTLY
    along the boat or EXACTLY square across it and the corrector has nothing
    left to do to it.  `mode` records which, so the seam re-derives its
    direction if the grain angle is changed later instead of being frozen at
    today's axis.

    The endpoints span the panel, taken from where the cut line actually enters
    and leaves the material, so the line the user sees on the flat view is the
    join they will be making.  `split_panel` extends every seam past the panel
    before cutting, so nothing depends on these endpoints being exact -- they
    are for the eye.
    """

    along, across = masters
    by_id = {panel.panel_id: panel for panel in panels}
    seams: list[Seam] = []
    for panel_id in sorted(layout.cuts):
        panel = by_id[panel_id]
        counts = {ALONG: 0, ACROSS: 0}
        for cut in sorted(layout.cuts[panel_id], key=lambda c: (c.direction, c.offset_mm)):
            counts[cut.direction] += 1
            direction = along if cut.is_along else across
            normal = across if cut.is_along else along
            centre = normal * cut.offset_mm
            start, end = _span(panel, centre, direction)
            seams.append(Seam(
                seam_id=f"auto-{panel_id}-{cut.direction}-{counts[cut.direction]}",
                x1=float(start[0]), y1=float(start[1]),
                x2=float(end[0]), y2=float(end[1]),
                panel_id=panel_id, snap=True,
                raw=(float(start[0]), float(start[1]), float(end[0]), float(end[1])),
                mode=cut.direction, angle_deg=None,
            ))
    return seams


def _span(panel: _Panel, centre: np.ndarray, direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where a cut line enters and leaves a panel.

    Falls back to the panel's bounding extent when the clip comes back empty,
    which it can for a cut that only grazes a corner: the seam still has to have
    two ends, and a zero-length seam would be dropped by everything downstream.
    """

    min_x, min_y, max_x, max_y = panel.polygon.bounds
    reach = float(math.hypot(max_x - min_x, max_y - min_y)) + 10.0
    line = LineString([centre - direction * reach, centre + direction * reach])
    clipped = panel.polygon.intersection(line)
    if not clipped.is_empty:
        parts = list(getattr(clipped, "geoms", [clipped]))
        distances = []
        for part in parts:
            for point in np.asarray(getattr(part, "coords", []), dtype=float):
                if len(point) >= 2:
                    distances.append(float(np.dot(point[:2] - centre, direction)))
        if distances:
            return centre + direction * min(distances), centre + direction * max(distances)
    return centre - direction * reach, centre + direction * reach


# --------------------------------------------------------------------------
# the exact confirmation
#
# Everything above is a proxy: coarser sampling, a coarser nest grid, and cut
# outlines with their arcs left as polylines.  Nothing above is ever reported.
# This is where the numbers the user reads come from, and it is the same call
# the Export Sheets button makes.


def _confirm(run_dir: Path, config: dict[str, Any], seams: Sequence[Seam],
             options: dict[str, Any], panels: Sequence[_Panel] | None = None,
             masters: tuple[np.ndarray, np.ndarray] | None = None,
             centre_mm: float | None = None) -> dict[str, Any]:
    """Run one seam set through the production pipeline and measure it.

    `sheetjob.plan` straightens the seams before it cuts with them, exactly as
    it will when the sheets are exported, so a seam this button placed that the
    corrector then slides onto a nearby fitted edge is measured where it will
    actually be cut -- not where the search put it.  That applies to the
    tidiness terms as much as to the waste: they are measured from the seams
    the plan came back with, so a cut the search put 8 mm off a console wall is
    scored as being ON the wall, because that is where the cutter will put it.
    """

    started = time.monotonic()
    result = sheetjob.plan(run_dir, config, seams=list(seams), write_files=False)
    elapsed = time.monotonic() - started
    return _metrics(result, options, elapsed, panels, masters, centre_mm)


def _measured_shape(result: dict[str, Any], options: dict[str, Any],
                    panels: Sequence[_Panel], masters: tuple[np.ndarray, np.ndarray],
                    centre_mm: float | None) -> _Shape:
    """The three tidiness terms of a job the production pipeline has just cut.

    Measured from the plan's OWN output, not from the cuts the search chose:

      * rectangularity from the real pieces' areas over the boxes the nester
        measured them into, which are in sheet axes already;
      * symmetry and alignment from `result["seams"]`, the seams AS CUT, after
        the corrector has straightened them and slid them onto whatever fitted
        edges it found.

    Each seam is classified by its own geometry rather than by its stored
    `mode`, so a hand-drawn seam that was never given a mode is measured on the
    same footing as one this module placed -- which is what makes the before
    and after numbers comparable at all.  A seam that is neither along nor
    across within `EDGE_TOLERANCE_DEG` is a diagonal, contributes to no term,
    and is simply not counted.
    """

    along, across = masters
    limit = math.cos(math.radians(EDGE_TOLERANCE_DEG))
    by_id = {panel.panel_id: panel for panel in panels}

    placed: list[_Placed] = []
    for row in result.get("seams") or []:
        start = np.array([float(row["x1"]), float(row["y1"])])
        end = np.array([float(row["x2"]), float(row["y2"])])
        span = end - start
        length = float(np.hypot(*span))
        if length < 1e-9:
            continue
        unit = span / length
        if abs(float(np.dot(unit, along))) >= limit:
            direction = ALONG
        elif abs(float(np.dot(unit, across))) >= limit:
            direction = ACROSS
        else:
            continue
        normal = across if direction == ALONG else along
        offset = float(np.dot((start + end) / 2.0, normal))
        # A seam stored with no panel applies wherever it crosses, so ask the
        # geometry which panel that is: the one it cuts the most of. Guessing
        # the primary panel instead would put a toe-rail seam two and a half
        # metres from where it really sits once the nest offset is undone.
        panel = by_id.get(row.get("panel_id"))
        if panel is None:
            panel = max(panels, key=lambda p: _chord_mm(p, direction, offset, masters),
                        default=None)
            if panel is None or _chord_mm(panel, direction, offset, masters) <= 0.0:
                continue
        placed.append(_placed(panel, direction, offset, masters, options))

    area = 0.0
    bbox_area = 0.0
    areas = {row["piece_id"]: float(row["area_mm2"]) for row in result.get("pieces") or []}
    for sheet in result.get("sheets") or []:
        for placement in sheet.get("placements") or []:
            piece_area = areas.get(placement["piece_id"])
            if piece_area is None:
                continue
            area += piece_area
            bbox_area += float(placement["width_mm"]) * float(placement["length_mm"])

    return _Shape(symmetry=_symmetry(placed, centre_mm),
                  rectangularity=_rectangularity(area, bbox_area),
                  alignment=_alignment(placed, options),
                  joins=_joins(int(result["seam_count"]), _minimum_seams(panels, options)))


def _metrics(result: dict[str, Any], options: dict[str, Any], elapsed_s: float,
             panels: Sequence[_Panel] | None = None,
             masters: tuple[np.ndarray, np.ndarray] | None = None,
             centre_mm: float | None = None) -> dict[str, Any]:
    """A plan result as the numbers the objective is made of.

    Waste is over WHOLE sheets -- 1016 x 2032 mm, what the shop pays for -- and
    over the pieces that actually went ON those sheets.  When the job is
    cuttable that is every piece, and the figure is the one the user asked for.
    When it is not, counting an unplaced piece's area against sheets it was
    never on gives a waste percentage that is meaninglessly small and can even
    come out negative, which reads as a good result for a job that cannot be
    cut at all.  Pieces that will not fit are counted first and separately
    instead, where a failure belongs.

    The blended `cost_percent` is what the exact objective sorts on, and it is
    built here from the exact waste and the exactly measured shape, so the
    search and the report are minimising the same quantity.
    """

    sheet_area = float(options["sheet_width_mm"]) * float(options["sheet_length_mm"])
    summary = result["summary"]
    sheet_count = int(summary["sheet_count"])
    blocked = sorted({item["piece_id"] for item in result["oversize"]}
                     | {item["piece_id"] for item in result.get("refused") or []}
                     | set(summary["unplaced_piece_ids"]))
    unplaced = set(summary["unplaced_piece_ids"])
    area = sum(float(piece["area_mm2"]) for piece in result["pieces"]
               if piece["piece_id"] not in unplaced)
    waste = 1.0 - area / (sheet_count * sheet_area) if sheet_count else 1.0
    weight = float(options["seam_tidiness_weight"])
    shape = (_measured_shape(result, options, panels, masters, centre_mm)
             if panels and masters is not None
             else _Shape(None, None, None, None))
    penalty = shape.penalty_percent(weight)
    return {
        "status": result["status"],
        "sheet_count": sheet_count,
        "piece_count": int(result["piece_count"]),
        "seam_count": int(result["seam_count"]),
        "waste_percent": round(waste * 100.0, 2),
        "utilisation": list(summary["utilisation"]),
        "oversize": blocked,
        "oversize_detail": list(result["oversize"]),
        "piece_area_mm2": round(area, 1),
        "envelope_mm": [float(options["max_part_width_mm"]), float(options["max_part_length_mm"])],
        "sheet_mm": [float(options["sheet_width_mm"]), float(options["sheet_length_mm"])],
        "shape": shape.to_dict(),
        "tidiness_weight": round(weight, 3),
        "tidiness_penalty_percent": round(penalty, 3),
        "cost_percent": round(waste * 100.0 + penalty, 3),
        "elapsed_s": round(elapsed_s, 2),
    }


def _exact_key(metrics: dict[str, Any]) -> tuple:
    """The objective again, on measured numbers -- same order, same reasons.

    Note which number the third term is: the BLENDED cost, so the exact
    ordering and the proxy ordering are the same function of the same things.
    A search that ranked on waste-plus-tidiness and then confirmed on waste
    alone would hand back an arrangement it had not chosen, which is the exact
    failure `_waste_bucket` was written to prevent.
    """

    return (len(metrics["oversize"]), metrics["sheet_count"],
            _waste_bucket(metrics["cost_percent"]), metrics["seam_count"])
