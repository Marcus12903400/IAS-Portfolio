"""The geometry the app draws over the 3D model and in the flat view.

Two things are asserted here.  First, that reconstructing an LWPOLYLINE from its
bulges spends points where the shape curves and nowhere else -- the flat view
draws those points straight through to the screen, so the sagitta tolerance is
literally how round an arc looks.  Second, that lifting a flat curve onto the
scanned deck produces a line as smooth as the DXF it came from.

That second one is the fabricator-visible bug this file exists for: the same
outline that VCarve cuts as a fair curve used to look faceted on the 3D preview.
The cause was the lift, not the curve.  The development mesh is the scan's own
mesh -- ~5 mm triangles carrying a few tenths of a millimetre of photogrammetry
noise -- so reading one triangle per sample transferred that noise into the drawn
line: half a millimetre of wobble across a 3 mm chord is about 37 degrees of
direction change.  bridge._make_lifter now fits a plane to the neighbourhood
instead, and bridge._triangle_lifter is kept as the unsmoothed reference these
tests measure it against.
"""

import json
import math
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from autodeck_app import bridge


def turn_angles_deg(points: np.ndarray) -> np.ndarray:
    """Direction change at each interior vertex of a polyline.  Zero-length
    segments are dropped first, otherwise a repeated point reads as a random
    turn."""

    steps = np.diff(np.asarray(points, dtype=float), axis=0)
    length = np.linalg.norm(steps, axis=1)
    steps = steps[length > 1e-9] / length[length > 1e-9][:, None]
    return np.degrees(np.arccos(np.clip((steps[:-1] * steps[1:]).sum(axis=1), -1.0, 1.0)))


def contains_exactly(points: np.ndarray, vertex) -> bool:
    return bool((np.asarray(points)[:, :2] == np.asarray(vertex, dtype=float)[:2]).all(axis=1).any())


def fake_development(uv, xyz, faces=()) -> SimpleNamespace:
    """Just the two attributes the lifters read, so a degenerate panel can be
    built without running the engine."""

    return SimpleNamespace(
        uv_mm=np.asarray(uv, dtype=float).reshape(-1, 2),
        mesh=SimpleNamespace(base_vertices_mm=np.asarray(xyz, dtype=float).reshape(-1, 3),
                             faces=np.asarray(faces, dtype=np.int32).reshape(-1, 3)))


# ---------------------------------------------------------------------------
# bulge sampling


def test_circle_from_two_bulges_is_sampled_inside_the_sagitta_tolerance():
    """A full circle is two 180-degree bulges.  Every sample must sit on the true
    circle, and the chords between them must never sag further inside it than the
    tolerance asked for -- that sag is the whole visual error budget."""

    radius = 500.0
    sag = 0.05
    ring = bridge._sample_bulge_polyline([(radius, 0.0, 1.0), (-radius, 0.0, 1.0)], True, sag_mm=sag)

    assert np.allclose(np.linalg.norm(ring, axis=1), radius, atol=1e-6)
    midpoints = (ring[:-1] + ring[1:]) / 2.0
    worst_sag = radius - float(np.linalg.norm(midpoints, axis=1).min())
    assert worst_sag <= sag + 1e-9
    # ...and not wastefully far inside it either: doubling the chord count would
    # quarter the sag, so anything under a quarter of the budget is over-sampling.
    assert worst_sag > sag / 4.0

    assert contains_exactly(ring, (radius, 0.0))
    assert contains_exactly(ring, (-radius, 0.0))
    assert np.array_equal(ring[0], ring[-1])            # the displayed ring closes


def test_a_reflex_bulge_sweeps_the_long_way_round():
    """A bulge over 1 is an arc of more than half a turn, and the fitted outlines
    really do contain them -- the widest in the cached run sweeps 207 degrees.
    The arc centre falls on the other side of the chord for these, which is what
    the sign flip in _sample_bulge_polyline is for; get it wrong and a rounded
    cutout comes back as the small arc instead of the large one, which is a hole
    cut in the wrong place."""

    radius = 100.0
    for sweep_deg in (200.0, 270.0, 350.0):
        theta = math.radians(sweep_deg)
        bulge = math.tan(theta / 4.0)                   # over 1 for every one of these
        start = np.array([radius, 0.0])
        end = radius * np.array([math.cos(theta), math.sin(theta)])
        arc = bridge._sample_bulge_polyline([(start[0], start[1], bulge), (end[0], end[1], 0.0)], False)

        # on the circle, and starting and ending exactly on the DXF's own vertices
        assert np.allclose(np.linalg.norm(arc, axis=1), radius, atol=1e-6)
        assert np.allclose(arc[0], start) and np.allclose(arc[-1], end)
        # and it really went the long way round: unwrapped, the angle swept is theta
        swept = np.unwrap(np.arctan2(arc[:, 1], arc[:, 0]))
        assert abs(math.degrees(swept[-1] - swept[0]) - sweep_deg) < 1e-6
        midpoints = (arc[:-1] + arc[1:]) / 2.0
        assert radius - float(np.linalg.norm(midpoints, axis=1).min()) <= 0.05 + 1e-9

    # the same sweep with a negative bulge is the mirror image, not a different shape
    clockwise = bridge._sample_bulge_polyline([(radius, 0.0, -math.tan(math.radians(270.0) / 4.0)),
                                               (0.0, radius, 0.0)], False)
    assert np.allclose(np.linalg.norm(clockwise, axis=1), radius, atol=1e-6)
    assert np.unwrap(np.arctan2(clockwise[:, 1], clockwise[:, 0]))[-1] < 0.0


