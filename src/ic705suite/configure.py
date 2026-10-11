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

CONNECTIONS = ("lan", "sim", "none", "server")
THEMES = ("dark", "light", "auto")
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
            (_("Locator"), config.locator or "-"),
            (_("Connection"), config.connection)]
    if config.connection == "server":
        rows.append((_("Radio's core"), config.connect or "-"))
    if config.connection == "lan":
        rows += [(_("Radio address"), config.host or "-"),
                 (_("User"), config.username or "-"),
                 (_("Password"), MASK if config.password else "-")]
    rows.append((_("Transmission"), _("enabled") if config.tx_enabled else _("disabled")))
    rows.append((_("Remote control from the phone"),
                 _("with a password") if config.swr_remote_password else
                 _("only from the antenna application")))
    rows.append((_("Graphical client"), config.theme))
    mycall = (config.packet_mycall or config.callsign or "-") if config.packet_client \
        else _("no terminal")
    if config.packet_tnc == "ic705":
        rows.append((_("Packet"), _("{call} on the IC-705 over WiFi, with Direwolf here")
                     .format(call=mycall)))
    elif config.packet_tnc == "direwolf" and config.packet_audio:
        rows.append((_("Packet"), _("{call} with Direwolf started here ({audio}, PTT {ptt})")
                     .format(call=mycall, audio=config.packet_audio,
                             ptt=config.packet_ptt or "VOX")))
    elif config.packet_host:
        rows.append((_("Packet"), _("{call} on the TNC at {host}:{port}").format(
            call=mycall, host=config.packet_host, port=config.packet_tcp_port)))
    else:
        rows.append((_("Packet"), _("not set up")))
    if config.packet_bbs and (config.packet_host or config.packet_tnc in ("direwolf", "ic705")):
        base = config.callsign.split("/")[0].split("-")[0]
        rows.append((_("BBS"), config.packet_bbs_call or f"{base}-1"))
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


def _tnc(value: str) -> str | None:
    host, _sep, port = value.rpartition(":") if ":" in value else (value, "", "")
    if not value or (host and (not port or port.isdigit())):
        return None
    return _("Write it as host or host:port, for example raspberrypi.local:8000")


def _packet_call(value: str) -> str | None:
    from ic705suite.apps.packet.agwpe import CALLSIGN as AX25_CALLSIGN

    if AX25_CALLSIGN.match(value):
        return None
    return _("A callsign of up to 6 characters and an SSID from 0 to 15, for example "
             "EA7XYZ-4")


PACKET_TNCS = ("none", "agwpe", "direwolf", "ic705")


def _packet_questions(q: Questions, config: Config) -> None:
    """Which TNC packet uses (if any), how to reach it or what Direwolf runs with here, and
    the callsign."""
    from ic705suite.apps.packet.direwolf import find_devices, find_direwolf
    from ic705suite.apps.packet.ic705 import loopback_help, loopback_present

    if config.packet_tnc == "ic705" or (config.packet_tnc == "direwolf" and
                                        config.packet_audio):
        current = config.packet_tnc
    else:
        current = "agwpe" if config.packet_host else "none"
    kind = q.choice(_("Packet TNC: none, agwpe (a Direwolf already running, here or on "
                      "another machine), direwolf (the suite starts its own here, e.g. with "
                      "a Digirig) or ic705 (the IC-705 over WiFi, with Direwolf here; Linux)"),
                    PACKET_TNCS, current)
    if kind == "none":
        config.packet_tnc, config.packet_host = "agwpe", ""
        return
    config.packet_tnc = kind
    if kind in ("direwolf", "ic705") and find_direwolf(config.packet_direwolf) is None:
        q.write(_("Direwolf is not installed here yet: install it (Debian, Ubuntu, "
                  "Raspberry Pi OS: apt install direwolf; Windows: its installer) before "
                  "using packet."))
    if kind == "ic705":
        q.write(_("The IC-705 transmits packet in FM-D with its DATA1 MOD input on WLAN (the "
                  "suite sets it; /qrg 144.800 in packet tunes it). Transmitting also needs "
                  "[rig] tx_enabled."))
        if not loopback_present():
            q.write(_("It needs the snd-aloop virtual sound card, missing now; load it once "
                      "with: {command}").format(command=loopback_help()))
    elif kind == "agwpe":
        address = (f"{config.packet_host}:{config.packet_tcp_port}"
                   if config.packet_host else "")
        tnc = q.text(_("Direwolf's AGWPE port as host or host:port"), address, _tnc_address)
        host, _sep, port = tnc.rpartition(":") if ":" in tnc else (tnc, "", "")
        config.packet_host, config.packet_tcp_port = host, int(port or 8000)
    else:
        devices = find_devices()
        for device in devices:
            q.write(_("Found: {device}").format(device=device.description))
        if not devices:
            q.write(_("No Digirig found plugged in: type its sound card and PTT."))
        found = devices[0] if devices else None
        config.packet_audio = q.text(
            _("Sound card for Direwolf (ADEVICE, e.g. plughw:Device,0)"),
            config.packet_audio or (found.audio if found else ""), _not_empty)
        ptt = q.text(_("PTT for Direwolf (e.g. /dev/ttyUSB0 RTS, COM5 RTS, CM108; vox for "
                       "none)"), config.packet_ptt or (found.ptt if found else "vox"))
        config.packet_ptt = "" if ptt.lower() in ("vox", "none", "-") else ptt
    base = config.callsign.split("/")[0].split("-")[0]
    config.packet_client = q.yes(_("Packet terminal (F7) with a callsign of its own?"),
                                 config.packet_client)
    if config.packet_client:
        config.packet_mycall = q.text(_("Packet callsign, with its SSID"),
                                      config.packet_mycall or f"{base}-4", _packet_call,
                                      upper=True)
    config.packet_bbs = q.yes(_("A BBS on this machine (messages and bulletins for the "
                                "stations that connect to it)?"), config.packet_bbs)
    if config.packet_bbs:
        config.packet_bbs_call = q.text(_("BBS callsign, with its SSID"),
                                        config.packet_bbs_call or f"{base}-1", _packet_call,
                                        upper=True)


