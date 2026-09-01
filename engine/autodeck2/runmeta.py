"""run.json: everything needed to understand and reproduce a run."""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import CACHE_SCHEMA_VERSION, __version__


def make_run_id(input_path: Path, when: datetime | None = None) -> str:
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    return f"{Path(input_path).stem}-{stamp}"


@dataclass
class RunMeta:
    run_id: str
    input_path: str
    input_name: str
    input_sha256: str
    units: str
    v1_version: str
    v1_source_fingerprint: str
    config_hash: str
    cache_key: str
    cache_hit: bool
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    v2_version: str = __version__
    cache_schema_version: int = CACHE_SCHEMA_VERSION
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    panels: list[dict[str, Any]] = field(default_factory=list)
    layout_mode: str = ""
    stage_timings_seconds: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    assignment_ambiguities: list[dict[str, Any]] = field(default_factory=list)
    unassigned_curves: list[dict[str, Any]] = field(default_factory=list)
    teak: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    status: str = "IN_PROGRESS"

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.__dict__, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)