def test_a_zero_length_segment_does_not_divide_by_zero():
    """Repeated vertices are legal in a DXF and the fitter emits them where two
    fitted pieces meet at a point.  A zero-length chord has no radius to derive,
    so the bulge on it has to be dropped rather than divided by."""

    with_repeat = bridge._sample_bulge_polyline(
        [(0.0, 0.0, 0.5), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)], False)
    assert np.isfinite(with_repeat).all()
    assert contains_exactly(with_repeat, (0.0, 0.0))
    assert contains_exactly(with_repeat, (10.0, 0.0))

    for degenerate, closed in ((([(1.0, 2.0, 0.4)] * 3), True), ([(1.0, 2.0, 0.4)], True),
                               ([(3.0, 4.0, 0.0), (3.0, 4.0, 0.9)], False)):
        out = bridge._sample_bulge_polyline(degenerate, closed)
        assert len(out) >= 2 and np.isfinite(out).all()


def test_large_radius_arc_is_capped_by_the_maximum_step():
    """On a 5 m radius the sagitta rule alone would allow 45 mm chords, which
    would visibly cut a corner off a curve seen from across the boat."""

    radius = 5000.0
    ring = bridge._sample_bulge_polyline([(radius, 0.0, 1.0), (-radius, 0.0, 1.0)], True,
                                         sag_mm=0.05, max_step_mm=25.0)
    chords = np.linalg.norm(np.diff(ring, axis=0), axis=1)
    assert chords.max() <= 25.0 + 1e-6


def test_the_step_cap_never_sets_the_sagitta_the_screen_sees():
    """Which of the two rules is the visual budget, pinned down so the next
    person to reach for max_step_mm knows what it can and cannot buy.

    The chord count is max(sagitta rule, step cap), so the tighter rule wins and
    the two cross where a chord of max_step_mm has exactly sag_mm of sag, at
    r = max_step^2 / (8 * sag): 1562 mm for the shipped 25 mm and 0.05 mm.  Below
    that radius the sagitta rule already asks for shorter chords and the cap does
    nothing at all; above it the cap makes chords shorter than the budget needs.
    Either way the worst sag reaching the screen is sag_mm and nothing else --
    measured over all 239 bulge arcs in the cached runs' final_auto.dxf files,
    the worst achieved sagitta is 0.0500 mm at a 25 mm cap, at a 12 mm cap and
    with no cap at all.  What the cap does change is the point count: 2719 arc
    points uncapped, 3100 at 25 mm, 3965 at 12 mm.
    """

    crossover = 25.0 ** 2 / (8.0 * 0.05)
    for radius, cap_should_bind in ((300.0, False), (1000.0, False), (5000.0, True), (9000.0, True)):
        longest = {}
        for cap in (25.0, 12.0, 1e9):
            ring = bridge._sample_bulge_polyline([(radius, 0.0, 1.0), (-radius, 0.0, 1.0)], True,
                                                 sag_mm=0.05, max_step_mm=cap)
            midpoints = (ring[:-1] + ring[1:]) / 2.0
            sag = radius - float(np.linalg.norm(midpoints, axis=1).min())
            assert sag <= 0.05 + 1e-9, f"r {radius} mm, cap {cap} mm: {sag:.4f} mm is over the budget"
            longest[cap] = float(np.linalg.norm(np.diff(ring, axis=0), axis=1).max())
        assert (radius > crossover) is cap_should_bind
        if not cap_should_bind:
            # below the crossover the cap changes nothing, not even the count
            assert longest[25.0] == pytest.approx(longest[1e9])


