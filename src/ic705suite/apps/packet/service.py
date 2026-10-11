"""Packet radio in the core: one TNC (Direwolf, over AGWPE) and the channels of the client.

The core keeps the connection with the TNC and the AX.25 sessions, so they go on while no
client is open, and any client (terminal or graphical, here or on another machine) sees the
same channels. As in BayCom, channel 0 is the monitor and channels 1 to 8 hold one session
each: a call we make takes the channel it is made from, a station calling us takes the first
free one.

Events (server -> client):
  pkt       {tnc: {...}, mycall, channels: [...], heard: [...], frames}  when it changes
  pkt.text  {channel, kind, text}  text of a channel as it arrives: kind is "rx" (received),
            "tx" (sent), "sys" (what happened: connected, disconnected...), "mon" (a frame
            heard) or "own" (a frame we sent), the last two on channel 0
Calls ("pkt.*"): state, history {channel}, connect {channel, call, via}, disconnect
  {channel}, send {channel, text}, unproto {dest, text, via}, read {channel}, clear {channel},
  retry
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from collections import deque
from dataclasses import dataclass, field

from ic705suite.apps.packet.agwpe import (
    CALLSIGN,
    MONITOR_KINDS,
    PID_TEXT,
    AgwClient,
    Frame,
    decode_text,
    digis,
    parse_monitor,
    parse_ports,
)
from ic705suite.config import Config
from ic705suite.i18n import N_

log = logging.getLogger(__name__)

CHANNELS = 8               # sessions at once, as BayCom's default
KEEP = 2000                # pieces of text kept per channel for clients that open later
HISTORY = 600              # of them sent to a client opening a channel
HEARD = 200                # stations kept in the heard list
RETRY_DELAY = 10.0         # between attempts to reach the TNC


class PacketError(Exception):
    """A call that cannot be done; the message is an English template (msgid)."""

    def __init__(self, msgid: str, **args: object) -> None:
        super().__init__(msgid.format(**args) if args else msgid)
        self.msgid = msgid
        self.args_ = args


@dataclass
class Channel:
    number: int
    call: str = ""
    via: list[str] = field(default_factory=list)
    state: str = "free"         # free, calling, connected, leaving
    incoming: bool = False
    since: float = 0.0
    unread: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    text: deque = field(default_factory=lambda: deque(maxlen=KEEP))

    def summary(self) -> dict:
        return {"number": self.number, "call": self.call, "via": self.via, "state": self.state,
                "incoming": self.incoming, "since": self.since, "unread": self.unread,
                "rx": self.rx_bytes, "tx": self.tx_bytes}

    def reset(self) -> None:
        self.call, self.via, self.state, self.incoming, self.since = "", [], "free", False, 0.0
        self.rx_bytes = self.tx_bytes = 0


@dataclass
class HeardStation:
    call: str
    last: float
    frames: int = 0
    port: int = 1
    dest: str = ""
    via: str = ""


def parse_via(value: object) -> list[str]:
    """"EA7URS-1,EA7RCM" or a list -> the digipeaters, upper case."""
    items = value if isinstance(value, list) else str(value or "").replace(",", " ").split()
    return [str(item).strip().upper() for item in items if str(item).strip()]


class PacketService:
    def __init__(self, config: Config, hub) -> None:
        self.config = config
        self.hub = hub
        self.channels = [Channel(n) for n in range(CHANNELS + 1)]   # 0: the monitor
        self.heard: dict[str, HeardStation] = {}
        self.frames = 0
        self.tnc_state = "off"          # off (not set up), connecting, up, down
        self.tnc_error = ""
        self.tnc_version = ""
        self.radio_ports: list[str] = []
        self.registered = False
        self._client: AgwClient | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._changed = True

    # ---- settings ------------------------------------------------------------------------

    @property
    def mycall(self) -> str:
        return (self.config.packet_mycall or self.config.callsign).upper()

    @property
    def radio_port(self) -> int:
        return max(1, self.config.packet_radio_port)

    @property
    def configured(self) -> bool:
        return bool(self.config.packet_host and self.mycall)

    # ---- life ----------------------------------------------------------------------------

    async def start(self) -> None:
        if self.configured and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._client is not None:
            await self._client.close()

    def changed(self) -> bool:
        """Whether the state moved since the last call (the core then sends it)."""
        changed, self._changed = self._changed, False
        return changed

    async def _run(self) -> None:
        host, port = self.config.packet_host, self.config.packet_tcp_port
        while True:
            self.tnc_state, self.registered = "connecting", False
            self._touch()
            client = AgwClient(host, port, self._on_frame)
            reason = ""
            try:
                await client.open()
                self._client = client
                self.tnc_state, self.tnc_error = "up", ""
                self._touch()
                self._system(0, N_("TNC connected: {host}:{port}"), host=host, port=port)
                client.send(Frame(0, "R"))
                client.send(Frame(0, "G"))
                client.send(Frame(0, "X", call_from=self.mycall))
                client.send(Frame(0, "m"))          # the monitor, always on in the core
                await client.pump()
            except asyncio.CancelledError:
                await client.close()
                raise
            except (OSError, TimeoutError, asyncio.IncompleteReadError, ConnectionError) as exc:
                reason = str(exc) or type(exc).__name__
            finally:
                self._client = None
            await client.close()
            self.tnc_state, self.tnc_error = "down", reason or "connection closed"
            for channel in self.channels[1:]:
                if channel.state != "free":
                    self._system(channel.number, N_("Link to the TNC lost: {call} is no longer "
                                                    "connected"), call=channel.call)
                    channel.reset()
            self._system(0, N_("Cannot reach the TNC at {host}:{port}: {error}; trying again "
                               "in {seconds} s"), host=host, port=port, error=self.tnc_error,
                         seconds=int(RETRY_DELAY))
            self._touch()
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), RETRY_DELAY)

    # ---- from the TNC --------------------------------------------------------------------

    def _on_frame(self, frame: Frame) -> None:
        kind = frame.kind
        if kind == "D":
            channel = self._channel_of(frame.call_from)
            if channel is not None:
                channel.rx_bytes += len(frame.data)
                self._text(channel, "rx", decode_text(frame.data))
        elif kind == "C":
            self._connected(frame)
        elif kind == "d":
            channel = self._channel_of(frame.call_from)
            if channel is not None:
                self._system(channel.number, N_("Disconnected from {call}"), call=channel.call)
                channel.reset()
                self._touch()
        elif kind in MONITOR_KINDS:
            self._monitored(frame)
        elif kind == "X":
            self.registered = bool(frame.data) and frame.data[0] == 1
            if not self.registered:
                self._system(0, N_("The TNC refused the callsign {call}"), call=self.mycall)
            self._touch()
        elif kind == "G":
            self.radio_ports = parse_ports(decode_text(frame.data))
            self._touch()
        elif kind == "R" and len(frame.data) >= 8:
            major, minor = struct.unpack("<II", frame.data[:8])
            self.tnc_version = f"{major}.{minor}"
            self._touch()

    def _connected(self, frame: Frame) -> None:
        call = frame.call_from
        channel = self._channel_of(call)
        incoming = channel is None
        if incoming:
            channel = next((c for c in self.channels[1:] if c.state == "free"), None)
            if channel is None:
                # Every channel busy: say so and hang up.
                self._send(Frame(frame.port, "D", self.mycall, call,
                                 b"*** All channels are busy, try later\r", PID_TEXT))
                self._send(Frame(frame.port, "d", self.mycall, call))
                self._system(0, N_("{call} called but every channel is busy"), call=call)
                return
            channel.call, channel.incoming = call, True
        channel.state, channel.since = "connected", time.time()
        if incoming:
            self._system(channel.number, N_("{call} connected to us"), call=call)
        else:
            self._system(channel.number, N_("Connected to {call}"), call=call)
        self._touch()

    def _monitored(self, frame: Frame) -> None:
        self.frames += 1
        text = decode_text(frame.data).replace("\r\n", "\r")
        own = frame.kind in "Tt"
        header, _sep, body = text.partition("\r")
        if not header.strip():
            return
        lines = [header]
        if body.strip("\r\n\0 "):
            if readable(body):
                lines += [line for line in body.split("\r") if line.strip()]
            else:
                # XID parameters, compressed or binary transfers: not text to show.
                lines.append(f"[{len(body.rstrip(chr(13)))} bytes of data]")
        self._text(self.channels[0], "own" if own else "mon", "\n".join(lines) + "\n",
                   unread=False)
        heard = parse_monitor(lines[0])
        if heard is not None and not own:
            station = self.heard.get(heard.src)
            if station is None:
                station = self.heard[heard.src] = HeardStation(heard.src, time.time())
                if len(self.heard) > HEARD:
                    del self.heard[min(self.heard.values(), key=lambda s: s.last).call]
            station.last, station.frames = time.time(), station.frames + 1
            station.port, station.dest, station.via = heard.port, heard.dest, heard.via
        self._touch()

    # ---- what clients ask ----------------------------------------------------------------

    def connect(self, number: int, call: str, via: list[str]) -> Channel:
        channel = self._session_channel(number)
        call = call.strip().upper()
        if not CALLSIGN.match(call):
            raise PacketError(N_("{call} is not a callsign"), call=call or "?")
        bad = next((digi for digi in via if not CALLSIGN.match(digi)), None)
        if bad:
            raise PacketError(N_("{call} is not a callsign"), call=bad)
        if len(via) > 7:
            raise PacketError(N_("At most 7 digipeaters"))
        if channel.state != "free":
            raise PacketError(N_("Channel {channel} is busy with {call}"),
                              channel=channel.number, call=channel.call)
        if self._channel_of(call) is not None:
            raise PacketError(N_("Already connected to {call}"), call=call)
        port = self.radio_port - 1
        if via:
            self._send(Frame(port, "v", self.mycall, call, digis(via), PID_TEXT))
        else:
            self._send(Frame(port, "C", self.mycall, call, pid=PID_TEXT))
        channel.call, channel.via, channel.state = call, via, "calling"
        channel.incoming, channel.since = False, time.time()
        target = call + (" via " + ",".join(via) if via else "")
        self._system(number, N_("Calling {call}…"), call=target)
        self._touch()
        return channel

    def disconnect(self, number: int) -> Channel:
        channel = self._session_channel(number)
        if channel.state == "free":
            raise PacketError(N_("Nothing to disconnect on channel {channel}"), channel=number)
        self._send(Frame(self.radio_port - 1, "d", self.mycall, channel.call))
        if channel.state == "calling":
            self._system(number, N_("Call to {call} cancelled"), call=channel.call)
        channel.state = "leaving"
        self._touch()
        return channel

    def send(self, number: int, text: str) -> Channel:
        channel = self._session_channel(number)
        if channel.state != "connected":
            raise PacketError(N_("Channel {channel} is not connected: /c CALL first"),
                              channel=number)
        data = text.replace("\r\n", "\n").replace("\n", "\r")
        if not data.endswith("\r"):
            data += "\r"
        payload = data.encode("utf-8", "replace")
        self._send(Frame(self.radio_port - 1, "D", self.mycall, channel.call, payload,
                         PID_TEXT))
        channel.tx_bytes += len(payload)
        self._text(channel, "tx", data.replace("\r", "\n"), unread=False)
        self._touch()
        return channel

    def unproto(self, dest: str, text: str, via: list[str]) -> None:
        dest = dest.strip().upper()
        if not CALLSIGN.match(dest):
            raise PacketError(N_("{call} is not a callsign"), call=dest or "?")
        payload = (text.rstrip("\r\n") + "\r").encode("utf-8", "replace")
        port = self.radio_port - 1
        if via:
            self._send(Frame(port, "V", self.mycall, dest, digis(via) + payload, PID_TEXT))
        else:
            self._send(Frame(port, "M", self.mycall, dest, payload, PID_TEXT))

    def state(self) -> dict:
        return {
            "tnc": {"state": self.tnc_state, "error": self.tnc_error,
                    "host": self.config.packet_host, "port": self.config.packet_tcp_port,
                    "version": self.tnc_version, "ports": self.radio_ports,
                    "registered": self.registered,
                    "rx": self._client.rx_bytes if self._client else 0,
                    "tx": self._client.tx_bytes if self._client else 0},
            "mycall": self.mycall,
            "radio_port": self.radio_port,
            "channels": [c.summary() for c in self.channels],
            "heard": [{"call": s.call, "last": s.last, "frames": s.frames, "port": s.port,
                       "dest": s.dest, "via": s.via}
                      for s in sorted(self.heard.values(), key=lambda s: -s.last)],
            "frames": self.frames,
        }

    async def call(self, method: str, params: dict) -> dict:
        """The "pkt.*" calls of the core protocol."""
        try:
            if method == "state":
                return self.state()
            if method == "history":
                channel = self._channel(int(params.get("channel", 0)))
                return {"channel": channel.number, "text": list(channel.text)[-HISTORY:]}
            if method == "connect":
                self.connect(int(params.get("channel", 1)), str(params.get("call", "")),
                             parse_via(params.get("via")))
            elif method == "disconnect":
                self.disconnect(int(params.get("channel", 1)))
            elif method == "send":
                self.send(int(params.get("channel", 1)), str(params.get("text", "")))
            elif method == "unproto":
                self.unproto(str(params.get("dest", "")), str(params.get("text", "")),
                             parse_via(params.get("via")))
            elif method == "read":
                self._channel(int(params.get("channel", 0))).unread = 0
                self._touch()
            elif method == "clear":
                channel = self._channel(int(params.get("channel", 0)))
                channel.text.clear()
                channel.unread = 0
                if channel.number == 0:
                    self.heard.clear()
                self._touch()
            elif method == "retry":
                if not self.configured:
                    raise PacketError(N_("Packet is not set up: [packet] host in the "
                                         "configuration (ic705suite --configure)"))
                if self._task is None:
                    await self.start()
                self._wake.set()
            else:
                return {"error": f"unknown method pkt.{method}"}
        except PacketError as exc:
            return {"error": str(exc), "msgid": exc.msgid, "args": _plain(exc.args_)}
        except (KeyError, ValueError) as exc:
            return {"error": f"bad request: {exc}"}
        return {"ok": True}

    # ---- helpers -------------------------------------------------------------------------

    def _send(self, frame: Frame) -> None:
        if self._client is None or not self._client.is_open:
            raise PacketError(N_("No TNC: {state}"), state=self.tnc_error or self.tnc_state)
        self._client.send(frame)

    def _channel(self, number: int) -> Channel:
        if not 0 <= number <= CHANNELS:
            raise PacketError(N_("There is no channel {channel}"), channel=number)
        return self.channels[number]

    def _session_channel(self, number: int) -> Channel:
        if not 1 <= number <= CHANNELS:
            raise PacketError(N_("Sessions use channels 1 to {last}"), last=CHANNELS)
        return self.channels[number]

    def _channel_of(self, call: str) -> Channel | None:
        return next((c for c in self.channels[1:] if c.state != "free" and c.call == call), None)

    def _text(self, channel: Channel, kind: str, text: str, unread: bool = True) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        channel.text.append({"kind": kind, "text": text})
        if unread:
            channel.unread += 1
            self._touch()
        self.hub.emit({"type": "pkt.text", "channel": channel.number, "kind": kind,
                       "text": text})

    def _system(self, number: int, msgid: str, **args: object) -> None:
        """A line saying what happened, on a channel (kept in English, translated by each
        client from its msgid)."""
        safe = _plain(args)
        text = msgid.format(**safe) if safe else msgid
        piece = {"kind": "sys", "text": text + "\n", "msgid": msgid, "args": safe}
        self.channels[number].text.append(piece)
        self.hub.emit({"type": "pkt.text", "channel": number, **piece})

    def _touch(self) -> None:
        self._changed = True


def readable(text: str) -> bool:
    """Whether a frame's contents are text (most characters printable)."""
    if not text:
        return True
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return printable >= 0.9 * len(text) and not any("\x80" <= ch <= "\x9f" for ch in text)


def _plain(args: dict) -> dict:
    return {k: v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
            for k, v in args.items()}
