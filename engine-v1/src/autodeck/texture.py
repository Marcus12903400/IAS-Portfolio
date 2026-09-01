"""Colour evidence from the scan's own texture, for deck-boundary detection.

A photogrammetry scan carries a photographic texture, and on a boat the deck
and the surrounding gelcoat are obviously different colours to a human long
before the geometry says anything.  This turns that into one more per-edge
boundary cue alongside the geometric ones in `relief.calculate_boundary_field`.

Two design rules, both about not making a working result worse:

*Chromaticity, not brightness.*  Scan textures are full of nuisance intensity:
baked shadows, exposure drift between capture positions, vignetting.  All of
those multiply RGB by a scalar, and chromaticity r/(r+g+b) is invariant to a
scalar.  So the colour distance is measured mostly in chromaticity, with
luminance contributing only weakly -- a shadow edge across the deck is nearly
invisible to this measure, while teak against white gelcoat is not.

*Additive only.*  The texture term contributes to the weighted sum but is
deliberately left out of the "decisive evidence" maximum, so it can strengthen
a boundary the geometry already suspects but can never declare one on its own.
A stain or a scuff therefore cannot invent a wall line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from .models import Adjacency, Mesh

Log = Callable[[str], None]

#: Largest texture we will decode. 8192^2 is a common scan atlas; beyond
#: ~12k square the memory cost stops being worth it for a per-face average.
MAX_TEXTURE_EDGE = 12288


def _silent(_message: str) -> None:
    return None


def diffuse_texture_path(mesh: Mesh) -> Path | None:
    """The first existing map_Kd referenced by the mesh's MTL files.

    `mesh_io.load_obj` already resolved and existence-checked these, so this is
    just picking the diffuse one.
    """

    for record in mesh.metadata.get("texture_files", []) or []:
        for texture in record.get("textures", []) or []:
            if str(texture.get("kind", "")).lower() == "map_kd" and texture.get("exists"):
                return Path(str(texture["path"]))
    return None


def face_colors(mesh: Mesh, config: dict[str, Any], log: Log | None = None) -> np.ndarray | None:
    """Mean linear RGB per face, sampled through the mesh's texture coordinates.

    Returns None -- and says why -- whenever the scan cannot support this:
    no texture, no UVs, unreadable image, or out-of-range texture indices.
    Callers treat None as "no texture evidence" and fall back to geometry only.
    """

    log = log or _silent
    settings = config.get("texture") or {}
    if not bool(settings.get("enabled", True)):
        return None

    path = diffuse_texture_path(mesh)
    if path is None:
        return None
    if mesh.uv is None or mesh.face_uv is None or not len(mesh.uv):
        log("TEXTURE: the scan has a diffuse map but no usable vt data; colour evidence skipped")
        return None
    if len(mesh.face_uv) != len(mesh.faces):
        log("TEXTURE: face/texture index count mismatch; colour evidence skipped")
        return None

    try:
        from PIL import Image
    except ImportError:
        log("TEXTURE: Pillow is not installed; colour evidence skipped")
        return None

    previous = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None       # bounded explicitly below instead
        with Image.open(path) as image:
            if max(image.size) > MAX_TEXTURE_EDGE:
                scale = MAX_TEXTURE_EDGE / max(image.size)
                image = image.resize((max(1, int(image.width * scale)),
                                      max(1, int(image.height * scale))), Image.BILINEAR)
            pixels = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    except Exception as exc:  # noqa: BLE001 - a bad texture must not fail the run
        log(f"TEXTURE: could not read {path.name}: {type(exc).__name__}: {exc}; colour evidence skipped")
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous

    height, width, _ = pixels.shape
    uv = np.asarray(mesh.uv, dtype=np.float64)
    face_uv = np.asarray(mesh.face_uv, dtype=np.int64)
    if face_uv.min() < 0 or face_uv.max() >= len(uv):
        log("TEXTURE: vt indices fall outside the texture coordinate array; colour evidence skipped")
        return None

    corners = uv[face_uv]                                   # (F, 3, 2)
    centres = corners.mean(axis=1)                          # face centre in UV
    # OBJ V runs bottom-up; image rows run top-down.
    u = np.clip(centres[:, 0], 0.0, 1.0)
    v = np.clip(1.0 - centres[:, 1], 0.0, 1.0)
    col = np.clip((u * (width - 1)).astype(np.int64), 0, width - 1)
    row = np.clip((v * (height - 1)).astype(np.int64), 0, height - 1)
    sampled = pixels[row, col].astype(np.float64)

    log(f"TEXTURE: sampled {len(sampled):,} face colours from {path.name} ({width}x{height})")
    return sampled


def _chromaticity(colors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split colour into (chromaticity, luminance).

    Chromaticity is c / sum(c), which is unchanged when the whole pixel is
    scaled -- exactly what a shadow or an exposure change does -- so a colour
    distance built on it ignores lighting and responds to material.
    """

    total = np.maximum(colors.sum(axis=1, keepdims=True), 1e-6)
    chroma = colors / total
    luminance = colors @ np.array([0.2126, 0.7152, 0.0722])
    return chroma, luminance