def test_straight_runs_cost_two_points_however_long_they_are():
    """The old fixed 3 mm step turned a 3 m straight edge into 1000 collinear
    points; a line needs two."""

    line = bridge._sample_bulge_polyline([(0.0, 0.0, 0.0), (3000.0, 0.0, 0.0)], False)
    assert len(line) == 2
    assert np.allclose(line, [[0.0, 0.0], [3000.0, 0.0]])

    square = [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (1000.0, 1000.0, 0.0), (0.0, 1000.0, 0.0)]
    ring = bridge._sample_bulge_polyline(square, True)
    assert len(ring) == 5                               # four corners plus the closing repeat
    for vertex in square:
        assert contains_exactly(ring, vertex)


def test_rounded_rectangle_keeps_every_vertex_and_only_bends_on_the_arcs():
    """A real fitted panel loop: straight sides with tangent fillets.  Every DXF
    vertex has to survive exactly -- a corner that moves is a corner the router
    cuts in the wrong place."""

    bulge = math.tan(math.pi / 8.0)                     # a quarter turn
    r, w, h = 50.0, 1000.0, 600.0
    loop = [(r, 0.0, 0.0), (w - r, 0.0, bulge), (w, r, 0.0), (w, h - r, bulge),
            (w - r, h, 0.0), (r, h, bulge), (0.0, h - r, 0.0), (0.0, r, bulge)]
    ring = bridge._sample_bulge_polyline(loop, True, sag_mm=0.05)

    for vertex in loop:
        assert contains_exactly(ring, vertex)
    # Four quarter-arcs of r=50 at a 0.05 mm sag need ~18 chords each; the four
    # straight sides need none.  Well under the 3 mm step's ~1000.
    assert 60 < len(ring) < 120
    assert turn_angles_deg(ring).max() < 10.0           # tangent fillets: no visible corner anywhere


# ---------------------------------------------------------------------------
# lifting a real run onto its scan


def cached_run() -> Path | None:
    """The newest run that has a fitted DXF and still has its scan on disk.
    These tests are about real photogrammetry noise, so there is nothing useful
    to assert on a synthetic panel -- a made-up mesh is exactly smooth."""

    roots = [Path(os.environ["AUTODECK_RUNS_DIR"]) if os.environ.get("AUTODECK_RUNS_DIR") else None,
             Path(__file__).resolve().parents[1] / "outputs" / "runs",
             Path.home() / "AutoDeck2" / "outputs" / "runs"]
    for root in roots:
        if root is None or not root.is_dir():
            continue
        for run_dir in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not ((run_dir / "run.json").is_file() and (run_dir / "final_auto.dxf").is_file()):
                continue
            try:
                meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if Path(str(meta.get("input_path", ""))).is_file():
                return run_dir
    return None


@pytest.fixture(scope="module")
def run_view():
    run_dir = cached_run()
    if run_dir is None:
        pytest.skip("no cached run with a final_auto.dxf and its scan still on disk")
    return run_dir, bridge.load_run(run_dir, lambda message: None)


@pytest.fixture(scope="module")
def fitted_loops(run_view):
    """Every closed loop of final_auto.dxf, densified to 3 mm in the flat frame
    and unplaced into its panel's uv -- 3 mm because that is the spacing the
    original diagnosis measured at, so the numbers below are comparable to it."""

    run_dir, view = run_view
    loops, _pattern = bridge.read_final_dxf(run_dir / "final_auto.dxf")
    samples = []
    for pid in sorted(loops):
        if pid not in view.placements:
            continue
        for piece in loops[pid]:
            dense = bridge._densify(piece, 3.0)
            samples.append((pid, dense, bridge._unplace(view.placements[pid], dense)))
    assert samples, "the cached run has no fitted loops to measure"
    return samples


