"""One place that decides how much of the machine AutoDeck may use.

Before v5 nothing here was parallel at all: a full outline run sat at exactly
1.00 of 24 logical cores.  The target is roughly half the machine -- enough to
be much faster, not so much that the computer becomes unusable while a scan
processes.

Why processes and not threads for panel development
---------------------------------------------------
Measured on this codebase's actual workloads (6 threads, work sized so pool
startup is negligible):

    igl.exact_geodesic          efficiency 0.15   holds the GIL
    igl.lscm + arap_solve       efficiency 0.22   holds the GIL
    cKDTree.query               efficiency 0.70   releases the GIL
    np.sort / np.unique         efficiency ~0.5   partially releases

The libigl bindings hold the GIL, and development (the slowest stage, 113 s of
a 197 s run) is almost entirely libigl.  Threads therefore do nothing for it;
only separate processes help.  Work that *does* release the GIL -- KD-tree
queries, sorting -- is better served by threads, so both are offered here.

BLAS threading
--------------
NumPy/SciPy link a threaded BLAS which already uses many cores for large
matrix work.  When we then run N worker processes, each one starting its own
BLAS pool oversubscribes the machine badly.  `worker_env` caps each worker to a
single BLAS thread; the parent keeps its own setting.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: Environment variables that control per-process BLAS/OpenMP thread pools.
_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

DEFAULT_CPU_FRACTION = 0.5


def logical_cpus() -> int:
    """Logical processors this process is actually allowed to run on."""

    try:                                    # respects affinity masks and cgroups
        return max(1, len(os.sched_getaffinity(0)))   # type: ignore[attr-defined]
    except AttributeError:                  # Windows has no sched_getaffinity
        return max(1, os.cpu_count() or 1)


def settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolved concurrency policy: config, overridden by environment."""

    section = dict((config or {}).get("parallel") or {})
    enabled = bool(section.get("enabled", True))
    fraction = float(section.get("cpu_fraction", DEFAULT_CPU_FRACTION))
    configured = int(section.get("max_workers", 0) or 0)

    override = os.environ.get("AUTODECK_WORKERS", "").strip()
    if override:
        try:
            configured = int(override)
        except ValueError:
            pass                            # a malformed override must not break a run

    total = logical_cpus()
    if configured > 0:
        workers = configured
    else:
        workers = int(round(total * max(0.0, min(1.0, fraction))))
    workers = max(1, min(workers, total))
    if not enabled:
        workers = 1
    return {
        "enabled": enabled and workers > 1,
        "workers": workers,
        "logical_cpus": total,
        "cpu_fraction": fraction,
        "source": "AUTODECK_WORKERS" if override else ("max_workers" if configured > 0 else "cpu_fraction"),
    }


def worker_count(config: dict[str, Any] | None = None) -> int:
    return int(settings(config)["workers"])


def describe(config: dict[str, Any] | None = None) -> str:
    resolved = settings(config)
    if not resolved["enabled"]:
        return f"single-threaded (1 of {resolved['logical_cpus']} logical CPUs)"
    return (f"{resolved['workers']} of {resolved['logical_cpus']} logical CPUs "
            f"({100.0 * resolved['workers'] / resolved['logical_cpus']:.0f}%)")


def worker_env() -> dict[str, str]:
    """Environment for a worker process: one BLAS thread each, so N workers do
    not each start a full thread pool and thrash the machine."""

    env = dict(os.environ)
    for name in _THREAD_VARS:
        env[name] = "1"
    return env


def _init_worker() -> None:
    for name in _THREAD_VARS:
        os.environ.setdefault(name, "1")


def map_threads(fn: Callable[[T], R], items: Sequence[T], workers: int) -> list[R]:
    """Run `fn` over `items` in threads, results in input order.

    Only worth using for work that releases the GIL (KD-tree queries, sorting,
    file I/O).  Falls back to a plain loop for a single worker or a single item.
    """

    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def map_processes(
    fn: Callable[..., R],
    argument_sets: Sequence[tuple],
    workers: int,
    on_error: Callable[[int, BaseException], None] | None = None,
) -> list[R | None]:
    """Run `fn(*args)` for each argument tuple in separate processes.

    Results come back in input order, so a parallel run produces exactly the
    same sequence a serial one would -- important because the engine cache is
    content-keyed and downstream stages are order-sensitive.

    `fn` must be importable by name (Windows uses spawn, not fork), and every
    argument must be picklable.  Any failure to even start the pool -- a
    frozen build, a sandbox that forbids subprocesses, a spawn import error --
    falls back to running serially rather than failing the run.
    """

    argument_sets = list(argument_sets)
    if not argument_sets:
        return []
    if workers <= 1 or len(argument_sets) == 1:
        return [fn(*args) for args in argument_sets]

    try:
        with ProcessPoolExecutor(max_workers=min(workers, len(argument_sets)),
                                 initializer=_init_worker) as pool:
            futures = [pool.submit(fn, *args) for args in argument_sets]
            results: list[R | None] = []
            for index, future in enumerate(futures):
                try:
                    results.append(future.result())
                except BaseException as exc:   # noqa: BLE001 - reported, not swallowed
                    if on_error is not None:
                        on_error(index, exc)
                    results.append(None)
            return results
    except (OSError, RuntimeError, ImportError, PermissionError, ValueError):
        # Could not use processes at all; correctness beats speed.
        return [fn(*args) for args in argument_sets]


def chunk_ranges(total: int, workers: int, minimum: int = 1) -> list[tuple[int, int]]:
    """Contiguous [start, stop) spans for splitting an array across workers."""

    if total <= 0:
        return []
    workers = max(1, workers)
    size = max(minimum, (total + workers - 1) // workers)
    return [(start, min(start + size, total)) for start in range(0, total, size)]
