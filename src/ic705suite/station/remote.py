"""Remote control from the phone: the core's WebSocket over HTTPS, with the page that uses it
(a web app the phone installs) on the same port. The phone listens to the radio and talks
through it with its own headphones and microphone, the PTT on its screen; the voice goes
as Opus both ways (ic705suite.opus), small enough for the mobile network through a VPN.

The browser only lends its microphone to a page over HTTPS with a certificate it trusts:
[remote] cert and key, e.g. Let's Encrypt's for a name the phone resolves to the core. A
renewed certificate (the files change) is taken without restarting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from importlib import resources
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from ic705suite.i18n import N_
from ic705suite.station.core import Core
from ic705suite.station.server import CoreServer

log = logging.getLogger(__name__)

DEFAULT_PORT = 7374
SOCKET_PATH = "/ws"
PAGES = {                        # what the page is made of: path -> (file, content type)
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


def page(name: str) -> bytes:
    return resources.files("ic705suite.station").joinpath("static", "remote", name).read_bytes()


class RemoteServer(CoreServer):
    def __init__(self, core: Core, host: str, port: int, token: str, cert: str,
                 key: str) -> None:
        if not token:
            raise ValueError("the remote control needs a token ([server] token in the config "
                             "file)")
        super().__init__(core, host, port, token)
        self.cert = str(Path(cert).expanduser())
        self.key = str(Path(key).expanduser()) if key else ""
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._loaded: tuple[float, float] | None = None
        self._load()

    def _stamps(self) -> tuple[float, float]:
        return (os.stat(self.cert).st_mtime, os.stat(self.key).st_mtime if self.key else 0.0)

    def _load(self) -> None:
        """The certificate (again, when its files changed: renewed)."""
        try:
            stamps = self._stamps()
            if stamps != self._loaded:
                self.context.load_cert_chain(self.cert, self.key or None)
                if self._loaded is not None:
                    log.info("remote control: certificate renewed, reloaded")
                self._loaded = stamps
        except (OSError, ssl.SSLError) as exc:
            if self._loaded is None:
                raise ValueError(f"the remote control's certificate: {exc}") from None
            log.error("remote control: the renewed certificate cannot be loaded: %s", exc)

    async def run(self, stop: asyncio.Event) -> None:
        async with serve(self._handle, self.host, self.port, ssl=self.context,
                         process_request=self._process_request, max_size=2**16) as server:
            ports = sorted({s.getsockname()[1] for s in server.sockets})
            log.info("remote control on https://%s:%s", self.host, ports)
            self.core.hub.info(N_("remote control for the phone on port {port} (HTTPS)"),
                               port=ports[0])
            await stop.wait()

    def _process_request(self, connection: ServerConnection,
                         request: Request) -> Response | None:
        """The page's files over plain HTTPS; the WebSocket goes on to the handshake."""
        self._load()
        path = request.path.split("?", 1)[0]
        if path == SOCKET_PATH:
            return None
        found = PAGES.get(path)
        if found is None:
            return connection.respond(404, "not found\n")
        name, content_type = found
        body = page(name)
        headers = Headers([("Content-Type", content_type), ("Content-Length", str(len(body))),
                           ("Cache-Control", "no-cache"),
                           ("X-Content-Type-Options", "nosniff"),
                           ("Referrer-Policy", "no-referrer")])
        return Response(200, "OK", headers, body)