@pytest.fixture(scope="module")
def surface(run_view):
    """Two exact distance queries per panel, both straight from libigl so that
    neither shares any code with the thing being measured.

    to_mesh is the honest "is this point on the deck" question: the true
    point-to-triangle distance to the development's own 3D mesh.  off_footprint
    asks the same question of the *flat* mesh -- the uv coordinates with the same
    triangles -- and answers zero when the query lands on a triangle and the size
    of the gap when it does not.  That second one is needed because a fitted CAM
    curve is a fair arc and the scan's meshed border is ragged, so a good part of
    every loop lands just off the edge of the panel, where there is no surface to
    be on and the lift can only continue the one it has.
    """

    _run_dir, view = run_view
    igl = pytest.importorskip("igl", reason="the surface check needs libigl's exact point-mesh distance")
    prepared: dict[int, tuple] = {}

    def panel(pid: int):
        if pid not in prepared:
            development = view.result.panels[pid].development
            uv = np.asarray(development.uv_mm, dtype=float)[:, :2]
            xyz = np.ascontiguousarray(np.asarray(development.mesh.base_vertices_mm, dtype=float))
            faces = np.ascontiguousarray(np.asarray(development.mesh.faces, dtype=np.int32))
            flat = np.ascontiguousarray(np.column_stack([uv, np.zeros(len(uv))]))
            prepared[pid] = (xyz, flat, faces)
        return prepared[pid]

    def distance(query, vertices, faces) -> np.ndarray:
        squared, _face, _closest = igl.point_mesh_squared_distance(
            np.ascontiguousarray(np.asarray(query, dtype=float)), vertices, faces)
        return np.sqrt(np.maximum(np.asarray(squared, dtype=float), 0.0))

    def to_mesh(pid: int, world_xyz) -> np.ndarray:
        xyz, _flat, faces = panel(pid)
        return distance(world_xyz, xyz, faces)

    def off_footprint(pid: int, uv_xy) -> np.ndarray:
        _xyz, flat, faces = panel(pid)
        uv_xy = np.asarray(uv_xy, dtype=float)
        return distance(np.column_stack([uv_xy, np.zeros(len(uv_xy))]), flat, faces)

    return SimpleNamespace(to_mesh=to_mesh, off_footprint=off_footprint)


def test_plane_fit_lift_is_as_smooth_as_the_dxf_it_came_from(run_view, fitted_loops):
    """Where the triangle lift makes a ragged line out of a fair curve, the plane
    fit must not.  The bar is the flat curve itself: a 3 mm chord on a tight
    fillet genuinely turns a few degrees, and the lift is not allowed to add more
    than a degree of its own on top of that.

    Measured on 21kwcockpit-1 across all eleven fitted loops: the triangle lift's
    p90 turn is between 7 and 85 degrees, while the plane fit's p90 lands within
    0.20 deg of the flat DXF curve's own p90 on every single loop.  Four loops
    are above 3 deg after the fit (4.34, 3.75, 3.67, 3.54) and so is the flat
    curve each came from (4.38, 3.75, 3.66, 3.53) -- those are genuinely tight
    fillets where a 3 mm chord really does turn four degrees, not roughness,
    which is why the bar here is the flat curve and not a fixed number.
    """

    _run_dir, view = run_view
    checked = 0
    for pid, flat, uv in fitted_loops:
        triangle = bridge._triangle_lifter(view.result.panels[pid].development)(uv)
        rough = float(np.percentile(turn_angles_deg(triangle), 90))
        if rough <= 20.0:
            continue                                    # already smooth; nothing to prove
        checked += 1
        flat_turn = float(np.percentile(turn_angles_deg(flat), 90))
        smooth = float(np.percentile(turn_angles_deg(view.lifters[pid](uv)), 90))
        assert smooth <= flat_turn + 1.0, f"panel {pid}: lift adds {smooth - flat_turn:.2f} deg of its own"
        assert smooth < 0.25 * rough, f"panel {pid}: {rough:.1f} deg -> {smooth:.1f} deg is not enough"
        if flat_turn < 1.0:
            # A loop that is straight and gently curved in the flat -- here the
            # absolute number means something, and it is the headline claim.
            assert smooth < 3.0
    assert checked >= 1, "no loop in this run was rough enough to be worth measuring"


def test_smoothing_the_surface_does_not_round_the_outlines_own_corners(run_view, fitted_loops):
    """The reason the fix smooths the *surface* and not the polyline.  A moving
    average along the drawn curve was tried and rejected because it cut up to
    9.4 mm off genuine corners; a plane fit in uv cannot do that, because the
    corner lives in the DXF and the DXF is never touched.

    Measured: on the four loops that turn a real right angle, the flat curve's
    p99 turn is between 73.6 and 91.6 deg and the lifted curve's p99 comes back
    within 0.2 deg of it every time.
    """

    _run_dir, view = run_view
    cornered = 0
    for pid, flat, uv in fitted_loops:
        flat_corner = float(np.percentile(turn_angles_deg(flat), 99))
        if flat_corner < 45.0:
            continue                                    # no real corner on this loop
        cornered += 1
        lifted_corner = float(np.percentile(turn_angles_deg(view.lifters[pid](uv)), 99))
        assert abs(lifted_corner - flat_corner) < 2.0, (
            f"panel {pid}: a {flat_corner:.1f} deg corner came back as {lifted_corner:.1f} deg")
    assert cornered >= 1, "no loop in this run has a corner sharp enough to be worth measuring"


