from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np


class PanelStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(slots=True)
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray
    uv: np.ndarray | None = None
    face_uv: np.ndarray | None = None
    source_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "Mesh":
        return Mesh(
            self.vertices.copy(),
            self.faces.copy(),
            None if self.uv is None else self.uv.copy(),
            None if self.face_uv is None else self.face_uv.copy(),
            self.source_path,
            dict(self.metadata),
        )


@dataclass(slots=True)
class AnalysisMesh:
    mesh_mm: Mesh
    source_vertex_indices: np.ndarray
    source_face_indices: np.ndarray
    input_units: str
    mm_per_input_unit: float
    preprocessing_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Adjacency:
    face_a: np.ndarray
    face_b: np.ndarray
    edge_u: np.ndarray
    edge_v: np.ndarray
    boundary_face: np.ndarray
    boundary_u: np.ndarray
    boundary_v: np.ndarray
    nonmanifold_edges: list[tuple[int, int, list[int]]]
    neighbors: list[list[tuple[int, int]]]


@dataclass(slots=True)
class GeometryFields:
    face_normals: np.ndarray
    vertex_normals: np.ndarray
    face_centroids: np.ndarray
    face_areas: np.ndarray
    principal_k1: np.ndarray
    principal_k2: np.ndarray
    principal_dir1: np.ndarray
    principal_dir2: np.ndarray
    mean_curvature: np.ndarray
    gaussian_curvature: np.ndarray
    multiscale_normal_variation_deg: dict[float, np.ndarray]
    conformability: np.ndarray


@dataclass(slots=True)
class OrientationField:
    smoothed_face_normals: np.ndarray
    raw_slope_deg: np.ndarray
    smoothed_slope_deg: np.ndarray
    upward_facing: np.ndarray
    core_candidate: np.ndarray
    fringe_candidate: np.ndarray
    recovered_noise_faces: np.ndarray


@dataclass(slots=True)
class BoundaryField:
    raw_cost: np.ndarray
    refined_cost: np.ndarray
    normal_angle_deg: np.ndarray
    transition_distance_mm: np.ndarray
    normal_turn_deg_per_mm: np.ndarray
    signed_turn: np.ndarray
    ridge_strength: np.ndarray
    valley_strength: np.ndarray
    fine_scale_strength: np.ndarray
    suppressed: np.ndarray
    reasons: list[str]


@dataclass(slots=True)
class BoundaryLoop:
    analysis_vertex_indices: list[int]
    is_closed: bool
    kind: str
    length_mm: float
    confidence: float


@dataclass(slots=True)
class PanelProposal:
    status: PanelStatus
    selected_faces: np.ndarray
    outer_loops: list[BoundaryLoop]
    internal_exclusions: list[BoundaryLoop]
    approved_geometry: None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CandidateRegion:
    candidate_id: int
    selected_faces: np.ndarray
    area_mm2: float
    face_count: int
    mean_slope_deg: float
    p95_slope_deg: float
    max_slope_deg: float
    bbox_min_mm: np.ndarray
    bbox_max_mm: np.ndarray
    centroid_mm: np.ndarray
    outer_loops: list[BoundaryLoop]
    internal_boundaries: list[BoundaryLoop]
    invalid_loops: list[BoundaryLoop]
    confidence: float
    confidence_label: str
    core_area_fraction: float
    touches_open_mesh_boundary: bool
    open_boundary_edge_count: int
    open_boundary_length_mm: float
    is_primary: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FeatureCurve:
    layer: str
    name: str
    points_input: np.ndarray
    closed: bool
    confidence: float
    metrics: dict[str, Any] = field(default_factory=dict)
    curve_id: str = ""


@dataclass(slots=True)
class AcceptedSurfaceGrid:
    """Accepted deck surface raster in physical millimetres."""

    x_min_mm: float
    y_min_mm: float
    resolution_mm: float
    accepted_mask: np.ndarray
    floor_height_mm: np.ndarray
    patch_masks: dict[int, np.ndarray] = field(default_factory=dict)


@dataclass(slots=True)
class ConditionedCurve:
    raw_curve: FeatureCurve
    raw_curve_id: str
    smoothed_curve: FeatureCurve
    smoothed_curve_id: str
    resampled_points_input: np.ndarray
    anchor_indices: list[int]
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FlattenedCurve:
    source: ConditionedCurve
    flattened_curve_id: str
    points_mm: np.ndarray
    layer: str
    closed: bool
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopViewResult:
    feature_curves: list[FeatureCurve]
    primary_outer_curve: FeatureCurve | None
    secondary_outer_curves: dict[int, FeatureCurve]
    statistics: dict[str, Any]
    warnings: list[str]
    artifact_paths: dict[str, str]
    accepted_surface: AcceptedSurfaceGrid | None = None
