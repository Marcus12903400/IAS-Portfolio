import json

import numpy as np
import rhino3dm
from shapely.geometry import Polygon

from autodeck2.config import load_config
from autodeck2.ingest import ingest_run, read_reference_geometry
from autodeck2.layout import compute_layout
from autodeck2.outline3dm import read_outline_summary, write_outline
from autodeck2.pipeline import panel_sources
from autodeck2.teak import generate_teak
from autodeck2 import v1compat
from helpers import fillet_rectangle, make_result


def _build_run(tmp_path, mode="nest", with_teak=False):
    result = make_result([(1, "primary", (0.0, 0.0), 1000.0, 600.0, 0.0), (2, "secondary", (100.0, 50.0), 200.0, 150.0, 0.0),
                          (3, "secondary", (900.0, 0.0), 300.0, 300.0, 0.0)])
    config = load_config()
    placements, _ = compute_layout(panel_sources(result), config, mode)
    teak = None
    if with_teak:
        pattern = v1compat.load_v1_config()["pattern"]
        teak, _info = generate_teak(result, placements, pattern)
    info = write_outline(tmp_path, result, placements, teak, config)
    return result, placements, info


def test_outline_layers_locks_units_and_no_overlap(tmp_path):
    result, placements, info = _build_run(tmp_path)
    summary = read_outline_summary(tmp_path / "outline.3dm")
    assert "Millimeters" in summary["units"]
    layers = summary["layers"]
    for pid in (1, 2, 3):
        assert layers[f"RAW::PANEL_{pid}::OUTER"]["locked"] and layers[f"RAW::PANEL_{pid}::OUTER"]["count"] == 1
        assert layers[f"USER_CAM::PANEL_{pid}"]["locked"] is False and layers[f"USER_CAM::PANEL_{pid}"]["count"] == 0
        assert layers[f"REF::PANEL_{pid}::ROBUST"]["locked"]
    assert layers["USER_CORNERS"]["locked"] is False and layers["USER_CORNERS"]["count"] == 0
    assert layers["RAW::PANEL_1::OBSTACLES"]["count"] == 1
    assert layers["LABELS"]["count"] == 3
    outers = summary["outers"]
    polys = {pid: Polygon(pts) for pid, pts in outers.items()}
    for a in polys:
        for b in polys:
            if a < b:
                assert polys[a].intersection(polys[b]).area <= 1.0, (a, b)
    assert info["overlaps"] == []
    panels = json.loads((tmp_path / "panels.json").read_text())
    assert panels["primary_panel_id"] == 1
    assert any(p["placement"]["moved_to_row"] for p in panels["panels"] if p["panel_id"] == 2)
    assert (tmp_path / "outline.dxf").is_file()


def test_teak_lines_are_63_5_apart_and_written(tmp_path):
    result, placements, info = _build_run(tmp_path, with_teak=True)
    summary = read_outline_summary(tmp_path / "outline.3dm")
    assert summary["layers"]["TEAK::PANEL_1"]["count"] > 5
    # read teak lines of panel 1 back and check the spacing between neighbours
    model = rhino3dm.File3dm.Read(str(tmp_path / "outline.3dm"))
    names = {i: l.Name for i, l in enumerate(model.Layers)}
    ys = []
    for obj in model.Objects:
        if names[obj.Attributes.LayerIndex] == "TEAK::PANEL_1":
            line = obj.Geometry.Line
            ys.append(0.5 * (line.From.Y + line.To.Y))
    ys = np.unique(np.round(sorted(ys), 3))
    gaps = np.diff(ys)
    assert np.allclose(gaps, 63.5, atol=1e-3) or np.allclose(gaps[gaps > 1], 63.5, atol=1e-3)


def test_ingest_end_to_end_from_written_outline(tmp_path):
    result, placements, info = _build_run(tmp_path)
    reference = read_reference_geometry(tmp_path / "outline.3dm")
    assert set(reference) == {1, 2, 3}
    assert len(reference[1]["obstacles"]) == 1
    # Draw on USER_CAM::PANEL_1: the primary is 1000x600 centred at origin.
    model = rhino3dm.File3dm.Read(str(tmp_path / "outline.3dm"))
    index = {l.Name: i for i, l in enumerate(model.Layers)}
    attrs = rhino3dm.ObjectAttributes(); attrs.LayerIndex = index["USER_CAM::PANEL_1"]
    for seg in fillet_rectangle(996.0, 596.0, 40.0, origin=(-498.0, -298.0)):
        if seg.kind == "LINE":
            model.Objects.AddLine(rhino3dm.Point3d(*seg.start, 0), rhino3dm.Point3d(*seg.end, 0), attrs)
        else:
            s = seg.sample(1.0); mid = s[len(s) // 2]
            arc = rhino3dm.Arc(rhino3dm.Point3d(*seg.start, 0), rhino3dm.Point3d(*mid, 0), rhino3dm.Point3d(*seg.end, 0))
            model.Objects.AddCurve(rhino3dm.ArcCurve.CreateFromArc(arc), attrs)
    # obstacle cut-out drawn as a hole exactly on the raw obstacle square (250,0)+-40
    hole_attrs = rhino3dm.ObjectAttributes(); hole_attrs.LayerIndex = index["USER_CAM::PANEL_1"]
    for seg in fillet_rectangle(80.0, 80.0, 5.0, origin=(210.0, -40.0)):
        if seg.kind == "LINE":
            model.Objects.AddLine(rhino3dm.Point3d(*seg.start, 0), rhino3dm.Point3d(*seg.end, 0), hole_attrs)
        else:
            s = seg.sample(1.0); mid = s[len(s) // 2]
            arc = rhino3dm.Arc(rhino3dm.Point3d(*seg.start, 0), rhino3dm.Point3d(*mid, 0), rhino3dm.Point3d(*seg.end, 0))
            model.Objects.AddCurve(rhino3dm.ArcCurve.CreateFromArc(arc), hole_attrs)
    drawn = tmp_path / "drawn.3dm"
    assert model.Write(str(drawn), 8)

    outcome = ingest_run(tmp_path, drawn, load_config())
    assert outcome.status == "VALID", outcome.summary
    assert outcome.final_dxf is not None and outcome.final_dxf.is_file()
    assert (tmp_path / "calibration.json").is_file() and (tmp_path / "calibration_report.md").is_file()
    panel = outcome.summary["panels"][0]
    outer_stats = panel["loops"][0]["stats"]
    assert outer_stats["tangent_failure_count"] == 0
    dev = outer_stats["deviation"]
    assert dev["forbidden_side_penetration"]["max_mm"] < 1e-6  # drawn 2 mm inside the raw wall
    assert 2.0 <= dev["deck_side_shortfall"]["p95_mm"] <= 45.0
    assert panel["loops"][1]["kind"].startswith("hole")
    assert outcome.summary["panels_without_drawing"] == [2, 3]
