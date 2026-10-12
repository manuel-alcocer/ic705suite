"""Suite configuration (~/.config/ic705suite/config.toml).

One file for the station core and every application that runs on it. hamrlog and hamrpkt
keep their own settings (F9 inside each of them).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ic705suite import paths

log = logging.getLogger(__name__)

DEFAULT_PATH = paths.config_dir() / "config.toml"
DEFAULT_SCP_PATH = DEFAULT_PATH.parent / "MASTER.SCP"
SCP_URL = "https://www.supercheckpartial.com/MASTER.SCP"
# The CW program this suite grew from; its settings are imported on the first run.
LEGACY_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "cwic705"

DEFAULT_MACROS = {
    "F2": "CQ CQ CQ DE {call} {call} K",
    "F3": "{call}",
    "F4": "TU 5NN",
    "F5": "R R TU 73 DE {call} <SK>",
    "F6": "QRZ?",
    "F7": "AGN?",
}

TEMPLATE = """\
# ic705suite configuration

[rig]
# Radio model: "ic705" (more models to come)
model = "ic705"
# How to reach it: "lan" (WiFi/Ethernet), "usb" (next version), "sim" (built-in simulator)
# or "none" (no radio: a core that serves packet only, e.g. a Raspberry Pi with a Digirig),
# or "server" (the radio is on another machine's core, ic705suite --serve: [client] connect;
# ic705suite then always connects there)
connection = "lan"
# IP address of the IC-705 (MENU > SET > WLAN Set > Connection Status shows it)
host = "192.168.1.50"
# Network User1 ID / password (MENU > SET > WLAN Set > Remote Settings)
username = "user"
password = "password"
# control_port = 50001
# sample_rate = 8000          # receive audio: 8000, 16000, 24000 or 48000
# civ_address = 0xA4          # only needed if you changed it on the radio
# serial_device = "/dev/ttyACM0"   # connection = "usb"
# Transmission is disabled unless enabled here or with --tx.
# Only enable it once the antenna SWR is correct for the band you use.
tx_enabled = false

[station]
callsign = "N0CALL"
# Maidenhead locator (FT8 sends its first four characters), e.g. "IM77"
# locator = ""

[database]
# Any SQLAlchemy URL. PostgreSQL is recommended (pip install ic705suite[postgres]):
# url = "postgresql+psycopg://user:password@host/ic705suite"
# Default: a SQLite file, ic705suite.db, in the suite's data folder
# (~/.local/share/ic705suite on Linux, %LOCALAPPDATA%\\ic705suite on Windows)

[keyer]
wpm = 20

[decoder]
afc = true
# "bayes": probabilistic decoder with a CW language model (default); "classic": thresholds
# engine = "bayes"

[skimmer]
# Super Check Partial database used to validate callsigns (ic705suite --update-scp)
# Default: MASTER.SCP next to this file
# scp_path = "~/MASTER.SCP"

[channels]
# Keep the session audio so CW channels can be saved with lossless FLAC audio
audio = true
# --serve: archive unsaved channels automatically when the server stops
autosave_on_server_exit = true

[swr]
# Antenna tuning page for the phone, served by the core: http://<core address>:8705
# Address it listens on: "0.0.0.0" for the phone on the local network, "127.0.0.1" for
# this computer only
web = true
host = "0.0.0.0"
port = 8705
# Highest RF power for SWR sweeps, percent (30 % = 3 W on the IC-705). SWR sweeps transmit,
# so they also need [rig] tx_enabled = true (or --tx); noise sweeps never transmit.
max_power = 30
# The remote control (the phone may sweep) is switched on in the antenna application. With
# a password here the phone page can switch it on too, once it is typed there (up at the
# antenna, without going down to the computer). Empty: only from the antenna application.
# remote_password = ""

[packet]
# Packet radio (F7): the suite's client over a Direwolf TNC.
# core = "raspberrypi.local:7373": packet runs on that machine's core (ic705suite --serve,
#   e.g. a Raspberry Pi with the Digirig and the BBS) and F7 opens it there, while the rest
#   of the suite stays on this one; core_token is its [server] token (default: [client]
#   token). The settings below are then that machine's, not these.
# tnc = "agwpe": a Direwolf already running, here or on another machine (e.g. a Raspberry
#   Pi with a Digirig), reached by its AGWPE port (AGWPORT): host and tcp_port.
# tnc = "direwolf": the suite starts its own Direwolf on this machine, with the sound card
#   and PTT below (ic705suite --configure finds a Digirig plugged in), AGWPE on tcp_port.
# tnc = "ic705": the IC-705 over WiFi is the radio, with Direwolf here (Linux, with the
#   snd-aloop virtual sound card); it transmits in FM-D (/qrg 144.800 in packet tunes it).
# tnc = "agwpe"
# host = "raspberrypi.local"
# tcp_port = 8000
# audio = "plughw:Device,0"      # Direwolf's ADEVICE (Windows: part of the card's name)
# ptt = "/dev/ttyUSB0 RTS"       # Direwolf's PTT: "COM5 RTS", "CM108", "" for the VOX
# txdelay = 30                   # tens of ms before the data, for the radio to key up
# The BBS (messages and bulletins) on its own callsign (default: your callsign -1), and the
# client's callsign (mycall): either, or both.
# bbs = false
# bbs_call = "N0CALL-1"
# client = true
# direwolf = "direwolf"          # the program, when it is not on the PATH
# Own callsign for packet, with its SSID (default: [station] callsign)
# mycall = "N0CALL-4"
# Radio port of the TNC, from 1
# radio_port = 1

