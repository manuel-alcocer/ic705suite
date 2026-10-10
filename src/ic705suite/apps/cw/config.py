"""User configuration (~/.config/cwic705/config.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cwic705" / "config.toml"

DEFAULT_SCP_PATH = DEFAULT_PATH.parent / "MASTER.SCP"
SCP_URL = "https://www.supercheckpartial.com/MASTER.SCP"

DEFAULT_MACROS = {
    "F2": "CQ CQ CQ DE {call} {call} K",
    "F3": "{call}",
    "F4": "TU 5NN",
    "F5": "R R TU 73 DE {call} <SK>",
    "F6": "QRZ?",
    "F7": "AGN?",
}

TEMPLATE = """\
# cwic705 configuration

[radio]
# IP address of the IC-705 (MENU > SET > WLAN Set > Connection Status shows it)
host = "192.168.1.50"
# Network User1 ID / password (MENU > SET > WLAN Set > Remote Settings)
username = "user"
password = "password"
# control_port = 50001
# sample_rate = 8000          # receive audio: 8000, 16000, 24000 or 48000
# civ_address = 0xA4          # only needed if you changed it on the radio

[station]
callsign = "N0CALL"

[keyer]
wpm = 20
# Transmission is disabled unless enabled here or with --tx.
# Only enable it once the antenna SWR is correct for the band you use.
tx_enabled = false

[decoder]
afc = true
# "bayes": probabilistic decoder with a CW language model (default); "classic": thresholds
# engine = "bayes"

[skimmer]
# Super Check Partial database used to validate callsigns (cwic705 --update-scp)
# scp_path = "~/.config/cwic705/MASTER.SCP"

[channels]
# Archive of channels (conversations): any SQLAlchemy URL. Default: SQLite file.
# database = "sqlite:///~/.local/share/cwic705/channels.db"
# database = "postgresql+psycopg://user:password@host/cwic705"   (pip install cwic705[postgres])
# Keep the session audio so channels can be saved with lossless FLAC audio
audio = true
# --serve: archive unsaved channels automatically when the server stops
autosave_on_server_exit = true

[ui]
# Interface language: "en" or "es" (default: the system language; CWIC705_LANG overrides)
# language = "es"

[server]
# cwic705 --serve: address to listen on and the token clients must present
listen = "0.0.0.0:7373"
token = "{token}"

[client]
# cwic705 --connect with no address uses these (e.g. the Raspberry Pi running --serve)
# connect = "rpi.local:7373"
token = "{token}"

[macros]
# Function keys F2..F9. {call} is replaced by your callsign.
F2 = "CQ CQ CQ DE {call} {call} K"
F3 = "{call}"
F4 = "TU 5NN"
F5 = "R R TU 73 DE {call} <SK>"
F6 = "QRZ?"
F7 = "AGN?"
"""


@dataclass
class Config:
    host: str | None = None
    username: str = ""
    password: str = ""
    control_port: int = 50001
    sample_rate: int = 8000
    civ_address: int | None = None
    callsign: str = ""
    wpm: int = 20
    tx_enabled: bool = False
    afc: bool = True
    decoder: str = "bayes"   # "bayes" (probabilistic) or "classic"
    scp_path: Path = DEFAULT_SCP_PATH
    channels_db: str = ""            # SQLAlchemy URL; "" = SQLite in ~/.local/share/cwic705
    channel_audio: bool = True       # keep the session audio so channels can be saved with it
    autosave_on_exit: bool = True    # server mode: archive unsaved channels when stopping
    language: str = ""
    listen: str = "127.0.0.1:7373"
    server_token: str = ""
    connect: str = ""
    client_token: str = ""
    macros: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MACROS))


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_PATH
    cfg = Config()
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        radio = data.get("radio", {})
        cfg.host = radio.get("host", cfg.host)
        cfg.username = radio.get("username", cfg.username)
        cfg.password = radio.get("password", cfg.password)
        cfg.control_port = int(radio.get("control_port", cfg.control_port))
        cfg.sample_rate = int(radio.get("sample_rate", cfg.sample_rate))
        cfg.civ_address = radio.get("civ_address", cfg.civ_address)
        cfg.callsign = str(data.get("station", {}).get("callsign", cfg.callsign)).upper()
        cfg.wpm = int(data.get("keyer", {}).get("wpm", cfg.wpm))
        cfg.tx_enabled = bool(data.get("keyer", {}).get("tx_enabled", cfg.tx_enabled))
        cfg.afc = bool(data.get("decoder", {}).get("afc", cfg.afc))
        cfg.decoder = str(data.get("decoder", {}).get("engine", cfg.decoder))
        if "scp_path" in data.get("skimmer", {}):
            cfg.scp_path = Path(data["skimmer"]["scp_path"]).expanduser()
        cfg.language = str(data.get("ui", {}).get("language", cfg.language))
        chans = data.get("channels", {})
        cfg.channels_db = str(chans.get("database", cfg.channels_db))
        cfg.channel_audio = bool(chans.get("audio", cfg.channel_audio))
        cfg.autosave_on_exit = bool(chans.get("autosave_on_server_exit", cfg.autosave_on_exit))
        server, client = data.get("server", {}), data.get("client", {})
        cfg.listen = str(server.get("listen", cfg.listen))
        cfg.server_token = str(server.get("token", cfg.server_token))
        cfg.connect = str(client.get("connect", cfg.connect))
        cfg.client_token = str(client.get("token", cfg.client_token))
        cfg.macros.update({k.upper(): str(v) for k, v in data.get("macros", {}).items()})
    cfg.password = os.environ.get("CWIC705_PASSWORD", cfg.password)
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
