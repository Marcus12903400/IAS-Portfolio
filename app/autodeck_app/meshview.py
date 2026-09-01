"""Scan preview: a fast OBJ reader, unit detection, vertex-clustering
decimation and a binary export for the browser, cached by content hash so a
scan is only read once (the Key West OBJ is 800 MB)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import settings

Log = Callable[[str], None]

_UNIT_RE = re.compile(rb"^\s*#\s*(?:units?|unit)\s*[:=]\s*(mm|cm|m|in)\s*$", re.I)
MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
MAGIC = 0x4D455348  # 'MESH'


def sha256_file(path: Path, log: Log | None = None) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    done = 0
    last = time.time()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(16 << 20)
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
            if log and time.time() - last > 1.0:
                log(f"Fingerprinting scan: {100 * done / max(size, 1):.0f}%")
                last = time.time()
    return digest.hexdigest()


def detect_units(path: Path) -> str | None:
    """`# units: mm` style header comment, as AutoDeck v1 reads it."""

    with path.open("rb") as stream:
        head = stream.read(65536)
    for line in head.split(b"\n")[:400]:
        match = _UNIT_RE.match(line)
        if match:
            return match.group(1).decode().lower()
    return None


def _parse_vertices(lines: list[bytes]) -> np.ndarray:
    if not lines:
        return np.empty((0, 3), dtype=np.float32)
    text = b"\n".join(lines)
    try:
        arr = np.loadtxt(io.BytesIO(text), dtype=np.float32, usecols=(1, 2, 3), ndmin=2)
    except ValueError:
        # odd lines (colours, missing z): fall back per line
        rows = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    rows.append([float(parts[1]), float(parts[2]), float(parts[3])])
                except ValueError:
                    continue
        arr = np.asarray(rows, dtype=np.float32).reshape(-1, 3)
    return arr


def _parse_faces(lines: list[bytes]) -> np.ndarray:
    if not lines:
        return np.empty((0, 3), dtype=np.int64)
    stripped = re.sub(rb"/[^\s]*", b"", b"\n".join(lines))
    rows = stripped.split(b"\n")
    simple = [r for r in rows if r.count(b" ") == 3 and not r.endswith(b" ")]
    others = [r for r in rows if r not in simple] if len(simple) != len(rows) else []
    out = []
    if simple:
        try:
            tri = np.loadtxt(io.BytesIO(b"\n".join(simple)), dtype=np.int64, usecols=(1, 2, 3), ndmin=2)
            out.append(tri)
        except ValueError:
            others = rows
    for row in others:
        parts = row.split()
        if len(parts) < 4:
            continue
        try:
            idx = [int(p) for p in parts[1:]]
        except ValueError:
            continue
        for k in range(1, len(idx) - 1):      # fan triangulation of polygons
            out.append(np.array([[idx[0], idx[k], idx[k + 1]]], dtype=np.int64))
    faces = np.vstack(out) if out else np.empty((0, 3), dtype=np.int64)
    return faces


def read_obj(path: Path, log: Log) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (N,3 float32, input units) and triangle faces (M,3 int64, 0-based)."""

    size = path.stat().st_size
    vertex_chunks: list[np.ndarray] = []
    face_chunks: list[np.ndarray] = []
    pending = b""
    done = 0
    vertex_count = 0
    last = time.time()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(48 << 20)
            if not chunk and not pending:
                break
            buf = pending + chunk
            if chunk:
                cut = buf.rfind(b"\n")
                if cut < 0:
                    pending = buf
                    continue
                block, pending = buf[:cut + 1], buf[cut + 1:]
            else:
                block, pending = buf, b""
            done += len(chunk)
            lines = block.split(b"\n")
            v_lines = [l for l in lines if l[:2] == b"v "]
            f_lines = [l for l in lines if l[:2] == b"f "]
            if v_lines:
                arr = _parse_vertices(v_lines)
                vertex_chunks.append(arr)
                vertex_count += len(arr)
            if f_lines:
                faces = _parse_faces(f_lines)
                negative = faces < 0
                if negative.any():          # relative indices count back from the vertices read so far
                    faces = np.where(negative, faces + vertex_count + 1, faces)
                face_chunks.append(faces)
            if time.time() - last > 1.0:
                log(f"Reading scan: {100 * done / max(size, 1):.0f}%  ({vertex_count:,} vertices)")
                last = time.time()
            if not chunk:
                break
    vertices = np.vstack(vertex_chunks) if vertex_chunks else np.empty((0, 3), dtype=np.float32)
    faces = (np.vstack(face_chunks) - 1) if face_chunks else np.empty((0, 3), dtype=np.int64)
    if len(faces):
        valid = np.all((faces >= 0) & (faces < len(vertices)), axis=1)
        faces = faces[valid]
    return vertices, faces


