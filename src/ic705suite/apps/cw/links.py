"""Client side of the core: the same interface whether the core runs here or on a server."""

from __future__ import annotations

import abc
import asyncio
import json
import logging
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidURI

from ic705suite.apps.cw.config import Config
from ic705suite.apps.cw.core import DEFAULT_PORT, PROTOCOL_VERSION, Core
from ic705suite.apps.cw.i18n import N_
from ic705suite.apps.cw.transport import Transport, TransportError

log = logging.getLogger(__name__)

EventCallback = Callable[[dict], None]


class LinkError(Exception):
    pass


class Link(abc.ABC):
    """What a user interface talks to. Events arrive through on_event."""

    def __init__(self) -> None:
        self.on_event: EventCallback = lambda event: None
        self.closed = asyncio.Event()  # set when the link ends (server gone, core stopped)
        self.description = ""

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    async def command(self, line: str) -> None: ...

    @abc.abstractmethod
    async def call(self, method: str, **params) -> dict:
        """Ask the core something and wait for the answer (channels, save_channel...)."""


class LocalLink(Link):
    """Runs the core in this process (the default, single-machine setup)."""

    def __init__(self, transport: Transport, config: Config) -> None:
        super().__init__()
        self.core = Core(transport, config)
        self.description = f"local ({transport.name})"
        self._unsubscribe: Callable[[], None] | None = None

    async def connect(self) -> None:
        # Subscribe first so connection progress messages reach the UI.
        self._unsubscribe = self.core.hub.subscribe(lambda e: self.on_event(e))
        try:
            await self.core.start()
        except BaseException:
            await self.core.stop()
            raise
        self._unsubscribe()
        self._unsubscribe = self.core.subscribe(lambda e: self.on_event(e), replay=False)

    async def close(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        await self.core.stop()
        self.closed.set()

    async def command(self, line: str) -> None:
        await self.core.command(line)

    async def call(self, method: str, **params) -> dict:
        return await self.core.call(method, params)


def parse_address(address: str) -> str:
    """'rpi', 'rpi:7373' or 'ws://rpi:7373' -> WebSocket URI."""
    if "://" in address:
        return address
    if address.count(":") == 1:
        return f"ws://{address}"
    return f"ws://{address}:{DEFAULT_PORT}"


class RemoteLink(Link):
    """Talks to a `cwic705 serve` instance over WebSocket."""

    def __init__(self, address: str, token: str, client_name: str = "cwic705") -> None:
        super().__init__()
        self.uri = parse_address(address)
        self.token = token
        self.client_name = client_name
        self.description = self.uri
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._closing = False
        self._calls: dict[int, asyncio.Future] = {}
        self._call_id = 0

    async def connect(self) -> None:
        try:
            self._ws = await connect(self.uri, open_timeout=10, max_size=2**20)
        except (OSError, InvalidURI, TimeoutError) as exc:
            raise LinkError(f"cannot connect to {self.uri}: {exc}") from None
        await self._ws.send(json.dumps({"type": "hello", "token": self.token,
                                        "version": PROTOCOL_VERSION, "client": self.client_name}))
        try:
            first = json.loads(await asyncio.wait_for(self._ws.recv(), 10))
        except (TimeoutError, ConnectionClosed, ValueError):
            raise LinkError(f"no answer from {self.uri}") from None
        if first.get("type") != "welcome":
            await self._ws.close()
            raise LinkError(first.get("text", "connection refused by the server"))
        self.on_event(first)
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("type") == "reply":
                    future = self._calls.pop(event.get("id"), None)
                    if future is not None and not future.done():
                        future.set_result(event.get("result") or {})
                    continue
                self.on_event(event)
        except ConnectionClosed:
            pass
        finally:
            if not self._closing:
                self.on_event({"type": "error", "msgid": N_("connection to the server lost"),
                               "args": {}, "text": "connection to the server lost"})
            self.closed.set()

    async def close(self) -> None:
        self._closing = True
        if self._reader:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        self.closed.set()

    async def command(self, line: str) -> None:
        if self._ws is None:
            raise LinkError("not connected")
        try:
            await self._ws.send(json.dumps({"type": "command", "line": line}))
        except ConnectionClosed:
            self.on_event({"type": "error", "text": "not connected to the server"})

    async def call(self, method: str, **params) -> dict:
        if self._ws is None:
            raise LinkError("not connected")
        self._call_id += 1
        future = asyncio.get_running_loop().create_future()
        self._calls[self._call_id] = future
        await self._ws.send(json.dumps({"type": "call", "id": self._call_id,
                                        "method": method, "params": params}))
        try:
            return await asyncio.wait_for(future, 30)
        except TimeoutError:
            self._calls.pop(self._call_id, None)
            raise LinkError(f"no answer from the server to {method}") from None


__all__ = ["Link", "LinkError", "LocalLink", "RemoteLink", "TransportError", "parse_address"]
