from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from autodeck.config import load_config
from autodeck.curve_fit import (
    _forbidden_violations,
    detect_manufacturing_corners,
    fit_manufacturing_curve,
)
from autodeck.development import (
    DevelopmentMesh,
    IntrinsicMeshDevelopment,
    RigidPlanarDevelopment,
    _boundary_statistics,
    _face_component_count,
    backproject_flat_points,
    build_development_mesh,
    map_curve_to_development,
)
from autodeck.dxf_export import write_manufacturing_dxfs
from autodeck.models import AcceptedSurfaceGrid, ConditionedCurve, FeatureCurve, FlattenedCurve
from autodeck.topview import classify_nonskid_evidence
from autodeck.analysis_pipeline import _v03_success_contract


def _direct_mesh(vertices: np.ndarray, faces: np.ndarray) -> DevelopmentMesh:
    boundary, _vertices = _boundary_statistics(faces)
    topology = {**boundary, "face_component_count": _face_component_count(faces)}
    return DevelopmentMesh(
        1, vertices, vertices.copy(), faces,
        np.arange(len(vertices)), np.arange(len(vertices)), topology, {},
    )


def _grid_mesh(function, width=500.0, height=200.0, nu=31, nv=15) -> DevelopmentMesh:
    x = np.linspace(0.0, width, nu); y = np.linspace(0.0, height, nv)
    vertices = np.asarray([[px, py, function(px, py)] for px in x for py in y], dtype=float)
    faces: list[list[int]] = []
    for row in range(nu - 1):
        for col in range(nv - 1):
            a = row * nv + col
            faces.extend([[a, a + nv, a + nv + 1], [a, a + nv + 1, a + 1]])
    return _direct_mesh(vertices, np.asarray(faces, dtype=np.int64))


def _flat_curve(points: np.ndarray, layer: str, closed: bool = True, name: str = "synthetic") -> FlattenedCurve:
    raw = FeatureCurve(layer, name, points.copy(), closed, 1.0)
    conditioned_feature = FeatureCurve(layer.replace("AUTODECK::", "AUTODECK::CONDITIONED_3D::"), name + "_conditioned", points.copy(), closed, 1.0)
    conditioned = ConditionedCurve(raw, "raw-0001", conditioned_feature, "conditioned-0001", points.copy(), [], "GOOD")
    return FlattenedCurve(conditioned, "flat-raw-0001", points.copy(), "FLAT_RAW_TEST", closed, "GOOD")


def test_tilted_seven_degree_rectangle_recovers_exact_physical_size():
    angle = math.radians(7.0)
    vertices = np.asarray([
        [0.0, 0.0, 0.0],
        [1000.0 * math.cos(angle), 0.0, 1000.0 * math.sin(angle)],
        [1000.0 * math.cos(angle), 500.0, 1000.0 * math.sin(angle)],
        [0.0, 500.0, 0.0],
    ])
    mesh = _direct_mesh(vertices, np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64))
    result = RigidPlanarDevelopment().develop(mesh, load_config())
    extent = np.ptp(result.uv_mm, axis=0)
    assert result.status == "GOOD"
    assert np.allclose(np.sort(extent), [500.0, 1000.0], atol=1e-8)
    assert result.distortion_metrics["absolute_edge_strain_percent"]["maximum"] < 1e-8


def test_development_mesh_preserves_circular_and_rectangular_holes():
    config = load_config(); resolution = 5.0
    rows, cols = 101, 201
    yy, xx = np.mgrid[:rows, :cols]
    mask = np.ones((rows, cols), dtype=bool)
    mask[(xx - 60) ** 2 + (yy - 50) ** 2 <= 10 ** 2] = False
    mask[35:65, 125:150] = False
    surface = AcceptedSurfaceGrid(0.0, 0.0, resolution, mask, np.zeros(mask.shape), {1: mask})
    mesh = build_development_mesh(surface, 1, config)
    assert mesh.topology["hole_fill_performed"] is False
    assert mesh.topology["estimated_hole_count"] == 2
    assert mesh.topology["closed_boundary_loop_count"] == 3
    assert mesh.topology["face_component_count"] == 1