def _tnc_address(value: str) -> str | None:
    return _not_empty(value) or _tnc(value)


def _locator(value: str) -> str | None:
    if not value or re.match(r"^[A-R]{2}\d{2}([A-X]{2}(\d{2})?)?$", value):
        return None
    return _("That does not look like a locator (for example IM77 or IM77AJ)")


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
        config.locator = q.text(_("Your locator (Maidenhead, e.g. IM77; FT8 sends it)"),
                                config.locator, _locator, upper=True)
        config.connection = q.choice(
            _("How to reach the radio: lan (WiFi/Ethernet), sim (simulator), none (no radio: "
              "a core for packet only) or server (through another machine's core)"),
            CONNECTIONS, config.connection if config.connection in CONNECTIONS else "lan")
        if config.connection == "lan":
            q.write(_("On the IC-705: MENU > SET > WLAN Set (Connection Status shows its "
                      "address; Remote Settings, its Network User1)."))
            config.host = q.text(_("Radio address (IP or name)"), config.host, _not_empty)
            config.username = q.text(_("Network user"), config.username, _not_empty)
            config.password = q.password(_("Network password"), config.password)
        if config.connection == "server":
            config.connect = q.text(_("Address of the machine with the radio's core "
                                      "(ic705suite --serve), host or host:port"),
                                    config.connect, _tnc_address)
            config.client_token = q.password(_("Its token ([server] token there)"),
                                             config.client_token)
        q.write(_("Transmission stays off until the antenna SWR is right for your bands."))
        config.tx_enabled = q.yes(_("Enable transmission?"), config.tx_enabled)
        config.swr_remote_password = q.password(
            _("Password for switching on the antenna's remote control from the phone (empty: "
              "only from the antenna application)"), config.swr_remote_password)
        config.theme = q.choice(
            _("Look of the graphical client (ic705suite --gui): dark, light or auto (as the "
              "system)"), THEMES, config.theme if config.theme in THEMES else "auto")
        _packet_questions(q, config)

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
        ("station", "locator"): config.locator,
        ("rig", "connection"): config.connection,
        ("ui", "theme"): config.theme,
        ("swr", "remote_password"): config.swr_remote_password,
        ("packet", "host"): config.packet_host,
        ("packet", "tnc"): config.packet_tnc,
        (_section_of(text, "tx_enabled", ("rig", "keyer")), "tx_enabled"): config.tx_enabled,
    }
    if config.packet_host or config.packet_tnc in ("direwolf", "ic705"):
        values.update({("packet", "tcp_port"): config.packet_tcp_port,
                       ("packet", "client"): config.packet_client,
                       ("packet", "bbs"): config.packet_bbs})
        if config.packet_client:
            values[("packet", "mycall")] = config.packet_mycall
        if config.packet_bbs:
            values[("packet", "bbs_call")] = config.packet_bbs_call
    if config.packet_tnc == "direwolf":
        values.update({("packet", "audio"): config.packet_audio,
                       ("packet", "ptt"): config.packet_ptt})
    if config.connection == "server":
        values.update({("client", "connect"): config.connect,
                       ("client", "token"): config.client_token})
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
