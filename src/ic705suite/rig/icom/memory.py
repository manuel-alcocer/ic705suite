"""The IC-705's memory channels (CI-V 1A 00): 100 groups of 100 channels, each read and
written whole.

After 1A 00 come the group and the channel (two BCD bytes each) and either FF (an empty
channel) or 115 bytes, laid out as wfview's IC-705.rig says (positions from 1):
  1-2 group, 3-4 channel, 5 split (high nibble) and scan (low), 6-10 frequency, 11 mode,
  12 filter, 13 data mode, 14 duplex (high nibble) and tone mode (low), 15 DV squelch,
  16-18 tone, 19-21 tone squelch, 22 DTCS polarity, 23-24 DTCS code, 25 DV code,
  26-28 duplex offset, 29-36 UR, 37-44 R1, 45-52 R2 (D-STAR), 53-99 the same for the
  split side (B), 100-115 the name.
Frequencies and the offset are little-endian BCD (the offset in hundreds of hertz), the
tones BCD tenths of hertz in their last two bytes. The B side is written as A.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ic705suite.rig.icom import civ

GROUPS = 100
CHANNELS = 100
EMPTY = 0xFF
SIZE = 115               # the bytes of a channel, group and channel included
NAME = 16
DUPLEX = {0: "", 1: "-", 2: "+"}
DUPLEX_CODES = {v: k for k, v in DUPLEX.items()}
TONE_MODES = {0: "", 1: "tone", 2: "tsql"}
TONE_MODE_CODES = {v: k for k, v in TONE_MODES.items()}


@dataclass
class Memory:
    frequency: int
    mode: int = 0x01             # the radio's code (rig.icom.rig.MODES): USB
    filter: int = 1
    data: int = 0
    duplex: str = ""             # "", "-" or "+"
    offset: int = 0              # Hz
    tone_mode: str = ""          # "", "tone" (on transmit) or "tsql" (both ways)
    tone: float = 88.5           # Hz
    tsql: float = 88.5
    name: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Memory:
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


def bcd(value: int, size: int) -> bytes:
    """`value` as `size` big-endian BCD bytes (group, channel, tones, DTCS)."""
    digits = f"{value:0{size * 2}d}"[-size * 2:]
    return bytes(int(digits[i:i + 2], 16) for i in range(0, size * 2, 2))


def from_bcd(data: bytes) -> int:
    return int(data.hex() or "0")


def address(group: int, channel: int) -> bytes:
    if not (0 <= group < GROUPS and 0 <= channel < CHANNELS):
        raise ValueError(f"no memory {group}/{channel}")
    return bcd(group, 2) + bcd(channel, 2)


def _offset(data: bytes) -> int:
    """Three bytes, little-endian BCD in hundreds of hertz."""
    return sum(civ.decode_bcd_byte(b) * 100**i for i, b in enumerate(data[:3])) * 100


def _offset_bytes(hz: int) -> bytes:
    units = max(0, min(999_999, hz // 100))
    return bytes(civ.encode_bcd_byte(units // 100**i % 100) for i in range(3))


def _tone(data: bytes) -> float:
    return from_bcd(data[1:3]) / 10


def _tone_bytes(hz: float) -> bytes:
    return b"\x00" + bcd(round(hz * 10), 2)


def decode(payload: bytes) -> tuple[int, int, Memory | None]:
    """The reply after 1A 00: (group, channel, its memory, None when it is empty)."""
    if len(payload) < 5:
        raise ValueError("a memory reply too short")
    group, channel = from_bcd(payload[0:2]), from_bcd(payload[2:4])
    if payload[4] == EMPTY or len(payload) < SIZE:
        return group, channel, None
    p = payload
    return group, channel, Memory(
        frequency=civ.decode_frequency(p[5:10]), mode=p[10],
        filter=civ.decode_bcd_byte(p[11]), data=civ.decode_bcd_byte(p[12]),
        duplex=DUPLEX.get(p[13] >> 4, ""), tone_mode=TONE_MODES.get(p[13] & 0x0F, ""),
        tone=_tone(p[15:18]), tsql=_tone(p[18:21]), offset=_offset(p[25:28]),
        name=p[99:99 + NAME].decode("ascii", "replace").rstrip())


def encode(group: int, channel: int, memory: Memory | None) -> bytes:
    """What follows 1A 00 to write a channel (memory None: clear it)."""
    if memory is None:
        return address(group, channel) + bytes([EMPTY])
    side = (civ.encode_frequency(memory.frequency) + bytes([
        memory.mode, civ.encode_bcd_byte(memory.filter), civ.encode_bcd_byte(memory.data),
        DUPLEX_CODES.get(memory.duplex, 0) << 4 | TONE_MODE_CODES.get(memory.tone_mode, 0),
        0x00]) + _tone_bytes(memory.tone) + _tone_bytes(memory.tsql)
        + b"\x00" + bcd(23, 2) + b"\x00" + _offset_bytes(memory.offset)
        + b"CQCQCQ  " + b" " * 8 + b" " * 8)
    name = memory.name.encode("ascii", "replace")[:NAME].ljust(NAME)
    payload = address(group, channel) + b"\x00" + side + side + name
    assert len(payload) == SIZE, len(payload)
    return payload
