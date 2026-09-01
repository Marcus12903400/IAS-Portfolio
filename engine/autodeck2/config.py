"""v2 configuration: config/default.yaml (JSON-compatible YAML) with an
optional git-ignored config/local.yaml override.  The v1 engine config is
loaded separately through v1compat and deep-merged in engine.py."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import paths


class ConfigError(Exception):
    pass


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"CONFIG MISSING: {path} does not exist (the checkout is incomplete).")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"CONFIG INVALID: {path} line {exc.lineno}: {exc.msg}") from exc


def load_config(override: Path | None = None) -> dict[str, Any]:
    config = _read(paths.default_config_path())
    local = paths.local_config_path()
    if local.is_file() and not os.environ.get("AUTODECK2_IGNORE_LOCAL_CONFIG"):
        config = deep_merge(config, _read(local))
    if override is not None:
        config = deep_merge(config, _read(Path(override)))
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    for section in ("engine", "assignment", "layout", "teak", "ingest", "outline"):
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"CONFIG INVALID: section '{section}' missing or not a mapping.")
    mode = config["layout"].get("mode", "nest")
    if mode not in {"nest", "boat-plan"}:
        raise ConfigError(f"CONFIG INVALID: layout.mode={mode!r} must be 'nest' or 'boat-plan'.")
    if float(config["layout"].get("nest_gap_mm", 150.0)) < 0:
        raise ConfigError("CONFIG INVALID: layout.nest_gap_mm must be >= 0.")
    if float(config["ingest"].get("snap_tolerance_mm", 0.5)) <= 0:
        raise ConfigError("CONFIG INVALID: ingest.snap_tolerance_mm must be > 0.")


def config_hash(config: dict[str, Any], sections: tuple[str, ...] | None = None) -> str:
    subset = config if sections is None else {key: config.get(key) for key in sections}
    payload = json.dumps(subset, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
