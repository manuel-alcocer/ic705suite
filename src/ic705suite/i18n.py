"""Translations of everything the operator reads.

The source language is English: every user-facing string is written in English and
wrapped in ``_()`` (or marked with ``N_()`` where it is defined before the language is
known). Other languages are gettext ``.po`` files under ``ic705suite/locales/<lang>/``,
parsed directly: no compile step and no ``.mo`` files to keep in sync. Same approach as
HAMRLOG.

Messages produced by the core (which may run on another machine) travel as an English
template plus arguments, and each client translates them into its own language::

    hub.info(N_("signal found at {freq} MHz"), freq="7.012.00")
    # event: {"type": "info", "msgid": "signal found at {freq} MHz",
    #         "args": {"freq": "7.012.00"}, "text": "signal found at 7.012.00 MHz"}
    render(event)  # -> "señal encontrada en 7.012.00 MHz" with the Spanish catalog
"""

from __future__ import annotations

import locale
import logging
import os
from importlib import resources

log = logging.getLogger(__name__)

LANG_ENV = "IC705SUITE_LANG"
SOURCE_LANGUAGE = "en"
AVAILABLE = ("en", "es")
# Language variables of the apps that bring their own catalogs (hamrlog, hamrpkt).
APP_LANG_ENVS = ("HAMRLOG_LANG", "HAMRPKT_LANG")

_catalog: dict[str, str] | None = None
_language: str | None = None


def language_code(value: str) -> str:
    """"es_ES.UTF-8", "es-ES" -> "es"."""
    return value.split(".")[0].split("_")[0].split("-")[0].strip().lower()


def detect_language(configured: str = "") -> str:
    """IC705SUITE_LANG, then the config file's [ui] language, then the system locale."""
    candidates = [os.environ.get(LANG_ENV, ""), configured]
    candidates += [os.environ.get(name, "") for name in ("LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    for value in candidates:
        code = language_code(value)
        if code in AVAILABLE:
            return code
    return SOURCE_LANGUAGE


def set_language(language: str | None = None, configured: str = "") -> str:
    """Choose the suite's language; hamrlog and hamrpkt, which keep their own catalogs,
    follow it through their environment variables."""
    global _catalog, _language
    _language = language or detect_language(configured)
    _catalog = {} if _language == SOURCE_LANGUAGE else load_catalog(_language)
    for name in APP_LANG_ENVS:
        os.environ[name] = _language
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


def N_(message: str) -> str:  # noqa: N802 - the gettext convention
    """Mark a string for translation without translating it yet."""
    return message


def render(event: dict) -> str:
    """Text of a core message event in the client's language."""
    msgid = event.get("msgid")
    if not msgid:
        return str(event.get("text", ""))
    try:
        return _(msgid).format(**event.get("args", {}))
    except (KeyError, IndexError, ValueError):
        return str(event.get("text", msgid))


# ------------------------------------------------------------------ .po files --

def load_catalog(language: str) -> dict[str, str]:
    catalog: dict[str, str] = {}
    try:
        folder = resources.files("ic705suite").joinpath("locales", language)
        files = sorted((e for e in folder.iterdir() if e.name.endswith(".po")),
                       key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError, OSError):
        log.warning("i18n: no translations for %s", language)
        return catalog
    for source in files:
        catalog.update(parse_po(source.read_text(encoding="utf-8")))
    return catalog


def parse_po(text: str) -> dict[str, str]:
    """msgid -> msgstr of a .po file (comments, multi-line strings; empty ones skipped)."""
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
    body, out, i = token[1:-1], [], 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append(escapes.get(body[i + 1], body[i + 1]))
            i += 2
            continue
        out.append(body[i])
        i += 1
    return "".join(out)
