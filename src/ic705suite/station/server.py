"""WebSocket server exposing a Core to remote clients (Textual, web, Qt...)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from ic705suite.i18n import N_
from ic705suite.station.core import AUDIO_RX, PROTOCOL_VERSION, Core

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 10.0
CLIENT_QUEUE = 1000  # events buffered for a slow client before it is dropped
# Audio frames are skipped, not queued, past this many messages waiting for a client: late
# audio is of no use, and it must not get a slow client dropped.
AUDIO_QUEUE = 50
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")


class CoreServer:
    def __init__(self, core: Core, host: str, port: int, token: str) -> None:
        if not token and host not in LOCAL_HOSTS:
            raise ValueError("a server reachable from the network needs a token "
                             "([server] token in the config file)")
        self.core = core
        self.host = host
        self.port = port
        self.token = token
        self.clients = 0

    async def run(self, stop: asyncio.Event) -> None:
        async with serve(self._handle, self.host, self.port, max_size=2**16) as server:
            ports = sorted({s.getsockname()[1] for s in server.sockets})
            log.info("listening on %s:%s", self.host, ports)
            self.core.hub.info(N_("server listening on {host}:{port}"), host=self.host,
                               port=ports[0])
            await stop.wait()

    async def _handle(self, ws: ServerConnection) -> None:
        peer = ws.remote_address[0] if ws.remote_address else "?"
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT))
        except (TimeoutError, ConnectionClosed, ValueError):
            return
        if hello.get("type") != "hello" or \
                not hmac.compare_digest(str(hello.get("token", "")), self.token):
            log.warning("rejected client %s: bad hello or token", peer)
            await ws.send(json.dumps({"type": "error", "fatal": True,
                                      "text": "authentication failed (wrong token)"}))
            return
        if hello.get("version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
            await ws.send(json.dumps({"type": "error", "fatal": True,
                                      "text": f"protocol version {PROTOCOL_VERSION} required"}))
            return

        queue: asyncio.Queue[str | bytes] = asyncio.Queue(CLIENT_QUEUE)
        dropped = asyncio.Event()

        def push(event: dict) -> None:
            try:
                queue.put_nowait(json.dumps(event))
            except asyncio.QueueFull:
                dropped.set()

        def push_audio(pcm: bytes) -> None:
            if queue.qsize() < AUDIO_QUEUE:
                queue.put_nowait(bytes([AUDIO_RX]) + pcm)

        stop_audio = None
        unsubscribe = self.core.subscribe(push)
        self.clients += 1
        log.info("client %s connected (%s)", peer, hello.get("client", "?"))
        sender = asyncio.create_task(self._send_loop(ws, queue, dropped))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "command" and isinstance(msg.get("line"), str):
                    app = msg.get("app") if msg.get("app") in ("cw", "rig") else "cw"
                    log.info("client %s (%s): %s", peer, app, msg["line"])
                    await self.core.command(msg["line"], app)
                elif msg.get("type") == "call" and msg.get("method") == "audio.listen":
                    # The client's own: the audio goes to this connection.
                    on = bool((msg.get("params") or {}).get("on"))
                    if on and stop_audio is None and self.core.audio_rate:
                        stop_audio = self.core.listen_audio(push_audio)
                        log.info("client %s listens to the receive audio", peer)
                    elif not on and stop_audio is not None:
                        stop_audio()
                        stop_audio = None
                    push({"type": "reply", "id": msg.get("id"),
                          "result": {"on": stop_audio is not None,
                                     "rate": self.core.audio_rate}})
                elif msg.get("type") == "call" and isinstance(msg.get("method"), str):
                    result = await self.core.call(msg["method"], msg.get("params") or {})
                    push({"type": "reply", "id": msg.get("id"), "result": result})
        except ConnectionClosed:
            pass
        finally:
            if stop_audio is not None:
                stop_audio()
            unsubscribe()
            sender.cancel()
            self.clients -= 1
            log.info("client %s disconnected", peer)

    @staticmethod
    async def _send_loop(ws: ServerConnection, queue: asyncio.Queue[str | bytes],
                         dropped: asyncio.Event) -> None:
        try:
            while not dropped.is_set():
                await ws.send(await queue.get())
            await ws.close(1011, "client too slow")
        except ConnectionClosed:
            pass
