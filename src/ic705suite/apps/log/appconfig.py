"""Application settings that live outside the database (F9).

The database path cannot be stored in the database it points to, and the
language has to be known before the first text is drawn, so these settings
are kept in a small JSON file in the configuration directory instead of the
``settings`` table.

What the environment says wins over the file, so a test or a one-off run
with ``HAMRLOG_DATABASE_URL`` or ``HAMRLOG_LANG`` never rewrites it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import config_dir

FILE_NAME = "config.json"

#: Languages the interface can be set to; empty follows the system.
LANGUAGES = ("", "en", "es")


@dataclass(slots=True)
class AppConfig:
    """What the settings view (F9) changes.

    Attributes:
        database: SQLite file to use; empty uses the default one. A value
            with ``://`` is taken as a full SQLAlchemy URL.
        language: "en", "es", or empty to follow the system.
        timezone: IANA zone (e.g. "Europe/Madrid") whose local time the
            status line shows next to UTC; empty shows UTC only.
        day_separator: Draw a dashed grey line between the QSOs of different days.
        check_updates: Look for a newer release on start and offer it.
    """

    database: str = ""
    language: str = ""
    timezone: str = ""
    day_separator: bool = True
    check_updates: bool = True

    @property
    def database_path(self) -> Path | None:
        """The SQLite file of ``database``; None for a URL or when not set."""
        value = self.database.strip()
        if not value or "://" in value:
            return None
        return Path(value).expanduser()

    @property
    def database_url(self) -> str:
        """The SQLAlchemy URL of ``database``, empty when it is not set."""
        path = self.database_path
        if path is not None:
            return f"sqlite:///{path.as_posix()}"
        return self.database.strip()


def config_path() -> Path:
    return config_dir() / FILE_NAME


def load() -> AppConfig:
    """The saved settings; the defaults when the file is missing or broken."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppConfig()
    if not isinstance(data, dict):
        return AppConfig()
    known = {field.name: field.type for field in fields(AppConfig)}
    values = {key: value for key, value in data.items() if key in known}
    config = AppConfig()
    for key, value in values.items():
        default = getattr(config, key)
        if isinstance(value, type(default)):
            setattr(config, key, value)
    return config


def save(config: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")