def test_smoothed_line_still_lies_on_the_deck(run_view, fitted_loops):
    """Smoothing the surface is only allowed to move the drawn line by the size
    of the noise it is removing.  Measured worst case on 21kwcockpit-1: median
    0.91 mm and p95 4.8 mm, both on panel 1's inner loop; panel 1's outer loop is
    0.01 mm median and 2.5 mm p95.  Half of the movement over 2 mm sits on the
    genuine corners of the fitted outline, where the triangle lift was itself
    jumping between triangles.
    """

    _run_dir, view = run_view
    for pid, _flat, uv in fitted_loops:
        triangle = bridge._triangle_lifter(view.result.panels[pid].development)(uv)
        moved = np.linalg.norm(view.lifters[pid](uv) - triangle, axis=1)
        assert float(np.median(moved)) < 1.0, f"panel {pid}: median move {np.median(moved):.2f} mm"
        assert float(np.percentile(moved, 95)) < 5.0, f"panel {pid}: p95 move {np.percentile(moved, 95):.2f} mm"


def test_the_smoothed_line_really_sits_on_the_scanned_surface(run_view, fitted_loops, surface):
    """Agreeing with the triangle lift is not the same as being on the deck, so
    this asks libigl directly instead.

    The samples split in two.  Where one lands over a triangle -- 6395 of the
    11478 samples on 21kwcockpit-1 -- the plane fit is on the surface to a median
    of 0.007 mm, a p99 of 0.093 mm and a worst case of 0.470 mm (panel 4, the one
    with real curvature across its neighbourhood).  The rest land *outside* the
    meshed footprint, by a median of 1.18 mm and up to 8.3 mm, because the CAM
    outline is a fair curve and the scan's meshed border is ragged; there the fit
    extrapolates its plane, which is the right thing to draw but is not a point
    on the mesh, so its distance to the mesh is the size of that gap and not an
    error in the lift.

    The claim that covers both halves is that the distance to the mesh is fully
    accounted for by the footprint gap: measured p99 0.084 mm and worst 0.470 mm
    over every sample of every loop.  The thresholds below are three to five
    times the measured worst -- loose enough not to be brittle, tight enough that
    a regression to millimetres of drift fails.
    """

    _run_dir, view = run_view
    for pid, _flat, uv in fitted_loops:
        lifted = view.lifters[pid](uv)
        gap = surface.off_footprint(pid, uv)
        distance = surface.to_mesh(pid, lifted)
        assert np.isfinite(distance).all()

        on_mesh = gap <= 1e-9
        assert on_mesh.any(), f"panel {pid}: no sample of this loop is over the mesh at all"
        assert float(np.percentile(distance[on_mesh], 99)) < 0.5, (
            f"panel {pid}: p99 {np.percentile(distance[on_mesh], 99):.3f} mm off the surface")
        assert float(distance[on_mesh].max()) < 2.0, (
            f"panel {pid}: worst {distance[on_mesh].max():.3f} mm off the surface")

        unexplained = np.abs(distance - gap)
        assert float(np.percentile(unexplained, 99)) < 0.5, (
            f"panel {pid}: p99 {np.percentile(unexplained, 99):.3f} mm that the footprint gap does not explain")
        assert float(unexplained.max()) < 1.5, (
            f"panel {pid}: worst {unexplained.max():.3f} mm that the footprint gap does not explain")


