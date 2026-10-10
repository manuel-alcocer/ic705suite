"""Platform-aware application paths.

Resolves data/config directories on Linux, Windows and macOS so the same code
runs unchanged from a Linux console or from PowerShell.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "hamrlog"


def _windows_appdata() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    return Path.home() / "AppData" / "Roaming"


def data_dir() -> Path:
    """Directory holding the database and exports."""
    override = os.environ.get("HAMRLOG_HOME")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = _windows_appdata()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base / APP_NAME


def config_dir() -> Path:
    """Directory holding user configuration files."""
    override = os.environ.get("HAMRLOG_HOME")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = _windows_appdata()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    return base / APP_NAME


def database_path() -> Path:
    """Default SQLite file location."""
    return data_dir() / "hamrlog.sqlite3"


def export_dir() -> Path:
    return data_dir() / "exports"


def ensure_dirs() -> None:
    """Create every directory the application writes to."""
    for path in (data_dir(), config_dir(), export_dir()):
        path.mkdir(parents=True, exist_ok=True)
