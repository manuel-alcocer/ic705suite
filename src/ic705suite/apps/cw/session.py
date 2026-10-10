"""Glue between transport, radio, decoder, scanner and the user interface."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import wave
from pathlib import Path
from typing import Protocol

import numpy as np

from ic705suite.apps.cw import bands, civ, morse
from ic705suite.apps.cw.audio import AudioRing
from ic705suite.apps.cw.config import Config
from ic705suite.apps.cw.decoder import CwDecoder
from ic705suite.apps.cw.i18n import N_
from ic705suite.apps.cw.radio import BREAK_IN_SEMI, CivError, Radio
from ic705suite.apps.cw.channels import AudioSpool, ChannelBook, ChannelRecord, ChannelStore
from ic705suite.apps.cw.playback import SidecarWriter, WavPlayback
from ic705suite.apps.cw.scanner import STEP_HZ, Scanner
from ic705suite.apps.cw.skimmer import Channel, Skimmer, load_scp
from ic705suite.apps.cw.transport import Transport, TransportError

log = logging.getLogger(__name__)
rx_log = logging.getLogger("cwic705.rx")

RECONNECT_DELAY = 5.0
SKIM_FILTER_HZ = 3600  # widest CW filter: about 2.2 kHz of band in the audio
# A radio left with an open session (program killed, WiFi dropped) ignores new
# connections until that session expires, which takes up to about a minute.
STALE_SESSION_WAIT = 90.0

HELP = [
    N_('Plain text + Enter      send it in CW (prosigns: <AR> <BT> <KN> <SK> <AS> <BK>)'),
    N_('/f <freq>               tune (7.030 = MHz, 7030.5 = kHz, 7030500 = Hz)'),
    N_('/b <band>               go to a band (160 80 60 40 30 20 17 15 12 10 6 2 70cm)'),
    N_('/up [Hz] /down [Hz]     move the dial (default 100 Hz)   PgUp/PgDn = +/-100 Hz'),
    N_("/scan [up|down] [band]  automatic search for CW signals in the band's CW segment"),
    N_('/next /prev             continue the search from the current frequency'),
    N_('/stop  (or Esc)         stop transmission and search'),
    N_('/wpm <n>                keyer speed (6-48)       /pitch <Hz>  CW pitch (300-900)'),
    N_('/power <%>              RF power                 /filter <Hz> IF filter width'),
    N_('/afc on|off             decoder follows the tone /rxwpm <n>   decoder speed hint'),
    N_('/call <CALL>            your callsign for macros /macros      list F-key macros'),
    N_('/skim  (F10)            skimmer: decode every CW signal in a 3.6 kHz window'),
    N_('/go <n>                 tune to skimmer station n and leave the skimmer'),
    N_('/play <file> [MHz] [x2] decode a recording as if it came from the radio'),
    N_('/play stop              back to the radio audio'),
    N_('/raw <hex>              send a raw CI-V command body, e.g. /raw 03'),
    N_('/clear  /help  /quit'),
]


class UiSink(Protocol):
    """Receives the session's output. Messages are an English template marked with N_()
    plus its arguments, so each client can translate them (see cwic705.i18n)."""

    def rx_text(self, text: str) -> None: ...
    def tx_text(self, text: str) -> None: ...
    def info(self, msgid: str, **args: object) -> None: ...
    def error(self, msgid: str, **args: object) -> None: ...
    def status_changed(self) -> None: ...


class Session:
    def __init__(self, transport: Transport, config: Config, ui: UiSink) -> None:
        self.transport = transport
        self.config = config
        self.ui = ui
        self.radio = Radio(transport, config.civ_address or civ.IC705_ADDR)
        self.radio.on_state = ui.status_changed
        self.radio.tx_enabled = config.tx_enabled
        self.audio = AudioRing(transport.sample_rate)
        self.scp: set[str] = load_scp(config.scp_path) if config.scp_path.exists() else set()
        self.decoder_class: type[CwDecoder] = CwDecoder
        if config.decoder == "bayes":
            from ic705suite.apps.cw.bayes import BayesDecoder

            self.decoder_class = BayesDecoder
            self.decoder = BayesDecoder(transport.sample_rate, on_text=self._on_rx_text,
                                        scp=self.scp)
        else:
            self.decoder = CwDecoder(transport.sample_rate, on_text=self._on_rx_text)
        self._rx_pending = ""
        self._rx_flushed = time.monotonic()
        self._recorder: wave.Wave_write | None = None
        self._sidecar: SidecarWriter | None = None
        self._recorded = 0
        self.playback: WavPlayback | None = None
        self._playback_task: asyncio.Task | None = None
        self.decoder.afc = config.afc
        self.scanner = Scanner(self.radio, self.audio, self._scan_progress)
        self.connected = False
        self.transmitting = False
        self.scanning = False
        self.scan_freq: int | None = None
        self.skimmer: Skimmer | None = None
        self._paused_skim: tuple[Skimmer, int | None] | None = None  # kept while away
        self.book = ChannelBook(self.scp, on_change=self._channels_changed,
                                sample_rate=transport.sample_rate)
        self.channels_version = 0
        self.spool = AudioSpool(transport.sample_rate) if config.channel_audio else None
        self._store: ChannelStore | None = None
        self._decoding_freq: int | None = None  # station decoded from a recording (/go)
        self.spots = 0
        self._skim_saved_filter: int | None = None
        self._skim_filter: int = SKIM_FILTER_HZ  # IF width actually in use while skimming
        if self.scp:
            log.info("loaded %d callsigns from %s", len(self.scp), config.scp_path)
        self.quit_event = asyncio.Event()
        self._tx_queue: asyncio.Queue[str] = asyncio.Queue()
        self._tx_current: asyncio.Task | None = None
        self._scan_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        transport.on_audio = self._on_audio
        transport.on_disconnect = self._on_disconnect

    def record_to(self, path: str) -> None:
        """Save all received audio to a 16-bit mono WAV file (for offline analysis)."""
        self._recorder = wave.open(path, "wb")
        self._recorder.setnchannels(1)
        self._recorder.setsampwidth(2)
        self._recorder.setframerate(self.transport.sample_rate)
        self._sidecar = SidecarWriter(path, self.transport.sample_rate)
        log.info("recording receive audio to %s", path)

    def _on_rx_text(self, text: str) -> None:
        self._rx_pending += text
        self.ui.rx_text(text)
        self.book.tuned_rx(self._decoding_freq or self.dial_frequency, text, self.decoder.wpm,
                           self.decoder.snr_db, self.spool.position if self.spool else None)

    def _channels_changed(self) -> None:
        self.channels_version += 1

    def _flush_rx_log(self, force: bool = False) -> None:
        """Write decoded text to the log with the conditions it was decoded under."""
        if not self._rx_pending.strip() or not (force or len(self._rx_pending) >= 60
                                                or time.monotonic() - self._rx_flushed > 10):
            return
        d, st = self.decoder, self.radio.state
        rx_log.info("%s Hz tone=%.0f wpm=%.1f snr=%.1fdB s=%d | %s", st.frequency, d.tone, d.wpm,
                    d.snr_db, st.smeter, self._rx_pending.strip())
        self._rx_pending = ""
        self._rx_flushed = time.monotonic()

    # ---- lifecycle ----------------------------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STALE_SESSION_WAIT
        warned = False
        while True:
            try:
                await self._connect()
                break
            except CivError as exc:
                # Right after a stale session expires the radio may open the streams but
                # ignore our CI-V commands; a clean logout and a new login fixes it.
                self.connected = False
                await self.transport.close()
                if loop.time() > deadline:
                    raise
                log.warning("CI-V not answering after connect (%s), reconnecting", exc)
                self.ui.info(N_("the radio is not answering CI-V yet; reconnecting..."))
                await asyncio.sleep(2.0)
            except TransportError as exc:
                if type(exc).__name__ != "RadioNotAnswering" or loop.time() > deadline:
                    raise
                if not warned:
                    self.ui.info(N_("the radio does not answer: it may still hold a previous "
                                    "session (expires in about a minute); waiting... "
                                    "(Ctrl-C to give up)"))
                    warned = True
                await self.transport.close()
        self._tasks = [asyncio.create_task(self._poll_loop()),
                       asyncio.create_task(self._tx_worker())]

    # ---- skimmer ----------------------------------------------------------------------------------

    @property
    def dial_frequency(self) -> int | None:
        """Frequency the audio being decoded was received on (the recording's while playing)."""
        if self.playback is not None:
            return self.playback.frequency
        return self.radio.state.frequency

    def channel_frequency(self, ch: Channel) -> int | None:
        dial = self.dial_frequency
        if dial is None:
            return None
        return round(dial + self.scanner.sideband_sign * (ch.tone - self.radio.state.pitch))

    def skim_range(self) -> tuple[int, int] | None:
        """RF range the skimmer analyses: the IF filter (centred on the CW pitch)
        intersected with the audio band the skimmer looks at, mapped to the dial."""
        dial = self.dial_frequency
        if self.skimmer is None or dial is None:
            return None
        pitch, half = self.radio.state.pitch, self._skim_filter / 2
        if self.skimmer.passband is not None:
            # Measured on the audio itself: right for recordings and whatever the filter is.
            audio_lo, audio_hi = self.skimmer.passband
        else:
            audio_lo = max(self.skimmer.f_lo, pitch - half)
            audio_hi = min(self.skimmer.f_hi, pitch + half)
        sign = self.scanner.sideband_sign
        ends = sorted(round(dial + sign * (a - pitch)) for a in (audio_lo, audio_hi))
        return ends[0], ends[1]

    def _on_spot(self, ch: Channel) -> None:
        self.spots += 1
        freq = self.channel_frequency(ch)
        self.book.skimmer_validated(ch, freq)
        known = " (SCP)" if ch.callsign in self.scp else ""
        self.ui.info(N_("spot {freq}  {call}{known}  {wpm} wpm  {snr} dB"),
                     freq=bands.format_frequency(freq), call=ch.callsign, known=known,
                     wpm=f"{ch.wpm:.0f}", snr=f"{ch.snr_db:.0f}")
        self.ui.status_changed()

    async def start_skim(self) -> None:
        if self.skimmer is not None:
            return
        if self.scanning:
            raise ValueError("stop the search first")
        try:
            self._skim_saved_filter = await self.radio.read_filter_width()
            self._skim_filter = self._skim_saved_filter
            await self.radio.set_filter_width(SKIM_FILTER_HZ)
            self._skim_filter = SKIM_FILTER_HZ
        except CivError as exc:
            log.warning("skimmer: cannot widen the filter: %s", exc)
        if self._paused_skim is not None:
            # Back from a station: the stations found before are still there, moved by
            # however much the dial moved meanwhile.
            self.skimmer, old_dial = self._paused_skim
            self._paused_skim = None
            new_dial = self.dial_frequency
            if old_dial is not None and new_dial is not None and new_dial != old_dial:
                self.skimmer.shift(self.scanner.sideband_sign * (new_dial - old_dial))
            self._decoding_freq = None
            self.ui.status_changed()
            self.ui.info(N_("skimmer resumed with {count} stations"), count=len(self.skimmer.channels))
            return
        self.skimmer = Skimmer(self.transport.sample_rate, scp=self.scp, on_callsign=self._on_spot,
                               decoder_class=self.decoder_class,
                               on_channel_text=self._skim_text)
        if self.scp:
            self.ui.info(N_("skimmer on: decoding every signal in the passband; "
                            "{count} callsigns in MASTER.SCP"), count=len(self.scp))
        else:
            self.ui.info(N_("skimmer on: decoding every signal in the passband; "
                            "no MASTER.SCP (cwic705 --update-scp)"))
        self.ui.status_changed()

    def _skim_text(self, ch: Channel, text: str) -> None:
        self.book.skimmer_rx(ch, text, self.channel_frequency(ch),
                             self.spool.position if self.spool else None)

    async def stop_skim(self, keep: bool = True) -> None:
        if self.skimmer is None:
            return
        if keep:
            self._paused_skim = (self.skimmer, self.dial_frequency)
        self.skimmer = None
        if self._skim_saved_filter:
            try:
                await self.radio.set_filter_width(self._skim_saved_filter)
            except CivError as exc:
                log.warning("skimmer: cannot restore the filter: %s", exc)
        self.decoder.reset(self.radio.state.pitch)
        self.ui.info(N_("skimmer off"))
        self.ui.status_changed()

    async def go_to_channel(self, index: int) -> None:
        """Tune the station of skimmer row `index` (0-based) and switch to single decoding."""
        if self.skimmer is None or not 0 <= index < len(self.skimmer.channels):
            raise ValueError("no such skimmer station")
        ch = self.skimmer.channels[index]
        dial, pitch = self.radio.state.frequency, self.radio.state.pitch
        station_freq = self.channel_frequency(ch)
        record = self.book.record_for(ch)
        if record is None:
            record = self.book._new("tuned", station_freq)
            record.callsign = ch.callsign
            record.seed(ch.text)
            self.book.attach(ch, record)  # its callsign, once validated, names this channel
        self.book.follow(record, station_freq)
        history = getattr(self.ui, "history", None)
        if history is not None and record.text.strip():
            history(record.label, record.text)  # what was heard before, to keep the thread
        if self.playback is not None:
            # A recording cannot be retuned: decode that station on its own tone instead.
            await self.stop_skim()
            self._decoding_freq = station_freq
            self.decoder.reset(ch.tone)
            self.ui.info(N_("decoding the station at {freq} from the recording"),
                         freq=bands.format_frequency(self.channel_frequency(ch)))
            return
        await self.stop_skim()
        if dial is not None:
            freq = await self.scanner.centre(dial, ch.tone, pitch)
            self.decoder.reset(pitch)
            if ch.callsign:
                self.ui.info(N_("tuned to {freq} MHz ({call})"),
                             freq=bands.format_frequency(freq), call=ch.callsign)
            else:
                self.ui.info(N_("tuned to {freq} MHz"), freq=bands.format_frequency(freq))

    # ---- channel archive -----------------------------------------------------------------------

    @property
    def store(self) -> ChannelStore:
        if self._store is None:
            self._store = ChannelStore(self.config.channels_db or None)
        return self._store

    def list_channels(self) -> dict:
        try:
            saved = self.store.list()
            error = None
        except Exception as exc:  # unreachable PostgreSQL, bad URL...
            log.error("channel archive: %s", exc)
            saved, error = [], str(exc)
        return {"session": self.book.summaries(), "saved": saved, "error": error,
                "audio_available": self.spool is not None}

    def channel_transcript(self, channel_id: int, saved: bool) -> dict | None:
        if saved:
            return self.store.transcript(channel_id)
        record = self.book.get(channel_id)
        if record is None:
            return None
        entries = list(record.entries)
        if record._line.strip():
            entries.append({"utc": record.last, "kind": "rx", "text": record._line.strip()})
        return {"id": record.id, "name": record.label, "callsign": record.callsign,
                "frequency": record.frequency, "stats": record.stats.as_dict(),
                "entries": entries, "audio": []}

    def save_channel(self, channel_id: int, name: str | None = None, auto: bool = False,
                     append: bool = False, audio: bool = False) -> dict:
        """Archive a session channel. Status: saved | appended | needs_name | name_exists |
        empty | error. With a callsign the key is (callsign, frequency): an archived channel
        with that key is appended to. Without one the caller must give a name (auto=True
        picks "Channel N"); an existing name is only appended to when append=True."""
        record = self.book.get(channel_id)
        if record is None or not record.stats.words:
            return {"status": "empty", "id": channel_id}
        try:
            store = self.store
            target = None
            if record.archive_id is not None:  # saved earlier in this session: continue it
                target = store.by_name(record.archive_name)
            elif record.callsign:
                target = store.by_key(record.callsign, record.frequency)
                if target is None:
                    name = name or record.callsign
                    if store.by_name(name):
                        name = f"{record.callsign} {bands.format_frequency(record.frequency)}"
            else:
                if auto or (name is not None and not name.strip()):
                    name = store.auto_name()
                elif not name:
                    return {"status": "needs_name", "id": channel_id,
                            "label": record.label, "suggestion": store.auto_name()}
                existing = store.by_name(name.strip())
                if existing is not None:
                    if not append:
                        return {"status": "name_exists", "id": channel_id, "name": name.strip()}
                    target = existing
                name = name.strip()
            archive_id, saved_name = store.save(record, name, target, self.spool, audio)
        except Exception as exc:
            log.exception("saving channel %s", channel_id)
            return {"status": "error", "id": channel_id, "error": str(exc)}
        self._channels_changed()
        return {"status": "appended" if target is not None else "saved", "id": channel_id,
                "name": saved_name, "archive_id": archive_id}

    def autosave(self) -> int:
        """Archive every unsaved channel without asking (server shutdown)."""
        saved = 0
        for record in self.book.records:
            record.flush()
            if record.unsaved:
                result = self.save_channel(record.id, auto=True, append=True,
                                           audio=self.config.channel_audio)
                saved += result["status"] in ("saved", "appended")
        return saved

    async def stop(self) -> None:
        for task in [*self._tasks, self._scan_task, self._tx_current, self._reconnect_task,
                     self._playback_task]:
            if task is not None:
                task.cancel()
        if self.connected and self.skimmer is not None:
            with contextlib.suppress(CivError, TransportError):
                await self.stop_skim()  # give the radio its filter back
        if self.connected and self.transmitting:
            with contextlib.suppress(CivError, TransportError):
                await self.radio.stop_cw()
        await self.transport.close()
        self._flush_rx_log(force=True)
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
        if self._sidecar is not None:
            self._sidecar.close()
            self._sidecar = None
        if self.spool is not None:
            self.spool.close()
            self.spool = None
        if self._store is not None:
            self._store.close()

    async def _connect(self) -> None:
        self.ui.info(N_("connecting ({transport})..."), transport=self.transport.name)
        await self.transport.connect()
        if self.config.civ_address is None and self.transport.civ_address is not None:
            self.radio.address = self.transport.civ_address
        self.connected = True
        await self._init_radio()
        self.ui.info(N_("connected to {radio}"), radio=self.transport.radio_name or "radio")
        self.ui.status_changed()

    async def _init_radio(self) -> None:
        state = self.radio.state
        state.frequency = await self.radio.read_frequency()
        mode = await self.radio.read_mode()
        if mode not in ("CW", "CW-R"):
            await self.radio.set_mode_cw()
            self.ui.info(N_("mode {mode} -> CW"), mode=mode)
        else:
            state.mode = mode
        self.decoder.reset(await self.radio.read_pitch())
        actions = [self.radio.read_key_speed(), self.radio.read_power()]
        if self.config.tx_enabled:
            actions += [self.radio.set_key_speed(self.config.wpm),
                        self.radio.set_break_in(BREAK_IN_SEMI)]
        else:
            self.ui.info(N_("receive-only mode: transmission disabled (--tx to enable)"))
        for action in actions:
            try:
                await action
            except CivError as exc:
                log.warning("init: %s", exc)

    def _on_disconnect(self, reason: str) -> None:
        if not self.connected:
            return
        self.connected = False
        self.ui.error(N_("connection lost ({reason}); reconnecting..."), reason=reason)
        self.ui.status_changed()
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        while not self.connected:
            await self.transport.close()
            await asyncio.sleep(RECONNECT_DELAY)
            try:
                await self._connect()
            except (TransportError, CivError, OSError) as exc:
                self.connected = False
                self.ui.error(N_("reconnect failed: {error}"), error=str(exc))

    # ---- audio / polling ------------------------------------------------------------------------

    def _on_audio(self, samples: np.ndarray) -> None:
        if self._recorder is not None:
            self._recorder.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            self._sidecar.note(self._recorded, self.radio.state.frequency)
            self._recorded += len(samples)
        if self.playback is None:  # a recording being played replaces the radio audio
            self._process_audio(samples)

    def _process_audio(self, samples: np.ndarray) -> None:
        self.audio.push(samples)
        if self.spool is not None:
            self.spool.write(samples, self.dial_frequency)
        if self.skimmer is not None and not self.transmitting:
            self.skimmer.process(samples)
            return
        self.decoder.muted = self.transmitting or self.scanning
        self.decoder.process(samples)

    async def _poll_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(0.25)
            if not self.connected or self.scanning:
                continue
            tick += 1
            self._flush_rx_log()
            if tick % 4 == 0:
                self.book.tick()  # transmissions that ended in silence get their summary
            try:
                self.radio.state.smeter = await self.radio.read_smeter()
                if tick % 4 == 0:
                    freq = await self.radio.read_frequency()
                    if freq != self.radio.state.frequency:
                        self._flush_rx_log(force=True)
                        log.info("frequency changed on the radio: %d Hz", freq)
                        old = self.radio.state.frequency
                        self.radio.state.frequency = freq
                        self.decoder.reset()
                        if self.skimmer is not None and old is not None:
                            self.skimmer.shift(self.scanner.sideband_sign * (freq - old))
                if tick % 8 == 0:
                    self.radio.state.mode = await self.radio.read_mode()
            except (CivError, TransportError) as exc:
                log.debug("poll: %s", exc)
            self.ui.status_changed()

    # ---- transmit -----------------------------------------------------------------------------------

    async def _tx_worker(self) -> None:
        while True:
            text = await self._tx_queue.get()
            if not self.connected:
                self.ui.error(N_("not connected, text discarded"))
                continue
            self.transmitting = True
            self.ui.status_changed()
            self.ui.tx_text(text)
            self.book.tuned_tx(self.dial_frequency, text)
            self._tx_current = asyncio.create_task(self.radio.send_cw(text))
            try:
                await self._tx_current
            except asyncio.CancelledError:
                if not self._tx_current.cancelled():
                    raise
            except (CivError, TransportError) as exc:
                self.ui.error(N_("TX failed: {error}"), error=str(exc))
            finally:
                self._tx_current = None
                self.transmitting = not self._tx_queue.empty()
                self.ui.status_changed()

    def send_text(self, text: str) -> None:
        if not self.config.tx_enabled:
            self.ui.error(N_("receive-only mode: text not sent (start with --tx to transmit)"))
            return
        text = self.expand(text)
        normalized, dropped = morse.normalize_tx(text)
        if dropped:
            self.ui.info(N_("characters not sendable in CW dropped: {chars}"), chars=dropped)
        if normalized:
            self._tx_queue.put_nowait(normalized)

    def expand(self, text: str) -> str:
        return text.replace("{call}", self.config.callsign or "N0CALL")

    def send_macro(self, key: str) -> None:
        macro = self.config.macros.get(key)
        if macro:
            self.send_text(macro)

    async def stop_all(self) -> None:
        stopped = []
        while not self._tx_queue.empty():
            self._tx_queue.get_nowait()
        if self._tx_current is not None:
            self._tx_current.cancel()
        if self.transmitting and self.connected:
            with contextlib.suppress(CivError, TransportError):
                await self.radio.stop_cw()
            stopped.append("tx")
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
            stopped.append("search")
        if stopped:
            self.ui.info({("tx",): N_("TX stopped"), ("search",): N_("search stopped"),
                          ("tx", "search"): N_("TX and search stopped")}[tuple(stopped)])

    # ---- scanning ---------------------------------------------------------------------------------------

    def _scan_progress(self, freq: int, snr: float) -> None:
        self.scan_freq = freq
        self.ui.status_changed()

    def start_scan(self, direction: int = 1, band: bands.Band | None = None) -> None:
        if self._scan_task is not None and not self._scan_task.done():
            self.ui.info(N_("search already running (/stop to cancel)"))
            return
        if self.skimmer is not None:
            self.ui.error(N_("leave the skimmer first (/skim or F10)"))
            return
        if self.playback is not None:
            self.ui.error(N_("a recording is playing: searching needs the radio (/play stop)"))
            return
        current = self.radio.state.frequency or 0
        if band is None:
            band = bands.band_for(current)
            if band is None:
                self.ui.error(N_("current frequency is outside the amateur bands; "
                                 "use /scan <band>"))
                return
        if band.cw_start <= current <= band.cw_end:
            start = current + direction * STEP_HZ
        else:
            start = band.cw_start if direction > 0 else band.cw_end
        self._scan_task = asyncio.create_task(self._scan(band, start, direction))

    async def _scan(self, band: bands.Band, start: int, direction: int) -> None:
        self.scanning = True
        self.ui.info(N_("searching {band} {start}-{end} up...") if direction > 0 else
                     N_("searching {band} {start}-{end} down..."), band=band.name,
                     start=bands.format_frequency(band.cw_start),
                     end=bands.format_frequency(band.cw_end))
        try:
            if self.radio.state.mode not in ("CW", "CW-R"):
                await self.radio.set_mode_cw()
            found = await self.scanner.search(band, start, direction)
        except (CivError, TransportError) as exc:
            self.ui.error(N_("search failed: {error}"), error=str(exc))
            return
        finally:
            self.scanning = False
            self.scan_freq = None
            self.decoder.reset(self.radio.state.pitch)
            self.ui.status_changed()
        if found is None:
            self.ui.info(N_("no CW signals found in {band}"), band=band.name)
        else:
            self.ui.info(N_("signal found at {freq} MHz (/next to keep searching)"),
                         freq=bands.format_frequency(found))

    # ---- commands -------------------------------------------------------------------------------------------

    async def execute(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if not line.startswith("/"):
            self.send_text(line)
            return
        name, *args = line[1:].split()
        handler = getattr(self, f"_cmd_{name.lower()}", None) or \
            getattr(self, f"_cmd_{_ALIASES.get(name.lower(), '')}", None)
        if handler is None:
            self.ui.error(N_("unknown command /{name} (/help)"), name=name)
            return
        try:
            await handler(args)
        except (ValueError, IndexError):
            self.ui.error(N_("bad arguments for /{name} (/help)"), name=name)
        except (CivError, TransportError) as exc:
            self.ui.error(N_("{error}"), error=str(exc))

    async def _tune(self, freq: int) -> None:
        if not 30_000 <= freq <= 470_000_000:
            raise ValueError
        if self.skimmer is not None and self.playback is None:
            old = self.radio.state.frequency
            if old is not None:
                self.skimmer.shift(self.scanner.sideband_sign * (freq - old))
        if self.playback is not None:
            self.ui.info(N_("the radio was tuned, but the audio still comes from the recording"))
        self._flush_rx_log(force=True)
        log.info("tune %d Hz", freq)
        await self.radio.set_frequency(freq)
        self.decoder.reset()

    async def _cmd_help(self, args: list[str]) -> None:
        for row in HELP:
            self.ui.info(row)

    async def _cmd_freq(self, args: list[str]) -> None:
        await self._tune(bands.parse_frequency(args[0]))

    async def _cmd_band(self, args: list[str]) -> None:
        band = bands.find_band(args[0])
        if band is None:
            raise ValueError
        await self._tune(band.default)
        if self.radio.state.mode not in ("CW", "CW-R"):
            await self.radio.set_mode_cw()

    async def _cmd_up(self, args: list[str]) -> None:
        await self._tune((self.radio.state.frequency or 0) + int(args[0] if args else 100))

    async def _cmd_down(self, args: list[str]) -> None:
        await self._tune((self.radio.state.frequency or 0) - int(args[0] if args else 100))

    async def _cmd_scan(self, args: list[str]) -> None:
        direction, band = 1, None
        for arg in args:
            if arg.lower() in ("up", "down"):
                direction = 1 if arg.lower() == "up" else -1
            else:
                band = bands.find_band(arg)
                if band is None:
                    raise ValueError
        self.start_scan(direction, band)

    async def _cmd_next(self, args: list[str]) -> None:
        self.start_scan(1)

    async def _cmd_prev(self, args: list[str]) -> None:
        self.start_scan(-1)

    async def _cmd_stop(self, args: list[str]) -> None:
        await self.stop_all()

    async def _cmd_wpm(self, args: list[str]) -> None:
        await self.radio.set_key_speed(int(args[0]))
        self.ui.info(N_("keyer speed {wpm} wpm"), wpm=self.radio.state.key_wpm)

    async def _cmd_pitch(self, args: list[str]) -> None:
        await self.radio.set_pitch(int(args[0]))
        self.decoder.reset(self.radio.state.pitch)
        self.ui.info(N_("CW pitch {hz} Hz"), hz=self.radio.state.pitch)

    async def _cmd_power(self, args: list[str]) -> None:
        await self.radio.set_power(int(args[0].rstrip("%")))
        self.ui.info(N_("RF power {percent} %"), percent=self.radio.state.power)

    async def _cmd_filter(self, args: list[str]) -> None:
        await self.radio.set_filter_width(int(args[0]))
        self.ui.info(N_("IF filter {hz} Hz"), hz=await self.radio.read_filter_width())

    async def _cmd_afc(self, args: list[str]) -> None:
        if args:
            self.decoder.afc = args[0].lower() in ("on", "1", "yes")
        else:
            self.decoder.afc = not self.decoder.afc
        if not self.decoder.afc:
            self.decoder.set_pitch(self.radio.state.pitch)
        self.ui.info(N_("AFC on") if self.decoder.afc else N_("AFC off"))

    async def _cmd_rxwpm(self, args: list[str]) -> None:
        self.decoder.dit = 1.2 / max(5, min(60, float(args[0])))

    async def _cmd_call(self, args: list[str]) -> None:
        self.config.callsign = args[0].upper()
        self.ui.info(N_("callsign {call}"), call=self.config.callsign)

    async def _cmd_macros(self, args: list[str]) -> None:
        for key in sorted(self.config.macros, key=lambda k: int(k[1:]) if k[1:].isdigit() else 99):
            self.ui.info(N_("{text}"), text=f"{key}: {self.expand(self.config.macros[key])}")

    async def _cmd_macro(self, args: list[str]) -> None:
        key = args[0].upper()
        if key not in self.config.macros:
            raise ValueError
        self.send_macro(key)

    async def _cmd_raw(self, args: list[str]) -> None:
        body = bytes.fromhex("".join(args))
        frame = await self.radio.transact(body, body[:1])  # OK/NG also match
        self.ui.info(N_("{text}"), text="<- " + frame.body.hex(" "))

    async def _cmd_skim(self, args: list[str]) -> None:
        if args and args[0] == "list":
            if self.skimmer is None:
                raise ValueError
            for i, ch in enumerate(self.skimmer.channels, 1):
                self.ui.info(N_("{text}"), text=(
                    f"{i:2d} {bands.format_frequency(self.channel_frequency(ch))} "
                    f"{ch.callsign or '-':10s} {ch.wpm:4.1f} wpm {ch.snr_db:4.0f} dB  "
                    f"{' '.join(ch.text.split())[-40:]}"))
            return
        if self.skimmer is None and (not args or args[0] == "on"):
            await self.start_skim()
        elif self.skimmer is not None and (not args or args[0] == "off"):
            await self.stop_skim()

    async def _cmd_play(self, args: list[str]) -> None:
        """/play FILE [MHz] [xSPEED] · /play stop"""
        if not args:
            raise ValueError
        if args[0].lower() == "stop":
            self.stop_playback()
            return
        path = Path(args[0]).expanduser()
        frequency, speed = None, 1.0
        for arg in args[1:]:
            if arg.lower().startswith("x"):
                speed = max(0.25, min(20.0, float(arg[1:])))
            else:
                frequency = bands.parse_frequency(arg)
        await self.start_playback(path, frequency, speed)

    async def start_playback(self, path: Path, frequency: int | None = None,
                             speed: float = 1.0) -> None:
        self.stop_playback(quiet=True)
        try:
            playback = WavPlayback(path, self.transport.sample_rate, self._process_audio,
                                   speed=speed, frequency=frequency)
        except (OSError, ValueError, EOFError) as exc:
            self.ui.error(N_("cannot play {file}: {error}"), file=str(path), error=str(exc))
            return
        self.playback = playback
        self.decoder.reset(self.radio.state.pitch)
        if self.skimmer is not None:
            self.skimmer.reset()
        self._playback_task = asyncio.create_task(self._play(playback))
        known = bands.format_frequency(playback.frequency) if playback.timeline else "?"
        self.ui.info(N_("playing {file} ({minutes} min, recorded on {freq} MHz)"),
                     file=path.name, minutes=f"{playback.duration / 60:.1f}", freq=known)
        self.ui.status_changed()

    async def _play(self, playback: WavPlayback) -> None:
        await playback.run()
        if self.playback is playback:
            self.playback = None
            self.decoder.reset(self.radio.state.pitch)
            if self.skimmer is not None:
                self.skimmer.reset()
            self.ui.info(N_("end of the recording; back to the radio audio"))
            self.ui.status_changed()

    def stop_playback(self, quiet: bool = False) -> None:
        if self._playback_task is not None:
            self._playback_task.cancel()
            self._playback_task = None
        if self.playback is not None:
            self.playback = None
            self.decoder.reset(self.radio.state.pitch)
            if self.skimmer is not None:
                self.skimmer.reset()
            if not quiet:
                self.ui.info(N_("playback stopped; back to the radio audio"))
            self.ui.status_changed()

    async def _cmd_channels(self, args: list[str]) -> None:
        listing = self.list_channels()
        for c in listing["session"]:
            s = c["stats"]
            self.ui.info(N_("{text}"), text=(
                f"{'*' if c['unsaved'] else ' '}{c['id']:3d} {c['label']:14s} "
                f"{bands.format_frequency(c['frequency'])}  {s['words']:4d} w  "
                f"{s['recognized_pct']:5.1f}% ok  {s['noise_pct']:5.1f}% noise"))
        if listing["saved"]:
            self.ui.info(N_("archived channels: {names}"),
                         names=", ".join(c["name"] for c in listing["saved"][:20]))

    async def _cmd_go(self, args: list[str]) -> None:
        await self.go_to_channel(int(args[0]) - 1)

    async def _cmd_wait(self, args: list[str]) -> None:
        """Pause (for scripted use in --plain mode); without argument waits for TX to finish."""
        if args:
            await asyncio.sleep(float(args[0]))
            return
        await asyncio.sleep(0.1)
        while self.transmitting or not self._tx_queue.empty():
            await asyncio.sleep(0.1)

    async def _cmd_quit(self, args: list[str]) -> None:
        # Clients handle /quit themselves (leaving the client); a server is stopped from
        # its own console, so a remote user cannot shut it down for everyone.
        self.quit_event.set()


_ALIASES = {"f": "freq", "b": "band", "h": "help", "?": "help", "q": "quit", "exit": "quit",
            "s": "scan", "n": "next", "p": "prev"}
