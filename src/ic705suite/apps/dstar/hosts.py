"""Where a reflector is: the Pi-Star host files (DCS, DExtra, DPlus) and the XLX directory,
fetched once a day and kept in the cache; REF reflectors also by their DNS names. Every
call here blocks (HTTP, DNS): the core runs them in threads.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

from ic705suite import paths
from ic705suite.apps.dstar.status import XLX_API_URL, http_get

HOST_FILES = {
    "DCS": "http://www.pistar.uk/downloads/DCS_Hosts.txt",
    "DExtra": "http://www.pistar.uk/downloads/DExtra_Hosts.txt",
    "DPlus": "http://www.pistar.uk/downloads/DPlus_Hosts.txt",
}
REFRESH = 24 * 3600         # s


def parse_host_file(text: str) -> dict[str, str]:
    table: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#"):
            table.setdefault(parts[0].upper(), parts[1])
    return table


class HostDirectory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.cache_dir() / "dstar_hosts.json"
        self.lock = threading.Lock()
        self.hosts: dict[str, dict[str, str]] = {"DCS": {}, "DExtra": {}, "DPlus": {},
                                                 "XLX": {}}
        self.fetched = 0.0
        try:
            cached = json.loads(self.path.read_text())
            self.hosts.update(cached.get("hosts", {}))
            self.fetched = cached.get("fetched", 0.0)
        except (OSError, ValueError):
            pass

    def refresh(self, force: bool = False) -> bool:
        if not force and time.time() - self.fetched < REFRESH:
            return True
        fresh = {}
        for protocol, url in HOST_FILES.items():
            try:
                fresh[protocol] = parse_host_file(http_get(url, timeout=20))
            except OSError:
                continue
        try:
            xml = http_get(XLX_API_URL, timeout=20)
            fresh["XLX"] = {name.upper(): ip for name, ip in re.findall(
                r"<name>(\w+)</name>\s*<lastip>([\d.]+)</lastip>", xml)}
        except OSError:
            pass
        if not fresh:
            return False
        with self.lock:
            self.hosts.update(fresh)
            self.fetched = time.time()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"fetched": self.fetched, "hosts": self.hosts}))
            except OSError:
                pass
        return True

    def lookup(self, name: str, protocol: str) -> str | None:
        """The address of 'DCS214' over 'DCS', etc., or None."""
        name = name.upper()
        with self.lock:
            address = self.hosts.get(protocol, {}).get(name)
            if not address and name.startswith(("XLX", "DCS")):
                address = self.hosts.get("XLX", {}).get("XLX" + name[3:])
            return address

    def resolve(self, name: str, protocol: str) -> str | None:
        """The IP address to link 'XLX214' over a protocol: the directories, then DNS."""
        self.refresh()
        address = self.lookup(name, protocol)
        if not address and protocol == "DPlus":
            address = f"{name.lower()}.dstargateway.org"
        if not address:
            return None
        try:
            return socket.gethostbyname(address)
        except OSError:
            return None
