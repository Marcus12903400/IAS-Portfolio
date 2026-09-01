from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np

from .models import Mesh


_UNIT_RE = re.compile(r"^\s*#\s*(?:units?|unit)\s*[:=]\s*(mm|cm|m|in)\s*$", re.I)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _obj_index(token: str, count: int) -> int:
    value = int(token)
    return value - 1 if value > 0 else count + value


def load_obj(path: Path) -> Mesh:
    path = Path(path).resolve()
    vertices: list[list[float]] = []
    uv: list[list[float]] = []
    faces: list[list[int]] = []
    face_uv: list[list[int]] = []
    mtllibs: list[str] = []
    materials: list[str] = []
    detected_units: str | None = None

    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_no, line in enumerate(stream, 1):
            if match := _UNIT_RE.match(line):
                detected_units = match.group(1).lower()
                continue
            fields = line.strip().split()
            if not fields:
                continue
            kind = fields[0]
            try:
                if kind == "v" and len(fields) >= 4:
                    vertices.append([float(fields[1]), float(fields[2]), float(fields[3])])
                elif kind == "vt" and len(fields) >= 3:
                    uv.append([float(fields[1]), float(fields[2])])
                elif kind == "mtllib" and len(fields) >= 2:
                    mtllibs.extend(fields[1:])
                elif kind == "usemtl" and len(fields) >= 2:
                    materials.append(" ".join(fields[1:]))
                elif kind == "f" and len(fields) >= 4:
                    polygon_v: list[int] = []
                    polygon_t: list[int] = []
                    for corner in fields[1:]:
                        indices = corner.split("/")
                        polygon_v.append(_obj_index(indices[0], len(vertices)))
                        polygon_t.append(
                            _obj_index(indices[1], len(uv)) if len(indices) > 1 and indices[1] else -1
                        )
                    for index in range(1, len(polygon_v) - 1):
                        faces.append([polygon_v[0], polygon_v[index], polygon_v[index + 1]])
                        face_uv.append([polygon_t[0], polygon_t[index], polygon_t[index + 1]])
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Malformed OBJ at {path}:{line_no}: {line.rstrip()}") from exc

    if not vertices or not faces:
        raise ValueError(f"OBJ contains no usable triangle mesh: {path}")
    vertex_array = np.asarray(vertices, dtype=np.float64)
    face_array = np.asarray(faces, dtype=np.int64)
    if face_array.min() < 0 or face_array.max() >= len(vertex_array):
        raise ValueError("OBJ face index lies outside the vertex array")

    texture_files = _parse_mtl_textures(path.parent, mtllibs)
    return Mesh(
        vertices=vertex_array,
        faces=face_array,
        uv=np.asarray(uv, dtype=np.float64) if uv else None,
        face_uv=np.asarray(face_uv, dtype=np.int64) if uv else None,
        source_path=path,
        metadata={
            "detected_units": detected_units,
            "mtllibs": mtllibs,
            "materials": sorted(set(materials)),
            "texture_files": texture_files,
            "sha256": sha256_file(path),
        },
    )


def _parse_mtl_textures(directory: Path, mtllibs: Iterable[str]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for name in mtllibs:
        mtl_path = (directory / name).resolve()
        record: dict[str, object] = {"mtl": str(mtl_path), "mtl_exists": mtl_path.exists(), "textures": []}
        if mtl_path.exists():
            textures: list[dict[str, object]] = []
            for line in mtl_path.read_text(encoding="utf-8", errors="replace").splitlines():
                fields = line.strip().split(maxsplit=1)
                if len(fields) == 2 and fields[0].lower() in {"map_kd", "map_ka", "map_bump", "bump"}:
                    tex = (mtl_path.parent / fields[1]).resolve()
                    textures.append({"kind": fields[0], "path": str(tex), "exists": tex.exists()})
            record["textures"] = textures
        found.append(record)
    return found


def mesh_statistics(mesh: Mesh) -> dict[str, object]:
    vertices = mesh.vertices
    faces = mesh.faces
    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    edge_counts: dict[tuple[int, int], int] = {}
    edge_faces: dict[tuple[int, int], list[int]] = {}
    edge_directions: dict[tuple[int, int], list[bool]] = {}
    for face_index, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (int(min(a, b)), int(max(a, b)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
            edge_faces.setdefault(edge, []).append(face_index)
            edge_directions.setdefault(edge, []).append(int(a) < int(b))
    boundary_edges = sum(count == 1 for count in edge_counts.values())
    nonmanifold_edges = sum(count > 2 for count in edge_counts.values())
    winding_conflicts = sum(
        len(directions) == 2 and directions[0] == directions[1]
        for directions in edge_directions.values()
    )
    parent = np.arange(len(faces), dtype=np.int64)
    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]; value = int(parent[value])
        return value
    def union(first: int, second: int) -> None:
        root_a, root_b = find(first), find(second)
        if root_a != root_b:
            parent[root_b] = root_a
    for incident in edge_faces.values():
        for face_index in incident[1:]:
            union(incident[0], face_index)
    connected_components = len({find(index) for index in range(len(faces))})
    face_uv = mesh.face_uv
    uv_corner_coverage = 0.0
    if face_uv is not None and face_uv.size:
        uv_corner_coverage = float(np.count_nonzero(face_uv >= 0) / face_uv.size)
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    return {
        "vertices": int(len(vertices)),
        "triangles": int(len(faces)),
        "bbox_min_input_units": bbox_min.tolist(),
        "bbox_max_input_units": bbox_max.tolist(),
        "bbox_extent_input_units": (bbox_max - bbox_min).tolist(),
        "surface_area_input_units2": float(areas.sum()),
        "degenerate_triangles": int(np.count_nonzero(areas <= np.finfo(float).eps)),
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "inconsistent_winding_edges": winding_conflicts,
        "connected_face_components": connected_components,
        "uv_vertices": 0 if mesh.uv is None else int(len(mesh.uv)),
        "uv_corner_coverage": uv_corner_coverage,
        "mtllibs": mesh.metadata.get("mtllibs", []),
        "texture_files": mesh.metadata.get("texture_files", []),
        "detected_units": mesh.metadata.get("detected_units"),
        "sha256": mesh.metadata.get("sha256"),
        "input_file_bytes": mesh.source_path.stat().st_size if mesh.source_path else None,
    }


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
