"""The suite core: the shared station plus the services that run on it, publishing events.

Everything a client can see is an event (a JSON-serialisable dict) and everything a client
can do is a command line ("/f 7.030", "CQ DE ...") addressed to an application, or a call
that returns an answer. The same contract serves in-process clients and remote ones over
WebSocket, so the core can run next to the radio (e.g. on a Raspberry Pi).

Events (server -> client), by "type":
  welcome  {version, server, rig: {...}, apps, tx_enabled, callsign, macros, audio_rate}
           first message; audio_rate: receive audio's sample rate (0: none reaches the core)
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
           pkt.* (see apps.packet.service), ft8.* (see apps.ft8.service),
           dstar.* (see apps.dstar.service),
           audio.listen {on}  receive audio for this client (remote: binary frames)
           voice.ptt {on, source}  key the radio for this client's microphone, or for the
           radio's own (source "radio"; see station.voice)
           memories, memory.save {id?, name, frequency, mode...}, memory.delete {id}
           (the suite's memory channels, station.memories), memory.tune {frequency, mode...},
           rig.memories {group}, rig.memory.write {group, channel, memory} (the radio's)
Binary frames, one byte of kind, then the data:
  AUDIO_RX  (server -> client, while it listens) the radio's receive audio, 16-bit
            little-endian mono PCM at audio_rate
  AUDIO_TX  (client -> server, while its PTT is down) its microphone, the same format
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable

import numpy as np

from ic705suite import __version__
from ic705suite.apps.cw.session import Session
from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.lotw import LotwService
from ic705suite.rig import open_rig
from ic705suite.rig.base import FEATURE_AUDIO, RigError
from ic705suite.rig.transport import Transport, TransportError
from ic705suite.shared_logbook import core_url, for_clients, use
from ic705suite.station.station import Station
from ic705suite.station.voice import Voice

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 2
DEFAULT_PORT = 7373
STATE_INTERVAL = 0.25
SKIM_INTERVAL = 1.0
CHANNELS_INTERVAL = 2.0
BACKLOG = 400          # text events replayed to a client that connects later
RETRY_DELAY = 10.0     # wait between attempts to reach the radio
AUDIO_RX = 0x01        # kinds of binary frame: receive audio,
AUDIO_TX = 0x02        # and a client's microphone

Event = dict
Subscriber = Callable[[Event], None]
PcmListener = Callable[[bytes], None]   # 16-bit little-endian mono PCM


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
        from ic705suite.apps.dstar.service import DStarService
        from ic705suite.apps.ft8.service import Ft8Service
        from ic705suite.apps.packet.service import PacketService
        from ic705suite.apps.swr.service import SwrService

        self.config = config
        self.logbook = core_url(config)        # the shared logbook, opened here
        if self.logbook:
            use(self.logbook)
        self.hub = EventHub()
        self.station = Station(open_rig(config, transport), config, self.hub)
        self.session = Session(self.station, config, self.hub)
        self.swr = SwrService(self.station, config, self.hub)
        self.packet = PacketService(config, self.hub, self.station)
        self.ft8 = Ft8Service(self.station, config, self.hub)
        self.lotw = LotwService(config, self.hub)
        self.voice = Voice(self.station, config, self.hub)
        self.dstar = DStarService(self.station, config, self.hub)
        self._memories = None                  # station.memories, opened when first asked
        self._ticker: asyncio.Task | None = None
        self._pcm: list[PcmListener] = []
        self.clients = 0                       # connected to it (the server's, a local one)
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
                "swr_port": self.swr.port, "swr_url": self.swr.url,
                "logbook": for_clients(self.config), "audio_rate": self.audio_rate}

    # ---- receive audio for the clients (the computer's speakers) -------------------------

    @property
    def audio_rate(self) -> int:
        """Sample rate of the receive audio; 0 when no audio reaches the core."""
        if self.config.connection == "none" or FEATURE_AUDIO not in self.station.model.features:
            return 0
        return self.station.sample_rate

    def listen_audio(self, callback: PcmListener) -> Callable[[], None]:
        """Hand the receive audio to `callback` as 16-bit PCM; returns how to stop."""
        if not self._pcm:
            self.station.add_audio_listener(self._to_pcm)
        self._pcm.append(callback)

        def stop() -> None:
            if callback in self._pcm:
                self._pcm.remove(callback)
                if not self._pcm:
                    self.station.remove_audio_listener(self._to_pcm)
        return stop

    def _to_pcm(self, samples: np.ndarray) -> None:
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        for callback in list(self._pcm):
            try:
                callback(pcm)
            except Exception:
                log.exception("audio listener failed")

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
        await self.lotw.start()
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
        await self.lotw.stop()
        await self.voice.stop()
        await self.dstar.stop()
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
        if method.startswith(("memor", "rig.memor")):
            return await self._memory_call(method, params)
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
        if method.startswith("lotw."):
            return await self.lotw.call(method[5:], params)
        if method.startswith("ft8."):
            return await self.ft8.call(method[4:], params)
        if method.startswith("dstar."):
            return await self.dstar.call(method[6:], params)
        return {"error": f"unknown method {method}"}

    # ---- memory channels: the suite's (database) and the radio's --------------------------

    def _memory_table(self):
        from ic705suite.station.memories import Memories

        if self._memories is None:
            self._memories = Memories(self.config.database_url or None)
        return self._memories

    async def _memory_call(self, method: str, params: dict) -> dict:
        from ic705suite.station.memories import MemoryError_

        rig = self.station.rig
        try:
            if method == "memories":
                return {"memories": await asyncio.to_thread(self._memory_table().entries)}
            if method == "memory.save":
                saved = await asyncio.to_thread(self._memory_table().save, params)
                return {"memory": saved,
                        "memories": await asyncio.to_thread(self._memory_table().entries)}
            if method == "memory.delete":
                await asyncio.to_thread(self._memory_table().delete, int(params["id"]))
                return {"memories": await asyncio.to_thread(self._memory_table().entries)}
            if method == "memory.tune":
                problems = await rig.apply_memory(params)
                for problem in problems:
                    self.hub.error(N_("memory tuned, but not all of it: {error}"), error=problem)
                return {"tuned": True, "problems": problems}
            if method == "rig.memories":
                if rig.memory_layout is None:
                    return {"error": "this radio has no memory channels here"}
                group = int(params.get("group", 0))
                found = []
                for channel in range(rig.memory_layout[1]):
                    memory = await rig.read_memory(group, channel)
                    if memory is not None:
                        found.append({"group": group, "channel": channel, **memory})
                return {"group": group, "channels": rig.memory_layout[1], "memories": found}
            if method == "rig.memory.write":
                group, channel = int(params["group"]), int(params["channel"])
                await rig.write_memory(group, channel, params.get("memory"))
                return {"group": group, "channel": channel,
                        "memory": await rig.read_memory(group, channel)}
        except (MemoryError_, ValueError, KeyError) as exc:
            return {"error": str(exc)}
        except (RigError, TransportError) as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the database: said, not a crash
            log.warning("memory call %s failed: %s", method, exc)
            return {"error": str(exc)}
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
                self.ft8.watch_clients(self.clients)
                if self.ft8.changed():
                    self.hub.emit({"type": "ft8", **self.ft8.state()})
                if self.dstar.changed():
                    self.hub.emit({"type": "dstar", **self.dstar.state()})