def decimate(vertices: np.ndarray, faces: np.ndarray, target_faces: int, log: Log) -> tuple[np.ndarray, np.ndarray]:
    """Vertex clustering on a grid, coarsened until the face budget is met."""

    if len(faces) <= target_faces:
        return vertices.astype(np.float32), faces.astype(np.uint32)
    lo = vertices.min(axis=0); hi = vertices.max(axis=0)
    diagonal = float(np.linalg.norm(hi - lo)) or 1.0
    cell = diagonal / 600.0
    for _ in range(12):
        q = np.floor((vertices - lo) / cell).astype(np.int64)
        span = q.max(axis=0) + 1
        key = (q[:, 0] * span[1] + q[:, 1]) * span[2] + q[:, 2]
        _uniq, inverse = np.unique(key, return_inverse=True)
        inverse = inverse.reshape(-1)
        counts = np.bincount(inverse).astype(np.float64)
        new_vertices = np.column_stack([np.bincount(inverse, weights=vertices[:, k].astype(np.float64)) / counts for k in range(3)])
        new_faces = inverse[faces]
        keep = (new_faces[:, 0] != new_faces[:, 1]) & (new_faces[:, 1] != new_faces[:, 2]) & (new_faces[:, 0] != new_faces[:, 2])
        new_faces = new_faces[keep]
        if len(new_faces):
            ordered = np.sort(new_faces, axis=1)
            _u, first = np.unique(ordered, axis=0, return_index=True)
            new_faces = new_faces[np.sort(first)]
        log(f"Preview mesh: cell {cell:.1f} -> {len(new_faces):,} faces")
        if len(new_faces) <= target_faces:
            return new_vertices.astype(np.float32), new_faces.astype(np.uint32)
        cell *= 1.35
    return new_vertices.astype(np.float32), new_faces.astype(np.uint32)


def preview_paths(sha: str) -> tuple[Path, Path]:
    return settings.PREVIEW_CACHE_DIR / f"{sha}.npz", settings.PREVIEW_CACHE_DIR / f"{sha}.json"


def build_preview(path: Path, sha: str, log: Log) -> dict[str, Any]:
    """Read, decimate and cache; returns the preview metadata."""

    npz_path, json_path = preview_paths(sha)
    if npz_path.exists() and json_path.exists():
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        log(f"Preview mesh from cache ({meta['preview_faces']:,} faces)")
        return meta
    settings.PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    log(f"Reading scan {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    vertices, faces = read_obj(path, log)
    if not len(vertices) or not len(faces):
        raise ValueError("no triangles found in the OBJ")
    log(f"Scan read: {len(vertices):,} vertices, {len(faces):,} faces in {time.time() - started:.0f} s")
    positions, indices = decimate(vertices, faces, settings.PREVIEW_TARGET_FACES, log)
    lo = vertices.min(axis=0); hi = vertices.max(axis=0)
    meta = {
        "sha256": sha, "source": str(path), "name": path.name, "size_bytes": path.stat().st_size,
        "units_detected": detect_units(path),
        "vertex_count": int(len(vertices)), "face_count": int(len(faces)),
        "preview_vertices": int(len(positions)), "preview_faces": int(len(indices)),
        "bbox_min": lo.tolist(), "bbox_max": hi.tolist(),
        "seconds": round(time.time() - started, 1),
    }
    np.savez(npz_path, positions=positions, indices=indices)
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"Preview ready: {len(indices):,} faces")
    return meta


def load_preview_binary(sha: str) -> bytes | None:
    npz_path, _ = preview_paths(sha)
    if not npz_path.exists():
        return None
    data = np.load(npz_path)
    positions = np.ascontiguousarray(data["positions"], dtype=np.float32)
    indices = np.ascontiguousarray(data["indices"], dtype=np.uint32)
    header = np.array([MAGIC, len(positions), len(indices), 0], dtype=np.uint32)
    return header.tobytes() + positions.tobytes() + indices.tobytes()
