from __future__ import annotations

from pathlib import Path

import numpy as np

from autodeck.config import load_config
from autodeck.curve_fit import fit_manufacturing_curve
from autodeck.dxf_export import write_manufacturing_dxfs
from autodeck.models import ConditionedCurve, FeatureCurve, FlattenedCurve
from autodeck.v03_export import export_flattened_preview
from autodeck.v031_reports import (
    make_length_audit,
    write_curve_fit_report,
    write_development_report,
    write_flatten_report,
    write_manufacturing_report,
)


def _flat(points: np.ndarray, layer: str = "AUTODECK::SEAMS_HIGH_CONFIDENCE", closed: bool = False) -> FlattenedCurve:
    raw = FeatureCurve(layer, "synthetic", points.copy(), closed, 1.0)
    smooth = FeatureCurve(layer, "synthetic_conditioned", points.copy(), closed, 1.0)
    conditioned = ConditionedCurve(raw, "raw-synthetic", smooth, "smooth-synthetic", points.copy(), [], "GOOD")
    return FlattenedCurve(conditioned, "flat-synthetic", points.copy(), "FLAT_RAW_TEST", closed, "GOOD")


def test_stair_step_organic_shape_becomes_low_complexity_cubic_spline():
    random = np.random.default_rng(5)
    x = np.linspace(0.0, 2000.0, 1001)
    y = 80.0 * np.sin(x / 330.0) + 18.0 * np.sin(x / 105.0)
    y = np.round(y) + random.normal(0.0, 0.15, len(x))
    points = np.column_stack((x, y, np.zeros(len(x))))
    config = load_config()
    config["manufacturing_fit"]["target_fit_deviation_mm"] = 1.5
    config["manufacturing_fit"]["absolute_max_fit_deviation_mm"] = 2.0
    result = fit_manufacturing_curve(_flat(points), 1, config)
    assert result.status == "GOOD"
    assert result.metrics["span_type_counts"]["SPLINE"] == 1
    assert result.metrics["span_type_counts"]["POLYLINE"] == 0
    assert result.spans[0].degree == 3
    assert len(result.spans[0].control_points_mm) < len(points) / 20
    assert result.metrics["deviation"]["maximum_mm"] <= 1.5


def test_noisy_two_metre_straight_edge_is_one_native_line():
    random = np.random.default_rng(1)
    x = np.linspace(0.0, 2000.0, 801)
    points = np.column_stack((x, random.normal(0.0, 0.25, len(x)), np.zeros(len(x))))
    result = fit_manufacturing_curve(_flat(points), 1, load_config())
    assert result.metrics["span_type_counts"]["LINE"] == 1
    assert result.metrics["control_point_count"] == 2
    assert result.metrics["span_type_counts"]["POLYLINE"] == 0


def test_gentle_non_circular_two_metre_curve_is_spline_not_arc():
    random = np.random.default_rng(2)
    x = np.linspace(0.0, 2000.0, 801)
    y = 40.0 * np.sin(np.pi * x / 2000.0) + 12.0 * np.sin(2.0 * np.pi * x / 2000.0)
    points = np.column_stack((x, y + random.normal(0.0, 0.1, len(x)), np.zeros(len(x))))
    result = fit_manufacturing_curve(_flat(points), 1, load_config())
    assert result.status == "GOOD"
    assert result.metrics["span_type_counts"]["SPLINE"] == 1
    assert result.metrics["span_type_counts"]["ARC"] == 0
    assert result.metrics["span_type_counts"]["POLYLINE"] == 0


def test_true_corner_is_exact_and_not_smoothed_through():
    x = np.linspace(0.0, 500.0, 501)
    first = np.column_stack((x, 2.0 * np.sin(x / 100.0), np.zeros(len(x))))
    y = np.linspace(2.0, 500.0, 500)
    second = np.column_stack((500.0 + 2.0 * np.sin(y / 100.0), y, np.zeros(len(y))))
    result = fit_manufacturing_curve(_flat(np.vstack((first, second))), 1, load_config())
    assert len(result.anchor_indices) == 1
    assert len(result.spans) == 2
    corner = result.anchor_points_mm[0]
    assert np.array_equal(result.spans[0].sampled_points_mm[-1], corner)
    assert np.array_equal(result.spans[1].sampled_points_mm[0], corner)
    assert result.metrics["maximum_join_gap_mm"] == 0.0


