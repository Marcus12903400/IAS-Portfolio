from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .boundaries import boundary_cost_for_faces
from .models import Adjacency, BoundaryField, GeometryFields, Mesh, OrientationField


def save_boundary_npz(path: Path, adjacency: Adjacency, boundary: BoundaryField) -> None:
    np.savez_compressed(
        path,
        face_a=adjacency.face_a,
        face_b=adjacency.face_b,
        edge_u=adjacency.edge_u,
        edge_v=adjacency.edge_v,
        raw_cost=boundary.raw_cost,
        refined_cost=boundary.refined_cost,
        normal_angle_deg=boundary.normal_angle_deg,
        transition_distance_mm=boundary.transition_distance_mm,
        normal_turn_deg_per_mm=boundary.normal_turn_deg_per_mm,
        signed_turn=boundary.signed_turn,
        ridge_strength=boundary.ridge_strength,
        valley_strength=boundary.valley_strength,
        fine_scale_strength=boundary.fine_scale_strength,
        suppressed=boundary.suppressed,
    )


def save_boundary_summary(path: Path, adjacency: Adjacency, boundary: BoundaryField, limit: int = 500) -> None:
    order = np.argsort(boundary.refined_cost)[::-1][:limit]
    transitions = []
    for rank, edge_index in enumerate(order):
        index = int(edge_index)
        transitions.append({
            "rank": rank + 1,
            "edge_index": index,
            "faces": [int(adjacency.face_a[index]), int(adjacency.face_b[index])],
            "vertices": [int(adjacency.edge_u[index]), int(adjacency.edge_v[index])],
            "raw_cost": float(boundary.raw_cost[index]),
            "refined_cost": float(boundary.refined_cost[index]),
            "normal_angle_degrees": float(boundary.normal_angle_deg[index]),
            "transition_distance_mm": float(boundary.transition_distance_mm[index]),
            "normal_turn_degrees_per_mm": float(boundary.normal_turn_deg_per_mm[index]),
            "ridge_strength": float(boundary.ridge_strength[index]),
            "valley_strength": float(boundary.valley_strength[index]),
            "fine_scale_strength": float(boundary.fine_scale_strength[index]),
            "temporary_overlay_suppressed": bool(boundary.suppressed[index]),
            "reason": boundary.reasons[index],
            "confidence": float(boundary.refined_cost[index]),
        })
    payload = {
        "transition_count": int(len(boundary.refined_cost)),
        "listed_highest_cost_transitions": int(len(transitions)),
        "full_numeric_data": "boundary_scores.npz",
        "transitions": transitions,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_colored_ply(
    path: Path,
    mesh: Mesh,
    adjacency: Adjacency,
    boundary: BoundaryField,
    selected: np.ndarray,
) -> None:
    face_cost = boundary_cost_for_faces(adjacency, boundary, len(mesh.faces))
    red = np.clip(255.0 * face_cost, 0, 255).astype(np.uint8)
    blue = np.clip(255.0 * (1.0 - face_cost), 0, 255).astype(np.uint8)
    green = np.where(selected, 90, 15).astype(np.uint8)
    lines = [
        "ply", "format ascii 1.0", f"element vertex {len(mesh.vertices)}",
        "property float x", "property float y", "property float z",
        f"element face {len(mesh.faces)}", "property list uchar int vertex_indices",
        "property uchar red", "property uchar green", "property uchar blue", "end_header",
    ]
    lines.extend(f"{v[0]:.9g} {v[1]:.9g} {v[2]:.9g}" for v in mesh.vertices)
    lines.extend(
        f"3 {face[0]} {face[1]} {face[2]} {red[index]} {green[index]} {blue[index]}"
        for index, face in enumerate(mesh.faces)
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_slope_bands_ply(
    path: Path,
    mesh: Mesh,
    orientation: OrientationField,
    core_slope_deg: float = 22.0,
    max_slope_deg: float = 25.0,
) -> None:
    """Write face colors for 0-10, 10-20, 20-22, 22-25, and >25 degree bands."""
    slope = orientation.smoothed_slope_deg
    colors = np.zeros((len(mesh.faces), 3), dtype=np.uint8)
    colors[:] = [120, 120, 120]  # downward/back-facing
    upward = orientation.upward_facing
    colors[upward & (slope <= 10.0)] = [30, 100, 255]
    colors[upward & (slope > 10.0) & (slope <= 20.0)] = [40, 220, 80]
    colors[upward & (slope > 20.0) & (slope <= core_slope_deg)] = [240, 230, 40]
    colors[upward & (slope > core_slope_deg) & (slope <= max_slope_deg)] = [255, 145, 20]
    colors[upward & (slope > max_slope_deg)] = [230, 35, 35]
    lines = [
        "ply", "format ascii 1.0", f"element vertex {len(mesh.vertices)}",
        "property float x", "property float y", "property float z",
        f"element face {len(mesh.faces)}", "property list uchar int vertex_indices",
        "property uchar red", "property uchar green", "property uchar blue", "end_header",
    ]
    lines.extend(f"{v[0]:.9g} {v[1]:.9g} {v[2]:.9g}" for v in mesh.vertices)
    lines.extend(
        f"3 {face[0]} {face[1]} {face[2]} {colors[index, 0]} {colors[index, 1]} {colors[index, 2]}"
        for index, face in enumerate(mesh.faces)
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_orientation_npz(path: Path, orientation: OrientationField) -> None:
    np.savez_compressed(
        path,
        smoothed_face_normals=orientation.smoothed_face_normals,
        raw_slope_deg=orientation.raw_slope_deg,
        smoothed_slope_deg=orientation.smoothed_slope_deg,
        upward_facing=orientation.upward_facing,
        core_candidate=orientation.core_candidate,
        fringe_candidate=orientation.fringe_candidate,
        recovered_noise_faces=orientation.recovered_noise_faces,
    )


def geometry_metrics(fields: GeometryFields) -> dict[str, object]:
    def summary(values: np.ndarray) -> dict[str, float]:
        return {
            "min": float(np.min(values)), "median": float(np.median(values)),
            "p95": float(np.percentile(values, 95)), "max": float(np.max(values)),
        }
    return {
        "principal_k1_per_mm": summary(fields.principal_k1),
        "principal_k2_per_mm": summary(fields.principal_k2),
        "mean_curvature_per_mm": summary(fields.mean_curvature),
        "gaussian_curvature_per_mm2": summary(fields.gaussian_curvature),
        "conformability": summary(fields.conformability),
        "multiscale_normal_variation_degrees": {
            str(scale): summary(values) for scale, values in fields.multiscale_normal_variation_deg.items()
        },
    }
