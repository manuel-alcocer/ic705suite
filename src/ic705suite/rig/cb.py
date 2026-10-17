"""The 27 MHz Citizens Band: its forty channels (CEPT and FCC alike, 23 between 22 and 24)."""

from __future__ import annotations

CHANNELS: tuple[int, ...] = (
    26_965_000, 26_975_000, 26_985_000, 27_005_000, 27_015_000,
    27_025_000, 27_035_000, 27_055_000, 27_065_000, 27_075_000,
    27_085_000, 27_105_000, 27_115_000, 27_125_000, 27_135_000,
    27_155_000, 27_165_000, 27_175_000, 27_185_000, 27_205_000,
    27_215_000, 27_225_000, 27_255_000, 27_235_000, 27_245_000,
    27_265_000, 27_275_000, 27_285_000, 27_295_000, 27_305_000,
    27_315_000, 27_325_000, 27_335_000, 27_345_000, 27_355_000,
    27_365_000, 27_375_000, 27_385_000, 27_395_000, 27_405_000,
)
# The eleven metres the dial may roam in CB mode (the forty channels and the ones beside).
START, END = 26_565_000, 27_995_000
CALLING_CHANNEL = 19
DEFAULT_MODE = "FM"
MODES = ("FM", "AM", "USB", "LSB")


def channel_frequency(channel: int) -> int:
    """Channel 1 to 40 -> Hz."""
    return CHANNELS[channel - 1]


def channel_for(frequency: int | None) -> int | None:
    """The channel the dial is on, or None between channels (and off the band)."""
    return CHANNELS.index(frequency) + 1 if frequency in CHANNELS else None


def in_cb(frequency: int | None) -> bool:
    return frequency is not None and START <= frequency <= END
