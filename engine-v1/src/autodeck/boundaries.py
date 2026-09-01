from __future__ import annotations

from collections import defaultdict

import numpy as np

from .models import Adjacency, BoundaryField, BoundaryLoop, Mesh


def _length(vertices: np.ndarray, indices: list[int], closed: bool) -> float:
    if len(indices) < 2:
        return 0.0
    points = vertices[indices]
    total = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
    if closed and indices[0] != indices[-1]:
        total += float(np.linalg.norm(points[-1] - points[0]))
    return total


def _rdp_indices(points: np.ndarray, tolerance: float) -> list[int]:
    if len(points) <= 2:
        return list(range(len(points)))
    start, end = points[0], points[-1]
    line = end - start
    line_length = float(np.linalg.norm(line))
    if line_length < 1e-12:
        distances = np.linalg.norm(points[1:-1] - start, axis=1)
    else:
        distances = np.linalg.norm(np.cross(points[1:-1] - start, line), axis=1) / line_length
    if len(distances) == 0 or float(distances.max()) <= tolerance:
        return [0, len(points) - 1]
    split = int(np.argmax(distances)) + 1
    left = _rdp_indices(points[: split + 1], tolerance)
    right = _rdp_indices(points[split:], tolerance)
    return left[:-1] + [split + index for index in right]


def _simplify_loop(vertices: np.ndarray, indices: list[int], closed: bool, tolerance: float) -> list[int]:
    if tolerance <= 0 or len(indices) <= 4:
        return indices
    if not closed:
        keep = _rdp_indices(vertices[indices], tolerance)
        return [indices[index] for index in keep]
    ring = indices[:-1] if indices[0] == indices[-1] else list(indices)
    if len(ring) <= 4:
        return ring + [ring[0]]
    farthest = int(np.argmax(np.linalg.norm(vertices[ring] - vertices[ring[0]], axis=1)))
    arc1 = ring[: farthest + 1]
    arc2 = ring[farthest:] + [ring[0]]
    keep1 = [arc1[index] for index in _rdp_indices(vertices[arc1], tolerance)]
    keep2 = [arc2[index] for index in _rdp_indices(vertices[arc2], tolerance)]
    combined = keep1[:-1] + keep2
    if combined[-1] != combined[0]:
        combined.append(combined[0])
    return combined


