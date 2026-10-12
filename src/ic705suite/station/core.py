"""The suite core: the shared station plus the services that run on it, publishing events.

Everything a client can see is an event (a JSON-serialisable dict) and everything a client
can do is a command line ("/f 7.030", "CQ DE ...") addressed to an application, or a call
that returns an answer. The same contract serves in-process clients and remote ones over
WebSocket, so the core can run next to the radio (e.g. on a Raspberry Pi).

Events (server -> client), by "type":
  welcome  {version, server, rig: {...}, apps, tx_enabled, callsign, macros}  first message
  rig      {connected, model, frequency, mode, smeter...}   see Station.snapshot (4 per s)
  state    {...}  CW engine snapshot (decoder, skimmer, playback), see snapshot()  (4 per s)
  skim     {channels: [{index, frequency, tone, callsign, wpm, snr_db, text}]}  (1 per s)
  channels {session: [{id, label, frequency, callsign, unsaved, stats...}]}   (every 2 s)
  swr      {status, sweep: {...}}  antenna tuning progress, while a sweep runs or changes
  pkt      {tnc, mycall, channels, heard...}  packet radio, when it changes; and
  pkt.text {channel, kind, text}  as it arrives (see apps.packet.service)
  ft8      {on, band, dial, decoding...}  when it changes; ft8.cycle each cycle decoded
           (see apps.ft8.service)
  history  {title, text}  earlier text of a station picked from the skimmer
  reply    {id, result}   answer to a call
  rx       {text}  decoded characters (single decoder mode)
  tx       {text}  text handed to the radio keyer
  info     {msgid, args, text}  / error {msgid, args, text}  messages for the operator:
           msgid is an English template to translate on the client (ic705suite.i18n.render),
           text the same message already formatted in English
Commands (client -> server):
  hello    {token, client}   must be first on a remote connection
  command  {line, app}       app: "cw" (default) or "rig"
  scope    {start, end, center, out_of_range, data}  band scope sweep, data = base64 of
           one byte per point (0..255); only while a client keeps calling scope.watch
  call     {id, method, params}  methods: rig, rig.describe, rig.set {id, value},
           scope.watch {seconds}, channels, transcript {id, saved},
           save_channel {id, name, auto, append, audio}, autosave, cw.activate,
           swr.state, swr.bands, swr.history {band}, swr.sweep {...}, swr.stop,
           swr.remote {on}, swr.final {coil, band?, note?}, swr.antennas, swr.antenna.select {id},
           swr.antenna.save {name, notes, coil_sign, id?}, swr.antenna.delete {id},
           pkt.* (see apps.packet.service), ft8.* (see apps.ft8.service)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable

from ic705suite import __version__
from ic705suite.apps.cw.session import Session
from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.rig import open_rig
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import Transport, TransportError
from ic705suite.station.station import Station

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 2
DEFAULT_PORT = 7373
STATE_INTERVAL = 0.25
SKIM_INTERVAL = 1.0
CHANNELS_INTERVAL = 2.0
BACKLOG = 400          # text events replayed to a client that connects later
RETRY_DELAY = 10.0     # wait between attempts to reach the radio

Event = dict
Subscriber = Callable[[Event], None]


class EventHub:
    """Message sink of the station and its services; turns their calls into events."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self.backlog: deque[Event] = deque(maxlen=BACKLOG)

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback) if callback in self._subscribers else None

    def emit(self, event: Event, keep: bool = False) -> None:
        if keep:
            # Merge consecutive rx characters so the backlog holds lines, not letters.
            if event["type"] == "rx" and self.backlog and self.backlog[-1]["type"] == "rx":
                self.backlog[-1] = {"type": "rx", "text": self.backlog[-1]["text"] + event["text"]}
            else:
                self.backlog.append(dict(event))
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:
                log.exception("event subscriber failed")

    # UiSink protocol
    def rx_text(self, text: str) -> None:
        self.emit({"type": "rx", "text": text}, keep=True)

    def tx_text(self, text: str) -> None:
        self.emit({"type": "tx", "text": text}, keep=True)

    def info(self, msgid: str, **args: object) -> None:
        self._message("info", msgid, args)

    def error(self, msgid: str, **args: object) -> None:
        self._message("error", msgid, args)

    def _message(self, kind: str, msgid: str, args: dict) -> None:
        safe = {k: v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                for k, v in args.items()}
        text = msgid.format(**safe) if safe else msgid
        self.emit({"type": kind, "msgid": msgid, "args": safe, "text": text}, keep=True)

    def status_changed(self) -> None:
        pass  # state is pushed at a fixed rate by Core

    def history(self, title: str, text: str) -> None:
        self.emit({"type": "history", "title": title, "text": text}, keep=True)


