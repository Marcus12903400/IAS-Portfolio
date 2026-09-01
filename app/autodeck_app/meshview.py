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


def _parse_uv(lines: list[bytes]) -> np.ndarray:
    """`vt u v [w]` lines -> (N, 2). Separate from _parse_vertices because a vt
    line has only two required components, not three."""

    if not lines:
        return np.empty((0, 2), dtype=np.float32)
    try:
        return np.loadtxt(io.BytesIO(b"\n".join(lines)), dtype=np.float32,
                          usecols=(1, 2), ndmin=2)
    except ValueError:
        rows = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    rows.append([float(parts[1]), float(parts[2])])
                except ValueError:
                    continue
        return np.asarray(rows, dtype=np.float32).reshape(-1, 2)


def _parse_face_uv(lines: list[bytes]) -> np.ndarray | None:
    """Texture-coordinate index per triangle corner, 1-based, 0 where absent.

    Only the common `f v/vt ...` and `f v/vt/vn ...` triangle forms take the
    fast vectorised path; anything else (quads, `v//vn`, mixed formats) returns
    None and the caller simply renders that block untextured rather than
    guessing.
    """

    if not lines:
        return None
    sample = lines[0].split()
    if len(sample) != 4:
        return None
    corner = sample[1].split(b"/")
    if len(corner) < 2 or not corner[1]:
        return None
    # v/vt or v/vt/vn -> replace the separators so every field becomes a column.
    text = b"\n".join(lines).replace(b"/", b" ")
    per_corner = len(corner)
    try:
        columns = tuple(1 + 1 + i * per_corner for i in range(3))   # skip "f", take each vt
        arr = np.loadtxt(io.BytesIO(text), dtype=np.int64, usecols=columns, ndmin=2)
    except (ValueError, IndexError):
        return None
    return arr if len(arr) == len(lines) else None


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


def read_obj(path: Path, log: Log, want_uv: bool = False):
    """Vertices (N,3 float32, input units) and triangle faces (M,3 int64, 0-based).

    With `want_uv`, also returns the texture coordinates and the per-corner
    texture index, so the preview can be drawn with the scan's own photographic
    texture: `(vertices, faces, uv, face_uv)`.  `uv`/`face_uv` are None when the
    OBJ carries no usable `vt` data.
    """

    size = path.stat().st_size
    vertex_chunks: list[np.ndarray] = []
    face_chunks: list[np.ndarray] = []
    uv_chunks: list[np.ndarray] = []
    face_uv_chunks: list[np.ndarray | None] = []
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
            if want_uv:
                t_lines = [l for l in lines if l[:3] == b"vt "]
                if t_lines:
                    uv_chunks.append(_parse_uv(t_lines))
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
                if want_uv:
                    corners = _parse_face_uv(f_lines)
                    face_uv_chunks.append(corners if corners is not None and len(corners) == len(faces) else None)
            if time.time() - last > 1.0:
                log(f"Reading scan: {100 * done / max(size, 1):.0f}%  ({vertex_count:,} vertices)")
                last = time.time()
            if not chunk:
                break
    vertices = np.vstack(vertex_chunks) if vertex_chunks else np.empty((0, 3), dtype=np.float32)
    faces = (np.vstack(face_chunks) - 1) if face_chunks else np.empty((0, 3), dtype=np.int64)
    keep = None
    if len(faces):
        keep = np.all((faces >= 0) & (faces < len(vertices)), axis=1)
        faces = faces[keep]
    if not want_uv:
        return vertices, faces

    uv = np.vstack(uv_chunks) if uv_chunks else None
    face_uv = None
    if uv is not None and len(uv) and face_uv_chunks and all(c is not None for c in face_uv_chunks):
        face_uv = np.vstack(face_uv_chunks) - 1
        if keep is not None:
            face_uv = face_uv[keep]
        if not (len(face_uv) == len(faces)
                and face_uv.size
                and face_uv.min() >= 0 and face_uv.max() < len(uv)):
            face_uv = None                  # out-of-range vt index: render untextured
    if face_uv is None:
        uv = None
    return vertices, faces, uv, face_uv


