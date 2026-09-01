from __future__ import annotations

"""Physical surface development and barycentric curve lineage for AutoDeck V0.3."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any

import numpy as np

from .models import AcceptedSurfaceGrid


@dataclass(slots=True)
class DevelopmentMesh:
    patch_id: int
    vertices_mm: np.ndarray
    base_vertices_mm: np.ndarray
    faces: np.ndarray
    raster_rows: np.ndarray
    raster_cols: np.ndarray
    topology: dict[str, Any]
    base_surface_metrics: dict[str, Any]


@dataclass(slots=True)
class DevelopmentResult:
    patch_id: int
    strategy: str
    mesh: DevelopmentMesh
    uv_mm: np.ndarray
    status: str
    distortion_metrics: dict[str, Any]
    planarity_metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CurveDevelopmentMap:
    source_triangle_ids: np.ndarray
    barycentric_coordinates: np.ndarray
    distances_mm: np.ndarray
    flat_points_mm: np.ndarray
    status: str


def _box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(np.asarray(values, dtype=np.float64), radius, mode="constant")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
    size = 2 * radius + 1
    return (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )


def _normalized_box(values: np.ndarray, valid: np.ndarray, radius: int) -> np.ndarray:
    numerator = _box_sum(np.where(valid, values, 0.0), radius)
    denominator = _box_sum(valid.astype(np.float64), radius)
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)


def _distribution(values: np.ndarray) -> dict[str, float]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        return {name: 0.0 for name in ("mean", "rms", "p50", "p95", "p99", "maximum")}
    return {
        "mean": float(np.mean(finite)),
        "rms": float(np.sqrt(np.mean(finite ** 2))),
        "p50": float(np.percentile(finite, 50)),
        "p95": float(np.percentile(finite, 95)),
        "p99": float(np.percentile(finite, 99)),
        "maximum": float(np.max(finite)),
    }


def _edge_table(faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.sort(
        np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1
    )
    return np.unique(edges, axis=0, return_counts=True)


def _boundary_statistics(faces: np.ndarray) -> tuple[dict[str, int], np.ndarray]:
    edges, counts = _edge_table(faces)
    boundary = edges[counts == 1]
    nonmanifold = int(np.count_nonzero(counts > 2))
    if not len(boundary):
        return {
            "boundary_edge_count": 0,
            "boundary_component_count": 0,
            "closed_boundary_loop_count": 0,
            "open_boundary_chain_count": 0,
            "nonmanifold_edge_count": nonmanifold,
        }, np.empty(0, dtype=np.int64)
    vertices, degrees = np.unique(boundary, return_counts=True)
    neighbors: dict[int, list[int]] = {int(v): [] for v in vertices}
    for a, b in boundary:
        neighbors[int(a)].append(int(b)); neighbors[int(b)].append(int(a))
    visited: set[int] = set()
    components = 0; loops = 0; chains = 0
    for start in neighbors:
        if start in visited:
            continue
        components += 1; stack = [start]; component: list[int] = []
        while stack:
            vertex = stack.pop()
            if vertex in visited:
                continue
            visited.add(vertex); component.append(vertex); stack.extend(neighbors[vertex])
        if component and all(len(neighbors[vertex]) == 2 for vertex in component):
            loops += 1
        else:
            chains += 1
    return {
        "boundary_edge_count": int(len(boundary)),
        "boundary_component_count": components,
        "closed_boundary_loop_count": loops,
        "open_boundary_chain_count": chains,
        "nonmanifold_edge_count": nonmanifold,
    }, vertices.astype(np.int64)


def _face_component_count(faces: np.ndarray) -> int:
    if not len(faces):
        return 0
    edges = np.sort(
        np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1
    )
    owners = np.tile(np.arange(len(faces), dtype=np.int64), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0])); edges = edges[order]; owners = owners[order]
    parent = np.arange(len(faces), dtype=np.int64)

    def root(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]; value = int(parent[value])
        return value

    for index in range(1, len(edges)):
        if np.array_equal(edges[index], edges[index - 1]):
            a = root(int(owners[index - 1])); b = root(int(owners[index]))
            if a != b:
                parent[b] = a
    return len({root(index) for index in range(len(faces))})


def build_development_mesh(
    surface: AcceptedSurfaceGrid,
    patch_id: int,
    config: dict[str, Any],
) -> DevelopmentMesh:
    cfg = config["development"]
    mask = surface.patch_masks.get(patch_id, surface.accepted_mask)
    mask = np.asarray(mask, dtype=bool) & np.isfinite(surface.floor_height_mm)
    requested_resolution = float(cfg["mesh_resolution_mm"])
    stride = max(1, int(round(requested_resolution / surface.resolution_mm)))
    rows = np.arange(0, mask.shape[0], stride, dtype=np.int64)
    cols = np.arange(0, mask.shape[1], stride, dtype=np.int64)
    if rows[-1] != mask.shape[0] - 1:
        rows = np.r_[rows, mask.shape[0] - 1]
    if cols[-1] != mask.shape[1] - 1:
        cols = np.r_[cols, mask.shape[1] - 1]

    valid = mask[np.ix_(rows, cols)]
    vertex_grid = np.full(valid.shape, -1, dtype=np.int64)
    vertex_grid[valid] = np.arange(np.count_nonzero(valid), dtype=np.int64)
    rr, cc = np.nonzero(valid)
    raster_rows = rows[rr]; raster_cols = cols[cc]
    x = surface.x_min_mm + (raster_cols + 0.5) * surface.resolution_mm
    y = surface.y_min_mm + (raster_rows + 0.5) * surface.resolution_mm
    source_z = surface.floor_height_mm[raster_rows, raster_cols].astype(np.float64)

    radius = max(1, int(round(0.5 * float(cfg["base_surface_scale_mm"]) / surface.resolution_mm)))
    base_z_grid = _normalized_box(surface.floor_height_mm, mask, radius)
    base_z = base_z_grid[raster_rows, raster_cols]
    maximum_shift = float(cfg["maximum_base_displacement_mm"])
    base_z = np.clip(base_z, source_z - maximum_shift, source_z + maximum_shift)
    vertices = np.column_stack((x, y, source_z))
    base_vertices = np.column_stack((x, y, base_z))

    # A coarse cell is supported only when every underlying accepted raster
    # sample is accepted. This deliberately preserves even narrow voids.
    integral = np.pad(mask.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    r0 = rows[:-1, None]; r1 = rows[1:, None]
    c0 = cols[None, :-1]; c1 = cols[None, 1:]
    counts = integral[r1 + 1, c1 + 1] - integral[r0, c1 + 1] - integral[r1 + 1, c0] + integral[r0, c0]
    areas = (r1 - r0 + 1) * (c1 - c0 + 1)
    supported = counts == areas
    supported &= valid[:-1, :-1] & valid[1:, :-1] & valid[:-1, 1:] & valid[1:, 1:]
    cell_rows, cell_cols = np.nonzero(supported)
    a = vertex_grid[cell_rows, cell_cols]
    b = vertex_grid[cell_rows, cell_cols + 1]
    c = vertex_grid[cell_rows + 1, cell_cols + 1]
    d = vertex_grid[cell_rows + 1, cell_cols]
    faces = np.vstack((np.column_stack((a, b, c)), np.column_stack((a, c, d)))).astype(np.int64)
    if len(faces):
        cross = np.cross(
            base_vertices[faces[:, 1]] - base_vertices[faces[:, 0]],
            base_vertices[faces[:, 2]] - base_vertices[faces[:, 0]],
        )
        keep = np.linalg.norm(cross, axis=1) > float(cfg["minimum_triangle_area_mm2"]) * 2.0
        degenerate_removed = int(np.count_nonzero(~keep)); faces = faces[keep]
    else:
        degenerate_removed = 0

    if len(faces):
        used = np.unique(faces)
        remap = np.full(len(vertices), -1, dtype=np.int64); remap[used] = np.arange(len(used))
        vertices = vertices[used]; base_vertices = base_vertices[used]
        raster_rows = raster_rows[used]; raster_cols = raster_cols[used]; faces = remap[faces]
    boundary_stats, _boundary_vertices = _boundary_statistics(faces) if len(faces) else (
        {"boundary_edge_count": 0, "boundary_component_count": 0, "closed_boundary_loop_count": 0,
         "open_boundary_chain_count": 0, "nonmanifold_edge_count": 0}, np.empty(0, dtype=np.int64)
    )
    topology = {
        "vertex_count": int(len(vertices)),
        "triangle_count": int(len(faces)),
        "face_component_count": _face_component_count(faces),
        "degenerate_triangles_removed": degenerate_removed,
        "estimated_hole_count": max(0, boundary_stats["closed_boundary_loop_count"] - 1),
        "source_mask_pixel_count": int(np.count_nonzero(mask)),
        "requested_resolution_mm": requested_resolution,
        "actual_grid_step_mm": stride * surface.resolution_mm,
        "hole_fill_performed": False,
        **boundary_stats,
    }
    displacement = base_z - source_z if len(source_z) else np.empty(0)
    base_surface_metrics = {
        "smoothing_scale_mm": float(cfg["base_surface_scale_mm"]),
        "maximum_allowed_displacement_mm": maximum_shift,
        "signed_displacement_mm": _distribution(displacement),
        "absolute_displacement_mm": _distribution(np.abs(displacement)),
    }
    return DevelopmentMesh(
        patch_id, vertices, base_vertices, faces, raster_rows, raster_cols,
        topology, base_surface_metrics,
    )


def _fit_plane(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    origin = np.mean(vertices, axis=0)
    _values, vectors = np.linalg.eigh((vertices - origin).T @ (vertices - origin) / max(len(vertices), 1))
    normal = vectors[:, 0]
    if normal[2] < 0:
        normal *= -1
    world_x = np.array([1.0, 0.0, 0.0]); u = world_x - np.dot(world_x, normal) * normal
    if np.linalg.norm(u) < 1e-9:
        u = np.array([0.0, 1.0, 0.0]) - normal[1] * normal
    u /= np.linalg.norm(u); v = np.cross(normal, u); v /= np.linalg.norm(v)
    residual = np.abs((vertices - origin) @ normal)
    metrics = {
        "sample_count": int(len(vertices)),
        "absolute_residual_mm": _distribution(residual),
        "origin_mm": origin.tolist(), "normal": normal.tolist(),
        "u_axis": u.tolist(), "v_axis": v.tolist(),
    }
    return origin, u, v, normal, metrics


def _triangle_angles(points: np.ndarray) -> np.ndarray:
    a = np.linalg.norm(points[:, 1] - points[:, 2], axis=1)
    b = np.linalg.norm(points[:, 0] - points[:, 2], axis=1)
    c = np.linalg.norm(points[:, 0] - points[:, 1], axis=1)
    angles = []
    for opposite, side1, side2 in ((a, b, c), (b, a, c), (c, a, b)):
        cosine = (side1 ** 2 + side2 ** 2 - opposite ** 2) / np.maximum(2 * side1 * side2, 1e-12)
        angles.append(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))
    return np.column_stack(angles)


def _geodesic_boundary_pins(
    vertices: np.ndarray, faces: np.ndarray, boundary: np.ndarray
) -> tuple[int, int, float, str]:
    """Choose deterministic far-apart boundary pins and their mesh distance.

    The distance is NOT advisory: `develop` passes it to LSCM as
    ``pin_uv = [[0, 0], [distance, 0]]``, which fixes the developed panel's
    scale outright, and ARAP does not wash it out.  Measured on a 5.4 m curved
    deck patch, substituting the shortest path along mesh EDGES for the true
    surface geodesic stretched the developed panel by +7.36% (+394 mm); the
    heat method was -2.13% (-114 mm).

    So the Dijkstra branch is a last-resort estimate, not an equivalent.  It
    reports its name back to the caller, which downgrades the panel rather
    than shipping a silently mis-scaled one.
    """

    import igl  # type: ignore
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    first = int(boundary[np.argmin(vertices[boundary, 0])])
    try:
        exact = np.asarray(igl.exact_geodesic(
            vertices, faces, np.asarray([first], dtype=np.int64), np.empty(0, dtype=np.int64),
            boundary.astype(np.int64), np.empty(0, dtype=np.int64),
        ), dtype=np.float64)
        second_offset = int(np.argmax(exact))
        if np.isfinite(exact[second_offset]) and exact[second_offset] > 0:
            return first, int(boundary[second_offset]), float(exact[second_offset]), "exact"
    except RuntimeError:
        pass
    edges, _counts = _edge_table(faces)
    weights = np.linalg.norm(vertices[edges[:, 1]] - vertices[edges[:, 0]], axis=1)
    graph = coo_matrix(
        (np.r_[weights, weights], (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(len(vertices), len(vertices)),
    ).tocsr()
    distances = np.asarray(dijkstra(graph, directed=False, indices=first), dtype=np.float64)
    boundary_distances = distances[boundary]
    second = int(boundary[np.argmax(boundary_distances)])
    return first, second, float(distances[second]), "edge-graph"


def development_distortion(
    vertices: np.ndarray,
    faces: np.ndarray,
    uv: np.ndarray,
    minimum_edge_mm: float,
) -> dict[str, Any]:
    edges, _counts = _edge_table(faces)
    source_length = np.linalg.norm(vertices[edges[:, 1]] - vertices[edges[:, 0]], axis=1)
    flat_length = np.linalg.norm(uv[edges[:, 1]] - uv[edges[:, 0]], axis=1)
    keep = source_length >= minimum_edge_mm
    strain = 100.0 * (flat_length[keep] - source_length[keep]) / np.maximum(source_length[keep], 1e-12)
    tri3 = vertices[faces]; tri2 = uv[faces]
    area3 = 0.5 * np.linalg.norm(np.cross(tri3[:, 1] - tri3[:, 0], tri3[:, 2] - tri3[:, 0]), axis=1)
    signed_area2 = 0.5 * (
        (tri2[:, 1, 0] - tri2[:, 0, 0]) * (tri2[:, 2, 1] - tri2[:, 0, 1])
        - (tri2[:, 1, 1] - tri2[:, 0, 1]) * (tri2[:, 2, 0] - tri2[:, 0, 0])
    )
    nonzero = np.abs(signed_area2) > 1e-12
    dominant_sign = 1.0 if np.count_nonzero(signed_area2[nonzero] > 0) >= np.count_nonzero(signed_area2[nonzero] < 0) else -1.0
    flips = int(np.count_nonzero(signed_area2 * dominant_sign <= 1e-12))
    area_strain = 100.0 * (np.abs(signed_area2) - area3) / np.maximum(area3, 1e-12)
    angle_change = np.abs(_triangle_angles(np.dstack((tri2, np.zeros((len(tri2), 3, 1))))) - _triangle_angles(tri3)).ravel()
    absolute = np.abs(strain)
    return {
        "minimum_source_edge_length_mm": minimum_edge_mm,
        "included_edge_count": int(np.count_nonzero(keep)),
        "excluded_short_edge_count": int(np.count_nonzero(~keep)),
        "edge_strain_percent": _distribution(strain),
        "absolute_edge_strain_percent": _distribution(absolute),
        "triangle_area_strain_percent": _distribution(area_strain),
        "triangle_angle_change_deg": _distribution(angle_change),
        "flipped_or_collapsed_triangle_count": flips,
        "source_area_mm2": float(area3.sum()),
        "flat_area_mm2": float(np.abs(signed_area2).sum()),
        "area_change_percent": float(100.0 * (np.abs(signed_area2).sum() - area3.sum()) / max(area3.sum(), 1e-12)),
    }


class SurfaceDevelopmentStrategy(ABC):
    name: str

    @abstractmethod
    def develop(self, mesh: DevelopmentMesh, config: dict[str, Any]) -> DevelopmentResult:
        raise NotImplementedError


class RigidPlanarDevelopment(SurfaceDevelopmentStrategy):
    name = "rigid-planar"

    def develop(self, mesh: DevelopmentMesh, config: dict[str, Any]) -> DevelopmentResult:
        origin, u, v, _normal, planarity = _fit_plane(mesh.base_vertices_mm)
        delta = mesh.base_vertices_mm - origin
        uv = np.column_stack((delta @ u, delta @ v))
        distortion = development_distortion(
            mesh.base_vertices_mm, mesh.faces, uv,
            float(config["development"]["minimum_distortion_edge_mm"]),
        )
        status, warnings = _development_status(distortion, config)
        return DevelopmentResult(mesh.patch_id, self.name, mesh, uv, status, distortion, planarity, warnings)


class IntrinsicMeshDevelopment(SurfaceDevelopmentStrategy):
    name = "intrinsic-lscm-arap"

    def develop(self, mesh: DevelopmentMesh, config: dict[str, Any]) -> DevelopmentResult:
        warnings: list[str] = []
        planarity = _fit_plane(mesh.base_vertices_mm)[4]
        if mesh.topology["nonmanifold_edge_count"] or mesh.topology["open_boundary_chain_count"]:
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                ["INTRINSIC DEVELOPMENT FAILURE: development mesh is not a manifold with closed boundary loops."],
            )
        if mesh.topology["face_component_count"] != 1 or not len(mesh.faces):
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                ["INTRINSIC DEVELOPMENT FAILURE: development mesh is empty or disconnected."],
            )
        try:
            import igl  # type: ignore
        except ImportError:
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                ["INTRINSIC DEVELOPMENT FAILURE: pinned libigl dependency is unavailable."],
            )
        boundary = np.asarray(igl.boundary_loop(mesh.faces), dtype=np.int64)
        if len(boundary) < 2:
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                ["INTRINSIC DEVELOPMENT FAILURE: no usable outer boundary was found."],
            )
        first, second, pin_distance, pin_method = _geodesic_boundary_pins(
            mesh.base_vertices_mm, mesh.faces, boundary
        )
        if pin_method != "exact":
            # The pin distance sets this panel's scale (see _geodesic_boundary_pins).
            # An estimate here mis-scales every cut dimension, so refuse rather
            # than emit a plausible-looking DXF that is several hundred mm out.
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                [
                    "INTRINSIC DEVELOPMENT FAILURE: exact geodesic pin distance unavailable "
                    f"(fell back to '{pin_method}'). That estimate has been measured at up to "
                    "+7.4% on a 5 m panel, which would scale every cut dimension, so the panel "
                    "is rejected instead of developed."
                ],
            )
        pins = np.asarray([first, second], dtype=np.int64)
        pin_uv = np.asarray([[0.0, 0.0], [pin_distance, 0.0]], dtype=np.float64)
        try:
            uv, _energy = igl.lscm(mesh.base_vertices_mm, mesh.faces, pins, pin_uv)
            data = igl.ARAPData(); data.max_iter = int(config["development"]["arap_iterations"])
            igl.arap_precomputation(mesh.base_vertices_mm, mesh.faces, 2, pins.astype(np.int32), data)
            uv = np.asarray(igl.arap_solve(pin_uv, data, uv), dtype=np.float64)
        except (RuntimeError, ValueError) as exc:
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                [f"INTRINSIC DEVELOPMENT FAILURE: libigl LSCM/ARAP failed: {exc}"],
            )
        if not np.all(np.isfinite(uv)):
            return DevelopmentResult(
                mesh.patch_id, self.name, mesh, np.empty((0, 2)), "INVALID", {}, planarity,
                ["INTRINSIC DEVELOPMENT FAILURE: non-finite UV coordinates."],
            )
        # Preserve a deterministic, right-handed orientation and align U toward
        # world +X without changing intrinsic scale.
        tri = uv[mesh.faces]
        signed = (tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) - (tri[:, 1, 1] - tri[:, 0, 1]) * (tri[:, 2, 0] - tri[:, 0, 0])
        if np.count_nonzero(signed < 0) > np.count_nonzero(signed > 0):
            uv[:, 1] *= -1
        centered_uv = uv - np.mean(uv, axis=0)
        centered_xy = mesh.base_vertices_mm[:, :2] - np.mean(mesh.base_vertices_mm[:, :2], axis=0)
        left, _singular, right = np.linalg.svd(centered_uv.T @ centered_xy)
        rotation = left @ right
        if np.linalg.det(rotation) < 0:
            left[:, -1] *= -1; rotation = left @ right
        uv = centered_uv @ rotation
        distortion = development_distortion(
            mesh.base_vertices_mm, mesh.faces, uv,
            float(config["development"]["minimum_distortion_edge_mm"]),
        )
        status, status_warnings = _development_status(distortion, config); warnings.extend(status_warnings)
        return DevelopmentResult(mesh.patch_id, self.name, mesh, uv, status, distortion, planarity, warnings)


def _development_status(distortion: dict[str, Any], config: dict[str, Any]) -> tuple[str, list[str]]:
    if int(distortion.get("flipped_or_collapsed_triangle_count", 0)):
        return "INVALID", ["DEVELOPMENT FAILURE: flipped or collapsed UV triangles were detected."]
    p95 = float(distortion.get("absolute_edge_strain_percent", {}).get("p95", math.inf))
    warning = float(config["development"]["distortion_warning_p95_percent"])
    failure = float(config["development"]["distortion_failure_p95_percent"])
    if p95 > failure:
        return "INVALID", [f"DEVELOPMENT FAILURE: P95 absolute edge strain {p95:.3f}% exceeds {failure:.3f}%."]
    if p95 > warning:
        return "NEEDS_REVIEW", [f"DEVELOPMENT REVIEW: P95 absolute edge strain {p95:.3f}% exceeds {warning:.3f}%; consider a later panel split."]
    return "GOOD", []


def develop_patch(surface: AcceptedSurfaceGrid, patch_id: int, config: dict[str, Any]) -> DevelopmentResult:
    mesh = build_development_mesh(surface, patch_id, config)
    if not len(mesh.faces):
        return DevelopmentResult(
            patch_id, "none", mesh, np.empty((0, 2)), "INVALID", {}, {},
            ["DEVELOPMENT FAILURE: physical development mesh contains no triangles."],
        )
    planarity = _fit_plane(mesh.base_vertices_mm)[4]
    residual = planarity["absolute_residual_mm"]
    requested = str(config["development"].get("strategy", "auto")).lower()
    planar = (
        float(residual["rms"]) <= float(config["development"]["planar_residual_rms_mm"])
        and float(residual["p95"]) <= float(config["development"]["planar_residual_p95_mm"])
    )
    if requested in {"planar", "rigid-planar"} or (requested == "auto" and planar):
        return RigidPlanarDevelopment().develop(mesh, config)
    if requested in {"intrinsic", "mesh", "lscm-arap"} or requested == "auto":
        return IntrinsicMeshDevelopment().develop(mesh, config)
    raise ValueError("development.strategy must be auto, rigid-planar, or intrinsic")


def _barycentric(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    a = triangles[:, 0]; v0 = triangles[:, 1] - a; v1 = triangles[:, 2] - a; v2 = points - a
    d00 = np.einsum("ij,ij->i", v0, v0); d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1); d20 = np.einsum("ij,ij->i", v2, v0); d21 = np.einsum("ij,ij->i", v2, v1)
    denominator = d00 * d11 - d01 * d01
    v = np.divide(d11 * d20 - d01 * d21, denominator, out=np.zeros_like(d20), where=np.abs(denominator) > 1e-16)
    w = np.divide(d00 * d21 - d01 * d20, denominator, out=np.zeros_like(d20), where=np.abs(denominator) > 1e-16)
    return np.column_stack((1.0 - v - w, v, w))


def map_curve_to_development(
    points_mm: np.ndarray,
    result: DevelopmentResult,
    maximum_distance_mm: float,
) -> CurveDevelopmentMap:
    if result.status == "INVALID" or not len(result.uv_mm):
        return CurveDevelopmentMap(np.empty(0, dtype=np.int64), np.empty((0, 3)), np.empty(0), np.empty((0, 3)), "INVALID")
    import igl  # type: ignore
    squared, triangle_ids, closest = igl.point_mesh_squared_distance(
        np.asarray(points_mm, dtype=np.float64), result.mesh.base_vertices_mm, result.mesh.faces
    )
    triangles = result.mesh.base_vertices_mm[result.mesh.faces[triangle_ids]]
    barycentric = _barycentric(closest, triangles)
    flat_xy = np.einsum("ni,nij->nj", barycentric, result.uv_mm[result.mesh.faces[triangle_ids]])
    distances = np.sqrt(np.maximum(squared, 0.0))
    status = "GOOD" if np.all(distances <= maximum_distance_mm) else "NEEDS_REVIEW"
    return CurveDevelopmentMap(
        triangle_ids.astype(np.int64), barycentric, distances,
        np.column_stack((flat_xy, np.zeros(len(flat_xy)))), status,
    )


def backproject_flat_points(
    points_mm: np.ndarray,
    result: DevelopmentResult,
    maximum_distance_mm: float,
) -> tuple[np.ndarray, CurveDevelopmentMap]:
    import igl  # type: ignore
    flat_vertices = np.column_stack((result.uv_mm, np.zeros(len(result.uv_mm))))
    query = np.column_stack((np.asarray(points_mm)[:, :2], np.zeros(len(points_mm))))
    squared, triangle_ids, closest = igl.point_mesh_squared_distance(query, flat_vertices, result.mesh.faces)
    triangles = flat_vertices[result.mesh.faces[triangle_ids]]
    barycentric = _barycentric(closest, triangles)
    points3d = np.einsum("ni,nij->nj", barycentric, result.mesh.base_vertices_mm[result.mesh.faces[triangle_ids]])
    distances = np.sqrt(np.maximum(squared, 0.0))
    status = "GOOD" if np.all(distances <= maximum_distance_mm) else "NEEDS_REVIEW"
    mapping = CurveDevelopmentMap(
        triangle_ids.astype(np.int64), barycentric, distances,
        np.column_stack((closest[:, :2], np.zeros(len(closest)))), status,
    )
    return points3d, mapping


def save_development_artifact(path: Path, result: DevelopmentResult) -> None:
    np.savez_compressed(
        path,
        patch_id=np.asarray([result.patch_id], dtype=np.int32),
        vertices_mm=result.mesh.vertices_mm.astype(np.float32),
        base_vertices_mm=result.mesh.base_vertices_mm.astype(np.float32),
        faces=result.mesh.faces.astype(np.int32),
        uv_mm=result.uv_mm.astype(np.float32),
        raster_rows=result.mesh.raster_rows.astype(np.int32),
        raster_cols=result.mesh.raster_cols.astype(np.int32),
    )
