"""Colour evidence: the two properties that keep it from making things worse."""

import numpy as np
import pytest

from autodeck.config import load_config
from autodeck.models import Adjacency, Mesh
from autodeck import texture


def empty_adjacency(face_a, face_b):
    n = len(face_a)
    zeros = np.zeros(n, dtype=np.int64)
    return Adjacency(np.asarray(face_a, dtype=np.int64), np.asarray(face_b, dtype=np.int64),
                     zeros, zeros, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64),
                     np.zeros(0, dtype=np.int64), [], [])


def test_chromaticity_ignores_brightness():
    """A baked shadow or an exposure change scales RGB; chromaticity must not
    move, or every shadow edge would read as a deck boundary."""

    base = np.array([[0.48, 0.33, 0.18], [0.88, 0.88, 0.87], [0.13, 0.12, 0.11]])
    chroma, luminance = texture._chromaticity(base)
    for factor in (0.35, 0.68, 1.0, 1.9):
        dimmed, dim_luminance = texture._chromaticity(base * factor)
        assert dimmed == pytest.approx(chroma, abs=1e-9)
        assert dim_luminance == pytest.approx(luminance * factor, rel=1e-9)


def test_a_shadow_edge_is_far_weaker_than_a_material_edge():
    config = load_config()
    teak = np.array([0.48, 0.33, 0.18])
    gelcoat = np.array([0.88, 0.88, 0.87])
    # face 0 teak, face 1 the same teak in shadow, face 2 gelcoat
    colors = np.vstack([teak, teak * 0.45, gelcoat])
    adjacency = empty_adjacency([0, 0], [1, 2])
    strength, _diag = texture.edge_strength(colors, adjacency, config)
    shadow_edge, material_edge = float(strength[0]), float(strength[1])
    assert material_edge > 0.9
    assert shadow_edge < 0.35
    assert material_edge > shadow_edge * 3


def test_region_average_erases_a_fine_stripe_pattern_but_keeps_a_material_step():
    """Teak caulk lines sit 63.5 mm apart and are near-black against the plank.
    Averaged over a larger region they must vanish, while the deck-to-gelcoat
    step survives -- otherwise every plank line adds boundary cost."""

    x = np.arange(0.0, 2000.0, 4.0)
    centroids = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    teak = np.array([0.48, 0.33, 0.18])
    caulk = np.array([0.13, 0.12, 0.11])
    gelcoat = np.array([0.88, 0.88, 0.87])

    stripe = (np.abs((x / 63.5) % 1.0 - 0.5) > 0.42)          # thin dark lines
    colors = np.where(stripe[:, None], caulk, teak)
    colors = np.where((x > 1200.0)[:, None], gelcoat, colors)  # material change

    smoothed = texture.region_average(colors, centroids, 90.0)
    deck = (x > 200) & (x < 1000)
    # within the teak field the smoothed colour is nearly constant...
    assert smoothed[deck].std(axis=0).max() < 0.05
    # ...while the deck/gelcoat step is still plainly there
    assert np.linalg.norm(smoothed[x < 1000].mean(axis=0) - smoothed[x > 1500].mean(axis=0)) > 0.3


def test_pattern_does_not_fire_but_the_material_change_does():
    config = load_config()
    x = np.arange(0.0, 2000.0, 4.0)
    centroids = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    teak = np.array([0.48, 0.33, 0.18])
    caulk = np.array([0.13, 0.12, 0.11])
    gelcoat = np.array([0.88, 0.88, 0.87])
    stripe = (np.abs((x / 63.5) % 1.0 - 0.5) > 0.42)
    colors = np.where(stripe[:, None], caulk, teak)
    colors = np.where((x > 1200.0)[:, None], gelcoat, colors)

    face_a = np.arange(len(x) - 1)
    adjacency = empty_adjacency(face_a, face_a + 1)

    raw, _d = texture.edge_strength(colors, adjacency, config)                 # no region averaging
    regional, _d2 = texture.edge_strength(colors, adjacency, config, centroids)

    inside = (x[:-1] > 200) & (x[:-1] < 1000)
    at_step = np.abs(x[:-1] - 1200.0) < 100.0
    # ~12% of interior edges sit on a caulk line, which matches what was
    # measured on a real textured deck before region averaging was added.
    assert raw[inside].mean() > 0.05, "without region averaging the plank lines fire"
    assert regional[inside].mean() < 0.02, "region averaging must silence the plank lines"
    assert raw[inside].mean() > regional[inside].mean() * 5
    assert regional[at_step].max() > 0.5, "but the deck/gelcoat change must still fire"


def test_no_texture_no_uv_or_disabled_all_degrade_to_none():
    config = load_config()
    mesh = Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]]))
    assert texture.diffuse_texture_path(mesh) is None
    assert texture.face_colors(mesh, config) is None

    off = {**config, "texture": {**config["texture"], "enabled": False}}
    assert texture.face_colors(mesh, off) is None


def test_edge_strength_on_an_empty_mesh_is_empty():
    config = load_config()
    strength, diagnostics = texture.edge_strength(np.zeros((0, 3)), empty_adjacency([], []), config)
    assert len(strength) == 0
    assert diagnostics["edges"] == 0