def per_vertex_uv(faces: np.ndarray, uv: np.ndarray, face_uv: np.ndarray,
                  vertex_count: int) -> np.ndarray:
    """One texture coordinate per mesh vertex, for the preview.

    OBJ stores texture coordinates per face corner, so a vertex on an atlas
    seam legitimately has several.  The full fix is to split such vertices, but
    that fights the position-based decimation used for the preview and would
    multiply the vertex count.  Taking the first corner that references each
    vertex smears the texture very slightly across atlas seams and is
    invisible at preview resolution; the engine still reads the real per-corner
    data from the OBJ.
    """

    out = np.zeros((vertex_count, 2), dtype=np.float32)
    seen = np.zeros(vertex_count, dtype=bool)
    for corner in range(3):
        vertex_index = faces[:, corner]
        fresh = ~seen[vertex_index]
        out[vertex_index[fresh]] = uv[face_uv[fresh, corner]]
        seen[vertex_index[fresh]] = True
    return out


def decimate(vertices: np.ndarray, faces: np.ndarray, target_faces: int, log: Log,
             uv: np.ndarray | None = None):
    """Vertex clustering on a grid, coarsened until the face budget is met.

    Returns `(positions, indices, uv)`; `uv` is None unless per-vertex texture
    coordinates were supplied, in which case they are averaged over each
    cluster exactly as the positions are.
    """

    if len(faces) <= target_faces:
        return (vertices.astype(np.float32), faces.astype(np.uint32),
                None if uv is None else uv.astype(np.float32))
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
            return (new_vertices.astype(np.float32), new_faces.astype(np.uint32),
                    _cluster_uv(uv, inverse, counts))
        cell *= 1.35
    return (new_vertices.astype(np.float32), new_faces.astype(np.uint32),
            _cluster_uv(uv, inverse, counts))


def _cluster_uv(uv: np.ndarray | None, inverse: np.ndarray, counts: np.ndarray) -> np.ndarray | None:
    if uv is None:
        return None
    averaged = np.column_stack([
        np.bincount(inverse, weights=uv[:, k].astype(np.float64), minlength=len(counts)) / counts
        for k in range(2)
    ])
    return averaged.astype(np.float32)


def preview_paths(sha: str) -> tuple[Path, Path]:
    return settings.PREVIEW_CACHE_DIR / f"{sha}.npz", settings.PREVIEW_CACHE_DIR / f"{sha}.json"


def texture_path(sha: str) -> Path:
    return settings.PREVIEW_CACHE_DIR / f"{sha}-texture.jpg"


_MAP_KEYS = (b"map_kd", b"map_ka")
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


def find_texture(obj_path: Path) -> dict[str, Any] | None:
    """The diffuse texture belonging to an OBJ.

    Follows `mtllib` -> `map_Kd`, which is what a photogrammetry export
    (Vega, RealityCapture, Metashape) writes.  If the MTL is missing or names
    a file that is not there, falls back to the single largest image sitting
    beside the OBJ, because scan folders are routinely moved around and the
    MTL path goes stale.  Returns None when there is nothing to show.
    """

    directory = obj_path.parent
    mtl_names: list[str] = []
    try:
        with obj_path.open("rb") as stream:
            head = stream.read(1 << 20)
        for line in head.split(b"\n"):
            if line[:7].lower() == b"mtllib ":
                mtl_names.extend(part.decode("utf-8", "replace") for part in line.split()[1:])
    except OSError:
        return None

    for name in mtl_names:
        mtl = (directory / name)
        if not mtl.is_file():
            continue
        try:
            for line in mtl.read_bytes().split(b"\n"):
                fields = line.strip().split(None, 1)
                if len(fields) == 2 and fields[0].lower() in _MAP_KEYS:
                    candidate = directory / fields[1].strip().decode("utf-8", "replace")
                    if candidate.is_file():
                        return {"image": str(candidate), "mtl": str(mtl), "source": "mtl"}
        except OSError:
            continue

    images = [p for p in directory.iterdir()
              if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES]
    if not images:
        return None
    largest = max(images, key=lambda p: p.stat().st_size)
    return {"image": str(largest), "mtl": mtl_names[0] if mtl_names else None, "source": "folder"}


