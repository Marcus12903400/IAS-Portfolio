from __future__ import annotations

import math
import json

import ezdxf
import numpy as np
from shapely.geometry import LineString, Point, Polygon

from autodeck.config import load_config
from autodeck.curve_fit import ManufacturingCurve, fit_manufacturing_curve
from autodeck.models import ConditionedCurve, FeatureCurve, FlattenedCurve
from autodeck.patterns import detect_boat_frame, generate_pattern, write_pattern_outputs
from autodeck.polyarc import (
    PolyarcPrimitive,
    _angle_mismatch_deg,
    _manual_broad_regime_fit,
    _repair_manual_joins,
    fit_polyarc_curve,
)
from autodeck.robust_reference import fit_robust_reference_curve
from autodeck.reprocess import rerun_pattern


def _flat(points: np.ndarray, layer: str, closed: bool, patch_id: int = 1) -> FlattenedCurve:
    raw = FeatureCurve(layer, "v033-synthetic", points.copy(), closed, 1.0)
    smooth = FeatureCurve(layer, "v033-synthetic-smooth", points.copy(), closed, 1.0)
    conditioned = ConditionedCurve(raw, "raw-v033", smooth, "smooth-v033", points.copy(), [], "GOOD")
    return FlattenedCurve(
        conditioned, "flat-v033", points.copy(), "FLAT_RAW_TEST", closed, "GOOD",
        {"development_patch_id": patch_id},
    )


def _manual_fit(points: np.ndarray, layer: str, closed: bool, index: int = 1):
    config = load_config()
    manufacturing = fit_manufacturing_curve(_flat(points, layer, closed), index, config)
    reference = fit_robust_reference_curve(manufacturing, config["robust_reference"])
    settings = dict(config["polyarc_fit"])
    settings["absolute_fit_tolerance_mm"] = config["manufacturing_fit"]["reference_max_deviation_mm"]
    polyarc = fit_polyarc_curve(manufacturing, settings, reference)
    return manufacturing, reference, polyarc


