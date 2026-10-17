"""The station: one connection to the rig, shared by every application of the suite.

It connects (waiting out a stale session on the radio), reconnects when the link drops,
polls the rig's state, hands the receive audio to whoever listens and runs the commands
that belong to the rig itself (tune, band, mode, power, filter). Applications (CW, antenna
tuning, the rig view...) attach to it instead of opening their own connection, so they
can all run at the same time against a radio that accepts a single remote client.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
import wave
from collections.abc import Awaitable, Callable
from typing import Protocol

import numpy as np

from ic705suite.config import Config
from ic705suite.i18n import N_
from ic705suite.rig import bands
from ic705suite.rig.base import Rig, RigError, smeter_text
from ic705suite.rig.transport import TransportError
from ic705suite.ui.sysload import LOAD

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5.0
POLL_INTERVAL = 0.25
# A radio left with an open session (program killed, WiFi dropped) ignores new
# connections until that session expires, which takes up to about a minute.
STALE_SESSION_WAIT = 90.0
# A session can go half dead (seen after reconnecting while the radio still held the old
# one): the radio answers the link's pings, so the link stays up, but CI-V gets no answers
# and the audio stops. Then the station treats it as lost and connects again.
SILENT_CIV = 10.0          # seconds of CI-V without an answer
SILENT_AUDIO = 15.0        # seconds without audio, when audio had been coming

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
    N_('/set <control> <value>  any front-panel control, e.g. /set af 40, /set agc fast'),
]
SCOPE_INTERVAL = 0.1   # at most 10 scope frames a second to the clients
RIG_COMMANDS = {"set": "set", "f": "freq", "freq": "freq", "b": "band", "band": "band", "up": "up",
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
        # While the radio is away, attempts follow one another: say so once, not each time.
        self._told_connecting = False
        self._told_unreachable = False
        rig.tx_enabled = config.tx_enabled
        rig.on_state = ui.status_changed
        self.connected = False
        self.keyed_by: str | None = None   # "voice" while a client's microphone talks
        self.paused: set[str] = set()   # reasons to stop polling (a scan owns the rig)
        self._audio: list[AudioListener] = []
        self._tune: list[TuneListener] = []
        self._on_connect: list[ConnectListener] = []
        self._recorder: wave.Wave_write | None = None
        self._sidecar = None
        self._recorded = 0
        self._tasks: list[asyncio.Task] = []
        self._reconnect_task: asyncio.Task | None = None
        self.unsupported: set[str] = set()   # controls the radio refused to read
        self._control_turn = 0
        self.scope_on = False
        self._scope_until = 0.0          # scope output stays on while viewers keep asking
        self._scope_sent = 0.0
        self.scope_frames = 0
        rig.transport.on_audio = self._on_audio
        rig.transport.on_disconnect = self._on_disconnect
        rig.on_scope = self._on_scope

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

    def remove_audio_listener(self, callback: AudioListener) -> None:
        if callback in self._audio:
            self._audio.remove(callback)

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
                if not self._told_unreachable:
                    self.ui.info(N_("the radio does not answer (switched off, out of WiFi, or "
                                    "still holding a previous session); trying again in the "
                                    "background"))
                    self._told_unreachable = True
                await self.rig.close()
        self._tasks = [asyncio.create_task(self._poll_loop()),
                       asyncio.create_task(self._scope_keeper())]

    async def stop(self) -> None:
        for task in [*self._tasks, self._reconnect_task]:
            if task is not None:
                task.cancel()
        if self.connected and self.scope_on:
            with contextlib.suppress(RigError, TransportError):
                await self.rig.scope_output(False)
        await self.rig.close()
        self.connected = False
        self.stop_recording()

    async def _connect(self) -> None:
        if not self._told_connecting:
            self.ui.info(N_("connecting ({transport})..."), transport=self.transport.name)
            self._told_connecting = True
        await self.rig.connect()
        self.connected = True
        self._told_connecting = self._told_unreachable = False
        self._answered_at = time.monotonic()
        self._audio_at: float | None = None
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
        await self._read_controls()
        if self.scope_on:   # back after a reconnection with viewers waiting
            with contextlib.suppress(RigError):
                await self.rig.scope_output(True)
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
        self._audio_at = time.monotonic()
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
                await self._poll_meters(tick)
                if tick % 2 == 0:
                    await self._poll_next_control()
                self._answered_at = time.monotonic()
            except (RigError, TransportError) as exc:
                log.debug("poll: %s", exc)
            self._check_alive()
            self.ui.status_changed()

    def _check_alive(self) -> None:
        """A session that only looks alive (see SILENT_CIV): lost, and connected again."""
        now = time.monotonic()
        if self.rig.state.transmitting and getattr(self, "_audio_at", None) is not None:
            self._audio_at = now                   # the radio may send none while it is
        if now - getattr(self, "_answered_at", now) > SILENT_CIV:
            self._on_disconnect(f"no answer to CI-V for {SILENT_CIV:.0f} s")
        elif getattr(self, "_audio_at", None) is not None and \
                now - self._audio_at > SILENT_AUDIO:
            self._on_disconnect(f"no audio for {SILENT_AUDIO:.0f} s")

    async def _poll_meters(self, tick: int) -> None:
        meters = self.rig.state.meters
        meters["s"] = self.rig.state.smeter
        # Transmitting is asked to the radio: it may be keyed by the microphone, the key,
        # break-in or VOX, not only by this program. The transmit meters (Po, SWR, ALC...)
        # are read while it is; their last values stay shown afterwards.
        with contextlib.suppress(RigError, TransportError):
            self.rig.state.transmitting = await self.rig.read_transmitting()
        for meter in self.rig.meters():
            if meter.id == "s":
                continue
            due = self.rig.state.transmitting if meter.tx_only else tick % 8 == 4
            if due:
                with contextlib.suppress(RigError):
                    meters[meter.id] = await self.rig.read_meter(meter.id)

    async def _poll_next_control(self) -> None:
        """One control per turn, so knobs turned on the radio show up within seconds."""
        controls = [c for c in self.rig.controls() if c.id not in self.unsupported]
        if not controls:
            return
        control = controls[self._control_turn % len(controls)]
        self._control_turn += 1
        with contextlib.suppress(RigError):
            self.rig.state.controls[control.id] = await self.rig.read_control(control.id)

    async def _read_controls(self) -> None:
        for control in self.rig.controls():
            try:
                self.rig.state.controls[control.id] = await self.rig.read_control(control.id)
            except RigError as exc:
                log.info("control %s not available: %s", control.id, exc)
                self.unsupported.add(control.id)

    # ---- front panel ----------------------------------------------------------------------

    def describe(self) -> dict:
        """Controls and meters of this rig, for the clients (sent in the welcome)."""
        return {"controls": [c.as_dict() for c in self.rig.controls()
                             if c.id not in self.unsupported],
                "meters": [{"id": m.id, "label": m.label, "unit": m.unit, "maximum": m.maximum,
                            "tx_only": m.tx_only} for m in self.rig.meters()]}

    async def set_control(self, control_id: str, value) -> object:
        control = next((c for c in self.rig.controls() if c.id == control_id), None)
        if control is None:
            raise ValueError(f"no control {control_id}")
        if isinstance(value, str):
            value = control.parse(value)
        await self.rig.set_control(control_id, value)
        with contextlib.suppress(RigError):
            value = await self.rig.read_control(control_id)
        self.rig.state.controls[control_id] = value
        self.ui.status_changed()
        return value

    # ---- band scope -----------------------------------------------------------------------

    async def scope_watch(self, seconds: float = 15.0) -> bool:
        """A viewer wants the scope for the next `seconds` (it asks again to keep it)."""
        if not self.model.has("scope"):
            return False
        self._scope_until = asyncio.get_running_loop().time() + seconds
        if not self.scope_on and self.connected:
            await self.rig.scope_output(True)
            self.scope_on = True
        return True

    async def _scope_keeper(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            if self.scope_on and asyncio.get_running_loop().time() > self._scope_until:
                self.scope_on = False
                if self.connected:
                    with contextlib.suppress(RigError, TransportError):
                        await self.rig.scope_output(False)

    def _on_scope(self, frame) -> None:
        self.scope_frames += 1
        loop = asyncio.get_running_loop()
        if loop.time() - self._scope_sent < SCOPE_INTERVAL:
            return
        self._scope_sent = loop.time()
        emit = getattr(self.ui, "emit", None)
        if emit is None:
            return
        data = bytes(min(255, max(0, round(a * 255))) for a in frame.amplitudes)
        emit({"type": "scope", "start": frame.start, "end": frame.end,
              "center": frame.center_mode, "out_of_range": frame.out_of_range,
              "data": base64.b64encode(data).decode("ascii")})

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
            "controls": dict(st.controls),
            "meters": dict(st.meters),
            "scope": self.scope_on,
            "host": LOAD.as_dict(),        # this machine: shown when the client is elsewhere
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

    async def _cmd_set(self, args: list[str]) -> None:
        value = await self.set_control(args[0].lower(), " ".join(args[1:]))
        self.ui.info(N_("{control} = {value}"), control=args[0].lower(), value=str(value))

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