def test_shallow_cylinder_develops_with_negligible_strain():
    radius = 1000.0
    mesh = _grid_mesh(lambda x, _y: radius * (1.0 - math.cos(x / radius)))
    mesh.base_vertices_mm[:, 0] = radius * np.sin(mesh.vertices_mm[:, 0] / radius)
    mesh.vertices_mm[:, 0] = mesh.base_vertices_mm[:, 0]
    result = IntrinsicMeshDevelopment().develop(mesh, load_config())
    assert result.status == "GOOD"
    assert result.distortion_metrics["flipped_or_collapsed_triangle_count"] == 0
    assert result.distortion_metrics["absolute_edge_strain_percent"]["p95"] < 1e-6


def test_compound_curvature_reports_unavoidable_distortion():
    mesh = _grid_mesh(lambda x, y: 0.0015 * ((x - 250.0) ** 2 + (y - 100.0) ** 2))
    result = IntrinsicMeshDevelopment().develop(mesh, load_config())
    assert result.distortion_metrics["absolute_edge_strain_percent"]["p95"] > 0.1
    assert result.status in {"NEEDS_REVIEW", "INVALID"}


def test_notch_has_persistent_protected_corners():
    points = np.asarray([
        [0, 0, 0], [240, 0, 0], [240, 140, 0], [150, 140, 0],
        [150, 80, 0], [90, 80, 0], [90, 140, 0], [0, 140, 0], [0, 0, 0],
    ], dtype=float)
    diagnostic, anchors, metrics = detect_manufacturing_corners(points, True, "deck", load_config())
    assert len(anchors) == 8
    assert metrics["physical_scales_mm"] == [10.0, 20.0, 40.0, 80.0]
    assert metrics["persistence_requirement"] == 3
    assert not metrics["over_anchored"]


def test_raster_staircase_circle_does_not_overprotect_noise():
    theta = np.linspace(0.0, 2.0 * math.pi, 1201)
    points = np.column_stack((np.round(100 * np.cos(theta) / 2) * 2, np.round(100 * np.sin(theta) / 2) * 2, np.zeros(len(theta))))
    _diagnostic, anchors, metrics = detect_manufacturing_corners(points, True, "small_obstacle", load_config())
    assert len(anchors) <= 4
    assert not metrics["over_anchored"]


def test_rectangular_obstacle_fits_four_lines_and_preserves_four_corners():
    points = np.asarray([[0, 0, 0], [80, 0, 0], [80, 50, 0], [0, 50, 0], [0, 0, 0]], dtype=float)
    result = fit_manufacturing_curve(_flat_curve(points, "AUTODECK::OBSTACLES_PRIMARY"), 1, load_config())
    assert result.status == "GOOD"
    assert len(result.anchor_indices) == 4
    assert result.metrics["span_type_counts"]["LINE"] == 4


def test_round_obstacle_fits_native_circle_without_corners():
    theta = np.linspace(0.0, 2.0 * math.pi, 401)
    points = np.column_stack((30 * np.cos(theta), 30 * np.sin(theta), np.zeros(len(theta))))
    result = fit_manufacturing_curve(_flat_curve(points, "AUTODECK::OBSTACLES_PRIMARY"), 1, load_config())
    assert result.status == "GOOD"
    assert len(result.anchor_indices) == 0
    assert result.metrics["span_type_counts"]["CIRCLE"] == 1


def test_organic_open_curve_selects_spline():
    x = np.linspace(0.0, 180.0, 361)
    points = np.column_stack((x, 18.0 * np.sin(x / 33.0) + 3.0 * np.sin(x / 9.0), np.zeros(len(x))))
    result = fit_manufacturing_curve(_flat_curve(points, "AUTODECK::SEAMS_HIGH_CONFIDENCE", False), 1, load_config())
    assert result.status == "GOOD"
    assert result.metrics["span_type_counts"]["SPLINE"] == 1
    assert result.metrics["deviation"]["maximum_mm"] <= load_config()["manufacturing_fit"]["maximum_fit_deviation_mm"]