def snapshot(session: Session) -> Event:
    """State of the CW engine, with the rig fields the CW screens show."""
    st, d = session.radio.state, session.decoder
    return {
        "type": "state",
        "connected": session.connected,
        "transport": session.transport.name,
        "radio": session.radio.name,
        "frequency": session.dial_frequency,
        "mode": st.mode,
        "smeter": st.smeter,
        "pitch": st.pitch,
        "key_wpm": st.key_wpm,
        "power": st.power,
        "tx_enabled": session.config.tx_enabled,
        "transmitting": session.transmitting,
        "scanning": session.scanning,
        "scan_freq": session.scan_freq,
        "skimming": session.skimmer is not None,
        "skim_range": session.skim_range(),
        "spots": session.spots,
        "playback": None if session.playback is None else {
            "file": session.playback.path.name,
            "elapsed": round(session.playback.elapsed, 1),
            "duration": round(session.playback.duration, 1),
        },
        "decoder": {
            "tone": round(d.tone, 1),
            "wpm": round(d.wpm, 1),
            "snr_db": round(d.snr_db, 1),
            "level": round(min(1.0, d.level / d.peak) if d.peak > 0 else 0.0, 3),
            "squelched": d.squelched,
            "afc": d.afc,
        },
    }


def skim_table(session: Session) -> Event:
    channels = session.skimmer.channels if session.skimmer is not None else []
    return {
        "type": "skim",
        "channels": [
            {"index": i, "frequency": session.channel_frequency(ch), "tone": round(ch.tone, 1),
             "callsign": ch.callsign, "known": ch.callsign in session.scp if ch.callsign else False,
             "wpm": round(ch.wpm, 1), "snr_db": round(ch.snr_db, 1),
             "text": " ".join(ch.text.split())[-80:]}
            for i, ch in enumerate(channels, 1)
        ],
    }


