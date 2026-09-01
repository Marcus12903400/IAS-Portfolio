import math
import random

import ezdxf
import numpy as np
import pytest
import rhino3dm
from shapely.geometry import Polygon

from autodeck2.calibration import Segment, arc_from_three_points, loop_statistics, signed_deviation
from autodeck2.config import load_config
from autodeck2.ingest import attach_corners, build_loops, order_loops, read_user_geometry, validate_panel, write_final_dxf
from helpers import fillet_rectangle


def _config():
    return load_config()


def test_signed_deviation_convention():
    square = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    inside = np.array([[50.0, 10.0]])   # 10 mm inside the wall
    outside = np.array([[50.0, -3.0]])  # 3 mm beyond the wall
    assert signed_deviation(inside, square, "outer")[0] == pytest.approx(10.0)
    assert signed_deviation(outside, square, "outer")[0] == pytest.approx(-3.0)
    # obstacle: inside the obstacle is forbidden
    assert signed_deviation(inside, square, "obstacle")[0] == pytest.approx(-10.0)
    assert signed_deviation(outside, square, "obstacle")[0] == pytest.approx(3.0)


def test_loop_builds_independent_of_order_and_direction():
    segments = fillet_rectangle(400.0, 200.0, 30.0)
    rng = random.Random(3)
    shuffled = list(segments)
    rng.shuffle(shuffled)
    shuffled = [s.reversed() if i % 2 else s for i, s in enumerate(shuffled)]
    loops, errors = build_loops(shuffled, 0.5, 0.05)
    assert errors == []
    assert len(loops) == 1
    outer, holes, order_errors = order_loops(loops)
    assert order_errors == [] and holes == []
    assert outer.is_ccw
    assert len(outer.segments) == 8
    stats = loop_statistics(outer, None, "outer", _config())
    assert stats["tangent_failure_count"] == 0
    assert stats["max_tangent_mismatch_non_corner_deg"] < 1e-6
    assert stats["max_gap_mm"] < 1e-9
    # CCW loop with convex fillets: every arc bulge positive
    assert all(s.bulge > 0 for s in outer.segments if s.kind == "ARC")
    assert stats["line_count"] == 4 and stats["arc_count"] == 4


def test_endpoint_snapping_within_tolerance_closes_small_gaps():
    segments = fillet_rectangle(400.0, 200.0, 30.0)
    segments[0].end = segments[0].end + np.array([0.3, -0.2])  # 0.36 mm off
    loops, errors = build_loops(segments, 0.5, 0.05)
    assert errors == [] and len(loops) == 1
    assert loops[0].joins()[0]["gap_mm"] < 1e-9


def test_gap_branch_and_duplicate_are_rejected_with_locations():
    base = fillet_rectangle(400.0, 200.0, 30.0)
    loops, errors = build_loops(base[:-1], 0.5, 0.05)
    assert loops == [] and any("gap" in e and "nearest other open end" in e for e in errors)

    branch = base + [Segment("LINE", base[0].end.copy(), base[0].end + np.array([0.0, 50.0]), source="stray")]
    loops, errors = build_loops(branch, 0.5, 0.05)
    assert loops == [] and any("branch" in e for e in errors)

    dup = base + [Segment("LINE", base[0].start.copy(), base[0].end.copy(), source="dup")]
    loops, errors = build_loops(dup, 0.5, 0.05)
    assert any("duplicate" in e for e in errors)


def test_sharp_corner_fails_unless_marked_on_user_corners():
    square = [
        Segment("LINE", np.array([0.0, 0.0]), np.array([100.0, 0.0]), source="a"),
        Segment("LINE", np.array([100.0, 0.0]), np.array([100.0, 100.0]), source="b"),
        Segment("LINE", np.array([100.0, 100.0]), np.array([0.0, 100.0]), source="c"),
        Segment("LINE", np.array([0.0, 100.0]), np.array([0.0, 0.0]), source="d"),
    ]
    loops, _ = build_loops(square, 0.5, 0.05)
    outer, _, _ = order_loops(loops)
    stats = loop_statistics(outer, None, "outer", _config())
    assert stats["tangent_failure_count"] == 4
    corners = np.array([[100.0, 0.0], [100.0, 100.0], [0.0, 100.0], [0.0, 0.0]])
    marked = attach_corners(outer, corners, 5.0)
    assert len(marked) == 4
    stats = loop_statistics(outer, None, "outer", _config())
    assert stats["tangent_failure_count"] == 0
    assert stats["intentional_corner_count"] == 4


def test_holes_are_ordered_inside_outer_and_clockwise():
    outer = fillet_rectangle(400.0, 200.0, 30.0)
    hole = fillet_rectangle(60.0, 40.0, 10.0, origin=(100.0, 80.0))
    loops, errors = build_loops(outer + hole, 0.5, 0.05)
    assert errors == [] and len(loops) == 2
    out, holes, order_errors = order_loops(loops)
    assert order_errors == [] and len(holes) == 1
    assert out.is_ccw and not holes[0].is_ccw