def test_the_lift_holds_up_where_the_neighbourhood_is_one_sided(run_view, surface):
    """The panel edge is the normal case for this code, not an edge case: the CAM
    outline runs 1.5 mm inside the detected border, so nearly every point the app
    draws on an outer loop has its 24-vertex neighbourhood pushed to one side by
    the rim.  A one-sided least-squares fit is extrapolating, and that is exactly
    where a plane fit is most likely to lift off the deck.

    Measured on 21kwcockpit-1: the neighbourhood's centroid sits up to 0.55 of
    the support radius away from the query point -- properly lopsided, a centred
    one would be near zero -- and on the most lopsided tenth of each outer loop
    the plane fit is still on the surface to a p99 of 0.365 mm and a worst case
    of 0.470 mm.
    """

    run_dir, view = run_view
    from scipy.spatial import cKDTree

    loops, _pattern = bridge.read_final_dxf(run_dir / "final_auto.dxf")
    worst_lopsided = 0.0
    for pid in sorted(loops):
        if pid not in view.placements:
            continue
        development = view.result.panels[pid].development
        uv_all = np.asarray(development.uv_mm, dtype=float)[:, :2]
        tree = cKDTree(uv_all)
        # the outer loop of a panel is the one covering the most ground
        outer = max(loops[pid], key=lambda piece: float(np.prod(piece.max(axis=0) - piece.min(axis=0))))
        uv = bridge._unplace(view.placements[pid], bridge._densify(outer, 3.0))

        span, index = tree.query(uv, k=min(24, len(uv_all)))
        span = np.asarray(span, dtype=float).reshape(len(uv), -1)
        index = np.asarray(index).reshape(len(uv), -1)
        # 0 means the neighbours surround the query point; 1 means they are all
        # to one side of it, out at the edge of the support radius.
        lopsided = np.linalg.norm(uv_all[index].mean(axis=1) - uv, axis=1) / np.maximum(span[:, -1], 1e-9)
        worst_lopsided = max(worst_lopsided, float(lopsided.max()))

        on_mesh = surface.off_footprint(pid, uv) <= 1e-9
        distance = surface.to_mesh(pid, view.lifters[pid](uv))[on_mesh]
        rim = np.argsort(lopsided[on_mesh])[-max(1, int(on_mesh.sum()) // 10):]
        assert float(np.percentile(distance[rim], 99)) < 0.5, (
            f"panel {pid}: p99 {np.percentile(distance[rim], 99):.3f} mm off the surface at the rim")
        assert float(distance[rim].max()) < 2.0, (
            f"panel {pid}: worst {distance[rim].max():.3f} mm off the surface at the rim")

    assert worst_lopsided > 0.3, (
        f"neighbourhoods are only {worst_lopsided:.2f} lopsided -- this run does not exercise the edge case")


def test_the_flat_layer_is_exactly_what_is_in_the_dxf(run_view):
    """The flat view labels itself "the exact VCarve file", so it is checked
    against ezdxf's own reading of that file rather than against bridge's.
    virtual_entities() expands each LWPOLYLINE into the LINEs and ARCs that ezdxf
    thinks its bulges mean, and the two directions below are the Hausdorff
    distance between the drawn polyline and those primitives.

    Overlay -> DXF is allowed only the output rounding.  _rounded keeps two
    decimals, so a point can move 0.005 mm on each axis, 0.00707 mm on the
    diagonal; the measured worst across the cached runs is 0.0069 mm.

    DXF -> overlay is allowed the sampling as well: a chord may sag sag_mm =
    0.05 mm inside its arc and is then rounded, so 0.057 mm.  The measured worst
    is 0.0547 mm.  Note this direction is measured against the drawn *segments*
    rather than the drawn points, because a straight run correctly has no
    interior points at all.
    """

    run_dir, view = run_view
    ezdxf = pytest.importorskip("ezdxf")

    primitives = []
    document = ezdxf.readfile(str(run_dir / "final_auto.dxf"))
    for entity in document.modelspace():
        if not bridge._DXF_PANEL_RE.search(str(entity.dxf.layer)):
            continue
        for part in (entity.virtual_entities() if entity.dxftype() == "LWPOLYLINE" else [entity]):
            if part.dxftype() == "LINE":
                primitives.append(("LINE", np.array([part.dxf.start.x, part.dxf.start.y]),
                                   np.array([part.dxf.end.x, part.dxf.end.y]), None))
            elif part.dxftype() == "ARC":
                primitives.append(("ARC", np.array([part.dxf.center.x, part.dxf.center.y]),
                                   float(part.dxf.radius),
                                   (math.radians(float(part.dxf.start_angle)),
                                    math.radians(float(part.dxf.end_angle)))))
    assert primitives, "final_auto.dxf has no panel geometry to compare against"

    layer = next((entry for entry in bridge.overlays(view, lambda message: None)["layers"]
                  if entry["id"] == "final_auto_file"), None)
    assert layer is not None, "the run has a final_auto.dxf but no layer drawn from it"
    drawn = [np.asarray(polyline, dtype=float) for polyline in layer["flat"]]

    def to_primitives(points: np.ndarray) -> np.ndarray:
        best = np.full(len(points), np.inf)
        for kind, anchor, size, sweep in primitives:
            if kind == "LINE":
                along = size - anchor
                t = np.clip(((points - anchor) @ along) / max(float(along @ along), 1e-12), 0.0, 1.0)
                gap = np.linalg.norm(anchor + t[:, None] * along - points, axis=1)
            else:
                start, end = sweep
                extent = (end - start) % (2.0 * math.pi)
                angle = (np.arctan2(points[:, 1] - anchor[1], points[:, 0] - anchor[0]) - start) % (2.0 * math.pi)
                ends = [anchor + size * np.array([math.cos(a), math.sin(a)]) for a in (start, end)]
                gap = np.where(angle <= extent + 1e-12,
                               np.abs(np.linalg.norm(points - anchor, axis=1) - size),
                               np.minimum(np.linalg.norm(points - ends[0], axis=1),
                                          np.linalg.norm(points - ends[1], axis=1)))
            best = np.minimum(best, gap)
        return best

    starts = np.vstack([polyline[:-1] for polyline in drawn])
    steps = np.vstack([polyline[1:] for polyline in drawn]) - starts
    lengths = np.maximum((steps * steps).sum(axis=1), 1e-12)

    def to_drawn(points: np.ndarray) -> np.ndarray:
        # against the drawn *segments*: a 750 mm straight edge is two points, and
        # a probe from its middle must measure zero, not 375 mm.
        out = np.empty(len(points))
        for i, point in enumerate(points):
            t = np.clip(((point - starts) * steps).sum(axis=1) / lengths, 0.0, 1.0)
            out[i] = np.linalg.norm(starts + t[:, None] * steps - point, axis=1).min()
        return out

    for polyline in drawn:
        assert float(to_primitives(polyline).max()) <= 0.0075, "the flat view is drawing something not in the DXF"

    for kind, anchor, size, sweep in primitives:
        if kind == "LINE":
            probe = anchor + np.linspace(0.0, 1.0, 25)[:, None] * (size - anchor)
        else:
            start, end = sweep
            extent = (end - start) % (2.0 * math.pi)
            angles = start + np.linspace(0.0, extent, max(8, int(extent * size / 2.0) + 2))
            probe = anchor + size * np.column_stack([np.cos(angles), np.sin(angles)])
        assert float(to_drawn(probe).max()) <= 0.10, "the flat view is missing part of the DXF"


def test_picking_a_lifted_point_gives_back_where_it_came_from(run_view):
    """The 3D seam tool's round trip: a point in the flat layout, lifted onto the
    model, clicked, and turned back into a flat point.  The samples are
    development vertices, i.e. points that really are on the panel's surface --
    a point off the edge of the mesh has no honest flat position to come back to.
    """

    _run_dir, view = run_view
    rng = np.random.default_rng(0)
    for pid in sorted(view.result.panels):
        if pid not in view.placements:
            continue
        uv_all = np.asarray(view.result.panels[pid].development.uv_mm, dtype=float)[:, :2]
        sample_uv = uv_all[rng.choice(len(uv_all), size=min(300, len(uv_all)), replace=False)]
        placed = view.placements[pid].apply(sample_uv)
        picks = bridge.pick_flat(view, view.lifters[pid](sample_uv))
        assert [p["panel_id"] for p in picks] == [pid] * len(picks)
        back = np.array([[p["x"], p["y"]] for p in picks])
        assert float(np.abs(back - placed).max()) < 1.0
        assert max(p["distance_mm"] for p in picks) < 1.0


def test_the_nearer_panel_wins_where_two_panels_are_close_together(run_view, surface):
    """Panels overlap in plan view -- a cockpit sole and the seat bases standing
    on it cover the same x and y and differ only in z -- so which panel the user
    clicked cannot be answered from the layout, only from the distance.

    Measured on 21kwcockpit-1: every one of panels 2 to 5 has points whose
    nearest other surface is 7 to 26 mm away.  Panel 5 comes within 7.4 mm of
    panel 1 and sits 35.6 mm from it on average.  All of that is inside
    PICK_MAX_MM, so a picker that took the first panel within tolerance, or one
    that went by the flat layout, would answer wrongly right here.
    """

    _run_dir, view = run_view
    rng = np.random.default_rng(1)
    contested = 0
    for pid in sorted(view.result.panels):
        if pid not in view.placements:
            continue
        vertices = np.asarray(view.result.panels[pid].development.mesh.base_vertices_mm, dtype=float)
        sample = vertices[rng.choice(len(vertices), size=min(200, len(vertices)), replace=False)]
        rival = np.full(len(sample), np.inf)
        for other in view.result.panels:
            if other != pid and other in view.placements:
                rival = np.minimum(rival, surface.to_mesh(other, sample))
        close = rival < bridge.PICK_MAX_MM
        if not close.any():
            continue                                    # this panel stands alone; nothing to contest
        contested += 1
        picks = bridge.pick_flat(view, sample[close])
        assert [p["panel_id"] for p in picks] == [pid] * len(picks), (
            f"panel {pid}: a point on its own surface went to another panel "
            f"{rival[close].min():.1f} mm away")
    assert contested >= 1, "no two panels in this run are close enough to contest a click"


def test_a_click_far_from_every_panel_is_reported_as_off_the_deck(run_view):
    """So the page can say so, instead of dropping a seam on the far side of the
    boat from where the user clicked."""

    _run_dir, view = run_view
    centre = np.asarray(view.result.panels[min(view.result.panels)].development.mesh.base_vertices_mm,
                        dtype=float).mean(axis=0)
    pick = bridge.pick_flat(view, (centre + [0.0, 0.0, 5000.0])[None, :])[0]
    assert pick["panel_id"] is None
    assert pick["distance_mm"] > bridge.PICK_MAX_MM


def test_building_every_overlay_is_fast_enough_for_every_page_load(run_view):
    """overlays() runs on the request thread every time the user opens a run, so
    a slow one is a spinner the fabricator sits and watches.  Measured on
    21kwcockpit-1 -- five panels, eleven fitted loops, 41k world points -- it is
    0.25 s cold and 0.19 s warm, which is where this bound came from."""

    _run_dir, view = run_view
    bridge.overlays(view, lambda message: None)          # the first call warms the DXF and 3dm reads
    started = time.perf_counter()
    result = bridge.overlays(view, lambda message: None)
    elapsed = time.perf_counter() - started
    assert result["layers"], "the cached run drew no layers at all"
    assert elapsed < 1.0, f"overlays() took {elapsed:.2f} s"


# ---------------------------------------------------------------------------
# degenerate input


def test_a_development_too_small_to_fit_a_plane_to_still_answers():
    """A panel that flattened to almost nothing must not take the whole page down
    with it.  There is no plane through two points, so the honest answer is the
    nearest vertex, and the lifter gives that rather than solving a singular
    system."""

    for uv, xyz in (([], []),
                    ([[0.0, 0.0]], [[1.0, 2.0, 3.0]]),
                    ([[0.0, 0.0], [10.0, 0.0]], [[0.0, 0.0, 1.0], [10.0, 0.0, 2.0]])):
        lift = bridge._make_lifter(fake_development(uv, xyz))
        for query in (np.empty((0, 2)), np.array([[0.0, 0.0], [3.0, 4.0]]), np.array([[1e6, -1e6]])):
            out = lift(query)
            assert out.shape[1] == 3
            assert np.isfinite(out).all()
            if not len(xyz):
                assert len(out) == 0
                continue
            assert len(out) == len(query)
            # with no plane to fit, whatever comes back has to be one of the
            # points it was given, not an extrapolation off into space
            assert np.isclose(out[:, None, :], np.asarray(xyz)[None, :, :]).all(axis=2).any(axis=1).all()


def test_a_neighbourhood_with_no_spread_does_not_produce_nonsense():
    """Duplicated vertices turn up wherever a scan has been stitched.  With every
    neighbour on the same spot, or strung out along a single line, the normal
    equations are singular in one or both directions and only the ridge keeps the
    solve alive -- so check the answer stays finite and stays near the data,
    instead of flying off."""

    coincident = fake_development(np.zeros((30, 2)), np.tile([1500.0, -600.0, 25.0], (30, 1)))
    line_uv = np.column_stack([np.arange(30.0) * 5.0, np.zeros(30)])
    collinear = fake_development(line_uv, np.column_stack([line_uv, np.full(30, 25.0)]))

    for development in (coincident, collinear):
        lift = bridge._make_lifter(development)
        for query in (np.empty((0, 2)), np.array([[0.0, 0.0], [3.0, 4.0], [-80.0, 15.0]])):
            out = lift(query)
            assert out.shape == (len(query), 3)
            assert np.isfinite(out).all()

    # Nothing constrains the gradient when every sample sits on one spot, so the
    # fit is free to tilt -- but it has to stay in the neighbourhood of the only
    # measurement it has rather than scaling away towards the origin.
    only = np.array([1500.0, -600.0, 25.0])
    assert np.linalg.norm(bridge._make_lifter(coincident)(np.array([[3.0, 4.0]]))[0] - only) < 0.1 * np.linalg.norm(only)


def test_an_empty_query_costs_nothing_and_returns_nothing(run_view):
    """The page asks for picks on an empty selection whenever the user clears a
    seam, and asks the lifter for an empty layer whenever a curve family is
    missing from the run."""

    _run_dir, view = run_view
    pid = min(view.result.panels)
    for empty in (np.empty((0, 2)), np.zeros((0, 2))):
        assert view.lifters[pid](empty).shape == (0, 3)
    for empty in (np.empty((0, 3)), []):
        assert bridge.pick_flat(view, empty) == []
    assert bridge.locator_for(view, pid)(np.empty((0, 3)))[0].shape == (0, 2)
