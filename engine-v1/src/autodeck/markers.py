from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Mesh


@dataclass(slots=True)
class MarkerDetection:
    marker_id: int
    point_input: np.ndarray
    confidence: float
    source_texture: str


def _barycentric_uv(point: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = triangles[:, 0]; v0 = triangles[:, 1] - a; v1 = triangles[:, 2] - a; v2 = point - a
    d00 = np.einsum("ij,ij->i", v0, v0); d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1); d20 = np.einsum("ij,ij->i", v2, v0); d21 = np.einsum("ij,ij->i", v2, v1)
    denominator = d00 * d11 - d01 * d01
    valid = np.abs(denominator) > 1e-15
    v = np.zeros(len(triangles)); w = np.zeros(len(triangles))
    v[valid] = (d11[valid] * d20[valid] - d01[valid] * d21[valid]) / denominator[valid]
    w[valid] = (d00[valid] * d21[valid] - d01[valid] * d20[valid]) / denominator[valid]
    weights = np.stack([1.0 - v - w, v, w], axis=1)
    inside = valid & np.all(weights >= -1e-5, axis=1) & np.all(weights <= 1.0 + 1e-5, axis=1)
    return inside, weights


def detect_aruco_seeds(mesh: Mesh, dictionary_name: str) -> tuple[list[MarkerDetection], list[str]]:
    """Detect tags in a single diffuse texture and map UV centers to mesh faces.

    Vega material-group validation remains necessary before using multi-texture scans.
    """
    warnings: list[str] = []
    try:
        import cv2  # type: ignore
    except ImportError:
        return [], ["OpenCV unavailable; automatic ArUco detection skipped"]
    if mesh.uv is None or mesh.face_uv is None:
        return [], ["Mesh has no usable OBJ UV coordinates; automatic ArUco detection skipped"]
    textures: list[str] = []
    for mtl in mesh.metadata.get("texture_files", []):
        for texture in mtl.get("textures", []):
            if str(texture.get("kind", "")).lower() == "map_kd" and texture.get("exists"):
                textures.append(str(texture["path"]))
    if not textures:
        return [], ["No existing diffuse texture referenced by MTL; automatic ArUco detection skipped"]
    if len(textures) > 1:
        warnings.append("Multiple diffuse textures found; V0.1 UV marker mapping uses the first texture only")
    image_path = Path(textures[0])
    image = cv2.imread(str(image_path))
    if image is None:
        return [], [f"OpenCV could not read diffuse texture: {image_path}"]
    dictionary_id = getattr(cv2.aruco, dictionary_name, None)
    if dictionary_id is None:
        return [], [f"Unknown OpenCV ArUco dictionary: {dictionary_name}"]
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    corners, ids, _ = cv2.aruco.detectMarkers(image, dictionary)
    if ids is None:
        return [], warnings
    valid_faces = np.all(mesh.face_uv >= 0, axis=1)
    face_indices = np.flatnonzero(valid_faces)
    uv_triangles = mesh.uv[mesh.face_uv[valid_faces]]
    height, width = image.shape[:2]
    detections: list[MarkerDetection] = []
    for marker_corners, marker_id in zip(corners, ids.ravel(), strict=True):
        pixel = marker_corners.reshape(-1, 2).mean(axis=0)
        uv_point = np.array([pixel[0] / width, 1.0 - pixel[1] / height])
        inside, weights = _barycentric_uv(uv_point, uv_triangles)
        candidates = np.flatnonzero(inside)
        if not len(candidates):
            warnings.append(f"ArUco {int(marker_id)} was detected in texture but did not map into any UV triangle")
            continue
        candidate = int(candidates[0]); face_index = int(face_indices[candidate])
        point = weights[candidate] @ mesh.vertices[mesh.faces[face_index]]
        detections.append(MarkerDetection(int(marker_id), point, 1.0, str(image_path)))
    return detections, warnings

