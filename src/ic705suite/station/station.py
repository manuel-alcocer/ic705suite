"""The station: one connection to the rig, shared by every application of the suite.

It connects (waiting out a stale session on the radio), reconnects when the link drops,
polls the rig's state, hands the receive audio to whoever listens and runs the commands
that belong to the rig itself (tune, band, mode, power, filter). Applications (CW, antenna
tuning, the rig view...) attach to it instead of opening their own connection, so they
can all run at the same time against a radio that accepts a single remote client.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import wave
from collections.abc import Awaitable, Callable
from typing import Protocol

import numpy as np

from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.rig import bands
from ic705suite.rig.base import Rig, RigError, smeter_text
from ic705suite.rig.transport import TransportError

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5.0
POLL_INTERVAL = 0.25
# A radio left with an open session (program killed, WiFi dropped) ignores new
# connections until that session expires, which takes up to about a minute.
STALE_SESSION_WAIT = 90.0

AudioListener = Callable[[np.ndarray], None]
TuneListener = Callable[[int | None, int], None]       # (old, new) dial frequency
ConnectListener = Callable[[], Awaitable[None]]

RIG_HELP = [
    N_('/f <freq>               tune (7.030 = MHz, 7030.5 = kHz, 7030500 = Hz)'),
    N_('/b <band>               go to a band (160 80 60 40 30 20 17 15 12 10 6 2 70cm)'),
    N_('/up [Hz] /down [Hz]     move the dial (default 100 Hz)   PgUp/PgDn = +/-100 Hz'),
    N_('/mode <mode>            operating mode (USB LSB CW CW-R AM FM RTTY DV...)'),
    N_('/power <%>              RF power                 /filter <Hz> IF filter width'),
    N_('/raw <hex>              send a raw CI-V command body, e.g. /raw 03'),
]
RIG_COMMANDS = {"f": "freq", "freq": "freq", "b": "band", "band": "band", "up": "up",
                "down": "down", "mode": "mode", "power": "power", "filter": "filter", "raw": "raw"}


class UiSink(Protocol):
    """Receives messages for the operator: an English template marked with N_() plus its
    arguments, so each client can translate them (see ic705suite.i18n)."""

    def info(self, msgid: str, **args: object) -> None: ...
    def error(self, msgid: str, **args: object) -> None: ...
    def status_changed(self) -> None: ...


class Station:
    def __init__(self, rig: Rig, config: Config, ui: UiSink) -> None:
        self.rig = rig
        self.config = config
        self.ui = ui
        rig.tx_enabled = config.tx_enabled
        rig.on_state = ui.status_changed
        self.connected = False
        self.paused: set[str] = set()   # reasons to stop polling (a scan owns the rig)
        self._audio: list[AudioListener] = []
        self._tune: list[TuneListener] = []
        self._on_connect: list[ConnectListener] = []
        self._recorder: wave.Wave_write | None = None
        self._sidecar = None
        self._recorded = 0
        self._tasks: list[asyncio.Task] = []
        self._reconnect_task: asyncio.Task | None = None
        rig.transport.on_audio = self._on_audio
        rig.transport.on_disconnect = self._on_disconnect

    @property
    def transport(self):
        return self.rig.transport

    @property
    def model(self):
        return self.rig.model

    @property
    def state(self):
        return self.rig.state

    @property
    def sample_rate(self) -> int:
        return self.rig.transport.sample_rate

    # ---- listeners ----------------------------------------------------------------------

    def add_audio_listener(self, callback: AudioListener) -> None:
        self._audio.append(callback)

    def add_tune_listener(self, callback: TuneListener) -> None:
        self._tune.append(callback)

    def add_connect_listener(self, callback: ConnectListener) -> None:
        """Called after every (re)connection, once the rig state has been read."""
        self._on_connect.append(callback)

    def _notify_tune(self, old: int | None, new: int) -> None:
        for callback in list(self._tune):
            try:
                callback(old, new)
            except Exception:
                log.exception("tune listener failed")

    # ---- lifecycle ----------------------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STALE_SESSION_WAIT
        warned = False
        while True:
            try:
                await self._connect()
                break
            except RigError as exc:
                # Right after a stale session expires the radio may open the streams but
                # ignore our commands; a clean logout and a new login fixes it.
                self.connected = False
                await self.rig.close()
                if loop.time() > deadline:
                    raise
                log.warning("rig not answering after connect (%s), reconnecting", exc)
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
                await self.rig.close()
        self._tasks = [asyncio.create_task(self._poll_loop())]

    async def stop(self) -> None:
        for task in [*self._tasks, self._reconnect_task]:
            if task is not None:
                task.cancel()
        await self.rig.close()
        self.connected = False
        self.stop_recording()

    async def _connect(self) -> None:
        self.ui.info(N_("connecting ({transport})..."), transport=self.transport.name)
        await self.rig.connect()
        self.connected = True
        await self._read_state()
        for callback in list(self._on_connect):
            await callback()
        self.ui.info(N_("connected to {radio}"), radio=self.rig.name or "radio")
        self.ui.status_changed()

    async def _read_state(self) -> None:
        state = self.rig.state
        state.frequency = await self.rig.read_frequency()
        state.mode = await self.rig.read_mode()
        with contextlib.suppress(RigError):
            await self.rig.read_power()
        if not self.config.tx_enabled:
            self.ui.info(N_("receive-only mode: transmission disabled (--tx to enable)"))

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
            await self.rig.close()
            await asyncio.sleep(RECONNECT_DELAY)
            try:
                await self._connect()
            except (TransportError, RigError, OSError) as exc:
                self.connected = False
                self.ui.error(N_("reconnect failed: {error}"), error=str(exc))

    # ---- audio --------------------------------------------------------------------------

    def record_to(self, path: str) -> None:
        """Save all received audio to a 16-bit mono WAV file plus a frequency sidecar."""
        from ic705suite.apps.cw.playback import SidecarWriter

        self._recorder = wave.open(path, "wb")
        self._recorder.setnchannels(1)
        self._recorder.setsampwidth(2)
        self._recorder.setframerate(self.sample_rate)
        self._sidecar = SidecarWriter(path, self.sample_rate)
        log.info("recording receive audio to %s", path)

    def stop_recording(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None
        if self._sidecar is not None:
            self._sidecar.close()
            self._sidecar = None

    def _on_audio(self, samples: np.ndarray) -> None:
        if self._recorder is not None:
            self._recorder.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            self._sidecar.note(self._recorded, self.rig.state.frequency)
            self._recorded += len(samples)
        for callback in self._audio:
            callback(samples)

    # ---- polling ------------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            if not self.connected or self.paused:
                continue
            tick += 1
            try:
                self.rig.state.smeter = await self.rig.read_smeter()
                if tick % 4 == 0:
                    freq = await self.rig.read_frequency()
                    old = self.rig.state.frequency
                    if freq != old:
                        log.info("frequency changed on the radio: %d Hz", freq)
                        self.rig.state.frequency = freq
                        self._notify_tune(old, freq)
                if tick % 8 == 0:
                    self.rig.state.mode = await self.rig.read_mode()
            except (RigError, TransportError) as exc:
                log.debug("poll: %s", exc)
            self.ui.status_changed()

    # ---- rig commands -------------------------------------------------------------------

    async def tune(self, freq: int) -> None:
        if not self.model.covers(freq):
            raise ValueError(f"{freq} Hz is outside the {self.model.name} coverage")
        old = self.rig.state.frequency
        log.info("tune %d Hz", freq)
        await self.rig.set_frequency(freq)
        self._notify_tune(old, freq)

    def snapshot(self) -> dict:
        """The rig as clients see it (the "rig" event)."""
        st, model = self.rig.state, self.model
        return {
            "type": "rig",
            "connected": self.connected,
            "model": model.id,
            "model_name": model.name,
            "manufacturer": model.manufacturer,
            "features": sorted(model.features),
            "transport": self.transport.name,
            "radio": self.rig.name,
            "frequency": st.frequency,
            "band": (bands.band_for(st.frequency).name
                     if st.frequency and bands.band_for(st.frequency) else None),
            "mode": st.mode,
            "smeter": st.smeter,
            "smeter_text": smeter_text(st.smeter),
            "power": st.power,
            "max_power_w": model.max_power_w,
            "tx_enabled": self.config.tx_enabled,
            "transmitting": st.transmitting,
            "recording": self._recorder is not None,
        }

    async def execute(self, line: str) -> bool:
        """Run a rig command line ("/f 7.030"). False when it is not a rig command."""
        line = line.strip()
        if not line.startswith("/"):
            return False
        name, *args = line[1:].split()
        handler = RIG_COMMANDS.get(name.lower())
        if handler is None:
            return False
        try:
            await getattr(self, f"_cmd_{handler}")(args)
        except (ValueError, IndexError):
            self.ui.error(N_("bad arguments for /{name} (/help)"), name=name)
        except (RigError, TransportError) as exc:
            self.ui.error(N_("{error}"), error=str(exc))
        return True

    async def _cmd_freq(self, args: list[str]) -> None:
        await self.tune(bands.parse_frequency(args[0]))

    async def _cmd_band(self, args: list[str]) -> None:
        band = bands.find_band(args[0])
        if band is None:
            raise ValueError
        await self.tune(band.default)

    async def _cmd_up(self, args: list[str]) -> None:
        await self.tune((self.rig.state.frequency or 0) + (int(args[0]) if args else 100))

    async def _cmd_down(self, args: list[str]) -> None:
        await self.tune((self.rig.state.frequency or 0) - (int(args[0]) if args else 100))

    async def _cmd_mode(self, args: list[str]) -> None:
        mode = args[0].upper()
        if mode not in self.model.modes:
            self.ui.error(N_("{model} has no {mode} mode"), model=self.model.name, mode=mode)
            return
        await self.rig.set_mode(mode)
        self.ui.info(N_("mode {mode}"), mode=mode)

    async def _cmd_power(self, args: list[str]) -> None:
        await self.rig.set_power(int(args[0].rstrip("%")))
        self.ui.info(N_("RF power {percent} %"), percent=self.rig.state.power)

    async def _cmd_filter(self, args: list[str]) -> None:
        await self.rig.set_filter_width(int(args[0]))
        self.ui.info(N_("IF filter {hz} Hz"), hz=await self.rig.read_filter_width())

    async def _cmd_raw(self, args: list[str]) -> None:
        reply = await self.rig.raw(bytes.fromhex("".join(args)))
        self.ui.info(N_("{text}"), text="<- " + reply.hex(" "))