class Core:
    def __init__(self, transport: Transport | None, config: Config) -> None:
        """transport=None opens the rig described by the config ([rig] model, connection)."""
        from ic705suite.apps.ft8.service import Ft8Service
        from ic705suite.apps.packet.service import PacketService
        from ic705suite.apps.swr.service import SwrService

        self.config = config
        self.hub = EventHub()
        self.station = Station(open_rig(config, transport), config, self.hub)
        self.session = Session(self.station, config, self.hub)
        self.swr = SwrService(self.station, config, self.hub)
        self.packet = PacketService(config, self.hub, self.station)
        self.ft8 = Ft8Service(self.station, config, self.hub)
        self._ticker: asyncio.Task | None = None
        self.started = False

    def welcome(self) -> Event:
        from ic705suite.apps import catalog

        model = self.station.model
        return {"type": "welcome", "version": PROTOCOL_VERSION, "server": __version__,
                "radio": self.station.rig.name,
                "rig": {"model": model.id, "name": model.name, "manufacturer": model.manufacturer,
                        "features": sorted(model.features), "modes": list(model.modes),
                        **self.station.describe()},
                "apps": catalog(model), "tx_enabled": self.config.tx_enabled,
                "callsign": self.config.callsign, "macros": dict(self.config.macros),
                "swr_port": self.swr.port, "swr_url": self.swr.url}

    def subscribe(self, callback: Subscriber, replay: bool = True) -> Callable[[], None]:
        """Register a client; it first gets welcome, the text backlog and the current state."""
        callback(self.welcome())
        if replay:
            for event in list(self.hub.backlog):
                callback(event)
        callback(self.station.snapshot())
        callback(snapshot(self.session))
        return self.hub.subscribe(callback)

    async def start(self, retry_forever: bool = False) -> None:
        # Services that do not need the radio (the antenna page, packet) come up first.
        await self.swr.start()
        await self.packet.start()
        self._ticker = asyncio.create_task(self._tick())
        if self.config.connection == "none":
            # A core for packet only (a Raspberry Pi with a Digirig): nothing to look for.
            self.hub.info(N_("no radio here: this core serves packet only"))
            self.started = True
            return
        reported = False
        while True:
            try:
                await self.station.start()
                break
            except (TransportError, RigError, OSError) as exc:
                if not retry_forever:
                    raise
                # Said once: the radio may stay off all session while the logbook is used.
                if not reported:
                    self.hub.error(N_("cannot reach the radio: {error}; it keeps trying in the "
                                      "background (the logbook and packet work without it)"),
                                   error=str(exc))
                    reported = True
                log.info("radio still unreachable (%s); retrying in %.0f s", exc, RETRY_DELAY)
                await self.station.rig.close()
                await asyncio.sleep(RETRY_DELAY)
        await self.session.start()
        self.started = True

    async def stop(self, autosave: bool = False) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
        if autosave:
            saved = self.session.autosave()
            if saved:
                log.info("archived %d unsaved channels on exit", saved)
        await self.swr.stop()
        await self.packet.stop()
        await self.ft8.stop()
        await self.session.stop()
        await self.station.stop()

    async def call(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        session = self.session
        if method == "rig":
            return self.station.snapshot()
        if method == "rig.describe":
            return self.station.describe()
        if method == "rig.set":
            try:
                value = await self.station.set_control(str(params["id"]), params.get("value"))
            except (ValueError, KeyError, RigError, TransportError) as exc:
                return {"error": str(exc)}
            return {"id": params["id"], "value": value}
        if method == "scope.watch":
            try:
                return {"scope": await self.station.scope_watch(float(params.get("seconds", 15)))}
            except (RigError, TransportError) as exc:
                return {"error": str(exc)}
        if method == "channels":
            return session.list_channels()
        if method == "transcript":
            return {"transcript": session.channel_transcript(int(params["id"]),
                                                              bool(params.get("saved")))}
        if method == "save_channel":
            return session.save_channel(int(params["id"]), params.get("name"),
                                        bool(params.get("auto")), bool(params.get("append")),
                                        bool(params.get("audio")))
        if method == "autosave":
            return {"saved": session.autosave()}
        if method == "cw.activate":
            await session.activate()
            return {"active": True}
        if method.startswith("swr."):
            return await self.swr.call(method[4:], params)
        if method.startswith("pkt."):
            return await self.packet.call(method[4:], params)
        if method.startswith("ft8."):
            return await self.ft8.call(method[4:], params)
        return {"error": f"unknown method {method}"}

    async def command(self, line: str, app: str = "cw") -> None:
        if line.strip().lower() in ("/quit", "/q", "/exit"):
            return  # leaving is the client's business; the core keeps running
        if app == "rig":
            if not await self.station.execute(line):
                self.hub.error(N_("unknown command {line}"), line=line.strip())
            return
        await self.session.execute(line)

    async def _tick(self) -> None:
        last_skim = last_channels = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(STATE_INTERVAL)
            with contextlib.suppress(Exception):
                self.hub.emit(self.station.snapshot())
                self.hub.emit(snapshot(self.session))
                if loop.time() - last_channels >= CHANNELS_INTERVAL:
                    last_channels = loop.time()
                    self.hub.emit({"type": "channels", "session": self.session.book.summaries()})
                if self.session.skimmer is not None and loop.time() - last_skim >= SKIM_INTERVAL:
                    last_skim = loop.time()
                    self.hub.emit(skim_table(self.session))
                if self.swr.changed():
                    self.hub.emit({"type": "swr", **self.swr.state()})
                if self.packet.changed():
                    self.hub.emit({"type": "pkt", **self.packet.state()})
                if self.ft8.changed():
                    self.hub.emit({"type": "ft8", **self.ft8.state()})
