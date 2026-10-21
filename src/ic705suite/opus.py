"""Opus for the phone's voice (station.remote): the system's libopus through ctypes, mono
16-bit PCM in and out, packets of 20 ms. Opus works at 8, 16, 24 or 48 kHz, the radio's own
rates, and a packet decodes at any of them whatever it was encoded at: the radio's side
encodes and decodes at the radio's rate, the browser's at its own.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools

RATES = (8000, 16000, 24000, 48000)
FRAME = 0.020                 # seconds per packet
LONGEST = 0.120               # seconds a packet may hold, the most
MAX_PACKET = 1500
APPLICATION_VOIP = 2048
SET_BITRATE = 4002            # OPUS_SET_BITRATE_REQUEST
BITRATE = 24000               # bits per second: voice from the radio, plenty over 4G


class OpusError(Exception):
    pass


@functools.cache
def library() -> ctypes.CDLL:
    name = ctypes.util.find_library("opus")
    if name is None:
        raise OpusError("libopus not found (apt install libopus0)")
    lib = ctypes.CDLL(name)
    lib.opus_encoder_create.restype = ctypes.c_void_p
    lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int,
                                        ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.opus_encode.restype = ctypes.c_int32
    lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                ctypes.c_char_p, ctypes.c_int32]
    lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_decoder_create.restype = ctypes.c_void_p
    lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int)]
    lib.opus_decode.restype = ctypes.c_int
    lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    return lib


def available() -> bool:
    try:
        library()
    except (OpusError, OSError):
        return False
    return True


def _check_rate(rate: int) -> None:
    if rate not in RATES:
        raise OpusError(f"Opus needs 8000, 16000, 24000 or 48000 Hz, not {rate}")


class Encoder:
    """PCM in pieces of any length; whole 20 ms packets out."""

    def __init__(self, rate: int, bitrate: int = BITRATE) -> None:
        _check_rate(rate)
        lib = library()
        error = ctypes.c_int()
        self._state = lib.opus_encoder_create(rate, 1, APPLICATION_VOIP, ctypes.byref(error))
        if error.value != 0 or not self._state:
            raise OpusError(f"opus_encoder_create failed ({error.value})")
        lib.opus_encoder_ctl(ctypes.c_void_p(self._state), ctypes.c_int(SET_BITRATE),
                             ctypes.c_int32(bitrate))
        self.frame = int(rate * FRAME)              # samples per packet
        self._pending = b""
        self._out = ctypes.create_string_buffer(MAX_PACKET)

    def encode(self, pcm: bytes) -> list[bytes]:
        self._pending += pcm
        size = self.frame * 2
        packets = []
        while len(self._pending) >= size:
            chunk, self._pending = self._pending[:size], self._pending[size:]
            n = library().opus_encode(self._state, chunk, self.frame, self._out, MAX_PACKET)
            if n < 0:
                raise OpusError(f"opus_encode failed ({n})")
            packets.append(self._out.raw[:n])
        return packets

    def close(self) -> None:
        if self._state:
            library().opus_encoder_destroy(self._state)
            self._state = None

    __del__ = close


class Decoder:
    """Packets in, PCM out; a packet that will not decode gives nothing."""

    def __init__(self, rate: int) -> None:
        _check_rate(rate)
        error = ctypes.c_int()
        self._state = library().opus_decoder_create(rate, 1, ctypes.byref(error))
        if error.value != 0 or not self._state:
            raise OpusError(f"opus_decoder_create failed ({error.value})")
        self._most = int(rate * LONGEST)
        self._out = ctypes.create_string_buffer(self._most * 2)

    def decode(self, packet: bytes) -> bytes:
        n = library().opus_decode(self._state, packet, len(packet), self._out, self._most, 0)
        return self._out.raw[:n * 2] if n > 0 else b""

    def close(self) -> None:
        if self._state:
            library().opus_decoder_destroy(self._state)
            self._state = None

    __del__ = close
