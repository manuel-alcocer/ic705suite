"""Where the suite keeps its files (XDG on Linux; the same layout elsewhere)."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "ic705suite"


def _xdg(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / default) / APP_NAME


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")
