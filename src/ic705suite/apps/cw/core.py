"""The cwic705 core: one Session (radio, decoder, skimmer, scanner, keyer) publishing events.

Everything a client can see is an event (a JSON-serialisable dict) and everything a
client can do is a command line, the same ones typed in the UI ("/f 7.030", "CQ DE ...").
That single contract serves the in-process UI and remote clients over WebSocket alike.

Events (server -> client), by "type":
  welcome  {version, radio, tx_enabled, callsign, macros}   first message of a session
  state    {...}  radio/decoder/flags snapshot, see snapshot()          (4 per second)
  skim     {channels: [{index, frequency, tone, callsign, wpm, snr_db, text}]}  (1 per s)
  channels {session: [{id, label, frequency, callsign, unsaved, stats...}]}  on change (2 s)
  history  {title, text}  earlier text of a station picked from the skimmer
  reply    {id, result}   answer to a call
  rx       {text}  decoded characters (single decoder mode)
  tx       {text}  text handed to the radio keyer
  info     {msgid, args, text}  / error {msgid, args, text}  messages for the operator:
           msgid is an English template to translate on the client (cwic705.i18n.render),
           text the same message already formatted in English
Commands (client -> server):
  hello    {token, client}   must be first on a remote connection
  command  {line}
  call     {id, method, params}  methods: channels, transcript {id, saved},
           save_channel {id, name, auto, append, audio}; answered with a reply event
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable

from ic705suite.apps.cw import __version__
from ic705suite.apps.cw.config import Config
from ic705suite.apps.cw.i18n import N_
from ic705suite.apps.cw.radio import CivError
from ic705suite.apps.cw.session import Session
from ic705suite.apps.cw.transport import Transport, TransportError

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_PORT = 7373
STATE_INTERVAL = 0.25
SKIM_INTERVAL = 1.0
CHANNELS_INTERVAL = 2.0
BACKLOG = 400          # text events replayed to a client that connects later
RETRY_DELAY = 10.0     # server mode: wait between attempts to reach the radio

Event = dict
Subscriber = Callable[[Event], None]


class EventHub:
    """UiSink for the Session that turns its calls into events for all subscribers."""

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
    st, d = session.radio.state, session.decoder
    return {
        "type": "state",
        "connected": session.connected,
        "transport": session.transport.name,
        "radio": session.transport.radio_name,
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
    def __init__(self, transport: Transport, config: Config) -> None:
        self.config = config
        self.hub = EventHub()
        self.session = Session(transport, config, self.hub)
        self._ticker: asyncio.Task | None = None

    def welcome(self) -> Event:
        return {"type": "welcome", "version": PROTOCOL_VERSION, "server": __version__,
                "radio": self.session.transport.radio_name, "tx_enabled": self.config.tx_enabled,
                "callsign": self.config.callsign, "macros": dict(self.config.macros)}

    def subscribe(self, callback: Subscriber, replay: bool = True) -> Callable[[], None]:
        """Register a client; it first gets welcome, the text backlog and the current state."""
        callback(self.welcome())
        if replay:
            for event in list(self.hub.backlog):
                callback(event)
        callback(snapshot(self.session))
        return self.hub.subscribe(callback)

    async def start(self, retry_forever: bool = False) -> None:
        while True:
            try:
                await self.session.start()
                break
            except (TransportError, CivError, OSError) as exc:
                if not retry_forever:
                    raise
                self.hub.error(N_("cannot reach the radio: {error}; retrying in {seconds} s"),
                               error=str(exc), seconds=int(RETRY_DELAY))
                await self.session.transport.close()
                await asyncio.sleep(RETRY_DELAY)
        self._ticker = asyncio.create_task(self._tick())

    async def stop(self, autosave: bool = False) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
        if autosave:
            saved = self.session.autosave()
            if saved:
                log.info("archived %d unsaved channels on exit", saved)
        await self.session.stop()

    async def call(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        session = self.session
        if method == "channels":
            return session.list_channels()
        if method == "transcript":
            return {"transcript": session.channel_transcript(int(params["id"]),
                                                              bool(params.get("saved")))}
        if method == "save_channel":
            return session.save_channel(int(params["id"]), params.get("name"),
                                        bool(params.get("auto")), bool(params.get("append")),
                                        bool(params.get("audio")))
        return {"error": f"unknown method {method}"}

    async def command(self, line: str) -> None:
        if line.strip().lower() in ("/quit", "/q", "/exit"):
            return  # leaving is the client's business; the core keeps running
        await self.session.execute(line)

    async def _tick(self) -> None:
        last_skim = last_channels = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(STATE_INTERVAL)
            with contextlib.suppress(Exception):
                self.hub.emit(snapshot(self.session))
                if loop.time() - last_channels >= CHANNELS_INTERVAL:
                    last_channels = loop.time()
                    self.hub.emit({"type": "channels", "session": self.session.book.summaries()})
                if self.session.skimmer is not None and loop.time() - last_skim >= SKIM_INTERVAL:
                    last_skim = loop.time()
                    self.hub.emit(skim_table(self.session))
