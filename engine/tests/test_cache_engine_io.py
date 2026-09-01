import numpy as np

from autodeck2 import cache
from autodeck2.engine import load_engine_result, save_engine_result
from helpers import make_result


def test_identity_key_changes_with_every_field():
    base = cache.make_identity("in", "cfg", "0.3.6", "fp")
    variants = [
        cache.make_identity("in2", "cfg", "0.3.6", "fp"),
        cache.make_identity("in", "cfg2", "0.3.6", "fp"),
        cache.make_identity("in", "cfg", "0.3.7", "fp"),
        cache.make_identity("in", "cfg", "0.3.6", "fp2"),
    ]
    assert len({base.key, *[v.key for v in variants]}) == 5


def test_lookup_requires_complete_entry_and_matching_identity(tmp_path):
    identity = cache.make_identity("a" * 64, "cfg", "0.3.6", "fp")
    assert cache.lookup(identity, tmp_path) is None
    entry = cache.begin_entry(identity, tmp_path)
    assert cache.lookup(identity, tmp_path) is None  # incomplete
    cache.complete_entry(entry)
    assert cache.lookup(identity, tmp_path) == entry
    # a changed v1 fingerprint is a miss even though the key directory differs anyway
    other = cache.make_identity("a" * 64, "cfg", "0.3.6", "fp-changed")
    assert cache.lookup(other, tmp_path) is None
    # tampered meta -> miss
    (entry / "meta.json").write_text('{"identity": {"input_sha256": "x"}}')
    assert cache.lookup(identity, tmp_path) is None


def test_engine_result_roundtrip(tmp_path):
    result = make_result([(1, "primary", (0.0, 0.0), 1000.0, 600.0, 0.0), (2, "secondary", (1200.0, 0.0), 300.0, 300.0, 0.0)])
    result.curves[0].assignment = {"method": "outer", "fraction": 1.0}
    save_engine_result(result, tmp_path)
    loaded = load_engine_result(tmp_path, cache_key="k", cache_hit=True)
    assert loaded.cache_hit and loaded.cache_key == "k"
    assert set(loaded.panels) == {1, 2}
    assert loaded.panels[1].role == "primary"
    assert np.allclose(loaded.panels[2].development.uv_mm, result.panels[2].development.uv_mm)
    assert np.allclose(loaded.panels[2].development.mesh.base_vertices_mm, result.panels[2].development.mesh.base_vertices_mm)
    assert [c.curve_id for c in loaded.curves] == [c.curve_id for c in result.curves]
    assert np.allclose(loaded.curves[0].flat_points_mm, result.curves[0].flat_points_mm)
    assert loaded.curves[1].family == "OBSTACLE" and loaded.curves[1].panel_id == 1


def test_clean_removes_entries(tmp_path):
    identity = cache.make_identity("b" * 64, "cfg", "0.3.6", "fp")
    entry = cache.begin_entry(identity, tmp_path)
    (entry / "blob.bin").write_bytes(b"x" * 1000)
    cache.complete_entry(entry)
    removed, freed = cache.clean(tmp_path)
    assert removed == 1 and freed >= 1000
    assert cache.list_entries(tmp_path) == []
