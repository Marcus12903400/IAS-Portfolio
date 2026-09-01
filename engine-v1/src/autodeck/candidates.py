from __future__ import annotations

import numpy as np

from .boundaries import extract_boundary_loops, extract_outer_boundary_only
from .models import Adjacency, BoundaryField, CandidateRegion, GeometryFields, Mesh, OrientationField
from .orientation import connected_candidate_components


def _open_boundary_statistics(mesh: Mesh, adjacency: Adjacency, selected: np.ndarray) -> tuple[int, float]:
    use = selected[adjacency.boundary_face]
    if not np.any(use):
        return 0, 0.0
    lengths = np.linalg.norm(
        mesh.vertices[adjacency.boundary_u[use]] - mesh.vertices[adjacency.boundary_v[use]], axis=1
    )
    return int(np.count_nonzero(use)), float(lengths.sum())


def _confidence(
    p95_slope: float,
    maximum_slope: float,
    core_fraction: float,
    loops: list,
    touches_open_boundary: bool,
) -> float:
    orientation_score = np.clip(1.0 - 0.4 * p95_slope / max(maximum_slope, 1e-9), 0.0, 1.0)
    loop_confidences = [loop.confidence for loop in loops if loop.kind != "invalid"]
    structural_support = float(np.mean(loop_confidences)) if loop_confidences else 0.0
    score = 0.50 * orientation_score + 0.20 * core_fraction + 0.30 * structural_support
    if touches_open_boundary:
        score -= 0.15
    return float(np.clip(score, 0.0, 1.0))


def discover_candidates(
    mesh: Mesh,
    adjacency: Adjacency,
    fields: GeometryFields,
    boundary: BoundaryField,
    orientation: OrientationField,
    config: dict,
    components: list[np.ndarray] | None = None,
    use_conformability: bool = True,
    use_structural_boundaries: bool = True,
    outer_only: bool = False,
) -> tuple[list[CandidateRegion], list[str]]:
    if components is None:
        eligible = orientation.fringe_candidate.copy()
        if use_conformability:
            eligible &= fields.conformability >= float(config["segmentation"]["minimum_conformability"])
        crossing_cost = float(config["segmentation"]["max_boundary_cost"]) if use_structural_boundaries else float("inf")
        components = connected_candidate_components(
            eligible, adjacency, boundary, crossing_cost,
            orientation.recovered_noise_faces,
        )
    minimum_area = float(config["segmentation"]["minimum_candidate_area_mm2"])
    candidates: list[CandidateRegion] = []
    warnings: list[str] = []
    for component in components:
        area = float(fields.face_areas[component].sum())
        if area < minimum_area:
            continue
        selected = np.zeros(len(mesh.faces), dtype=bool); selected[component] = True
        loops, loop_warnings = (
            extract_outer_boundary_only(mesh, adjacency, boundary, selected, config)
            if outer_only else extract_boundary_loops(mesh, adjacency, boundary, selected, config)
        )
        warnings.extend(loop_warnings)
        outer = [loop for loop in loops if loop.kind == "outer"]
        # Selection holes remain available for V0.2 obstacle analysis. Raw
        # structural edges are preserved in boundary_scores.npz and are no
        # longer expanded into hundreds of thousands of CAD polylines.
        internal = [loop for loop in loops if loop.kind == "internal_exclusion"]
        invalid = [loop for loop in loops if loop.kind == "invalid"]
        slopes = orientation.smoothed_slope_deg[component]
        face_areas = fields.face_areas[component]
        centroid = np.average(fields.face_centroids[component], axis=0, weights=face_areas)
        vertices = mesh.vertices[np.unique(mesh.faces[component].ravel())]
        open_count, open_length = _open_boundary_statistics(mesh, adjacency, selected)
        core_area = float(fields.face_areas[component[orientation.core_candidate[component]]].sum())
        core_fraction = core_area / max(area, 1e-12)
        score = _confidence(
            float(np.percentile(slopes, 95)), float(config["orientation"]["max_slope_deg"]),
            core_fraction, loops, open_count > 0,
        )
        label = "HIGH_CONFIDENCE" if score >= 0.75 else ("CANDIDATE" if score >= 0.50 else "LOW_CONFIDENCE")
        candidate_warnings = list(loop_warnings)
        if open_count:
            candidate_warnings.append(
                f"Candidate touches {open_count} open mesh-cut edges ({open_length:.1f} mm); the physical transition may have been trimmed away"
            )
        candidates.append(CandidateRegion(
            0, selected, area, int(len(component)), float(np.average(slopes, weights=face_areas)),
            float(np.percentile(slopes, 95)), float(np.max(slopes)), vertices.min(axis=0), vertices.max(axis=0),
            centroid, outer, internal, invalid, score, label, core_fraction,
            open_count > 0, open_count, open_length, False, candidate_warnings,
        ))
    candidates.sort(key=lambda item: (-item.area_mm2, -item.confidence))
    for index, candidate in enumerate(candidates, 1):
        candidate.candidate_id = index
        candidate.is_primary = index == 1
    return candidates, warnings


def candidate_from_selection(
    mesh: Mesh,
    adjacency: Adjacency,
    fields: GeometryFields,
    boundary: BoundaryField,
    orientation: OrientationField,
    selected: np.ndarray,
    config: dict,
    candidate_id: int = 1,
) -> tuple[CandidateRegion, list[str]]:
    """Describe a region produced by optional manual/marker seeded growth."""
    component = np.flatnonzero(selected)
    if not len(component):
        raise ValueError("Seeded segmentation selected no faces")
    area = float(fields.face_areas[component].sum())
    loops, loop_warnings = extract_boundary_loops(mesh, adjacency, boundary, selected, config)
    outer = [loop for loop in loops if loop.kind == "outer"]
    internal = [loop for loop in loops if loop.kind == "internal_exclusion"]
    invalid = [loop for loop in loops if loop.kind == "invalid"]
    slopes = orientation.smoothed_slope_deg[component]
    face_areas = fields.face_areas[component]
    centroid = np.average(fields.face_centroids[component], axis=0, weights=face_areas)
    vertices = mesh.vertices[np.unique(mesh.faces[component].ravel())]
    open_count, open_length = _open_boundary_statistics(mesh, adjacency, selected)
    core_area = float(fields.face_areas[component[orientation.core_candidate[component]]].sum())
    core_fraction = core_area / max(area, 1e-12)
    score = _confidence(
        float(np.percentile(slopes, 95)), float(config["orientation"]["max_slope_deg"]),
        core_fraction, loops, open_count > 0,
    )
    label = "HIGH_CONFIDENCE" if score >= 0.75 else ("CANDIDATE" if score >= 0.50 else "LOW_CONFIDENCE")
    warnings = list(loop_warnings)
    if open_count:
        warnings.append(
            f"Candidate touches {open_count} open mesh-cut edges ({open_length:.1f} mm); the physical transition may have been trimmed away"
        )
    return CandidateRegion(
        candidate_id, selected.copy(), area, int(len(component)), float(np.average(slopes, weights=face_areas)),
        float(np.percentile(slopes, 95)), float(np.max(slopes)), vertices.min(axis=0), vertices.max(axis=0),
        centroid, outer, internal, invalid, score, label, core_fraction, open_count > 0,
        open_count, open_length, True, warnings,
    ), warnings
