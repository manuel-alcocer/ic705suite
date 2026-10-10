"""Icom CI-V framing and BCD helpers."""

from __future__ import annotations

from dataclasses import dataclass

PREAMBLE = b"\xfe\xfe"
EOM = 0xFD
CONTROLLER_ADDR = 0xE0
BROADCAST_ADDR = 0x00
OK = 0xFB
NG = 0xFA
IC705_ADDR = 0xA4


@dataclass(frozen=True)
class CivFrame:
    dst: int
    src: int
    body: bytes  # command byte(s) and data, without preamble/addresses/EOM

    @property
    def cmd(self) -> int:
        return self.body[0] if self.body else -1


def build_frame(radio_addr: int, body: bytes, src: int = CONTROLLER_ADDR) -> bytes:
    return PREAMBLE + bytes([radio_addr, src]) + body + bytes([EOM])


class CivParser:
    """Incremental parser: feed raw bytes, get complete frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[CivFrame]:
        self._buf += data
        frames: list[CivFrame] = []
        while True:
            start = self._buf.find(PREAMBLE)
            if start < 0:
                # Keep a trailing 0xFE: it may be the first half of a preamble.
                self._buf = self._buf[-1:] if self._buf[-1:] == b"\xfe" else bytearray()
                return frames
            # Skip any extra 0xFE (the preamble may be longer than two bytes).
            while start + 2 < len(self._buf) and self._buf[start + 2] == 0xFE:
                start += 1
            end = self._buf.find(bytes([EOM]), start + 2)
            if end < 0:
                del self._buf[:start]
                return frames
            raw = bytes(self._buf[start + 2:end])
            del self._buf[:end + 1]
            if len(raw) < 3 or 0xFC in raw:  # too short or bus collision
                continue
            frames.append(CivFrame(dst=raw[0], src=raw[1], body=raw[2:]))


def encode_frequency(hz: int) -> bytes:
    """Frequency in Hz to 5-byte little-endian BCD."""
    if not 0 <= hz < 10**10:
        raise ValueError(f"frequency out of range: {hz}")
    digits = f"{hz:010d}"
    out = bytearray()
    for i in range(5):
        pair = digits[8 - 2 * i:10 - 2 * i]
        out.append((int(pair[0]) << 4) | int(pair[1]))
    return bytes(out)


def decode_frequency(data: bytes) -> int:
    hz = 0
    for i, b in enumerate(data[:5]):
        hz += ((b >> 4) * 10 + (b & 0x0F)) * 100**i
    return hz


def encode_level(value: int) -> bytes:
    """0..255 level to 2-byte big-endian BCD (e.g. 128 -> 01 28)."""
    value = max(0, min(255, int(value)))
    digits = f"{value:04d}"
    return bytes([(int(digits[0]) << 4) | int(digits[1]), (int(digits[2]) << 4) | int(digits[3])])


def decode_level(data: bytes) -> int:
    value = 0
    for b in data[:2]:
        value = value * 100 + (b >> 4) * 10 + (b & 0x0F)
    return value


def encode_bcd_byte(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def decode_bcd_byte(b: int) -> int:
    return (b >> 4) * 10 + (b & 0x0F)
