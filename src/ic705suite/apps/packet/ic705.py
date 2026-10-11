"""The IC-705 over WiFi as the packet radio ([packet] tnc = "ic705"; Linux).

Direwolf cannot reach the radio's network audio, so the core stands in between:

  receive   the radio's audio (already here for the CW decoder) goes to Direwolf by UDP
            (ADEVICE udp:PORT ...), as 16-bit samples at the radio's sample rate;
  transmit  Direwolf plays into the snd-aloop virtual sound card (plughw:Loopback,0,0);
            the core records the other end (plughw:Loopback,1,0, with arecord) and sends it
            to the radio over the network while it is keyed;
  PTT       Direwolf asks a rigctld for it (PTT RIG 2 127.0.0.1:PORT): the core answers as
            one and keys the radio over CI-V, through its transmit guard.

The radio transmits the network audio in a data mode (FM-D) whose modulation input (DATA1
MOD) is WLAN: the core sets that input the first time it keys, and asks for the data mode
to be chosen (/qrg) rather than changing the operator's mode by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ic705suite.i18n import N_
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import TransportError

log = logging.getLogger(__name__)

UDP_PORT = 7355            # Direwolf's usual port for audio by UDP
RIGCTL_PORT = 4575         # not hamlib's 4532, which a real rigctld may hold
PLAYBACK = "plughw:Loopback,0,0"
CAPTURE = "plughw:Loopback,1,0"
LONGEST_KEY = 30.0         # the watchdog's limit while packet keeps the radio keyed
TAIL = 0.35                # audio still on its way (recording, network, the radio's buffer)
PERIOD = 0.02              # seconds of audio per piece sent to the radio
DATA1_MOD = b"\x1a\x05\x01\x19"     # DATA1 MOD input (IC-705; wfview's IC-705.rig)
MOD_WLAN = 0x03

# What rigctld answers to \dump_state for hamlib's dummy rig (protocol 1), recorded from
# Hamlib 4.7 and cut down to what netrigctl needs to open the rig.
DUMP_STATE = (
    "1\n1\n0\n"
    "150000.000000 1500000000.000000 0x1ff -1 -1 0x17e00007 0x1f\n0 0 0 0 0 0 0\n"
    "150000.000000 1500000000.000000 0x1ff 5000 100000 0x17e00007 0x1f\n0 0 0 0 0 0 0\n"
    "0x1ff 1\n0x1ff 0\n0 0\n"
    "0xc 2400\n0xc 1800\n0xc 3000\n0xc 0\n0x2 500\n0x2 2400\n0x2 50\n0x2 0\n"
    "0x10 300\n0x10 2400\n0x10 50\n0x10 0\n0x1 8000\n0x1 2400\n0x1 10000\n0x20 15000\n"
    "0x20 8000\n0x40 230000\n0 0\n"
    "9990\n9990\n10000\n0\n10 \n10 20 30 \n"
    "0xffffffffffffffff\n0xffffffffffffffff\n0xfffffffff7ffffff\n0xfffeff7083ffffff\n"
    "0xffffffffffffffff\n0xffffffffffffffbf\n"
    "vfo_ops=0x7ffffff\nptt_type=0x1\ntargetable_vfo=0x10c3\nhas_set_vfo=1\nhas_get_vfo=1\n"
    "has_set_freq=1\nhas_get_freq=1\nhas_set_conf=1\nhas_get_conf=1\nhas_power2mW=1\n"
    "has_mW2power=1\nhas_get_ant=1\nhas_set_ant=1\ntimeout=0\nrig_model=1\ndone\n"
)


def loopback_present(cards: Path = Path("/proc/asound/cards")) -> bool:
    try:
        return "Loopback" in cards.read_text()
    except OSError:
        return False


def loopback_help() -> str:
    return ("sudo modprobe snd-aloop; echo snd-aloop | sudo tee "
            "/etc/modules-load.d/snd-aloop.conf")


class Rigctld:
    """Enough of hamlib's rigctld for Direwolf's PTT (and what it asks on connecting)."""

    def __init__(self, port: int, ptt: Callable[[bool], asyncio.Future],
                 frequency: Callable[[], int], keyed: Callable[[], bool]) -> None:
        self.port = port
        self.ptt = ptt
        self.frequency = frequency
        self.keyed = keyed
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", self.port)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
            self.server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                writer.write((await self.answer(line.decode("ascii", "replace").strip()))
                             .encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def answer(self, command: str) -> str:
        name, _sep, rest = command.partition(" ")
        if name in ("T", "\\set_ptt"):
            ok = await self.ptt(rest.strip() not in ("", "0"))
            return "RPRT 0\n" if ok else "RPRT -9\n"     # -9: the rig rejected it
        if name in ("t", "\\get_ptt"):
            return f"{int(self.keyed())}\n"
        if name == "\\dump_state":
            return DUMP_STATE
        if name in ("\\get_powerstat", "\\chk_vfo"):
            return "1\n" if name == "\\get_powerstat" else "0\n"
        if name in ("v", "\\get_vfo"):
            return "VFOA\n"
        if name in ("f", "\\get_freq"):
            return f"{self.frequency()}\n"
        if name in ("l", "\\get_level"):
            return "0\n"
        if name in ("q", "Q", "\\quit"):
            return ""
        return "RPRT -11\n"                                  # -11: not available


class Ic705Port:
    """The IC-705 as Direwolf's radio: its audio both ways and its PTT."""

    def __init__(self, station, config, say: Callable[..., None],
                 on_change: Callable[[], None]) -> None:
        self.station = station
        self.config = config
        self.say = say                     # say(msgid, **args): a line on the monitor
        self.on_change = on_change
        self.rate = station.transport.sample_rate
        self.keyed = False
        self.release_at = 0.0
        self.tx_bytes = 0
        self.refused = ""                  # why the last PTT was refused (its msgid)
        self._sock: socket.socket | None = None
        self._rigctld = Rigctld(RIGCTL_PORT, self.ptt, lambda: station.rig.state.frequency or 0,
                                lambda: self.keyed)
        self._recorder: subprocess.Popen | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._release: asyncio.Task | None = None
        self._mod_set = False
        self._running = False

    # ---- what Direwolf is told ---------------------------------------------------------------

    @property
    def audio(self) -> str:
        return f"udp:{UDP_PORT} {PLAYBACK}"

    @property
    def ptt_line(self) -> str:
        return f"RIG 2 127.0.0.1:{RIGCTL_PORT}"

    def problem(self) -> tuple[str, dict] | None:
        """Why it cannot work here, as (msgid, args); None when it can."""
        if not sys.platform.startswith("linux"):
            return N_("Packet with the IC-705 over WiFi needs Linux (snd-aloop)"), {}
        if not self.station.transport.can_send_audio:
            return N_("This connection to the radio cannot send it audio"), {}
        if not loopback_present():
            return (N_("The snd-aloop virtual sound card is missing; load it once with: "
                       "{command}"), {"command": loopback_help()})
        return None

    # ---- life ---------------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._running = True
        await self._rigctld.start()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.station.add_audio_listener(self._received)
        self._recorder = subprocess.Popen(
            ["arecord", "-q", "-D", CAPTURE, "-f", "S16_LE", "-r", str(self.rate), "-c", "1",
             "-t", "raw", "--buffer-time=60000", "--period-time=20000"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._record, args=(self._recorder,), daemon=True,
                         name="packet-tx-audio").start()

    async def stop(self) -> None:
        self._running = False
        self.station.remove_audio_listener(self._received)
        await self._rigctld.stop()
        if self._release is not None:
            self._release.cancel()
        if self.keyed or self.release_at:
            self.keyed, self.release_at = False, 0.0
            with contextlib.suppress(RigError, TransportError):
                await self.station.rig.unkey()
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            recorder.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                await asyncio.to_thread(recorder.wait, 3)
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def state(self) -> dict:
        return {"keyed": self.keyed, "tx_bytes": self.tx_bytes, "refused": self.refused,
                "rate": self.rate}

    # ---- receive: the radio's audio to Direwolf -----------------------------------------------

    def _received(self, samples: np.ndarray) -> None:
        if self._sock is None:
            return
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        with contextlib.suppress(OSError):
            for start in range(0, len(pcm), 1024):      # small datagrams
                self._sock.sendto(pcm[start:start + 1024], ("127.0.0.1", UDP_PORT))

    # ---- transmit: Direwolf's audio to the radio ----------------------------------------------

    def _record(self, recorder: subprocess.Popen) -> None:
        size = int(self.rate * PERIOD) * 2
        while self._running:
            piece = recorder.stdout.read(size)
            if not piece:
                return
            loop = self._loop
            if loop is not None and not loop.is_closed():
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(self._to_radio, piece)

    def _to_radio(self, piece: bytes) -> None:
        if not (self.keyed or time.monotonic() < self.release_at):
            return
        with contextlib.suppress(TransportError):
            self.station.transport.send_audio(piece)
            self.tx_bytes += len(piece)

    # ---- PTT ----------------------------------------------------------------------------------

    async def ptt(self, on: bool) -> bool:
        if not on:
            if self.keyed:
                self.keyed = False
                # Keep sending what is still on its way, then release.
                self.release_at = time.monotonic() + TAIL
                self._release = asyncio.create_task(self._release_later())
                self.on_change()
            return True
        reason = await self._cannot_key()
        if reason is not None:
            msgid, args = reason
            if msgid != self.refused:           # said once, not at every retry
                self.say(msgid, **args)
            self.refused = msgid
            self.on_change()
            return False
        self.refused = ""
        if self._release is not None:
            self._release.cancel()
            self._release = None
        try:
            await self.station.rig.ptt(True, longest=LONGEST_KEY)
        except (RigError, TransportError) as exc:
            self.say(N_("The IC-705 did not key: {error}"), error=str(exc))
            return False
        self.keyed, self.release_at = True, 0.0
        self.on_change()
        return True

    async def _release_later(self) -> None:
        await asyncio.sleep(TAIL)
        self.release_at = 0.0
        with contextlib.suppress(RigError, TransportError):
            await self.station.rig.unkey()
        self.on_change()

    async def _cannot_key(self) -> tuple[str, dict] | None:
        if not self.config.tx_enabled:
            return N_("Packet cannot transmit: transmission is off ([rig] tx_enabled, "
                      "--tx)"), {}
        if not self.station.connected:
            return N_("Packet cannot transmit: the IC-705 is not connected"), {}
        rig = self.station.rig
        try:
            data_mode, _filter = await rig.read_data_mode()
            if not data_mode:
                return N_("Packet cannot transmit: the IC-705 is not in a data mode (FM-D); "
                          "/qrg FREQUENCY tunes it for packet"), {}
            if not self._mod_set:
                await rig.transact(DATA1_MOD + bytes([MOD_WLAN]))
                self._mod_set = True
        except (RigError, TransportError) as exc:
            return N_("Packet cannot transmit: the IC-705 does not answer ({error})"), \
                {"error": str(exc)}
        return None

    async def qrg(self, frequency: int) -> None:
        """Tune for packet: the frequency, FM with the data mode on, its input from WLAN."""
        rig = self.station.rig
        await rig.set_frequency(frequency)
        await rig.set_mode("FM")
        await rig.set_data_mode(1, 1)
        await rig.transact(DATA1_MOD + bytes([MOD_WLAN]))
        self._mod_set = True