def extract_boundary_loops(
    mesh: Mesh,
    adjacency: Adjacency,
    boundary: BoundaryField,
    selected: np.ndarray,
    config: dict,
) -> tuple[list[BoundaryLoop], list[str]]:
    incident: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    for face_index, face in enumerate(mesh.faces):
        for u, v in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            incident[(int(min(u, v)), int(max(u, v)))].append((face_index, int(u), int(v)))
    boundary_edges: list[tuple[int, int]] = []
    for records in incident.values():
        selected_records = [record for record in records if selected[record[0]]]
        if len(selected_records) == 1 and len(selected_records) != len(records) or len(records) == 1 and len(selected_records) == 1:
            boundary_edges.append((selected_records[0][1], selected_records[0][2]))

    per_vertex: dict[int, list[tuple[int, int]]] = defaultdict(list)
    unused: set[tuple[int, int]] = set()
    for u, v in boundary_edges:
        key = (min(u, v), max(u, v)); unused.add(key)
        per_vertex[u].append(key); per_vertex[v].append(key)
    raw_loops: list[tuple[list[int], bool]] = []
    warnings: list[str] = []
    while unused:
        first = next(iter(unused))
        component_vertices = [vertex for vertex in first if any(edge in unused for edge in per_vertex[vertex])]
        start = next((vertex for vertex in component_vertices if sum(edge in unused for edge in per_vertex[vertex]) == 1), first[0])
        chain = [start]; current = start
        while True:
            candidates = [edge for edge in per_vertex[current] if edge in unused]
            if not candidates:
                break
            edge = candidates[0]
            unused.remove(edge)
            current = edge[1] if edge[0] == current else edge[0]
            chain.append(current)
            if current == start:
                break
        closed = len(chain) > 2 and chain[-1] == chain[0]
        raw_loops.append((chain, closed))
        if not closed:
            warnings.append("Open/invalid region contour detected")

    edge_cost: dict[tuple[int, int], float] = {
        (int(min(u, v)), int(max(u, v))): float(cost)
        for u, v, cost in zip(adjacency.edge_u, adjacency.edge_v, boundary.refined_cost, strict=True)
    }
    tolerance = float(config["boundary_curve"]["simplify_tolerance_mm"])
    minimum_length = float(config["segmentation"]["minimum_loop_length_mm"])
    loops: list[BoundaryLoop] = []
    for indices, closed in raw_loops:
        indices = _simplify_loop(mesh.vertices, indices, closed, tolerance)
        length = _length(mesh.vertices, indices, closed)
        if length < minimum_length:
            continue
        costs = [edge_cost.get((min(a, b), max(a, b)), 0.5) for a, b in zip(indices[:-1], indices[1:], strict=True)]
        confidence = float(np.mean(costs)) if costs else 0.0
        loops.append(BoundaryLoop(indices, closed, "unclassified", length, confidence))

    closed_indices = [index for index, loop in enumerate(loops) if loop.is_closed]
    if closed_indices:
        outer_index = max(closed_indices, key=lambda index: loops[index].length_mm)
        for index, loop in enumerate(loops):
            loop.kind = "outer" if index == outer_index else ("internal_exclusion" if loop.is_closed else "invalid")
    else:
        for loop in loops:
            loop.kind = "invalid"
    return loops, warnings


def extract_outer_boundary_only(
    mesh: Mesh,
    adjacency: Adjacency,
    boundary: BoundaryField,
    selected: np.ndarray,
    config: dict,
) -> tuple[list[BoundaryLoop], list[str]]:
    """Extract only the largest closed selection contour from adjacency edges.

    V0.2 derives obstacles from top-view voids, so automatic orientation mode no
    longer needs to materialize thousands of microscopic selection-hole loops.
    """
    crossing = selected[adjacency.face_a] ^ selected[adjacency.face_b]
    edge_u = adjacency.edge_u[crossing].astype(np.int64, copy=False)
    edge_v = adjacency.edge_v[crossing].astype(np.int64, copy=False)
    edge_cost = boundary.refined_cost[crossing].astype(float, copy=False)
    open_use = selected[adjacency.boundary_face]
    if np.any(open_use):
        edge_u = np.concatenate((edge_u, adjacency.boundary_u[open_use]))
        edge_v = np.concatenate((edge_v, adjacency.boundary_v[open_use]))
        edge_cost = np.concatenate((edge_cost, np.full(np.count_nonzero(open_use), 0.5)))
    if not len(edge_u):
        return [], ["No boundary edges were available for the selected region"]

    per_vertex: dict[int, list[int]] = defaultdict(list)
    for edge_index, (u, v) in enumerate(zip(edge_u, edge_v, strict=True)):
        per_vertex[int(u)].append(edge_index); per_vertex[int(v)].append(edge_index)
    unused = set(range(len(edge_u)))
    best_chain: list[int] | None = None; best_length = 0.0; best_costs: list[float] = []
    open_chain_count = 0
    while unused:
        first_edge = next(iter(unused))
        u0, v0 = int(edge_u[first_edge]), int(edge_v[first_edge])
        start = u0 if sum(index in unused for index in per_vertex[u0]) == 1 else v0
        chain = [start]; costs: list[float] = []; current = start
        while True:
            choices = [index for index in per_vertex[current] if index in unused]
            if not choices:
                break
            edge_index = choices[0]; unused.remove(edge_index); costs.append(float(edge_cost[edge_index]))
            u, v = int(edge_u[edge_index]), int(edge_v[edge_index])
            current = v if u == current else u; chain.append(current)
            if current == start:
                break
        closed = len(chain) > 2 and chain[-1] == chain[0]
        if not closed:
            open_chain_count += 1; continue
        length = _length(mesh.vertices, chain, True)
        if length > best_length:
            best_chain, best_length, best_costs = chain, length, costs

    warnings = [f"Ignored {open_chain_count} open/invalid contour chain(s) while selecting the outer perimeter"] if open_chain_count else []
    if best_chain is None:
        return [], warnings + ["No closed primary outer contour was found"]
    simplified = _simplify_loop(
        mesh.vertices, best_chain, True, float(config["boundary_curve"]["simplify_tolerance_mm"])
    )
    length = _length(mesh.vertices, simplified, True)
    confidence = float(np.mean(best_costs)) if best_costs else 0.0
    return [BoundaryLoop(simplified, True, "outer", length, confidence)], warnings


