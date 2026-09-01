from __future__ import annotations

import numpy as np

from .models import Adjacency, BoundaryField, GeometryFields, Mesh


def _soft_range(values: np.ndarray, soft: float, hard: float) -> np.ndarray:
    return np.clip((values - soft) / max(hard - soft, 1e-12), 0.0, 1.0)


def _coherence_refine(cost: np.ndarray, adjacency: Adjacency, config: dict) -> np.ndarray:
    cfg = config["boundary"]
    high = float(cfg["coherence_high_score"])
    fraction_needed = float(cfg["coherence_neighbor_fraction"])
    fill_score = float(cfg["coherence_fill_score"])
    refined = cost.copy()
    for edge_index, (face_a, face_b) in enumerate(zip(adjacency.face_a, adjacency.face_b, strict=True)):
        nearby = {neighbor_edge for _, neighbor_edge in adjacency.neighbors[int(face_a)]}
        nearby.update(neighbor_edge for _, neighbor_edge in adjacency.neighbors[int(face_b)])
        nearby.discard(edge_index)
        if len(nearby) < 3:
            continue
        nearby_indices = np.fromiter(nearby, dtype=np.int64)
        high_fraction = float(np.mean(cost[nearby_indices] >= high))
        if high_fraction >= fraction_needed:
            refined[edge_index] = max(refined[edge_index], fill_score * high_fraction)
    return refined


def calculate_boundary_field(
    mesh: Mesh,
    adjacency: Adjacency,
    fields: GeometryFields,
    config: dict,
    paper_centers_mm: list[np.ndarray] | None = None,
) -> tuple[BoundaryField, list[str]]:
    a, b = adjacency.face_a, adjacency.face_b
    if len(a) == 0:
        empty = np.zeros(0)
        return BoundaryField(empty, empty, empty, empty, empty, empty, empty, empty, empty, np.zeros(0, bool), []), []
    dot = np.clip(np.einsum("ij,ij->i", fields.face_normals[a], fields.face_normals[b]), -1.0, 1.0)
    normal_angle = np.degrees(np.arccos(dot))
    center_delta = fields.face_centroids[b] - fields.face_centroids[a]
    distance = np.linalg.norm(center_delta, axis=1)
    concentration = normal_angle / np.maximum(distance, 1e-6)

    normal_delta = fields.face_normals[b] - fields.face_normals[a]
    signed_turn = np.einsum("ij,ij->i", normal_delta, center_delta / np.maximum(distance[:, None], 1e-12))
    curvature_cfg = config["curvature"]
    turn_strength = _soft_range(
        concentration,
        float(curvature_cfg["normal_turn_soft_deg_per_mm"]),
        float(curvature_cfg["normal_turn_hard_deg_per_mm"]),
    )
    boundary_cfg = config["boundary"]
    dihedral_strength = _soft_range(
        normal_angle,
        float(boundary_cfg["dihedral_soft_degrees"]),
        float(boundary_cfg["dihedral_hard_degrees"]),
    )
    ridge = turn_strength * np.clip(signed_turn * 8.0, 0.0, 1.0)
    valley = turn_strength * np.clip(-signed_turn * 8.0, 0.0, 1.0)

    scales = sorted(fields.multiscale_normal_variation_deg)
    fine_face = fields.multiscale_normal_variation_deg[scales[0]]
    large_face = fields.multiscale_normal_variation_deg[scales[-1]]
    local_contrast = np.maximum(0.0, 0.5 * (fine_face[a] + fine_face[b]) - 0.2 * (large_face[a] + large_face[b]))
    fine_strength = _soft_range(local_contrast, 2.0, 18.0)

    weights = boundary_cfg
    weighted_sum = (
        float(weights["normal_turn_weight"]) * turn_strength
        + float(weights["dihedral_weight"]) * dihedral_strength
        + float(weights["ridge_weight"]) * ridge
        + float(weights["valley_weight"]) * valley
        + float(weights["fine_scale_weight"]) * fine_strength
    )
    # A single decisive cue must be able to stop growth. A plain weighted average
    # incorrectly caps concentrated normal rotation below the segmentation cutoff.
    decisive_evidence = np.maximum.reduce((
        0.95 * turn_strength,
        0.90 * dihedral_strength,
        0.85 * ridge,
        0.90 * valley,
        0.75 * fine_strength,
    ))
    raw = np.clip(np.maximum(weighted_sum, decisive_evidence), 0.0, 1.0)
    unsuppressed = raw.copy()
    suppressed = np.zeros(len(raw), dtype=bool)
    warnings: list[str] = []
    paper_centers_mm = paper_centers_mm or []
    if paper_centers_mm:
        edge_midpoints = 0.5 * (mesh.vertices[adjacency.edge_u] + mesh.vertices[adjacency.edge_v])
        radius = float(config["markers"]["paper_suppression_radius_mm"])
        for index, center in enumerate(paper_centers_mm):
            radial_distance = np.linalg.norm(edge_midpoints - center[None, :], axis=1)
            within = radial_distance <= radius
            suppressed |= within
            surrounding_ring = (radial_distance >= 0.85 * radius) & (radial_distance <= 1.15 * radius)
            if np.any(unsuppressed[surrounding_ring] >= float(config["markers"]["overlap_warning_score"])):
                warnings.append(
                    f"Marker paper {index} overlaps strong structural evidence; relocate it away from seams/walls/steps"
                )
        raw[suppressed] *= float(config["markers"]["suppression_factor"])
    refined = _coherence_refine(raw, adjacency, config)
    refined[suppressed] = raw[suppressed]

    reasons: list[str] = []
    for index in range(len(raw)):
        if suppressed[index]:
            reasons.append("temporary marker paper: structural crossing cost suppressed")
        elif concentration[index] >= float(curvature_cfg["normal_turn_hard_deg_per_mm"]):
            reasons.append("concentrated normal rotation")
        elif valley[index] >= 0.5:
            reasons.append("concentrated concave relief")
        elif ridge[index] >= 0.5:
            reasons.append("concentrated convex relief")
        elif fine_strength[index] >= 0.5:
            reasons.append("fine-scale localized relief")
        else:
            reasons.append("gradual or low-salience transition")
    return BoundaryField(
        unsuppressed, refined, normal_angle, distance, concentration, signed_turn,
        ridge, valley, fine_strength, suppressed, reasons,
    ), warnings


def temporary_overlay_face_mask(
    face_centroids_mm: np.ndarray,
    paper_centers_mm: list[np.ndarray],
    radius_mm: float,
) -> np.ndarray:
    mask = np.zeros(len(face_centroids_mm), dtype=bool)
    for center in paper_centers_mm:
        mask |= np.linalg.norm(face_centroids_mm - center[None, :], axis=1) <= radius_mm
    return mask