def cache_texture(image_path: str, sha: str, log: Log) -> dict[str, Any] | None:
    """Downscale the scan's texture to something a browser can actually hold.

    Photogrammetry atlases are commonly 8192 or 16384 square. 16384^2 is 268
    megapixels, which trips Pillow's DecompressionBomb guard and exceeds
    MAX_TEXTURE_SIZE on plenty of integrated GPUs, so the raw file is never
    served -- it is resampled to at most PREVIEW_TEXTURE_MAX px and re-encoded.
    """

    try:
        from PIL import Image
    except ImportError:
        log("Pillow is not installed; the scan will be shown untextured")
        return None

    target = int(getattr(settings, "PREVIEW_TEXTURE_MAX", 4096))
    source = Path(image_path)
    destination = texture_path(sha)
    if destination.exists():
        with Image.open(destination) as existing:
            return {"width": existing.width, "height": existing.height,
                    "source": str(source), "cached": True}
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None       # we bound the size ourselves, below
        with Image.open(source) as image:
            original = (image.width, image.height)
            if max(original) > 24000:       # not a texture; refuse rather than thrash
                log(f"Texture {source.name} is {original[0]}x{original[1]}; too large to use")
                return None
            image.draft("RGB", (target, target))     # cheap JPEG downscale on decode
            image = image.convert("RGB")
            if max(image.size) > target:
                image.thumbnail((target, target), Image.LANCZOS)
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=88, optimize=True)
            log(f"Texture {source.name}: {original[0]}x{original[1]} -> {image.width}x{image.height}")
            return {"width": image.width, "height": image.height,
                    "original_width": original[0], "original_height": original[1],
                    "source": str(source), "cached": False}
    except Exception as exc:  # noqa: BLE001 - a bad texture must not stop the scan loading
        log(f"Could not read texture {source.name}: {type(exc).__name__}: {exc}")
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


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
    vertices, faces, uv, face_uv = read_obj(path, log, want_uv=True)
    if not len(vertices) or not len(faces):
        raise ValueError("no triangles found in the OBJ")
    log(f"Scan read: {len(vertices):,} vertices, {len(faces):,} faces in {time.time() - started:.0f} s")

    vertex_uv = None
    texture = find_texture(path)
    if uv is not None and face_uv is not None and texture is not None:
        vertex_uv = per_vertex_uv(faces, uv, face_uv, len(vertices))
        log(f"Texture: {Path(texture['image']).name} ({len(uv):,} texture coordinates)")
    elif texture is not None:
        log("Texture found but the OBJ has no usable vt data; showing the mesh untextured")

    positions, indices, preview_uv = decimate(
        vertices, faces, settings.PREVIEW_TARGET_FACES, log, uv=vertex_uv)
    lo = vertices.min(axis=0); hi = vertices.max(axis=0)
    texture_meta = None
    if texture is not None and preview_uv is not None:
        texture_meta = cache_texture(texture["image"], sha, log)
    meta = {
        "sha256": sha, "source": str(path), "name": path.name, "size_bytes": path.stat().st_size,
        "units_detected": detect_units(path),
        "vertex_count": int(len(vertices)), "face_count": int(len(faces)),
        "preview_vertices": int(len(positions)), "preview_faces": int(len(indices)),
        "bbox_min": lo.tolist(), "bbox_max": hi.tolist(),
        "has_uv": bool(preview_uv is not None),
        "texture": texture_meta,
        "mtl": None if texture is None else texture.get("mtl"),
        "seconds": round(time.time() - started, 1),
    }
    if preview_uv is None:
        np.savez(npz_path, positions=positions, indices=indices)
    else:
        np.savez(npz_path, positions=positions, indices=indices, uvs=preview_uv)
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"Preview ready: {len(indices):,} faces")
    return meta


HAS_UV_FLAG = 1          # header word 3, bit 0


def load_preview_binary(sha: str) -> bytes | None:
    """positions, indices and (when present) UVs, as one little-endian blob.

    Header word 3 was reserved and always zero, so it becomes the flags word:
    bit 0 says a UV block follows the indices.  A cache entry written before
    v5 simply has no `uvs` array and reports the flag clear, which is why the
    lookup is `in data.files` rather than a direct index.
    """

    npz_path, _ = preview_paths(sha)
    if not npz_path.exists():
        return None
    data = np.load(npz_path)
    positions = np.ascontiguousarray(data["positions"], dtype=np.float32)
    indices = np.ascontiguousarray(data["indices"], dtype=np.uint32)
    uvs = None
    if "uvs" in data.files:
        candidate = np.ascontiguousarray(data["uvs"], dtype=np.float32)
        if len(candidate) == len(positions):
            uvs = candidate
    flags = HAS_UV_FLAG if uvs is not None else 0
    header = np.array([MAGIC, len(positions), len(indices), flags], dtype=np.uint32)
    blob = header.tobytes() + positions.tobytes() + indices.tobytes()
    return blob if uvs is None else blob + uvs.tobytes()