def test_featureless_and_key_west_weak_grit_abstain_but_molded_pattern_detects():
    config = load_config()["nonskid"]
    empty, _ = classify_nonskid_evidence({"p95": 0.0}, {"p50": 0.0, "p95": 0.0}, config)
    key_west, evidence = classify_nonskid_evidence(
        {"p95": 0.9556583881}, {"p50": 0.4315016568, "p95": 0.7689396262}, config
    )
    molded, _ = classify_nonskid_evidence(
        {"p95": 0.5335361242}, {"p50": 0.0, "p95": 0.1520885825}, config
    )
    assert empty == "NO_DETECTABLE_SIGNAL"
    assert key_west == "NO_DETECTABLE_SIGNAL"
    assert evidence["density_contrast_over_background"] < evidence["required_density_contrast_over_background"]
    assert molded == "DETECTED"


def test_forbidden_side_rejects_shortcut_across_deck_notch():
    polygon = np.asarray([
        [0, 0, 0], [200, 0, 0], [200, 100, 0], [120, 100, 0],
        [120, 60, 0], [80, 60, 0], [80, 100, 0], [0, 100, 0], [0, 0, 0],
    ], dtype=float)
    shortcut = np.column_stack((np.linspace(80, 120, 81), np.full(81, 90.0), np.zeros(81)))
    assert _forbidden_violations(shortcut, polygon, "outer", 0.35) > 0


def test_barycentric_curve_mapping_round_trips_tilted_plane():
    angle = math.radians(7.0)
    vertices = np.asarray([
        [0.0, 0.0, 0.0], [1000 * math.cos(angle), 0.0, 1000 * math.sin(angle)],
        [1000 * math.cos(angle), 500.0, 1000 * math.sin(angle)], [0.0, 500.0, 0.0],
    ])
    mesh = _direct_mesh(vertices, np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64))
    result = RigidPlanarDevelopment().develop(mesh, load_config())
    source = np.linspace(vertices[0], vertices[2], 31)
    mapping = map_curve_to_development(source, result, 1e-6)
    reconstructed, inverse = backproject_flat_points(mapping.flat_points_mm, result, 1e-6)
    assert mapping.status == "GOOD" and inverse.status == "GOOD"
    assert np.max(np.linalg.norm(reconstructed - source, axis=1)) < 1e-6
    assert np.allclose(mapping.barycentric_coordinates.sum(axis=1), 1.0)


def test_ezdxf_native_and_polyline_roundtrip(tmp_path: Path):
    rectangle = np.asarray([[0, 0, 0], [100, 0, 0], [100, 60, 0], [0, 60, 0], [0, 0, 0]], dtype=float)
    theta = np.linspace(0.0, 2.0 * math.pi, 301)
    circle = np.column_stack((160 + 20 * np.cos(theta), 30 + 20 * np.sin(theta), np.zeros(len(theta))))
    x = np.linspace(0.0, 180.0, 361)
    organic = np.column_stack((x, 100 + 15 * np.sin(x / 30.0), np.zeros(len(x))))
    config = load_config()
    curves = [
        fit_manufacturing_curve(_flat_curve(rectangle, "AUTODECK::OBSTACLES_PRIMARY", name="rectangle"), 1, config),
        fit_manufacturing_curve(_flat_curve(circle, "AUTODECK::OBSTACLES_PRIMARY", name="circle"), 2, config),
        fit_manufacturing_curve(_flat_curve(organic, "AUTODECK::SEAMS_HIGH_CONFIDENCE", False, "organic"), 3, config),
    ]
    metrics = write_manufacturing_dxfs(tmp_path, curves, config)
    assert metrics["status"] == "GOOD"
    assert metrics["native"]["valid"] and metrics["polyline"]["valid"]
    assert metrics["native"]["entity_counts"]["LINE"] == 4
    assert metrics["native"]["entity_counts"]["CIRCLE"] == 1
    assert metrics["native"]["entity_counts"]["SPLINE"] == 1
    assert metrics["native"]["units"] == "millimetres"
    assert metrics["native"]["auditor_error_count"] == 0


def test_v03_success_contract_cannot_substitute_secondary_for_invalid_primary():
    common = (True, "GOOD", "NEEDS_REVIEW")
    assert not _v03_success_contract(*common, "INVALID", "GOOD", True, True, 8)
    assert not _v03_success_contract(*common, "NOT_EVALUATED", "GOOD", True, True, 8)
    assert _v03_success_contract(*common, "NEEDS_REVIEW", "GOOD", True, True, 8)