[ft8]
# FT8 (F8) decodes each cycle with WSJT-X's jt9 (apt install wsjtx): the program, when it is
# not on the PATH or where WSJT-X installs it, and how hard it tries (1 fast, 3 deepest; 0,
# the default: a quick pass and, when there is time, a deeper one, as this machine allows)
# jt9 = "jt9"
# depth = 0
# Audio frequency (Hz) of our transmissions, until a click or a key moves it
# tx_freq = 1500
# Transmit audio, of full scale (0.05-1): lower it if the ALC moves while sending (F8 shows
# it); the radio's power sets the watts
# level = 0.5
# The logbook's setup (radio and antennas) the QSOs go with; by default the first setup
# whose radio for the band is the one FT8 uses
# setup = "IC-705 HF casa"

[qrz]
# QRZ.com's logbook access key (on QRZ.com: your logbook > Settings > API Access Key);
# ic705suite --qrz-status checks it. Keep this file to yourself.
# api_key = ""

[ui]
# Interface language: "en" or "es" (default: the system language; IC705SUITE_LANG overrides)
# language = "es"
# Characters for the screen: "auto" (plain ones on the Linux console, TERM=linux), "unicode",
# or "console" (block font for the frequency, shaded 16-colour waterfall, square borders)
# glyphs = "auto"
# The graphical client's look (ic705suite --gui): "dark", "light" or "auto" (as the system)
# theme = "auto"

[server]
# ic705suite --serve: address to listen on and the token clients must present
listen = "0.0.0.0:7373"
token = "{token}"

[client]
# ic705suite --connect with no address uses these (e.g. the Raspberry Pi running --serve)
# connect = "rpi.local:7373"
token = "{token}"

