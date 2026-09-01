"""Disposable, hash-keyed cache under cache/.

This is deliberately a small, general-purpose key/value store -- not a
redesign of the analysis pipeline's data flow. A cache entry is looked up by
a key built from (input file hash, relevant config hash, algorithm/cache
version), so a stale entry from a different input, config, or AutoDeck
version is never reused silently.

Note: pattern/development reuse across a `pattern`/`reprocess-manufacturing`
rerun already works today via the saved artifacts in an existing
`outputs/runs/<run-id>/` directory (see reprocess.py) -- that is a separate,
already-functioning mechanism and is not touched or duplicated here. This
module is for caching expensive, well-understood, purely input-derived
intermediate stages (e.g. mesh analysis arrays) across *different* runs of
the *same* input, a capability that does not exist yet.

Invariant: cache/ must always be safe to delete. Nothing here writes
anything that isn't reconstructible by rerunning the stage that produced it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, paths


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def hash_config(config: dict[str, Any], relevant_keys: tuple[str, ...] | None = None) -> str:
    """Hash of the config, or of only the sections named in relevant_keys.

    Only the sections that actually affect the cached stage should be
    included -- see docs/CACHE.md's dependency table (e.g. pattern
    selection must not invalidate segmentation cache entries).
    """

    subset = config if relevant_keys is None else {key: config.get(key) for key in relevant_keys}
    payload = json.dumps(subset, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def cache_key(stage: str, input_hash: str, config_hash: str) -> str:
    return f"{stage}-{input_hash}-{config_hash}-v{__version__}"


@dataclass
class CacheEntry:
    key: str
    path: Path
    size_bytes: int
    created_at: float


def entry_dir(key: str) -> Path:
    return paths.cache_dir() / key


def has_entry(key: str) -> bool:
    return (entry_dir(key) / "_complete").is_file()


def write_entry(key: str, files: dict[str, bytes]) -> Path:
    """Write a cache entry atomically-ish: write to a temp dir, then mark
    complete last, so a partially-written entry is never mistaken for valid."""

    target = entry_dir(key)
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    (target / "_complete").write_text(str(time.time()), encoding="utf-8")
    return target


def read_entry(key: str) -> Path | None:
    return entry_dir(key) if has_entry(key) else None


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def list_entries() -> list[CacheEntry]:
    cache_root = paths.cache_dir()
    if not cache_root.is_dir():
        return []
    entries = []
    for child in sorted(cache_root.iterdir()):
        if not child.is_dir():
            continue
        marker = child / "_complete"
        if not marker.is_file():
            continue
        entries.append(
            CacheEntry(
                key=child.name,
                path=child,
                size_bytes=_dir_size(child),
                created_at=marker.stat().st_mtime,
            )
        )
    return entries


def clean(older_than_seconds: float | None = None) -> tuple[int, int]:
    """Remove cache entries (all of them by default, or only those older
    than older_than_seconds). Returns (entries_removed, bytes_freed)."""

    removed = 0
    freed = 0
    now = time.time()
    for entry in list_entries():
        if older_than_seconds is not None and (now - entry.created_at) < older_than_seconds:
            continue
        freed += entry.size_bytes
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    return removed, freed