def region_average(colors: np.ndarray, centroids: np.ndarray, radius_mm: float) -> np.ndarray:
    """Colour averaged over a physical neighbourhood, on two half-shifted grids.

    This is what separates a MATERIAL change from a PATTERN.  Decking is
    covered in deliberate fine colour detail -- caulk lines between teak
    planks sit 63.5 mm apart and are near-black against the plank -- and a
    plain neighbour-to-neighbour colour gradient lights up on every one of
    them, which would add boundary cost right across the deck and fragment it.

    Averaging over a radius comfortably larger than that spacing erases the
    pattern (both sides of a caulk line are the same teak region) while a real
    deck-to-gelcoat transition survives as a step.  The two half-cell-shifted
    grids are the same trick `curvature.multiscale_normal_variation` uses to
    stop cell boundaries showing up as fake structure.
    """

    if radius_mm <= 0:
        return colors
    origin = centroids.min(axis=0)
    accumulated = np.zeros_like(colors)
    for shift in (0.0, 0.5):
        keys = np.floor((centroids - origin) / radius_mm + shift).astype(np.int64)
        low = keys.min(axis=0)
        span = (keys.max(axis=0) - low + 1).astype(np.int64)
        flat = ((keys[:, 0] - low[0]) * span[1] + (keys[:, 1] - low[1])) * span[2] + (keys[:, 2] - low[2])
        _unique, inverse = np.unique(flat, return_inverse=True)
        inverse = inverse.reshape(-1)
        cells = int(inverse.max()) + 1
        counts = np.bincount(inverse, minlength=cells).astype(np.float64)
        means = np.empty((cells, colors.shape[1]))
        for channel in range(colors.shape[1]):
            means[:, channel] = np.bincount(inverse, weights=colors[:, channel], minlength=cells) / counts
        accumulated += means[inverse]
    return accumulated / 2.0


def edge_strength(colors: np.ndarray, adjacency: Adjacency, config: dict[str, Any],
                  centroids: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Per-edge colour-change strength in [0, 1], plus diagnostics.

    Shaped like the geometric strengths in relief.py -- 0 below `soft`, 1 above
    `hard`, linear between -- so it can be weighted alongside them.

    With `centroids`, the comparison is made on region-averaged colour so that
    surface pattern (teak caulk lines, diamond stitching) does not register as
    a boundary; see `region_average`.
    """

    settings = config.get("texture") or {}
    soft = float(settings.get("color_soft", 0.035))
    hard = float(settings.get("color_hard", 0.13))
    luminance_weight = float(settings.get("luminance_weight", 0.25))
    radius = float(settings.get("region_radius_mm", 90.0))

    a, b = adjacency.face_a, adjacency.face_b
    if not len(a):
        return np.zeros(0), {"edges": 0}

    regional = colors if centroids is None else region_average(colors, centroids, radius)
    chroma, luminance = _chromaticity(regional)
    chroma_distance = np.linalg.norm(chroma[a] - chroma[b], axis=1)
    luminance_distance = np.abs(luminance[a] - luminance[b])
    distance = chroma_distance + luminance_weight * luminance_distance
    strength = np.clip((distance - soft) / max(hard - soft, 1e-9), 0.0, 1.0)

    diagnostics = {
        "edges": int(len(strength)),
        "mean_color_distance": float(distance.mean()),
        "p95_color_distance": float(np.quantile(distance, 0.95)),
        "edges_above_soft": int(np.count_nonzero(distance > soft)),
        "edges_at_full_strength": int(np.count_nonzero(strength >= 1.0)),
        "soft": soft, "hard": hard, "luminance_weight": luminance_weight,
        "region_radius_mm": radius if centroids is not None else None,
    }
    return strength, diagnostics


def analysis_face_colors(original: Mesh, source_face_indices: np.ndarray,
                         config: dict[str, Any], log: Log | None = None) -> np.ndarray | None:
    """Face colours for the ANALYSIS mesh.

    Sampling happens on the original mesh, which is the one `load_obj` gave
    texture coordinates to, and the result is then indexed through
    `AnalysisMesh.source_face_indices`.  Doing it this way means preprocessing
    is free to repair, drop or reorder faces without texture support quietly
    depending on whether it happens to carry uv arrays along.
    """

    colors = face_colors(original, config, log)
    if colors is None:
        return None
    index = np.asarray(source_face_indices, dtype=np.int64)
    if not len(index) or index.min() < 0 or index.max() >= len(colors):
        (log or _silent)("TEXTURE: analysis faces do not map back to the original mesh; colour evidence skipped")
        return None
    return colors[index]
