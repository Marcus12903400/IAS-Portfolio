from __future__ import annotations

from autodeck import cache


def test_hash_file_is_stable_and_content_sensitive(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello")
    assert cache.hash_file(a) == cache.hash_file(b)
    b.write_bytes(b"different")
    assert cache.hash_file(a) != cache.hash_file(b)


def test_hash_config_only_considers_relevant_keys():
    config = {"segmentation": {"mode": "orientation"}, "pattern": {"selected": "teak"}}
    key_a = cache.hash_config(config, relevant_keys=("segmentation",))
    changed = {"segmentation": {"mode": "orientation"}, "pattern": {"selected": "diamond"}}
    key_b = cache.hash_config(changed, relevant_keys=("segmentation",))
    # Changing pattern must not invalidate a segmentation-only cache key.
    assert key_a == key_b
    key_c = cache.hash_config(config, relevant_keys=("pattern",))
    key_d = cache.hash_config(changed, relevant_keys=("pattern",))
    assert key_c != key_d


def test_cache_entry_roundtrip_and_clean_is_always_safe(monkeypatch, tmp_path):
    monkeypatch.setattr("autodeck.cache.paths.cache_dir", lambda: tmp_path)
    key = cache.cache_key("mesh-analysis", "inputhash1234", "confighash5678")
    assert not cache.has_entry(key)

    cache.write_entry(key, {"result.json": b'{"ok": true}'})
    assert cache.has_entry(key)
    entry_path = cache.read_entry(key)
    assert entry_path is not None
    assert (entry_path / "result.json").read_bytes() == b'{"ok": true}'

    entries = cache.list_entries()
    assert len(entries) == 1
    assert entries[0].key == key

    removed, freed = cache.clean()
    assert removed == 1
    assert freed > 0
    # The whole point: deleting cache entries must never raise or leave the
    # cache directory in a broken state -- just an empty, regenerable one.
    assert not cache.has_entry(key)
    assert cache.list_entries() == []
