"""Single source of truth for AutoDeck2's on-disk layout."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    return project_root() / "config"


def default_config_path() -> Path:
    return config_dir() / "default.yaml"


def local_config_path() -> Path:
    return config_dir() / "local.yaml"


def cache_dir() -> Path:
    return project_root() / "cache"


def boats_dir() -> Path:
    return project_root() / "inputs" / "boats"


def runs_dir() -> Path:
    return project_root() / "outputs" / "runs"
