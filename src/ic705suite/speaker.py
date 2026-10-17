"""The radio's receive audio on this computer's speakers, and its microphone for the radio.

The audio comes in bursts (the radio's packets, the network's jitter) and the sound card
takes it at its own steady pace, from a thread of its own: a jitter buffer between them
waits for some audio before it starts playing, plays silence when it runs out (and waits
again), and drops the oldest audio when too much piles up, so the delay stays short.

The microphone goes the other way, at the radio's rate: captured in blocks, each handed
to the asyncio loop as 16-bit PCM for the core.

PortAudio (the sounddevice package) plays and captures: on Windows and macOS its wheels
bring the library, on Linux it is the system's (portaudio). Without it the speaker and the
microphone cannot be used, and the rest of the client works as ever.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

PREFILL = 0.15     # seconds of audio waited for before playing (and after running out)
MAXIMUM = 0.6      # seconds buffered at most: older audio is dropped beyond this


class JitterBuffer:
    """Samples pushed in bursts, pulled at a steady pace (from another thread)."""

    def __init__(self, rate: int, prefill: float = PREFILL, maximum: float = MAXIMUM) -> None:
        self.prefill = max(1, int(rate * prefill))
        self.maximum = max(self.prefill * 2, int(rate * maximum))
        self._buf = np.zeros(0, np.float32)
        self._lock = threading.Lock()
        self._playing = False
        self.underruns = 0

    def __len__(self) -> int:
        return len(self._buf)

    def push(self, samples: np.ndarray) -> None:
        with self._lock:
            buf = np.concatenate((self._buf, samples.astype(np.float32, copy=False)))
            if len(buf) > self.maximum:          # too far behind: back to the prefill
                buf = buf[len(buf) - self.prefill:]
            self._buf = buf

    def pull(self, count: int) -> np.ndarray:
        """`count` samples to play: silence while it fills up."""
        out = np.zeros(count, np.float32)
        with self._lock:
            if not self._playing:
                if len(self._buf) < self.prefill:
                    return out
                self._playing = True
            n = min(count, len(self._buf))
            out[:n] = self._buf[:n]
            self._buf = self._buf[n:]
            if n < count:                        # ran out: wait for the prefill again
                self._playing = False
                self.underruns += 1
        return out


class Resampler:
    """Linear interpolation from one sample rate to another, block after block: the
    radio's 8 kHz for a sound card that only takes 48 kHz (voice needs no better)."""

    def __init__(self, source: int, target: int) -> None:
        self.step = source / target              # input samples per output sample
        self._last = np.zeros(1, np.float32)     # the previous block's last sample
        self._pos = 0.0                          # next output's position, from _last

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        if self.step == 1.0 or not len(samples):
            return samples.astype(np.float32, copy=False)
        data = np.concatenate((self._last, samples.astype(np.float32, copy=False)))
        end = len(data) - 1                      # positions up to the newest sample
        positions = np.arange(self._pos, end, self.step)
        out = np.interp(positions, np.arange(len(data)), data).astype(np.float32)
        if len(positions):
            self._pos = positions[-1] + self.step - end
        else:                                    # none in this block: still ahead
            self._pos -= len(samples)
        self._last = data[-1:]
        return out


class LowPass:
    """A windowed-sinc FIR, block after block: what is above the radio's band goes before
    the microphone's 48 kHz are taken down to 8 kHz (else it folds back into the voice)."""

    def __init__(self, rate: int, cutoff: float, taps: int = 63) -> None:
        n = np.arange(taps) - (taps - 1) / 2
        kernel = np.sinc(2 * cutoff / rate * n) * np.hamming(taps)
        self.kernel = (kernel / kernel.sum()).astype(np.float32)
        self._history = np.zeros(taps - 1, np.float32)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        data = np.concatenate((self._history, samples.astype(np.float32, copy=False)))
        self._history = data[len(data) - len(self._history):]
        return np.convolve(data, self.kernel, mode="valid").astype(np.float32)


def float_to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def pcm_to_float(pcm: bytes) -> np.ndarray:
    """16-bit little-endian PCM to float samples in [-1, 1]."""
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


class SpeakerError(Exception):
    pass


