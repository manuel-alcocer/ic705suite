"""The settings view (F9): how the application itself behaves.

One row per setting. Enter or E puts its value in the entry line, Enter
saves it. The values are kept in the configuration file (see
``hamrlog.appconfig``), not in the database: one of them says which database
to open. The database and the language take effect on the next start; the
rest at once.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import appconfig
from ..core.services import ServiceError
from ..i18n import N_, _, language
from ..paths import database_path
from .inventory import Item, Kind

#: Row ids, in the order the list shows them.
DATABASE, LANGUAGE, TIMEZONE, DAY_SEPARATOR, UPDATES = 1, 2, 3, 4, 5

#: Names of each language in the box, in either language.
LANGUAGE_WORDS: dict[str, str] = {
    "": "", "auto": "", "system": "", "sistema": "",
    "en": "en", "english": "en", "inglés": "en", "ingles": "en",
    "es": "es", "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
}

LANGUAGE_NAMES: dict[str, str] = {"en": N_("English"), "es": N_("Spanish")}

YES_WORDS = frozenset({"on", "yes", "y", "true", "1", "sí", "si", "s"})
NO_WORDS = frozenset({"off", "no", "n", "false", "0"})

RESTART_NOTE = N_("takes effect the next time hamrlog starts")
NOW_NOTE = N_("takes effect at once")


def _on_off(value: bool) -> str:
    return _("on") if value else _("off")


def _switch(text: str) -> bool:
    word = text.lower()
    if word in YES_WORDS:
        return True
    if word in NO_WORDS:
        return False
    raise ServiceError(_("Type on or off."))


def _language_value(code: str) -> str:
    if not code:
        return _("automatic ({language})").format(
            language=_(LANGUAGE_NAMES.get(language(), "English"))
        )
    return _(LANGUAGE_NAMES[code])


class SettingKind(Kind):
    key = "settings"
    title = N_("Settings")
    fields = ("value",)
    columns = (
        (N_("SETTING"), 18),
        (N_("VALUE"), 48),
        (N_("APPLIES"), None),
    )
    can_add = False
    can_delete = False

    def items(self, query: str = "") -> list[Item]:
        config = appconfig.load()
        rows = (
            (
                DATABASE,
                N_("Database"),
                config.database or _("default: {path}").format(path=database_path()),
                RESTART_NOTE,
                (
                    N_("SQLite file of the log; empty uses the default one"),
                    N_("A path such as ~/radio/hamrlog.sqlite3, or a full database URL"),
                ),
            ),
            (
                LANGUAGE,
                N_("Language"),
                _language_value(config.language),
                RESTART_NOTE,
                (
                    N_("Language of the interface: en, es, or empty to follow the system"),
                    N_("The HAMRLOG_LANG variable, when set, takes precedence"),
                ),
            ),
            (
                TIMEZONE,
                N_("Time zone"),
                config.timezone or _("UTC only"),
                NOW_NOTE,
                (
                    N_("Local time shown on the status line next to UTC"),
                    N_("A zone such as Europe/Madrid or Atlantic/Canary; empty shows UTC only"),
                ),
            ),
            (
                DAY_SEPARATOR,
                N_("Day separator"),
                _on_off(config.day_separator),
                NOW_NOTE,
                (
                    N_("A dashed grey line in the log between the QSOs of different days (UTC)"),
                    N_("on or off"),
                ),
            ),
            (
                UPDATES,
                N_("Updates"),
                _on_off(config.check_updates),
                RESTART_NOTE,
                (
                    N_("On start, look for a newer version and offer to install it"),
                    N_("on or off; the HAMRLOG_NO_UPDATE_CHECK variable turns it off as well"),
                ),
            ),
        )
        return [
            Item(
                row_id,
                _(name),
                (_(name), value, _(note)),
                (f"{_(name)} · {_(note)}", value, " · ".join(_(line) for line in help_lines)),
            )
            for row_id, name, value, note, help_lines in rows
        ]

    def values(self, item_id: int) -> dict[str, str]:
        config = appconfig.load()
        value = {
            DATABASE: config.database,
            LANGUAGE: config.language,
            TIMEZONE: config.timezone,
            DAY_SEPARATOR: _on_off(config.day_separator),
            UPDATES: _on_off(config.check_updates),
        }.get(item_id, "")
        return {"value": value}

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        config = appconfig.load()
        text = values.get("value", "").strip()
        if item_id == DATABASE:
            config.database = text
            name = _("Database")
        elif item_id == LANGUAGE:
            code = LANGUAGE_WORDS.get(text.lower())
            if code is None:
                raise ServiceError(
                    _("Unknown language: «{text}». Use en, es, or leave it empty.").format(
                        text=text
                    )
                )
            config.language = code
            name = _("Language")
        elif item_id == TIMEZONE:
            if text:
                try:
                    ZoneInfo(text)
                except (ZoneInfoNotFoundError, ValueError):
                    raise ServiceError(
                        _("Unknown time zone: «{text}». Use a name such as Europe/Madrid.")
                        .format(text=text)
                    ) from None
            config.timezone = text
            name = _("Time zone")
        elif item_id == DAY_SEPARATOR:
            config.day_separator = _switch(text)
            name = _("Day separator")
        elif item_id == UPDATES:
            config.check_updates = _switch(text)
            name = _("Updates")
        else:
            raise ServiceError(_("Settings are changed, not added."))
        appconfig.save(config)
        return name


#: The settings view has a single list.
SETTING_KINDS: tuple[Kind, ...] = (SettingKind(),)
