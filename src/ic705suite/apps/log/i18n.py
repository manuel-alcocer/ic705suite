"""Translations of everything the operator reads.

The source language is English: every user-facing string in the code is
written in English and wrapped in ``_()``. Other languages live in gettext
``.po`` files under ``hamrlog/locales/<lang>/``; every ``.po`` file in that
folder is read, so translations can be split by area.

The ``.po`` files are parsed directly: no compile step, no ``.mo`` files to
keep in sync, and nothing extra to bundle.

Usage::

    from ic705suite.apps.log.i18n import _, N_

    panel.feedback(_("Band {band}").format(band=name))

    # Module level tables are built at import time, before the language is
    # known, so they hold the English text marked with N_() and translate it
    # with _() where it is shown.
    LABELS = {"call": N_("CALL")}
    label = _(LABELS["call"])
"""

from __future__ import annotations

import locale
import logging
import os
import sys
from importlib import resources

logger = logging.getLogger(__name__)

#: Overrides the language picked from the system locale, e.g. "en" or "es".
LANG_ENV = "HAMRLOG_LANG"

#: Languages with a catalog, plus English, which is the source.
SOURCE_LANGUAGE = "en"
AVAILABLE = ("en", "es")

_catalog: dict[str, str] | None = None
_language: str | None = None


#: Windows names its locales in English words ("Spanish_Spain"), not codes.
WINDOWS_NAMES = {"spanish": "es", "english": "en"}


def detect_language() -> str:
    """The language to use: HAMRLOG_LANG, the settings (F9), the system's, English."""
    from . import appconfig

    candidates = [os.environ.get(LANG_ENV, ""), appconfig.load().language]
    candidates += [os.environ.get(name, "") for name in ("LC_ALL", "LC_MESSAGES", "LANG")]
    candidates.append(_windows_ui_language())
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:  # pragma: no cover - unusual locale settings
        pass
    for value in candidates:
        code = language_code(value)
        if code in AVAILABLE:
            return code
    return SOURCE_LANGUAGE


def language_code(value: str) -> str:
    """"es_ES.UTF-8", "es-ES", "Spanish_Spain.1252" -> "es"."""
    head = value.split(".")[0].split("_")[0].split("-")[0].strip().lower()
    return WINDOWS_NAMES.get(head, head)


def _windows_ui_language() -> str:
    """The language of the Windows interface, e.g. "es_ES"; empty elsewhere.

    Windows rarely sets LANG, and Python's locale reflects the regional
    format rather than the language the user reads, so ask the system.
    """
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        return locale.windows_locale.get(lcid, "")
    except (AttributeError, OSError):  # pragma: no cover - only on odd systems
        return ""


def set_language(language: str | None = None) -> str:
    """Load the catalog for ``language`` (detected when None) and use it."""
    global _catalog, _language
    _language = language or detect_language()
    _catalog = {} if _language == SOURCE_LANGUAGE else load_catalog(_language)
    return _language


def language() -> str:
    if _language is None:
        set_language()
    return _language  # type: ignore[return-value]


def _(message: str) -> str:
    """The translation of ``message``, or ``message`` itself when there is none."""
    if _catalog is None:
        set_language()
    return (_catalog or {}).get(message) or message


def untranslate(text: str) -> str:
    """The English text whose translation is ``text``, ignoring case.

    For values the operator types in their language but the program stores
    in English, such as a country: «españa» gives "Spain". Text with no such
    translation comes back as it was.
    """
    if _catalog is None:
        set_language()
    needle = text.strip().lower()
    for msgid, msgstr in (_catalog or {}).items():
        if msgstr.lower() == needle:
            return msgid
    return text.strip()


def N_(message: str) -> str:  # noqa: N802 - the gettext convention
    """Mark a string for translation without translating it yet."""
    return message


# --------------------------------------------------------------- .po files --
def load_catalog(language: str) -> dict[str, str]:
    """Every msgid -> msgstr of the ``.po`` files of one language."""
    catalog: dict[str, str] = {}
    try:
        folder = resources.files("ic705suite.apps.log").joinpath("locales", language)
        files = sorted(
            (entry for entry in folder.iterdir() if entry.name.endswith(".po")),
            key=lambda entry: entry.name,
        )
    except (FileNotFoundError, NotADirectoryError, OSError):
        logger.warning("i18n: no translations for %s", language)
        return catalog
    for source in files:
        catalog.update(parse_po(source.read_text(encoding="utf-8")))
    return catalog


def parse_po(text: str) -> dict[str, str]:
    """Translations in the text of a ``.po`` file.

    Handles what these files use: comments, ``msgid``/``msgstr`` pairs and
    strings continued over several quoted lines. Empty translations and the
    header entry are left out, so the English text shows instead.
    """
    entries: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    current: list[str] | None = None

    def flush() -> None:
        if msgid is not None and msgstr is not None:
            key, value = "".join(msgid), "".join(msgstr)
            if key and value:
                entries[key] = value

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            flush()
            msgid, msgstr = [_unquote(line[6:])], None
            current = msgid
        elif line.startswith("msgstr "):
            msgstr = [_unquote(line[7:])]
            current = msgstr
        elif line.startswith('"') and current is not None:
            current.append(_unquote(line))
    flush()
    return entries


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        return ""
    body = token[1:-1]
    out: list[str] = []
    index = 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            out.append(escapes.get(body[index + 1], body[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)