class Speaker:
    """Plays 16-bit PCM at `rate` on the default output device, at `volume` (0..1)."""

    BLOCK = 0.02                                 # seconds the sound card asks for at once

    def __init__(self, rate: int, volume: float = 1.0) -> None:
        self.rate = rate
        self.volume = volume
        self._stream = None
        self._resample: Resampler | None = None
        self.buffer: JitterBuffer | None = None

    def start(self) -> None:
        try:
            import sounddevice
        except (ImportError, OSError) as exc:    # OSError: no PortAudio library
            raise SpeakerError(f"no audio output: {exc}") from None
        try:
            self._stream = self._open(sounddevice, self.rate)
            self._resample = None
        except sounddevice.PortAudioError:
            # The device does not take the radio's rate: its own, resampled here.
            try:
                device_rate = int(sounddevice.query_devices(kind="output")["default_samplerate"])
                self._stream = self._open(sounddevice, device_rate)
                self._resample = Resampler(self.rate, device_rate)
            except (sounddevice.PortAudioError, ValueError) as exc:
                raise SpeakerError(f"cannot open the audio output: {exc}") from None
        self._stream.start()
        log.info("playing the receive audio at %d Hz on %s", self._stream.samplerate,
                 self._stream.device)

    def _open(self, sounddevice, rate: int):
        self.buffer = JitterBuffer(rate)
        return sounddevice.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                        blocksize=int(rate * self.BLOCK),
                                        callback=self._callback)

    def _callback(self, outdata, frames, _time, _status) -> None:
        outdata[:, 0] = self.buffer.pull(frames) * self.volume

    def play(self, pcm: bytes) -> None:
        """Audio as it arrives from the core."""
        if self._stream is None:
            return
        samples = pcm_to_float(pcm)
        if self._resample is not None:
            samples = self._resample(samples)
        self.buffer.push(samples)

    def stop(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                stream.stop()
                stream.close()
            except Exception:                    # the device gone (unplugged)
                log.exception("closing the audio output")

    @property
    def playing(self) -> bool:
        return self._stream is not None


class Microphone:
    """Captures the default input device at `rate` and hands `send` 16-bit PCM blocks, on
    the asyncio loop that started it; `level` is the latest block's peak (0..1)."""

    BLOCK = 0.02

    def __init__(self, rate: int, send: Callable[[bytes], None], gain: float = 1.0) -> None:
        self.rate = rate
        self.send = send
        self.gain = gain
        self.level = 0.0
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._convert: Callable[[np.ndarray], np.ndarray] | None = None

    def start(self) -> None:
        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            raise SpeakerError(f"no audio input: {exc}") from None
        self._loop = asyncio.get_running_loop()
        try:
            self._stream = self._open(sounddevice, self.rate)
            self._convert = None
        except sounddevice.PortAudioError:
            try:
                device_rate = int(sounddevice.query_devices(kind="input")["default_samplerate"])
                self._stream = self._open(sounddevice, device_rate)
            except (sounddevice.PortAudioError, ValueError) as exc:
                raise SpeakerError(f"cannot open the microphone: {exc}") from None
            low_pass = LowPass(device_rate, 0.45 * self.rate)
            resample = Resampler(device_rate, self.rate)
            self._convert = lambda samples: resample(low_pass(samples))
        self._stream.start()
        log.info("microphone at %d Hz on %s", self._stream.samplerate, self._stream.device)

    def _open(self, sounddevice, rate: int):
        return sounddevice.InputStream(samplerate=rate, channels=1, dtype="float32",
                                       blocksize=int(rate * self.BLOCK),
                                       callback=self._callback)

    def _callback(self, indata, _frames, _time, _status) -> None:
        samples = indata[:, 0].copy()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._deliver, samples)
            except RuntimeError:                 # the loop closing
                pass

    def _deliver(self, samples: np.ndarray) -> None:
        if self._stream is None:
            return
        if self._convert is not None:
            samples = self._convert(samples)
        samples = samples * self.gain
        self.level = float(np.abs(samples).max()) if len(samples) else 0.0
        self.send(float_to_pcm(samples))

    def stop(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("closing the microphone")
        self.level = 0.0
