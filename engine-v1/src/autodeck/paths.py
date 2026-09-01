"""Single source of truth for AutoDeck's on-disk project layout.

Every module that needs the project root, config directory, cache
directory, or output directory should go through this module instead of
independently guessing (e.g. `Path(__file__).resolve().parents[N]`) --
that pattern is exactly how a project accumulates silently-inconsistent
path resolution across modules. Nothing here depends on the user's home
directory or any developer-specific path.
"""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """The AutoDeck project root (the directory containing src/, config/,
    cache/, outputs/, inputs/, reference-data/)."""

    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return project_root() / "config"


def default_config_path() -> Path:
    return config_dir() / "default.yaml"


def local_config_path() -> Path:
    """Optional, git-ignored override. See docs/CONFIG_REFERENCE.md for
    precedence rules. Does not need to exist."""

    return config_dir() / "local.yaml"


def cache_dir() -> Path:
    """Disposable, regenerable intermediate data. Always safe to delete."""

    return project_root() / "cache"


def inputs_dir() -> Path:
    return project_root() / "inputs"


def boats_dir() -> Path:
    return inputs_dir() / "boats"


def outputs_dir() -> Path:
    return project_root() / "outputs"


def runs_dir() -> Path:
    return outputs_dir() / "runs"


def reference_data_dir() -> Path:
    """Irreplaceable legacy analysis data. Never treat this as cache."""

    return project_root() / "reference-data"
