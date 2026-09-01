"""Disposable cache of the expensive engine stage, with strong identity.

An entry is reused only when EVERY identity field matches: input SHA-256,
engine-relevant v2 config hash, v1 version, v1 source fingerprint, and the
v2 cache schema version.  Nothing here is irreplaceable; `cache/` is always
safe to delete.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import CACHE_SCHEMA_VERSION, __version__, paths


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CacheIdentity:
    input_sha256: str
    config_hash: str
    v1_version: str
    v1_source_fingerprint: str
    v2_version: str
    cache_schema_version: int

    @property
    def key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_identity(input_sha256: str, config_hash: str, v1_version: str, v1_source_fingerprint: str) -> CacheIdentity:
    return CacheIdentity(
        input_sha256=input_sha256,
        config_hash=config_hash,
        v1_version=v1_version,
        v1_source_fingerprint=v1_source_fingerprint,
        v2_version=__version__,
        cache_schema_version=CACHE_SCHEMA_VERSION,
    )


def entry_dir(identity: CacheIdentity, root: Path | None = None) -> Path:
    return (root or paths.cache_dir()) / identity.key


def lookup(identity: CacheIdentity, root: Path | None = None) -> Path | None:
    """Return the entry directory only if its recorded identity matches
    field-for-field and the entry was completely written."""

    directory = entry_dir(identity, root)
    meta_path = directory / "meta.json"
    if not meta_path.is_file() or not (directory / "_complete").is_file():
        return None
    try:
        recorded = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if recorded.get("identity") != identity.to_dict():
        return None
    return directory


def begin_entry(identity: CacheIdentity, root: Path | None = None) -> Path:
    directory = entry_dir(identity, root)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(
        json.dumps({"identity": identity.to_dict(), "created_at": time.time()}, indent=2), encoding="utf-8",
    )
    return directory


def complete_entry(directory: Path) -> None:
    (Path(directory) / "_complete").write_text(str(time.time()), encoding="utf-8")


def list_entries(root: Path | None = None) -> list[dict[str, Any]]:
    cache_root = root or paths.cache_dir()
    entries: list[dict[str, Any]] = []
    if not cache_root.is_dir():
        return entries
    for child in sorted(cache_root.iterdir()):
        meta = child / "meta.json"
        if not child.is_dir() or not meta.is_file():
            continue
        size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        entries.append({"key": child.name, "size_bytes": size, "complete": (child / "_complete").is_file(),
                        "path": str(child)})
    return entries


def clean(root: Path | None = None) -> tuple[int, int]:
    removed = 0
    freed = 0
    for entry in list_entries(root):
        freed += int(entry["size_bytes"])
        shutil.rmtree(entry["path"], ignore_errors=True)
        removed += 1
    return removed, freed
