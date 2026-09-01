"""The concurrency policy, and the guarantees that make it safe to turn on."""

import os

import pytest

from autodeck2 import parallel
from autodeck2.config import load_config
from autodeck2.engine import engine_identity, merged_v1_config


@pytest.fixture(autouse=True)
def clear_override():
    previous = os.environ.pop("AUTODECK_WORKERS", None)
    yield
    if previous is not None:
        os.environ["AUTODECK_WORKERS"] = previous
    else:
        os.environ.pop("AUTODECK_WORKERS", None)


def test_default_targets_about_half_the_machine():
    resolved = parallel.settings(load_config())
    total = parallel.logical_cpus()
    assert resolved["logical_cpus"] == total
    assert resolved["workers"] == max(1, round(total * 0.5))
    assert 1 <= resolved["workers"] <= total


def test_cpu_fraction_and_max_workers():
    config = load_config()
    quarter = {**config, "parallel": {**config["parallel"], "cpu_fraction": 0.25}}
    assert parallel.settings(quarter)["workers"] == max(1, round(parallel.logical_cpus() * 0.25))

    exact = {**config, "parallel": {**config["parallel"], "max_workers": 3}}
    assert parallel.settings(exact)["workers"] == 3
    assert parallel.settings(exact)["source"] == "max_workers"


def test_environment_override_wins_and_a_bad_one_is_ignored():
    config = load_config()
    os.environ["AUTODECK_WORKERS"] = "4"
    assert parallel.settings(config)["workers"] == 4
    assert parallel.settings(config)["source"] == "AUTODECK_WORKERS"

    os.environ["AUTODECK_WORKERS"] = "not a number"
    assert parallel.settings(config)["workers"] == max(1, round(parallel.logical_cpus() * 0.5))


def test_worker_count_is_clamped_into_range():
    config = load_config()
    huge = {**config, "parallel": {**config["parallel"], "max_workers": 10_000}}
    assert parallel.settings(huge)["workers"] == parallel.logical_cpus()
    zero = {**config, "parallel": {**config["parallel"], "cpu_fraction": 0.0}}
    assert parallel.settings(zero)["workers"] == 1


def test_disabling_falls_back_to_one_worker():
    config = load_config()
    off = {**config, "parallel": {**config["parallel"], "enabled": False}}
    resolved = parallel.settings(off)
    assert resolved["workers"] == 1 and resolved["enabled"] is False
    assert "single-threaded" in parallel.describe(off)


def test_worker_env_pins_blas_to_one_thread_per_process():
    """N worker processes each starting a full BLAS pool would oversubscribe
    the machine badly."""

    env = parallel.worker_env()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        assert env[name] == "1"


def test_worker_count_does_not_change_the_engine_cache_key(tmp_path):
    """Parallelism must not invalidate cached work: the same scan has to give
    the same key at any worker count."""

    scan = tmp_path / "scan.obj"
    scan.write_text("# units: mm\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    config = load_config()
    serial = {**config, "parallel": {**config["parallel"], "enabled": False}}
    wide = {**config, "parallel": {"enabled": True, "cpu_fraction": 1.0, "max_workers": 32}}
    assert (engine_identity(scan, serial, merged_v1_config(serial)).key
            == engine_identity(scan, wide, merged_v1_config(wide)).key)


def square(value):
    """Module level so a process pool can pickle it (Windows spawns)."""
    return value * value


def test_map_processes_preserves_input_order():
    results = parallel.map_processes(square, [(n,) for n in range(6)], workers=3)
    assert results == [0, 1, 4, 9, 16, 25]


def test_map_processes_runs_serially_for_one_worker_or_one_item():
    assert parallel.map_processes(square, [(5,)], workers=8) == [25]
    assert parallel.map_processes(square, [(2,), (3,)], workers=1) == [4, 9]
    assert parallel.map_processes(square, [], workers=4) == []


def test_map_threads_matches_a_plain_loop():
    items = list(range(10))
    assert parallel.map_threads(square, items, workers=4) == [square(i) for i in items]
    assert parallel.map_threads(square, items, workers=1) == [square(i) for i in items]


def test_chunk_ranges_covers_everything_exactly_once():
    for total, workers in ((100, 4), (7, 3), (1, 8), (0, 4)):
        spans = parallel.chunk_ranges(total, workers)
        covered = [i for start, stop in spans for i in range(start, stop)]
        assert covered == list(range(total))