def extract_internal_structural_boundaries(
    mesh: Mesh,
    adjacency: Adjacency,
    boundary: BoundaryField,
    selected: np.ndarray,
    config: dict,
) -> list[BoundaryLoop]:
    """Retain strong structural edges inside a parent orientation candidate.

    These lines are refinement/debug evidence. They do not cut connectivity in
    orientation mode, which lets a hatch seam remain visible without destroying
    the surrounding cockpit-floor candidate.
    """
    threshold = float(config["segmentation"]["max_boundary_cost"])
    edge_mask = (
        selected[adjacency.face_a]
        & selected[adjacency.face_b]
        & (boundary.refined_cost > threshold)
    )
    selected_edges = np.flatnonzero(edge_mask)
    if not len(selected_edges):
        return []

    per_vertex: dict[int, list[int]] = defaultdict(list)
    for edge_index in selected_edges:
        u = int(adjacency.edge_u[edge_index]); v = int(adjacency.edge_v[edge_index])
        per_vertex[u].append(int(edge_index)); per_vertex[v].append(int(edge_index))
    unused = set(int(index) for index in selected_edges)
    tolerance = float(config["boundary_curve"]["simplify_tolerance_mm"])
    minimum_length = float(config["segmentation"]["minimum_loop_length_mm"])
    result: list[BoundaryLoop] = []
    while unused:
        first_edge = next(iter(unused))
        first_u = int(adjacency.edge_u[first_edge]); first_v = int(adjacency.edge_v[first_edge])
        start = first_u if sum(index in unused for index in per_vertex[first_u]) == 1 else first_v
        chain = [start]
        costs: list[float] = []
        current = start
        while True:
            choices = [index for index in per_vertex[current] if index in unused]
            if not choices:
                break
            edge_index = choices[0]
            unused.remove(edge_index)
            costs.append(float(boundary.refined_cost[edge_index]))
            u = int(adjacency.edge_u[edge_index]); v = int(adjacency.edge_v[edge_index])
            current = v if u == current else u
            chain.append(current)
            if current == start:
                break
        closed = len(chain) > 2 and chain[-1] == chain[0]
        original_length = _length(mesh.vertices, chain, closed)
        if original_length < minimum_length:
            continue
        simplified = _simplify_loop(mesh.vertices, chain, closed, tolerance)
        result.append(BoundaryLoop(
            simplified,
            closed,
            "internal_boundary",
            _length(mesh.vertices, simplified, closed),
            float(np.mean(costs)) if costs else 0.0,
        ))
    return result


def boundary_cost_for_faces(adjacency: Adjacency, boundary: BoundaryField, face_count: int) -> np.ndarray:
    total = np.zeros(face_count); counts = np.zeros(face_count)
    np.add.at(total, adjacency.face_a, boundary.refined_cost)
    np.add.at(total, adjacency.face_b, boundary.refined_cost)
    np.add.at(counts, adjacency.face_a, 1.0)
    np.add.at(counts, adjacency.face_b, 1.0)
    return total / np.maximum(counts, 1.0)