[macros]
# CW function keys F2..F9. {call} is replaced by your callsign.
F2 = "CQ CQ CQ DE {call} {call} K"
F3 = "{call}"
F4 = "TU 5NN"
F5 = "R R TU 73 DE {call} <SK>"
F6 = "QRZ?"
F7 = "AGN?"
"""


@dataclass
class Config:
    # [rig]
    rig_model: str = "ic705"
    connection: str = ""             # "" = the model's default (lan for the IC-705)
    host: str | None = None
    username: str = ""
    password: str = ""
    control_port: int = 50001
    sample_rate: int = 8000
    civ_address: int | None = None
    serial_device: str = ""
    tx_enabled: bool = False
    # [station]
    callsign: str = ""
    locator: str = ""
    # [database]
    database_url: str = ""           # SQLAlchemy URL; "" = SQLite in the data folder
    # CW
    wpm: int = 20
    afc: bool = True
    decoder: str = "bayes"   # "bayes" (probabilistic) or "classic"
    scp_path: Path = DEFAULT_SCP_PATH
    channels_db: str = ""            # overrides database_url for the CW channels only
    channel_audio: bool = True       # keep the session audio so channels can be saved with it
    autosave_on_exit: bool = True    # server mode: archive unsaved channels when stopping
    macros: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MACROS))
    # [swr]
    swr_web: bool = True
    swr_host: str = "0.0.0.0"     # antenna page: all interfaces, for the phone
    swr_port: int = 8705
    swr_max_power: int = 30
    swr_remote_password: str = ""    # lets the phone page switch the remote control on
    # [packet]
    packet_host: str = ""            # the TNC's AGWPE server; "" = packet not set up
    packet_tcp_port: int = 8000
    packet_mycall: str = ""          # "" = the station callsign
    packet_radio_port: int = 1
    packet_tnc: str = "agwpe"        # agwpe (one running), direwolf (started here) or ic705
    packet_audio: str = ""
    packet_ptt: str = ""
    packet_txdelay: int = 30
    packet_direwolf: str = ""
    packet_bbs: bool = False
    # [ft8]
    ft8_jt9: str = ""
    ft8_depth: int = 0               # 0: chosen from how long each depth takes here
    ft8_tx_freq: int = 1500
    ft8_level: float = 0.5
    ft8_setup: str = ""              # the logbook's setup FT8 QSOs go with ("": by radio)
    # [qrz]
    qrz_api_key: str = ""            # QRZ.com's logbook access key (never shown)
    packet_core: str = ""            # "host:port" of the core packet runs on ("" = this one)
    packet_core_token: str = ""
    packet_bbs_call: str = ""        # "" = the station callsign -1
    packet_client: bool = True
    # [ui] [server] [client]
    language: str = ""
    glyphs: str = "auto"
    theme: str = "auto"              # the graphical client: dark, light or auto
    listen: str = "127.0.0.1:7373"
    server_token: str = ""
    connect: str = ""
    client_token: str = ""
    # Where it was read from (the graphical client saves the theme chosen in its menu there).
    source: Path | None = None

    @property
    def channels_url(self) -> str:
        return self.channels_db or self.database_url


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    cfg = Config(source=path)
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        # [radio] is the name the section had in cwic705.
        rig = {**data.get("radio", {}), **data.get("rig", {})}
        cfg.rig_model = str(rig.get("model", cfg.rig_model))
        cfg.connection = str(rig.get("connection", cfg.connection))
        cfg.host = rig.get("host", cfg.host)
        cfg.username = rig.get("username", cfg.username)
        cfg.password = rig.get("password", cfg.password)
        cfg.control_port = int(rig.get("control_port", cfg.control_port))
        cfg.sample_rate = int(rig.get("sample_rate", cfg.sample_rate))
        cfg.civ_address = rig.get("civ_address", cfg.civ_address)
        cfg.serial_device = str(rig.get("serial_device", cfg.serial_device))
        keyer = data.get("keyer", {})
        cfg.tx_enabled = bool(rig.get("tx_enabled", keyer.get("tx_enabled", cfg.tx_enabled)))
        cfg.callsign = str(data.get("station", {}).get("callsign", cfg.callsign)).upper()
        cfg.locator = str(data.get("station", {}).get("locator", cfg.locator)).upper()
        cfg.database_url = str(data.get("database", {}).get("url", cfg.database_url))
        cfg.wpm = int(keyer.get("wpm", cfg.wpm))
        cfg.afc = bool(data.get("decoder", {}).get("afc", cfg.afc))
        cfg.decoder = str(data.get("decoder", {}).get("engine", cfg.decoder))
        if "scp_path" in data.get("skimmer", {}):
            cfg.scp_path = Path(data["skimmer"]["scp_path"]).expanduser()
        cfg.language = str(data.get("ui", {}).get("language", cfg.language))
        cfg.glyphs = str(data.get("ui", {}).get("glyphs", cfg.glyphs))
        cfg.theme = str(data.get("ui", {}).get("theme", cfg.theme)).lower()
        chans = data.get("channels", {})
        cfg.channels_db = str(chans.get("database", cfg.channels_db))
        cfg.channel_audio = bool(chans.get("audio", cfg.channel_audio))
        cfg.autosave_on_exit = bool(chans.get("autosave_on_server_exit", cfg.autosave_on_exit))
        swr = data.get("swr", {})
        cfg.swr_web = bool(swr.get("web", cfg.swr_web))
        cfg.swr_host = str(swr.get("host", cfg.swr_host))
        cfg.swr_port = int(swr.get("port", cfg.swr_port))
        cfg.swr_max_power = int(swr.get("max_power", cfg.swr_max_power))
        cfg.swr_remote_password = str(swr.get("remote_password", cfg.swr_remote_password))
        packet = data.get("packet")
        if packet is None:
            packet = hamrpkt_settings()
        cfg.packet_host = str(packet.get("host", cfg.packet_host))
        cfg.packet_tcp_port = int(packet.get("tcp_port", cfg.packet_tcp_port))
        cfg.packet_mycall = str(packet.get("mycall", cfg.packet_mycall)).upper()
        cfg.packet_radio_port = int(packet.get("radio_port", cfg.packet_radio_port))
        cfg.packet_tnc = str(packet.get("tnc", cfg.packet_tnc)).lower()
        cfg.packet_audio = str(packet.get("audio", cfg.packet_audio))
        cfg.packet_ptt = str(packet.get("ptt", cfg.packet_ptt))
        cfg.packet_txdelay = int(packet.get("txdelay", cfg.packet_txdelay))
        cfg.packet_direwolf = str(packet.get("direwolf", cfg.packet_direwolf))
        cfg.packet_bbs = bool(packet.get("bbs", cfg.packet_bbs))
        cfg.packet_core = str(packet.get("core", cfg.packet_core))
        cfg.packet_core_token = str(packet.get("core_token", cfg.packet_core_token))
        cfg.packet_bbs_call = str(packet.get("bbs_call", cfg.packet_bbs_call)).upper()
        cfg.packet_client = bool(packet.get("client", cfg.packet_client))
        ft8 = data.get("ft8", {})
        cfg.ft8_jt9 = str(ft8.get("jt9", cfg.ft8_jt9))
        cfg.ft8_depth = int(ft8.get("depth", cfg.ft8_depth))
        cfg.ft8_tx_freq = int(ft8.get("tx_freq", cfg.ft8_tx_freq))
        cfg.ft8_level = float(ft8.get("level", cfg.ft8_level))
        cfg.ft8_setup = str(ft8.get("setup", cfg.ft8_setup))
        cfg.qrz_api_key = str(data.get("qrz", {}).get("api_key", cfg.qrz_api_key)).strip()
        server, client = data.get("server", {}), data.get("client", {})
        cfg.listen = str(server.get("listen", cfg.listen))
        cfg.server_token = str(server.get("token", cfg.server_token))
        cfg.connect = str(client.get("connect", cfg.connect))
        cfg.client_token = str(client.get("token", cfg.client_token))
        cfg.macros.update({k.upper(): str(v) for k, v in data.get("macros", {}).items()})
    cfg.password = os.environ.get("IC705SUITE_PASSWORD", cfg.password)
    return cfg


def hamrpkt_settings() -> dict:
    """The [packet] settings of hamrpkt's own file, while the suite's has none: its AGWPE
    node, callsign and radio port carry on."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = Path(os.environ.get("HAMRPKT_HOME") or base / "hamrpkt") / "config.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    link = data.get("link", {})
    if link.get("type", "agw") != "agw":
        return {}
    found = {"host": link.get("host", ""), "tcp_port": link.get("port", 8000),
             "radio_port": link.get("radio_port", 1)}
    if data.get("station", {}).get("mycall"):
        found["mycall"] = data["station"]["mycall"]
    return found


