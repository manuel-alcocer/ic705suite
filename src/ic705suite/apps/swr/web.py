"""Small web server for the phone on the rooftop: the antenna page and its JSON API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from ic705suite.apps.swr.service import SweepError, SwrService

log = logging.getLogger(__name__)


def _response(status: str, body: bytes, content_type: str) -> bytes:
    head = (f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n")
    return head.encode() + body


def _json(data, status: str = "200 OK") -> bytes:
    return _response(status, json.dumps(data).encode(), "application/json")


async def handle(service: SwrService, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter) -> None:
    try:
        request_line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1").split()
        if len(request_line) < 2:
            return
        method, target = request_line[0], request_line[1]
        headers = {}
        while (line := (await reader.readline()).decode("latin-1").strip()):
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        length = min(int(headers.get("content-length", 0)), 65536)
        body = await reader.readexactly(length) if length else b""
        writer.write(await route(service, method, target, body))
        await writer.drain()
    except (TimeoutError, ConnectionError, asyncio.IncompleteReadError, ValueError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def route(service: SwrService, method: str, target: str, body: bytes) -> bytes:
    url = urlsplit(target)
    path = url.path
    if path in ("/", "/index.html"):
        page = resources.files("ic705suite.apps.swr").joinpath("static", "index.html").read_bytes()
        return _response("200 OK", page, "text/html; charset=utf-8")
    if not path.startswith("/api/"):
        return _response("404 Not Found", b"not found", "text/plain")
    try:
        if path == "/api/state":
            return _json(service.state())
        if path == "/api/bands":
            return _json(service.bands())
        if path == "/api/history":
            band = parse_qs(url.query).get("band", [""])[0]
            return _json(service.history.entries(band, limit=20))
        if method != "POST":
            return _json({"error": "method not allowed"}, "405 Method Not Allowed")
        if path == "/api/sweep":
            service.start_sweep(json.loads(body or b"{}"))
            return _json(service.state())
        if path == "/api/final":
            return _json(service.save_final(json.loads(body or b"{}").get("coil", "")))
        if path in ("/api/stop", "/api/disconnect"):
            # The radio stays connected: other applications of the suite are using it.
            service.stop_sweep()
            return _json(service.state())
    except (SweepError, KeyError, ValueError) as exc:
        return _json({"error": str(exc)}, "400 Bad Request")
    return _json({"error": "not found"}, "404 Not Found")


def lan_address() -> str:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # no packet is sent: only picks the outgoing interface
        return s.getsockname()[0]
    return "127.0.0.1"


async def serve(service: SwrService, host: str, port: int) -> asyncio.Server:
    server = await asyncio.start_server(lambda r, w: handle(service, r, w), host, port)
    log.info("antenna page on http://%s:%d", lan_address() if host in ("0.0.0.0", "") else host,
             port)
    return server
