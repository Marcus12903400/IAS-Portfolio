from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import paths

_KNOWN_SEGMENTATION_MODES = {"orientation", "structural-experimental", "advanced"}
_KNOWN_PATTERN_KINDS = {"none", "teak", "diamond", "hex"}


class ConfigError(Exception):
    """Raised for a config problem the user must fix before AutoDeck can run
    -- missing file, unparseable content, or an invalid/unknown value in a
    field AutoDeck checks explicitly. The message always names the offending
    file/field and the next action, per the "fail loud and early" rule: a
    bad config must never surface as an obscure KeyError forty minutes into
    processing."""


def default_config_path() -> Path:
    return paths.default_config_path()


def local_config_path() -> Path:
    return paths.local_config_path()


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_json_yaml(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML without making PyYAML a core dependency."""

    if not path.is_file():
        raise ConfigError(
            f"CONFIG MISSING: expected a config file at {path}, found nothing there. "
            "AutoDeck ships config/default.yaml at the project root -- if it's missing, "
            "the project checkout is incomplete."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"CONFIG INVALID: {path} is not valid JSON-compatible YAML "
            f"(line {exc.lineno}, column {exc.colno}: {exc.msg}). Fix the syntax before rerunning."
        ) from exc


def load_config(path: Path | None = None, *, validate: bool = True) -> dict[str, Any]:
    """Load the effective configuration.

    Precedence (each layer merges over the previous, later wins):
    config/default.yaml -> config/local.yaml (optional, git-ignored, if
    present) -> ``path`` (an explicit override, e.g. from --config).
    """

    config = _read_json_yaml(default_config_path())
    local_path = local_config_path()
    # The test suite sets AUTODECK_IGNORE_LOCAL_CONFIG so a developer's
    # personal config/local.yaml experiment can never silently change what
    # the tests are asserting against.
    if local_path.is_file() and not os.environ.get("AUTODECK_IGNORE_LOCAL_CONFIG"):
        config = _deep_merge(config, _read_json_yaml(local_path))
    if path is not None:
        config = _deep_merge(config, _read_json_yaml(Path(path)))
    if validate:
        validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    """Fail loudly and specifically on a config problem, instead of letting
    a missing/invalid value surface as a bare KeyError deep inside the
    pipeline. Deliberately checks only fields AutoDeck reads as enums/critical
    switches -- see docs/CONFIG_REFERENCE.md for the full field reference.
    """

    segmentation = config.get("segmentation")
    if not isinstance(segmentation, dict):
        raise ConfigError(
            "CONFIG INVALID: top-level 'segmentation' section is missing or not a mapping. "
            "Check config/default.yaml (and config/local.yaml, if present) for a corrupted override."
        )
    mode = segmentation.get("mode", "orientation")
    if mode not in _KNOWN_SEGMENTATION_MODES:
        raise ConfigError(
            f"CONFIG INVALID: segmentation.mode={mode!r} is not one of "
            f"{sorted(_KNOWN_SEGMENTATION_MODES)}. Fix config/local.yaml or the --segmentation-mode flag."
        )

    pattern = config.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, dict):
            raise ConfigError(
                "CONFIG INVALID: top-level 'pattern' section is present but not a mapping."
            )
        selected = pattern.get("selected", "none")
        if selected not in _KNOWN_PATTERN_KINDS:
            raise ConfigError(
                f"CONFIG INVALID: pattern.selected={selected!r} is not one of "
                f"{sorted(_KNOWN_PATTERN_KINDS)}."
            )

    for section in (
        "mesh", "orientation", "curvature", "boundary", "segmentation",
        "manufacturing_fit", "robust_reference", "polyarc_fit", "dxf",
    ):
        value = config.get(section)
        if not isinstance(value, dict):
            raise ConfigError(
                f"CONFIG INVALID: top-level '{section}' section is missing or not a mapping. "
                "Check config/default.yaml (and config/local.yaml, if present) for a corrupted override."
            )


def unit_scale_to_mm(units: str) -> float:
    scales = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
    try:
        return scales[units.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported units {units!r}; choose mm, cm, m, or in") from exc