def test_forbidden_notch_is_preserved_by_native_fit():
    points = np.asarray([
        [0, 0, 0], [240, 0, 0], [240, 140, 0], [150, 140, 0],
        [150, 80, 0], [90, 80, 0], [90, 140, 0], [0, 140, 0], [0, 0, 0],
    ], dtype=float)
    result = fit_manufacturing_curve(_flat(points, "AUTODECK::DECK_PRIMARY_OUTER", True), 1, load_config())
    assert result.metrics["forbidden_side_violation_count"] == 0
    assert not result.metrics["self_intersection"]
    assert result.metrics["span_type_counts"]["POLYLINE"] == 0
    assert result.metrics["logical_path_closed"]


def test_compatibility_dxf_uses_chord_error_and_native_dxf_keeps_spline(tmp_path: Path):
    x = np.linspace(0.0, 2000.0, 801)
    points = np.column_stack((x, 40.0 * np.sin(np.pi * x / 2000.0), np.zeros(len(x))))
    curve = fit_manufacturing_curve(_flat(points), 1, load_config())
    metrics = write_manufacturing_dxfs(tmp_path, [curve], load_config())
    assert metrics["status"] == "GOOD"
    assert metrics["native"]["entity_counts"] == {"SPLINE": 1}
    assert metrics["polyline"]["tessellation_method"] == "adaptive chord error"
    assert metrics["polyline"]["total_vertex_count"] < 200
    assert metrics["polyline"]["maximum_roundtrip_deviation_mm"] <= 0.1


def test_rhino_preview_contains_true_nurbs_and_required_layers(tmp_path: Path):
    import rhino3dm

    x = np.linspace(0.0, 300.0, 401)
    points = np.column_stack((x, 20.0 * np.sin(x / 35.0), np.zeros(len(x))))
    flat = _flat(points); curve = fit_manufacturing_curve(flat, 1, load_config())
    path = tmp_path / "flattened_preview.3dm"
    metrics = export_flattened_preview(path, [flat], [curve])
    model = rhino3dm.File3dm.Read(str(path))
    assert metrics["native_smooth_object_count"] == 1
    layers = {layer.Name for layer in model.Layers}
    assert {"FLAT_RAW", "CAM_POLYLINE_OLD_STYLE", "CAM_NATIVE_SMOOTH", "PROTECTED_CORNERS"} <= layers
    native = [
        item for item in model.Objects
        if model.Layers[item.Attributes.LayerIndex].Name == "CAM_NATIVE_SMOOTH"
    ]
    assert len(native) == 1
    assert type(native[0].Geometry).__name__ == "NurbsCurve"


def test_reports_are_subsystem_specific_and_length_audit_finds_source_loss(tmp_path: Path):
    x = np.linspace(0.0, 200.0, 201)
    points = np.column_stack((x, 10.0 * np.sin(x / 30.0), np.zeros(len(x))))
    flat = _flat(points); curve = fit_manufacturing_curve(flat, 1, load_config())
    mapped = np.column_stack((np.linspace(0.0, 100.0, 101), np.zeros(101), np.zeros(101)))
    audit = make_length_audit("primary", 110.0, mapped, mapped, mapped, mapped)
    write_manufacturing_report(tmp_path / "manufacturing_report.md", [curve], {}, [])
    write_curve_fit_report(tmp_path / "curve_fit_report.md", [curve], load_config())
    write_development_report(tmp_path / "development_report.md", {}, [audit])
    write_flatten_report(tmp_path / "flatten_report.md", [flat], {}, {})
    headings = [(tmp_path / name).read_text(encoding="utf-8").splitlines()[0] for name in (
        "manufacturing_report.md", "curve_fit_report.md", "development_report.md", "flatten_report.md",
    )]
    assert len(set(headings)) == 4
    curve_report = (tmp_path / "curve_fit_report.md").read_text(encoding="utf-8")
    assert "| Span | Model | Result and measured reason |" in curve_report
    assert all(model in curve_report for model in ("LINE", "ARC", "SPLINE", "POLYLINE"))
    assert audit["largest_absolute_transition"]["from"] == "conditioned_3d_mm"
    assert audit["largest_absolute_transition"]["to"] == "mapped_source_mesh_3d_mm"
