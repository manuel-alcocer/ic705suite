"""Client side of the core: the same interface whether the core runs here or on a server.

One link serves the whole client session: the dashboard and every application opened from
it attach to it, in turn in the terminal (one slot), side by side in the graphical client
(a slot each: its tabs and windows are all open at once). A link remembers the welcome,
the latest state events and the text backlog, so an application opened later starts with
the full picture.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import Callable
from urllib.parse import urlparse

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidURI

from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.rig.transport import Transport, TransportError
from ic705suite.shared_logbook import client_url, use
from ic705suite.station.core import (
    AUDIO_RX,
    BACKLOG,
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    Core,
    PcmListener,
)
from ic705suite.ui.sysload import set_core

log = logging.getLogger(__name__)

EventCallback = Callable[[dict], None]
# Events of which only the latest matters; replayed to a newly attached client.
LATEST = ("rig", "state", "skim", "channels", "swr", "scope", "pkt", "ft8")
# Events kept in the backlog (text for the operator).
TEXT = ("rx", "tx", "info", "error", "history")
MAIN = "main"              # the receivers' slot, taken in turn (the terminal's screens)


class LinkError(Exception):
    pass


# The answer to a call when the connection to the server is lost.
LOST = {"error": "not connected to the server", "msgid": N_("not connected to the server"),
        "args": {}}


class Link(abc.ABC):
    """What a user interface talks to. Events arrive through the attached callback."""

    def __init__(self) -> None:
        self._listeners: dict[object, EventCallback] = {}   # slot -> receiver
        self.closed = asyncio.Event()  # set when the link ends (server gone, core stopped)
        self.connected = False
        self.description = ""
        self.welcome: dict | None = None
        self.latest: dict[str, dict] = {}
        self.backlog: deque[dict] = deque(maxlen=BACKLOG)
        self._connecting: asyncio.Task | None = None

    # Kept for clients written before attach(): assigning replaces the listener, no replay.
    @property
    def on_event(self) -> EventCallback:
        return self._dispatch

    @on_event.setter
    def on_event(self, callback: EventCallback) -> None:
        self._listeners[MAIN] = callback

    def attach(self, callback: EventCallback, replay: bool = True,
               slot: object = None) -> None:
        """Make `callback` the receiver of events in `slot` (the one other receivers take
        in turn, by default), replaying what is known so far."""
        self._listeners[MAIN if slot is None else slot] = callback
        if not replay:
            return
        if self.welcome is not None:
            callback(self.welcome)
        for event in list(self.backlog):
            callback(event)
        for kind in LATEST:
            if kind in self.latest:
                callback(self.latest[kind])

    def detach(self, slot: object = None) -> None:
        self._listeners.pop(MAIN if slot is None else slot, None)

    def _dispatch(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "welcome":
            self.welcome = event
        elif kind in LATEST:
            self.latest[kind] = event
        elif kind in TEXT:
            if kind == "rx" and self.backlog and self.backlog[-1].get("type") == "rx":
                self.backlog[-1] = {"type": "rx", "text": self.backlog[-1]["text"] + event["text"]}
            else:
                self.backlog.append(event)
        for listener in list(self._listeners.values()):
            listener(event)

    async def connect(self) -> None:
        """Connect once; later calls (another application opening) wait for the same
        connection instead of opening a new one."""
        if self.connected:
            return
        if self._connecting is None or (self._connecting.done() and not self.connected):
            self._connecting = asyncio.ensure_future(self._connect())
        await asyncio.shield(self._connecting)
        self.connected = True

    @abc.abstractmethod
    async def _connect(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    async def command(self, line: str, app: str = "cw") -> None: ...

    @abc.abstractmethod
    async def call(self, method: str, **params) -> dict:
        """Ask the core something and wait for the answer (channels, save_channel...)."""

    async def listen_audio(self, callback: PcmListener | None) -> int:
        """Hand the radio's receive audio (16-bit mono PCM) to `callback`, or stop with None.
        Returns its sample rate, 0 when no audio reaches the core."""
        return 0


class LocalLink(Link):
    """Runs the core in this process (the default, single-machine setup)."""

    def __init__(self, transport: Transport | None, config: Config) -> None:
        super().__init__()
        self.core = Core(transport, config)
        self.description = f"local ({self.core.station.transport.name})"
        self._unsubscribe: Callable[[], None] | None = None
        self._stop_audio: Callable[[], None] | None = None

    async def _connect(self) -> None:
        # Subscribe first so connection progress messages reach the UI.
        self._dispatch(self.core.welcome())
        self._unsubscribe = self.core.hub.subscribe(self._dispatch)
        try:
            # Until the radio appears: the applications that do not need it work meanwhile.
            await self.core.start(retry_forever=True)
        except BaseException:
            self._unsubscribe()
            await self.core.stop()
            raise
        self._dispatch(self.core.welcome())  # now with the radio's own name

    async def close(self) -> None:
        if self._connecting is not None and not self._connecting.done():
            self._connecting.cancel()       # still looking for the radio
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._connecting
        if self._unsubscribe:
            self._unsubscribe()
        await self.listen_audio(None)
        await self.core.stop()
        self.connected = False
        self.closed.set()

    async def command(self, line: str, app: str = "cw") -> None:
        await self.core.command(line, app)

    async def call(self, method: str, **params) -> dict:
        return await self.core.call(method, params)

    async def listen_audio(self, callback: PcmListener | None) -> int:
        if self._stop_audio is not None:
            self._stop_audio()
            self._stop_audio = None
        if callback is not None and self.core.audio_rate:
            self._stop_audio = self.core.listen_audio(callback)
        return self.core.audio_rate


def parse_address(address: str) -> str:
    """'rpi', 'rpi:7373' or 'ws://rpi:7373' -> WebSocket URI."""
    if "://" in address:
        return address
    if address.count(":") == 1:
        return f"ws://{address}"
    return f"ws://{address}:{DEFAULT_PORT}"


class RemoteLink(Link):
    """Talks to an `ic705suite --serve` instance over WebSocket.

    When the connection is lost (the core restarted, the network down) it says so, shows the
    radio as disconnected, answers calls with an error at once, and connects again by itself
    when the core is back: then a "reconnected" event lets a screen start over."""

    RETRY = 5.0                    # seconds between attempts to connect again

    def __init__(self, address: str, token: str, client_name: str = "ic705suite") -> None:
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
        self._again: asyncio.Task | None = None
        self._audio: PcmListener | None = None

    async def _connect(self) -> None:
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
        if first.get("logbook"):
            # The core's logbook, at the address the core was reached at: every client the same.
            use(client_url(first["logbook"], urlparse(self.uri).hostname or "localhost"))
        self._dispatch(first)
        self._reader = asyncio.create_task(self._read())
        if self._audio is not None:      # listening before: on this connection too
            await self._ws.send(json.dumps({"type": "call", "id": 0, "method": "audio.listen",
                                            "params": {"on": True}}))

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    if raw[:1] == bytes([AUDIO_RX]) and self._audio is not None:
                        self._audio(raw[1:])
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("type") == "reply":
                    future = self._calls.pop(event.get("id"), None)
                    if future is not None and not future.done():
                        future.set_result(event.get("result") or {})
                    continue
                if event.get("type") == "rig":
                    set_core(event.get("host"))    # the core's machine, for the status lines
                self._dispatch(event)
        except ConnectionClosed:
            pass
        finally:
            self.connected = False
            for future in self._calls.values():
                if not future.done():
                    future.set_result(dict(LOST))
            self._calls.clear()
            if self._closing:
                self.closed.set()
            else:
                self._lost()

    def _lost(self) -> None:
        self._dispatch({"type": "error", "msgid": N_("connection to the server lost; "
                                                     "connecting again when it is back"),
                        "args": {}, "text": "connection to the server lost"})
        if "rig" in self.latest:          # every screen's status line: no radio now
            self._dispatch({**self.latest["rig"], "connected": False})
        if self._again is None or self._again.done():
            self._again = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        while not self._closing:
            await asyncio.sleep(self.RETRY)
            if self._closing:
                return
            try:
                await self._connect()
            except LinkError as exc:
                log.debug("reconnecting: %s", exc)
                continue
            self.connected = True
            self._dispatch({"type": "info", "msgid": N_("connected to the server again"),
                            "args": {}, "text": "connected to the server again"})
            self._dispatch({"type": "reconnected"})
            return

    async def close(self) -> None:
        self._closing = True
        if self._again is not None:
            self._again.cancel()
        if self._reader:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        self.connected = False
        self.closed.set()

    async def command(self, line: str, app: str = "cw") -> None:
        if self._ws is None:
            raise LinkError("not connected")
        try:
            await self._ws.send(json.dumps({"type": "command", "line": line, "app": app}))
        except ConnectionClosed:
            self._dispatch({"type": "error", "text": "not connected to the server"})

    async def call(self, method: str, **params) -> dict:
        if self._ws is None:
            raise LinkError("not connected")
        if not self.connected and self._again is not None:
            return dict(LOST)                  # lost: an error at once, not a hang
        self._call_id += 1
        call_id = self._call_id
        future = asyncio.get_running_loop().create_future()
        self._calls[call_id] = future
        try:
            await self._ws.send(json.dumps({"type": "call", "id": call_id,
                                            "method": method, "params": params}))
        except ConnectionClosed:
            self._calls.pop(call_id, None)
            return dict(LOST)
        try:
            return await asyncio.wait_for(future, 30)
        except TimeoutError:
            self._calls.pop(call_id, None)
            raise LinkError(f"no answer from the server to {method}") from None

    async def listen_audio(self, callback: PcmListener | None) -> int:
        self._audio = callback
        rate = (self.welcome or {}).get("audio_rate", 0)
        if self._ws is None or not self.connected:
            return rate                 # asked for again when it connects
        result = await self.call("audio.listen", on=callback is not None)
        return int(result.get("rate", rate) or 0)


__all__ = ["Link", "LinkError", "LocalLink", "RemoteLink", "TransportError", "parse_address"]
