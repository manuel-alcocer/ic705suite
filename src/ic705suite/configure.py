"""The settings asked for at the terminal (ic705suite --configure) and shown (--show-config).

What the installers run after installing or upgrading, and show before uninstalling. The values
already in the config file are the defaults (Enter keeps them); a new file starts from the
template. Only the lines asked about change, so the file keeps its comments and every other
setting.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from ic705suite import i18n
from ic705suite.config import DEFAULT_PATH, TEMPLATE, Config, load_config, set_values
from ic705suite.i18n import _

CONNECTIONS = ("lan", "sim")
CALLSIGN = re.compile(r"^[A-Z0-9]+(/[A-Z0-9]+)*$")
MASK = "********"


def _section_of(text: str, key: str, sections: tuple[str, ...]) -> str:
    """The first of `sections` where `key` is set, else the first one (tx_enabled used to
    live in [keyer], in cwic705's files)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return sections[0]
    return next((s for s in sections if key in data.get(s, {})), sections[0])


def summary(config: Config) -> list[tuple[str, str]]:
    language = config.language or _("automatic ({language})").format(
        language=i18n.detect_language())
    rows = [(_("Language"), language),
            (_("Callsign"), config.callsign or "-"),
            (_("Connection"), config.connection)]
    if config.connection == "lan":
        rows += [(_("Radio address"), config.host or "-"),
                 (_("User"), config.username or "-"),
                 (_("Password"), MASK if config.password else "-")]
    rows.append((_("Transmission"), _("enabled") if config.tx_enabled else _("disabled")))
    return rows


def show(config: Config, write: Callable[[str], None] = print) -> None:
    width = max(len(name) for name, _value in summary(config))
    for name, value in summary(config):
        write(f"  {name.ljust(width)}  {value}")


def show_config(path: Path | None = None, write: Callable[[str], None] = print,
                language: str | None = None) -> int:
    path = path or DEFAULT_PATH
    if not path.exists():
        write(_("No settings yet ({path})").format(path=path))
        return 1
    config = load_config(path)
    i18n.set_language(language, config.language)
    write(_("Settings in {path}:").format(path=path))
    show(config, write)
    return 0


class Questions:
    """Asks at the terminal; `ask` and `secret` are input() and getpass() unless a test
    gives others."""

    def __init__(self, ask: Callable[[str], str] = input,
                 secret: Callable[[str], str] = getpass.getpass,
                 write: Callable[[str], None] = print) -> None:
        self.ask, self.secret, self.write = ask, secret, write

    def text(self, prompt: str, default: str, check: Callable[[str], str | None] | None = None,
             upper: bool = False) -> str:
        while True:
            answer = self.ask(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
            value = (answer.upper() if upper else answer) or default
            problem = check(value) if check else None
            if problem is None:
                return value
            self.write(problem)

    def choice(self, prompt: str, options: tuple[str, ...], default: str) -> str:
        def check(value: str) -> str | None:
            if value in options:
                return None
            return _("Choose one of: {options}").format(options=", ".join(options))
        return self.text(f"{prompt} ({'/'.join(options)})", default, check)

    def password(self, prompt: str, current: str) -> str:
        hint = _("Enter keeps the current one") if current else _("Enter leaves it empty")
        return self.secret(f"{prompt} ({hint}): ") or current

    def yes(self, prompt: str, default: bool) -> bool:
        yes_no = _("[Y/n]") if default else _("[y/N]")
        while True:
            answer = self.ask(f"{prompt} {yes_no}: ").strip().lower()
            if not answer:
                return default
            if answer[0] in ("y", "s"):     # yes / sí
                return True
            if answer[0] == "n":
                return False


def _callsign(value: str) -> str | None:
    if CALLSIGN.match(value) and 3 <= len(value) <= 15:
        return None
    return _("That does not look like a callsign (for example EA7XYZ or EA7XYZ/P)")


def _not_empty(value: str) -> str | None:
    return None if value else _("It cannot be empty")


def configure(path: Path | None = None, questions: Questions | None = None,
              language: str | None = None) -> int:
    """Asks for the settings, shows them and saves them once confirmed. 0 when saved or
    left as they were, 130 when interrupted (nothing written then)."""
    path = path or DEFAULT_PATH
    q = questions or Questions()
    existed = path.exists()
    text = path.read_text(encoding="utf-8") if existed else TEMPLATE
    config = load_config(path) if existed else Config(callsign="", connection="lan")
    # The password typed in the environment is not the file's: never written back.
    if os.environ.get("IC705SUITE_PASSWORD") and existed:
        config.password = str(tomllib.loads(text).get("rig", {}).get("password", ""))
    i18n.set_language(language, config.language)
    try:
        if existed:
            q.write(_("ic705suite is already set up in {path}:").format(path=path))
            show(config, q.write)
            q.write(_("Enter keeps each value as it is."))
        else:
            q.write(_("Setting up ic705suite ({path}).").format(path=path))

        config.language = q.choice(_("Language"), i18n.AVAILABLE, i18n.language())
        i18n.set_language(config.language)
        config.callsign = q.text(_("Your callsign"), config.callsign, _callsign, upper=True)
        config.connection = q.choice(
            _("How to reach the radio: lan (WiFi/Ethernet) or sim (simulator, no radio)"),
            CONNECTIONS, config.connection if config.connection in CONNECTIONS else "lan")
        if config.connection == "lan":
            q.write(_("On the IC-705: MENU > SET > WLAN Set (Connection Status shows its "
                      "address; Remote Settings, its Network User1)."))
            config.host = q.text(_("Radio address (IP or name)"), config.host, _not_empty)
            config.username = q.text(_("Network user"), config.username, _not_empty)
            config.password = q.password(_("Network password"), config.password)
        q.write(_("Transmission stays off until the antenna SWR is right for your bands."))
        config.tx_enabled = q.yes(_("Enable transmission?"), config.tx_enabled)

        q.write("")
        q.write(_("The settings will be:"))
        show(config, q.write)
        if not q.yes(_("Save them in {path}?").format(path=path), True):
            q.write(_("Settings not changed."))
            return 0
    except (KeyboardInterrupt, EOFError):
        q.write("")
        q.write(_("Settings not changed."))
        return 130

    values = {
        ("ui", "language"): config.language,
        ("station", "callsign"): config.callsign,
        ("rig", "connection"): config.connection,
        (_section_of(text, "tx_enabled", ("rig", "keyer")), "tx_enabled"): config.tx_enabled,
    }
    if config.connection == "lan":
        values.update({("rig", "host"): config.host, ("rig", "username"): config.username,
                       ("rig", "password"): config.password})
    if not existed:   # a fresh file keeps the template's random server token
        import secrets

        text = text.replace("{token}", secrets.token_urlsafe(18))
    save(path, set_values(text, values))
    q.write(_("Saved in {path}.").format(path=path))
    return 0


def save(path: Path, text: str) -> None:
    """Writes the whole file at once (a half-written config never exists) and private: it
    holds the radio's password."""
    tomllib.loads(text)                       # never write a file the suite cannot read
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def run(path: Path | None = None, language: str | None = None) -> int:
    """ic705suite --configure: needs a terminal to ask at."""
    if not sys.stdin.isatty():
        print(_("No terminal to ask at: edit {path} or run ic705suite --configure in a "
                "terminal.").format(path=path or DEFAULT_PATH), file=sys.stderr)
        return 1
    return configure(path, language=language)
