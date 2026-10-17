"""Voice from a client's microphone: its PTT keys the radio, its audio is modulated; or,
with source "radio", the client's PTT keys the radio and its own microphone talks.

One client talks at a time (the one that keyed). The radio takes the modulation of the
voice modes (SSB, AM, FM) from its DATA OFF MOD input: set to WLAN only while keyed, as it
was when released, because the radio's own microphone is silent while it is WLAN. Never
keyed for long: the release comes when the client lets go, leaves or stops sending audio
(a connection gone quiet), or after LONGEST seconds whatever happens.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from ic705suite.i18n import N_
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import TransportError

log = logging.getLogger(__name__)

DATA_OFF_MOD = b"\x1a\x05\x01\x18"   # the voice modes' MOD input (IC-705; wfview's IC-705.rig)
MOD_MIC = 0x00
MOD_MIC_USB = 0x02
MOD_WLAN = 0x03
VOICE_MODES = ("LSB", "USB", "AM", "FM")
LONGEST = 180.0      # seconds keyed at most (a time-out timer, as on a repeater)
SILENT = 1.0         # seconds without the talker's audio (or, with the radio's microphone,
                     # its PTT said again): its connection is gone, release
TAIL = 0.15          # seconds after letting go before releasing: the audio on its way


class VoiceError(Exception):
    def __init__(self, msgid: str, **args: object) -> None:
        super().__init__(msgid.format(**args) if args else msgid)
        self.msgid, self.args_ = msgid, args


class Voice:
    def __init__(self, station, config, hub) -> None:
        self.station = station
        self.config = config
        self.hub = hub
        self.talker: object | None = None    # who keyed: one client at a time
        self.keyed_at = 0.0
        self.heard_at = 0.0                  # the talker's latest audio
        self._mod_before: int | None = None  # DATA OFF MOD as it was, put back on release
        self.source = "pc"                   # whose microphone talks: "pc" or "radio"
        self._watch: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._released = asyncio.Event()     # no release on its way (set: none pending)
        self._released.set()

    @property
    def keyed(self) -> bool:
        return self.talker is not None

    async def ptt(self, on: bool, talker: object, source: str = "pc") -> dict:
        """Key (or release) for `talker`; an answer for the client: {on} or {error}.
        Said again while keyed, it only tells the talker is still there."""
        if not on:
            await self.release(talker)
            return {"on": False}
        try:
            async with self._lock:
                if self.talker is talker:
                    self.heard_at = time.monotonic()
                    return {"on": True}
                await self._key(talker, source)
        except VoiceError as exc:
            self.hub.error(exc.msgid, **exc.args_)
            return {"error": str(exc), "msgid": exc.msgid, "args": exc.args_}
        return {"on": True}

    async def _key(self, talker: object, source: str = "pc") -> None:
        if not self.config.tx_enabled:
            raise VoiceError(N_("Voice cannot transmit: transmission is off ([rig] tx_enabled, "
                                "--tx)"))
        if self.talker is not None:
            raise VoiceError(N_("Voice cannot transmit: another client is talking"))
        station = self.station
        if not station.connected:
            raise VoiceError(N_("Voice cannot transmit: the radio is not connected"))
        if source != "radio" and not station.transport.can_send_audio:
            raise VoiceError(N_("Voice cannot transmit: this connection cannot send the radio "
                                "audio"))
        rig = station.rig
        # Pressed again during the last release's tail: that release ends first.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._released.wait(), 1.0)
        if rig.state.transmitting:
            raise VoiceError(N_("Voice cannot transmit: the radio is transmitting already"))
        try:
            mode = await rig.read_mode()
            data_mode, _filter = await rig.read_data_mode()
            if mode not in VOICE_MODES or data_mode:
                raise VoiceError(N_("Voice cannot transmit in {mode}: SSB, AM or FM, without "
                                    "DATA"), mode=mode + ("-D" if data_mode else ""))
            frame = await rig.transact(DATA_OFF_MOD, DATA_OFF_MOD)
            body = frame.body                    # the command, then the value
            before = body[len(DATA_OFF_MOD)] if len(body) > len(DATA_OFF_MOD) else None
            # The computer's audio comes over WLAN; the radio's microphone, from MIC.
            wanted = (MOD_MIC, MOD_MIC_USB) if source == "radio" else (MOD_WLAN,)
            if before is not None and before not in wanted:
                await rig.transact(DATA_OFF_MOD + bytes([wanted[0]]))
                self._mod_before = before
            self.talker, self.source = talker, source
            self.keyed_at = self.heard_at = time.monotonic()
            station.keyed_by = "voice"
            await rig.ptt(True, longest=LONGEST + 2)
        except (RigError, TransportError) as exc:
            await self._unkey()
            raise VoiceError(N_("Voice cannot transmit: the radio does not answer ({error})"),
                             error=str(exc)) from None
        log.info("voice: keyed (%s microphone)", source)
        self._watch = asyncio.create_task(self._watchdog())

    def audio(self, talker: object, pcm: bytes) -> None:
        """The talker's microphone (16-bit mono PCM at the transport's rate); anyone else's
        is dropped."""
        if talker is not self.talker or talker is None:
            return
        self.heard_at = time.monotonic()
        with contextlib.suppress(TransportError):
            self.station.transport.send_audio(pcm)

    async def release(self, talker: object, tail: float = TAIL) -> None:
        """Let go: the talker's PTT up, or the talker gone."""
        if talker is not self.talker or talker is None:
            return
        self.talker = None                   # its audio from now on is dropped
        if self._watch is not None and self._watch is not asyncio.current_task():
            self._watch.cancel()
        self._watch = None
        self._released.clear()
        try:
            await asyncio.sleep(tail)
            await self._unkey()
        finally:
            self._released.set()
        log.info("voice: released")

    async def _unkey(self) -> None:
        self.talker = None
        rig = self.station.rig
        with contextlib.suppress(RigError, TransportError):
            await rig.unkey()
        if self._mod_before is not None:
            before, self._mod_before = self._mod_before, None
            try:
                await rig.transact(DATA_OFF_MOD + bytes([before]))
            except (RigError, TransportError) as exc:
                log.error("voice: DATA OFF MOD not put back to %d: %s", before, exc)
        if getattr(self.station, "keyed_by", None) == "voice":
            self.station.keyed_by = None

    async def _watchdog(self) -> None:
        while self.talker is not None:
            await asyncio.sleep(0.2)
            now = time.monotonic()
            if now - self.heard_at > SILENT:
                self.hub.error(N_("Voice released: no audio from the microphone")
                               if self.source == "pc" else
                               N_("Voice released: the client holding the PTT went quiet"))
                await self.release(self.talker, tail=0)
            elif now - self.keyed_at > LONGEST:
                self.hub.error(N_("Voice released: keyed for {seconds:.0f} s, the longest"),
                               seconds=LONGEST)
                await self.release(self.talker, tail=0)

    async def stop(self) -> None:
        if self.talker is not None:
            await self.release(self.talker, tail=0)
