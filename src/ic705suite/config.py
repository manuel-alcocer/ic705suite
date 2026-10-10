"""Suite configuration (~/.config/ic705suite/config.toml).

One file for the station core and every application that runs on it. hamrlog and hamrpkt
keep their own settings (F9 inside each of them).
"""

from __future__ import annotations

import logging
import os
import shutil
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
# How to reach it: "lan" (WiFi/Ethernet), "usb" (next version) or "sim" (built-in simulator)
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

[database]
# Any SQLAlchemy URL. PostgreSQL is recommended (pip install ic705suite[postgres]):
# url = "postgresql+psycopg://user:password@host/ic705suite"
# Default: a SQLite file in ~/.local/share/ic705suite/ic705suite.db

[keyer]
wpm = 20

[decoder]
afc = true
# "bayes": probabilistic decoder with a CW language model (default); "classic": thresholds
# engine = "bayes"

[skimmer]
# Super Check Partial database used to validate callsigns (ic705suite --update-scp)
# scp_path = "~/.config/ic705suite/MASTER.SCP"

[channels]
# Keep the session audio so CW channels can be saved with lossless FLAC audio
audio = true
# --serve: archive unsaved channels automatically when the server stops
autosave_on_server_exit = true

[swr]
# Antenna tuning page for the phone, served by the core: http://<core address>:8705
web = true
port = 8705
# Highest RF power for SWR sweeps, percent (30 % = 3 W on the IC-705). SWR sweeps transmit,
# so they also need [rig] tx_enabled = true (or --tx); noise sweeps never transmit.
max_power = 30

[ui]
# Interface language: "en" or "es" (default: the system language; IC705SUITE_LANG overrides)
# language = "es"
# Characters for the screen: "auto" (plain ones on the Linux console, TERM=linux), "unicode",
# or "console" (block font for the frequency, shaded 16-colour waterfall, square borders)
# glyphs = "auto"

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
    # [database]
    database_url: str = ""           # SQLAlchemy URL; "" = SQLite in ~/.local/share/ic705suite
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
    swr_port: int = 8705
    swr_max_power: int = 30
    # [ui] [server] [client]
    language: str = ""
    glyphs: str = "auto"
    listen: str = "127.0.0.1:7373"
    server_token: str = ""
    connect: str = ""
    client_token: str = ""

    @property
    def channels_url(self) -> str:
        return self.channels_db or self.database_url


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    cfg = Config()
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
        cfg.database_url = str(data.get("database", {}).get("url", cfg.database_url))
        cfg.wpm = int(keyer.get("wpm", cfg.wpm))
        cfg.afc = bool(data.get("decoder", {}).get("afc", cfg.afc))
        cfg.decoder = str(data.get("decoder", {}).get("engine", cfg.decoder))
        if "scp_path" in data.get("skimmer", {}):
            cfg.scp_path = Path(data["skimmer"]["scp_path"]).expanduser()
        cfg.language = str(data.get("ui", {}).get("language", cfg.language))
        cfg.glyphs = str(data.get("ui", {}).get("glyphs", cfg.glyphs))
        chans = data.get("channels", {})
        cfg.channels_db = str(chans.get("database", cfg.channels_db))
        cfg.channel_audio = bool(chans.get("audio", cfg.channel_audio))
        cfg.autosave_on_exit = bool(chans.get("autosave_on_server_exit", cfg.autosave_on_exit))
        swr = data.get("swr", {})
        cfg.swr_web = bool(swr.get("web", cfg.swr_web))
        cfg.swr_port = int(swr.get("port", cfg.swr_port))
        cfg.swr_max_power = int(swr.get("max_power", cfg.swr_max_power))
        server, client = data.get("server", {}), data.get("client", {})
        cfg.listen = str(server.get("listen", cfg.listen))
        cfg.server_token = str(server.get("token", cfg.server_token))
        cfg.connect = str(client.get("connect", cfg.connect))
        cfg.client_token = str(client.get("token", cfg.client_token))
        cfg.macros.update({k.upper(): str(v) for k, v in data.get("macros", {}).items()})
    cfg.password = os.environ.get("IC705SUITE_PASSWORD", cfg.password)
    return cfg


def write_template(path: Path | None = None) -> Path:
    path = path or DEFAULT_PATH
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import secrets

    # Literal braces of the macros ({call}) survive: only {token} is replaced.
    path.write_text(TEMPLATE.replace("{token}", secrets.token_urlsafe(18)))
    path.chmod(0o600)  # contains the radio password
    return path


def migrate_legacy(path: Path | None = None, legacy_dir: Path = LEGACY_DIR) -> Path | None:
    """First run after cwic705: copy its settings and MASTER.SCP. Returns the new file, or
    None when there was nothing to migrate. The old files are left untouched."""
    path = path or DEFAULT_PATH
    old = legacy_dir / "config.toml"
    if path.exists() or not old.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    text = old.read_text()
    text = text.replace("[radio]", '[rig]\nmodel = "ic705"\nconnection = "lan"', 1)
    text = text.replace("~/.config/cwic705/", "~/.config/ic705suite/")
    path.write_text("# Imported from ~/.config/cwic705/config.toml by ic705suite\n" + text)
    path.chmod(0o600)
    scp = legacy_dir / "MASTER.SCP"
    if scp.exists() and not (path.parent / "MASTER.SCP").exists():
        shutil.copy2(scp, path.parent / "MASTER.SCP")
    log.info("settings imported from %s", old)
    return path