def _rectangle(length: float, width: float, angle_deg: float = 0.0, center=(0.0, 0.0)) -> np.ndarray:
    base = np.asarray([
        [-0.5 * length, -0.5 * width],
        [0.5 * length, -0.5 * width],
        [0.5 * length, 0.5 * width],
        [-0.5 * length, 0.5 * width],
    ])
    angle = math.radians(angle_deg)
    rotation = np.asarray([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    xy = base @ rotation.T + np.asarray(center)
    return np.column_stack((xy, np.zeros(len(xy))))


def _rounded_rectangle() -> tuple[np.ndarray, list[int]]:
    segments = [
        np.column_stack((np.linspace(100.0, 900.0, 41), np.zeros(41))),
        np.column_stack((900.0 + 100.0 * np.cos(np.linspace(-0.5 * np.pi, 0.0, 21)),
                         100.0 + 100.0 * np.sin(np.linspace(-0.5 * np.pi, 0.0, 21)))),
        np.column_stack((np.full(21, 1000.0), np.linspace(100.0, 400.0, 21))),
        np.column_stack((900.0 + 100.0 * np.cos(np.linspace(0.0, 0.5 * np.pi, 21)),
                         400.0 + 100.0 * np.sin(np.linspace(0.0, 0.5 * np.pi, 21)))),
        np.column_stack((np.linspace(900.0, 100.0, 41), np.full(41, 500.0))),
        np.column_stack((100.0 + 100.0 * np.cos(np.linspace(0.5 * np.pi, np.pi, 21)),
                         400.0 + 100.0 * np.sin(np.linspace(0.5 * np.pi, np.pi, 21)))),
        np.column_stack((np.zeros(21), np.linspace(400.0, 100.0, 21))),
        np.column_stack((100.0 + 100.0 * np.cos(np.linspace(np.pi, 1.5 * np.pi, 21)),
                         100.0 + 100.0 * np.sin(np.linspace(np.pi, 1.5 * np.pi, 21)))),
    ]
    points: list[np.ndarray] = []
    anchors: list[int] = []
    for segment in segments:
        anchors.append(len(points))
        points.extend(segment[:-1])
    return np.asarray(points, dtype=float), anchors


def test_noise_spike_is_suppressed_but_raw_is_preserved_and_cam_is_one_line():
    x = np.arange(0.0, 2002.0, 2.0)
    y = np.zeros(len(x)); y[len(y) // 2] = 4.0
    points = np.column_stack((x, y, np.zeros(len(x))))
    _manufacturing, reference, cam = _manual_fit(points, "AUTODECK::SEAMS_HIGH_CONFIDENCE", False)
    assert reference.raw_points[len(y) // 2, 1] == 4.0
    assert abs(reference.points[len(y) // 2, 1]) < 2.0
    assert cam.line_count == 1
    assert cam.primitive_count == 1
    assert cam.metrics["fit_reference"] == "ROBUST_PHYSICAL_REFERENCE"
    assert "p99_mm" in cam.metrics["raw_to_cam_deviation"]


def test_persistent_three_mm_feature_survives_robust_reference():
    x = np.arange(0.0, 1002.0, 2.0)
    y = np.zeros(len(x)); y[(x >= 400.0) & (x <= 500.0)] = 3.0
    points = np.column_stack((x, y, np.zeros(len(x))))
    manufacturing = fit_manufacturing_curve(
        _flat(points, "AUTODECK::SEAMS_HIGH_CONFIDENCE", False), 1, load_config(),
    )
    reference = fit_robust_reference_curve(manufacturing, load_config()["robust_reference"])
    plateau = reference.points[(x >= 425.0) & (x <= 475.0), 1]
    assert float(np.median(plateau)) > 2.8


def test_broad_regime_solver_recovers_manual_line_arc_complexity_without_vertex_chatter():
    points, anchors = _rounded_rectangle()
    broad, primitives, diagnostics = _manual_broad_regime_fit(
        points,
        anchors,
        True,
        3.0,
        {
            "manual_reference_scales_mm": [10.0, 20.0, 40.0, 80.0],
            "manual_fit_headroom_mm": 0.05,
            "manual_candidate_sampling_mm": 1.0,
            "manual_dp_node_stride": 1,
        },
    )
    assert len(broad) == len(points)
    assert len(primitives) == 8
    assert sum(item.kind == "LINE" for item in primitives) == 4
    assert sum(item.kind == "ARC" for item in primitives) == 4
    assert diagnostics["primitive_count"] == 8
    assert diagnostics["join_refinement_status"] == "REQUIRES_EXACT_CONNECTION_AND_G1_REFINEMENT"


def test_local_manual_join_repair_adds_bounded_exact_g1_connectors():
    before = PolyarcPrimitive(
        "LINE", np.array([0.0, 0.0]), np.array([100.0, 0.0]), 0, 10, 0,
    )
    direction = np.array([math.cos(math.radians(35.0)), math.sin(math.radians(35.0))])
    after = PolyarcPrimitive(
        "LINE", np.array([103.0, 2.0]), np.array([103.0, 2.0]) + 100.0 * direction,
        10, 20, 0,
    )
    reference = np.vstack((before.start, before.end, after.start, after.end))
    repaired, diagnostics = _repair_manual_joins(
        [before, after], reference, False, "neutral", None, None, 0.5,
        {"manual_join_trim_mm": 5.0},
    )
    assert diagnostics["status"] == "PASSED"
    assert diagnostics["original_primitive_count"] == 2
    assert diagnostics["repaired_primitive_count"] == 4
    assert diagnostics["smooth_connector_primitives"] in {1, 2}
    assert max(
        np.linalg.norm(first.end - second.start)
        for first, second in zip(repaired, repaired[1:])
    ) < 1e-7
    assert max(
        _angle_mismatch_deg(first.tangent_end(), second.tangent_start())
        for first, second in zip(repaired, repaired[1:])
    ) < 1e-5


def test_rotated_boat_axis_is_detected_without_pattern_angle():
    _manufacturing, _reference, cam = _manual_fit(
        _rectangle(2000.0, 600.0, 37.0), "AUTODECK::DECK_PRIMARY_OUTER", True,
    )
    frame = detect_boat_frame(cam, load_config()["pattern"])
    expected = np.asarray([math.cos(math.radians(37.0)), math.sin(math.radians(37.0))])
    assert abs(float(np.dot(frame.longitudinal, expected))) > 0.999
    assert frame.axis_confidence > 0.5


def test_teak_uses_centerline_spacing_and_clips_off_center_obstacle():
    outer_m, _outer_r, outer = _manual_fit(
        _rectangle(2000.0, 800.0), "AUTODECK::DECK_PRIMARY_OUTER", True, 1,
    )
    obstacle_m, _obstacle_r, obstacle = _manual_fit(
        _rectangle(300.0, 180.0, center=(250.0, 140.0)), "AUTODECK::OBSTACLES_PRIMARY", True, 2,
    )
    config = load_config(); config["pattern"]["selected"] = "teak"
    result = generate_pattern([outer, obstacle], [outer_m, obstacle_m], config)
    assert result.frame is not None
    assert abs(float(np.dot(result.frame.longitudinal, np.array([1.0, 0.0])))) > 0.999
    offsets = sorted({round(float((line.start - result.frame.origin) @ result.frame.transverse), 3) for line in result.lines})
    assert 0.0 in offsets
    assert any(abs(abs(value) - 63.5) < 0.01 for value in offsets)
    obstacle_polygon = Polygon(obstacle.sampled_points)
    assert all(not obstacle_polygon.contains(Point(0.5 * (line.start + line.end))) for line in result.lines)


def test_diamond_and_hex_dimensions_are_developed_mm_and_shared_edges_are_unique(tmp_path):
    manufacturing, _reference, cam = _manual_fit(
        _rectangle(1200.0, 700.0, 7.0), "AUTODECK::DECK_PRIMARY_OUTER", True,
    )
    for selected in ("diamond", "hex"):
        config = load_config(); config["pattern"]["selected"] = selected
        result = generate_pattern([cam], [manufacturing], config)
        outputs = write_pattern_outputs(tmp_path / selected, result, [cam], [manufacturing])
        assert result.metrics["dimension_checks"]["valid"]
        assert result.metrics["dimension_checks"]["selected_dimension_error_mm"] == 0.0
        assert result.metrics["coincident_duplicates_final"] == 0
        assert outputs["status"] == "GOOD"
        doc = ezdxf.readfile(tmp_path / selected / "pattern_only.dxf")
        assert all(entity.dxftype() == "LINE" for entity in doc.modelspace())


def test_manual_tangent_corner_remains_line_arc_line():
    angles = np.linspace(-0.5 * np.pi, 0.0, 101)
    chain = np.vstack([
        np.column_stack((np.linspace(0.0, 300.0, 121), np.zeros(121))),
        np.column_stack((300.0 + 100.0 * np.cos(angles[1:]), 100.0 + 100.0 * np.sin(angles[1:]))),
        np.column_stack((np.full(120, 400.0), np.linspace(102.5, 400.0, 120))),
    ])
    _manufacturing, _reference, cam = _manual_fit(
        np.column_stack((chain, np.zeros(len(chain)))), "AUTODECK::SEAMS_HIGH_CONFIDENCE", False,
    )
    assert [primitive.kind for primitive in cam.primitives] == ["LINE", "ARC", "LINE"]
    assert cam.metrics["maximum_smooth_tangent_mismatch_deg"] <= 0.05


def test_cached_pattern_rerun_does_not_repeat_scan_or_development(tmp_path):
    manufacturing, _reference, cam = _manual_fit(
        _rectangle(1200.0, 600.0), "AUTODECK::DECK_PRIMARY_OUTER", True,
    )
    run = tmp_path / "cached-run"
    development_dir = run / "debug" / "development"
    development_dir.mkdir(parents=True)
    metrics = {
        "manufacturing_fit": {
            "curves": [{
                "cam_curve_id": manufacturing.cam_curve_id,
                "closed": True,
                "feature_class": "AUTODECK::DECK_PRIMARY_OUTER",
                "flat_raw_id": manufacturing.source.flattened_curve_id,
                "layer": manufacturing.layer,
                "protected_corner_indices": manufacturing.anchor_indices,
                "protected_corner_points_mm": manufacturing.anchor_points_mm.tolist(),
                "status": manufacturing.status,
                "metrics": manufacturing.metrics,
                "warnings": manufacturing.warnings,
            }],
        },
        "polyarc_fit": {"curves": [cam.to_dict()]},
        "export": {},
    }
    detail = {
        "flat_raw_curves": [{
            "flattened_curve_id": manufacturing.source.flattened_curve_id,
            "metrics": {"development_patch_id": 1},
        }],
    }
    (run / "debug_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (development_dir / "development_metrics.json").write_text(json.dumps(detail), encoding="utf-8")
    config = load_config(); config["pattern"]["selected"] = "teak"
    result = rerun_pattern(run, config)
    assert result["cached_pattern_only_rerun"]
    assert not result["scan_reprocessed"]
    assert not result["development_reprocessed"]
    artifacts = run / "processing_and_debug"
    assert (artifacts / "pattern_only.dxf").exists()
    assert (artifacts / "flattened_with_pattern.dxf").exists()
    assert (run / "final.dxf").exists()
