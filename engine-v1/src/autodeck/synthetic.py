"""Small deterministic meshes used to falsify geometry assumptions before real-scan tuning."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from .models import Mesh


def grid_surface(
    nx: int,
    ny: int,
    spacing_mm: float,
    height: Callable[[float, float], float] | None = None,
) -> Mesh:
    height = height or (lambda _x, _y: 0.0)
    vertices = np.asarray([
        [i * spacing_mm, j * spacing_mm, height(i * spacing_mm, j * spacing_mm)]
        for j in range(ny + 1) for i in range(nx + 1)
    ], dtype=float)
    faces: list[list[int]] = []
    row = nx + 1
    for j in range(ny):
        for i in range(nx):
            a = j * row + i; b = a + 1; d = (j + 1) * row + i; c = d + 1
            faces.extend(([a, b, c], [a, c, d]))
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def curved_strip(radius_mm: float, angle_degrees: float = 90.0, segments: int = 24, across: int = 4, width_mm: float = 40.0) -> Mesh:
    angles = np.linspace(0.0, np.radians(angle_degrees), segments + 1)
    y_values = np.linspace(0.0, width_mm, across + 1)
    vertices = np.asarray([
        [radius_mm * np.sin(angle), y, radius_mm * (1.0 - np.cos(angle))]
        for y in y_values for angle in angles
    ], dtype=float)
    faces: list[list[int]] = []; row = segments + 1
    for j in range(across):
        for i in range(segments):
            a = j * row + i; b = a + 1; d = (j + 1) * row + i; c = d + 1
            faces.extend(([a, b, c], [a, c, d]))
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def extruded_profile(profile_xz: Sequence[tuple[float, float]], width_mm: float = 40.0, across: int = 4) -> Mesh:
    y_values = np.linspace(0.0, width_mm, across + 1)
    vertices = np.asarray([[x, y, z] for y in y_values for x, z in profile_xz], dtype=float)
    segments = len(profile_xz) - 1; row = len(profile_xz); faces: list[list[int]] = []
    for j in range(across):
        for i in range(segments):
            a = j * row + i; b = a + 1; d = (j + 1) * row + i; c = d + 1
            faces.extend(([a, b, c], [a, c, d]))
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def save_obj(mesh: Mesh, path) -> None:
    lines = ["# units: mm"]
    lines.extend(f"v {point[0]} {point[1]} {point[2]}" for point in mesh.vertices)
    lines.extend(f"f {face[0] + 1} {face[1] + 1} {face[2] + 1}" for face in mesh.faces)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

