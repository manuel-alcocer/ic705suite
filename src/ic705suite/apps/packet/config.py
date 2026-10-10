"""Settings: who we are and where the node lives.

Stored as a small TOML file, readable and editable by hand. The F9 dialog
writes the same file, so both ways of changing it stay in step.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "hamrpkt"

#: Usual TCP ports of each link type in LinBPQ (AGWPORT and TCPPORT).
DEFAULT_TCP_PORTS: dict[str, int] = {"agw": 8000, "telnet": 8010}

LINK_TYPES = tuple(DEFAULT_TCP_PORTS)


def config_dir() -> Path:
    override = os.environ.get("HAMRPKT_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def data_dir() -> Path:
    override = os.environ.get("HAMRPKT_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def default_config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass(slots=True)
class LinkConfig:
    """How to reach the node.

    Attributes:
        type: "agw" for the AGWPE interface (connected sessions, monitor and
            heard list) or "telnet" for the node console.
        host: Name or address of the machine running the node.
        port: TCP port of that interface.
        user: Login, for Telnet always and for AGWPE when the node requires it.
        password: Password matching ``user``.
        radio_port: Radio port to work on, numbered from 1 as the node does.
        monitor: Whether to ask for monitored frames as soon as the link is up.
    """

    type: str = "agw"
    host: str = "wpsd.alcocer.net"
    port: int = DEFAULT_TCP_PORTS["agw"]
    user: str = ""
    password: str = ""
    radio_port: int = 1
    monitor: bool = True


@dataclass(slots=True)
class BpqConfig:
    """LinBPQ's Telnet console, used for ``/c bpq:CALL``.

    The radio link goes to Direwolf, which cannot reach the node sharing it:
    the node's own stations (BBS, chat…) are reached through its console.

    Attributes:
        port: Telnet port of the node (TCPPORT), on the same host as the link.
        user: A USER= of the node's Telnet port.
        password: Its password.
        apps: Node command that enters each of its own callsigns, e.g.
            ``{"EA7KLX-1": "BBS"}``; an empty command means the node itself.
            A callsign missing here is reached with ``C CALL``.
    """

    port: int = DEFAULT_TCP_PORTS["telnet"]
    user: str = ""
    password: str = ""
    apps: dict[str, str] = field(default_factory=dict)

    def command_for(self, call: str) -> str:
        return self.apps.get(call.upper(), f"C {call.upper()}")


@dataclass(slots=True)
class Config:
    mycall: str = ""
    link: LinkConfig = field(default_factory=LinkConfig)
    bpq: BpqConfig = field(default_factory=BpqConfig)

    @property
    def is_complete(self) -> bool:
        return bool(self.mycall and self.link.host)


def load(path: Path | None = None) -> Config:
    """Read the settings, falling back to defaults for anything missing."""
    path = path or default_config_path()
    config = Config()
    if not path.exists():
        return config
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    config.mycall = str(raw.get("station", {}).get("mycall", "")).upper()
    link = raw.get("link", {})
    defaults = asdict(LinkConfig())
    for key, default in defaults.items():
        if key in link:
            setattr(config.link, key, type(default)(link[key]))
    bpq = raw.get("bpq", {})
    config.bpq.port = int(bpq.get("port", config.bpq.port))
    config.bpq.user = str(bpq.get("user", ""))
    config.bpq.password = str(bpq.get("password", ""))
    config.bpq.apps = {str(k).upper(): str(v) for k, v in bpq.get("apps", {}).items()}
    return config


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def save(config: Config, path: Path | None = None) -> Path:
    """Write the settings. The file may hold a password, so only we can read it."""
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[station]", f"mycall = {_toml_value(config.mycall)}", "", "[link]"]
    lines += [f"{key} = {_toml_value(value)}" for key, value in asdict(config.link).items()]
    bpq = config.bpq
    lines += ["", "[bpq]", f"port = {bpq.port}", f"user = {_toml_value(bpq.user)}",
              f"password = {_toml_value(bpq.password)}", "", "[bpq.apps]"]
    lines += [f"{_toml_value(call)} = {_toml_value(cmd)}" for call, cmd in bpq.apps.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