def test_final_dxf_roundtrip_preserves_arcs():
    outer = fillet_rectangle(400.0, 200.0, 30.0)
    loops, _ = build_loops(outer, 0.5, 0.05)
    out, holes, _ = order_loops(loops)
    from autodeck2.ingest import PanelIngest
    import tempfile, pathlib
    path = pathlib.Path(tempfile.mkdtemp()) / "final.dxf"
    info = write_final_dxf(path, {1: PanelIngest(1, out, holes)})
    assert info["polyline_count"] == 1 and info["all_closed"] and info["units"] == 4
    doc = ezdxf.readfile(str(path))
    pl = list(doc.modelspace().query("LWPOLYLINE"))[0]
    pts = list(pl.get_points("xyb"))
    arcs = [b for (_, _, b) in pts if abs(b) > 1e-12]
    assert len(arcs) == 4
    # bulge -> radius: chord = 2 r sin(sweep/2), sweep = 4 atan(b); fillet chord = 30*sqrt(2)
    sweep = 4 * math.atan(arcs[0]); chord = 30.0 * math.sqrt(2)
    assert chord / (2 * math.sin(abs(sweep) / 2)) == pytest.approx(30.0, abs=1e-6)


def _drawn_3dm(path, segments, corners=()):
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Millimeters
    layer = rhino3dm.Layer(); layer.Name = "USER_CAM::PANEL_1"; li = model.Layers.Add(layer)
    clayer = rhino3dm.Layer(); clayer.Name = "USER_CORNERS"; ci = model.Layers.Add(clayer)
    attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = li
    for seg in segments:
        if seg.kind == "LINE":
            model.Objects.AddLine(rhino3dm.Point3d(*seg.start, 0), rhino3dm.Point3d(*seg.end, 0), attrs)
        else:
            mid = seg.sample(1.0)[len(seg.sample(1.0)) // 2]
            arc = rhino3dm.Arc(rhino3dm.Point3d(*seg.start, 0), rhino3dm.Point3d(*mid, 0), rhino3dm.Point3d(*seg.end, 0))
            model.Objects.AddCurve(rhino3dm.ArcCurve.CreateFromArc(arc), attrs)
    cattrs = rhino3dm.ObjectAttributes(); cattrs.LayerIndex = ci
    for c in corners:
        model.Objects.AddPoint(rhino3dm.Point3d(float(c[0]), float(c[1]), 0), cattrs)
    assert model.Write(str(path), 8)


def test_read_user_geometry_from_rhino_lines_arcs_and_corner_points(tmp_path):
    segments = fillet_rectangle(400.0, 200.0, 30.0)
    path = tmp_path / "drawn.3dm"
    _drawn_3dm(path, segments, corners=[(5.0, 5.0)])
    per_panel, corners, rejections = read_user_geometry(path, 0.01)
    assert rejections == []
    assert len(per_panel[1]) == 8
    assert corners.shape == (1, 2)
    loops, errors = build_loops(per_panel[1], 0.5, 0.05)
    assert errors == [] and len(loops) == 1
    outer, _, _ = order_loops(loops)
    stats = loop_statistics(outer, None, "outer", _config())
    assert stats["arc_count"] == 4 and stats["tangent_failure_count"] == 0
    arc = next(s for s in outer.segments if s.kind == "ARC")
    assert arc.radius_mm == pytest.approx(30.0, abs=1e-6)


def test_validate_panel_measures_against_raw_and_flags_obstacle_crossing():
    segments = fillet_rectangle(400.0, 200.0, 30.0)
    raw_outer = np.array([[-1.0, -1.0], [401.0, -1.0], [401.0, 201.0], [-1.0, 201.0]])  # 1 mm outside the drawing
    reference = {"outer": raw_outer, "obstacles": [np.array([[190.0, 90.0], [210.0, 90.0], [210.0, 110.0], [190.0, 110.0]])]}
    panel = validate_panel(1, segments, np.empty((0, 2)), reference, _config())
    assert panel.valid
    dev = panel.loops_report[0]["stats"]["deviation"]
    assert dev["forbidden_side_penetration"]["max_mm"] == pytest.approx(0.0, abs=1e-9)
    assert 1.0 <= dev["deck_side_shortfall"]["max_mm"] <= 1.0 + 30.0  # fillets cut the corners further inside
    # a drawing that runs through the obstacle is rejected
    crossing = [Segment("LINE", np.array([0.0, 100.0]), np.array([400.0, 100.0]), source="x"),
                Segment("LINE", np.array([400.0, 100.0]), np.array([400.0, 200.0]), source="y"),
                Segment("LINE", np.array([400.0, 200.0]), np.array([0.0, 200.0]), source="z"),
                Segment("LINE", np.array([0.0, 200.0]), np.array([0.0, 100.0]), source="w")]
    reference2 = {"outer": raw_outer, "obstacles": [np.array([[150.0, 60.0], [250.0, 60.0], [250.0, 140.0], [150.0, 140.0]])]}
    bad = validate_panel(1, crossing, np.array([[400.0, 100.0], [400.0, 200.0], [0.0, 200.0], [0.0, 100.0]]), reference2, _config())
    assert not bad.valid
    assert any("obstacle" in p for p in bad.loops_report[0]["problems"])