def write_template(path: Path | None = None) -> Path:
    path = path or DEFAULT_PATH
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import secrets

    # Literal braces of the macros ({call}) survive: only {token} is replaced.
    path.write_text(TEMPLATE.replace("{token}", secrets.token_urlsafe(18)), encoding="utf-8")
    path.chmod(0o600)  # contains the radio password
    return path


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)   # a valid TOML basic string


def set_values(text: str, values: dict[tuple[str, str], object]) -> str:
    """The config file `text` with each (section, key) set to its value, everything else as
    it was: the key's line is replaced, a commented-out one ("# key = ...") brought back, or
    a new line added at the end of the section (the section too, if missing)."""
    lines = text.splitlines()
    for (section, key), value in values.items():
        _set_value(lines, section, key, f"{key} = {toml_value(value)}")
    return "\n".join(lines) + "\n"


def _set_value(lines: list[str], section: str, key: str, line: str) -> None:
    start, end = None, len(lines)
    for i, current in enumerate(lines):
        header = current.strip()
        if header.startswith("[") and header.endswith("]") and not header.startswith("[["):
            if start is not None:
                end = i
                break
            if header[1:-1].strip() == section:
                start = i
    if start is None:
        lines.extend(["", f"[{section}]", line] if lines and lines[-1].strip() else
                     [f"[{section}]", line])
        return
    for pattern in (rf"^\s*{re.escape(key)}\s*=", rf"^\s*#\s*{re.escape(key)}\s*="):
        for i in range(start + 1, end):
            if re.match(pattern, lines[i]):
                lines[i] = line
                return
    last = end
    while last - 1 > start and not lines[last - 1].strip():
        last -= 1
    lines.insert(last, line)


def migrate_legacy(path: Path | None = None, legacy_dir: Path = LEGACY_DIR) -> Path | None:
    """First run after cwic705: copy its settings and MASTER.SCP. Returns the new file, or
    None when there was nothing to migrate. The old files are left untouched."""
    path = path or DEFAULT_PATH
    old = legacy_dir / "config.toml"
    if path.exists() or not old.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    text = old.read_text(encoding="utf-8")
    text = text.replace("[radio]", '[rig]\nmodel = "ic705"\nconnection = "lan"', 1)
    text = text.replace("~/.config/cwic705/", "~/.config/ic705suite/")
    path.write_text("# Imported from ~/.config/cwic705/config.toml by ic705suite\n" + text,
                    encoding="utf-8")
    path.chmod(0o600)
    scp = legacy_dir / "MASTER.SCP"
    if scp.exists() and not (path.parent / "MASTER.SCP").exists():
        shutil.copy2(scp, path.parent / "MASTER.SCP")
    log.info("settings imported from %s", old)
    return path
