"""Where the suite keeps its files: XDG on Linux (and elsewhere), the AppData folders on
Windows (settings roam with the profile in %APPDATA%; data, logs and caches stay on this
machine in %LOCALAPPDATA%)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ic705suite"


def _xdg(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / default) / APP_NAME


def _appdata(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / "AppData" / default) / APP_NAME


def config_dir() -> Path:
    if sys.platform == "win32":
        return _appdata("APPDATA", "Roaming")
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    if sys.platform == "win32":
        return _appdata("LOCALAPPDATA", "Local")
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    if sys.platform == "win32":
        return data_dir() / "cache"
    return _xdg("XDG_CACHE_HOME", ".cache")


def state_dir() -> Path:
    if sys.platform == "win32":
        return data_dir() / "logs"
    return _xdg("XDG_STATE_HOME", ".local/state")
