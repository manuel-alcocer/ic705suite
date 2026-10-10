"""Front-panel controls and meters of Icom radios over CI-V.

Command codes from the Icom "IC-705 CI-V Reference Guide" (A7560-8EX-4b), cross-checked
with wfview and hamlib. Each control pairs a generic description (rig.controls) with a
codec: the command bytes, how to read the value from the reply and how to encode a new one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ic705suite.i18n import N_
from ic705suite.rig.base import Meter
from ic705suite.rig.controls import CHOICE, LEVEL, OFFSET, TOGGLE, Control
from ic705suite.rig.icom import civ

# ---- value codecs ------------------------------------------------------------------------


def _level_to(lo: float, hi: float, step: float = 1) -> tuple[Callable, Callable]:
    """0..255 BCD level <-> lo..hi (rounded to `step`)."""
    def decode(data: bytes) -> float:
        value = lo + civ.decode_level(data[:2]) * (hi - lo) / 255
        return round(round(value / step) * step, 2)

    def encode(value: float) -> bytes:
        return civ.encode_level(round((float(value) - lo) * 255 / (hi - lo)))
    return decode, encode


def _byte_choice(codes: dict[str, int], bcd: bool = False) -> tuple[Callable, Callable]:
    by_code = {v: k for k, v in codes.items()}

    def decode(data: bytes) -> str | None:
        raw = civ.decode_bcd_byte(data[0]) if bcd else data[0]
        return by_code.get(raw)

    def encode(value: str) -> bytes:
        code = codes[value]
        return bytes([civ.encode_bcd_byte(code) if bcd else code])
    return decode, encode


def _toggle() -> tuple[Callable, Callable]:
    return (lambda data: bool(data[0])), (lambda value: bytes([1 if value else 0]))


def _decode_rit(data: bytes) -> int:
    hz = civ.decode_bcd_byte(data[0]) + 100 * civ.decode_bcd_byte(data[1])
    return -hz if len(data) > 2 and data[2] else hz


def _encode_rit(value: float) -> bytes:
    hz = min(9999, abs(round(float(value))))
    return bytes([civ.encode_bcd_byte(hz % 100), civ.encode_bcd_byte(hz // 100),
                  1 if value < 0 else 0])


def _decode_ref(data: bytes) -> float:
    centi = civ.decode_level(data[:2])       # |dB| * 100 as 4 BCD digits
    return -centi / 100 if len(data) > 2 and data[2] else centi / 100


def _encode_ref(value: float) -> bytes:
    value = max(-20.0, min(20.0, round(float(value) * 2) / 2))
    centi = round(abs(value) * 100)
    digits = f"{centi:04d}"
    return bytes([(int(digits[0]) << 4) | int(digits[1]), (int(digits[2]) << 4) | int(digits[3]),
                  1 if value < 0 else 0])


SPANS = (2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000)   # half spans, Hz


def _decode_span(data: bytes) -> str:
    return str(civ.decode_frequency(data[:5]))


def _encode_span(value: str) -> bytes:
    return civ.encode_frequency(int(value))


@dataclass(frozen=True)
class IcomControl:
    control: Control
    command: bytes                       # read: send this; set: this + encoded value
    decode: Callable[[bytes], object]    # data after the command bytes -> value
    encode: Callable[[object], bytes]
    arms_tx: object = None               # value that lets the radio transmit by itself


def _level(cid, label, group, cmd, help_text="", lo=0.0, hi=100.0, step=1.0, unit="%",
           kind=LEVEL) -> IcomControl:
    decode, encode = _level_to(lo, hi, step)
    return IcomControl(Control(cid, label, group, kind, minimum=lo, maximum=hi, step=step,
                               unit=unit, help=help_text), cmd, decode, encode)


def _switch(cid, label, group, cmd, help_text="", arms_tx=None) -> IcomControl:
    decode, encode = _toggle()
    return IcomControl(Control(cid, label, group, TOGGLE, help=help_text), cmd, decode, encode,
                       arms_tx)


def _choice(cid, label, group, cmd, options: list[tuple[str, str, int]], help_text="",
            bcd=False, arms_tx=None) -> IcomControl:
    decode, encode = _byte_choice({key: code for key, _, code in options}, bcd)
    return IcomControl(Control(cid, label, group, CHOICE,
                               choices=tuple((key, text) for key, text, _ in options),
                               help=help_text), cmd, decode, encode, arms_tx)


# The PBT knobs: 0..255 with 128 in the middle, shown as -100..+100 %.
_pbt_decode, _pbt_encode = _level_to(-100.4, 100.4, 1)

IC705_CONTROLS: tuple[IcomControl, ...] = (
    # ---- receive
    _level("af", N_("AF"), "rx", b"\x14\x01", N_("Audio volume")),
    _level("rf", N_("RF"), "rx", b"\x14\x02", N_("RF gain")),
    _level("sql", N_("SQL"), "rx", b"\x14\x03", N_("Squelch")),
    _choice("preamp", N_("P.AMP"), "rx", b"\x16\x02",
            [("off", N_("off"), 0), ("p1", "P.AMP1", 1), ("p2", "P.AMP2", 2)],
            N_("Preamplifier (on 144/430 MHz P.AMP1 means on)")),
    _choice("att", N_("ATT"), "rx", b"\x11", [("off", N_("off"), 0x00), ("20", "20 dB", 0x20)],
            N_("Attenuator (HF and 50 MHz)")),
    _choice("agc", N_("AGC"), "rx", b"\x16\x12",
            [("fast", N_("fast"), 1), ("mid", N_("mid"), 2), ("slow", N_("slow"), 3)],
            N_("AGC speed")),
    # ---- DSP
    _switch("nb", N_("NB"), "dsp", b"\x16\x22", N_("Noise blanker")),
    _level("nb_level", N_("NB lvl"), "dsp", b"\x14\x12", N_("Noise blanker level")),
    _switch("nr", N_("NR"), "dsp", b"\x16\x40", N_("Noise reduction")),
    _level("nr_level", N_("NR lvl"), "dsp", b"\x14\x06", N_("Noise reduction level")),
    _switch("anf", N_("ANF"), "dsp", b"\x16\x41", N_("Automatic notch filter")),
    _switch("mnf", N_("MNF"), "dsp", b"\x16\x48", N_("Manual notch filter")),
    _level("notch", N_("Notch"), "dsp", b"\x14\x0d", N_("Manual notch position")),
    _choice("notch_width", N_("N.wid"), "dsp", b"\x16\x57",
            [("wide", N_("wide"), 0), ("mid", N_("mid"), 1), ("nar", N_("narrow"), 2)],
            N_("Manual notch width")),
    IcomControl(Control("pbt_in", N_("PBT1"), "dsp", OFFSET, minimum=-100, maximum=100,
                        help=N_("Twin PBT inner (narrows the passband from one side)")),
                b"\x14\x07", _pbt_decode, _pbt_encode),
    IcomControl(Control("pbt_out", N_("PBT2"), "dsp", OFFSET, minimum=-100, maximum=100,
                        help=N_("Twin PBT outer (narrows the passband from the other side)")),
                b"\x14\x08", _pbt_decode, _pbt_encode),
    _choice("filter_shape", N_("Shape"), "dsp", b"\x16\x56",
            [("sharp", N_("sharp"), 0), ("soft", N_("soft"), 1)], N_("DSP IF filter shape")),
    _switch("twin_peak", N_("TPF"), "dsp", b"\x16\x4f", N_("Twin peak filter (RTTY)")),
    # ---- transmit (settings only: none of them keys the radio)
    _level("power", N_("PWR"), "tx", b"\x14\x0a", N_("RF power")),
    _level("mic", N_("MIC"), "tx", b"\x14\x0b", N_("Microphone gain")),
    _switch("comp", N_("COMP"), "tx", b"\x16\x44", N_("Speech compressor")),
    _level("comp_level", N_("C.lvl"), "tx", b"\x14\x0e", N_("Compressor level (0-10)"),
           lo=0, hi=10, step=1, unit=""),
    _choice("tx_width", N_("TBW"), "tx", b"\x16\x58",
            [("wide", N_("wide"), 0), ("mid", N_("mid"), 1), ("nar", N_("narrow"), 2)],
            N_("SSB transmit bandwidth")),
    _switch("vox", N_("VOX"), "tx", b"\x16\x46",
            N_("VOX (refused in receive-only mode: the voice would key the radio)"), arms_tx=True),
    _level("vox_gain", N_("VOX g"), "tx", b"\x14\x16", N_("VOX gain")),
    _level("anti_vox", N_("A-VOX"), "tx", b"\x14\x17", N_("Anti-VOX gain")),
    _switch("monitor", N_("MON"), "tx", b"\x16\x45", N_("Transmit monitor")),
    _level("monitor_gain", N_("MON g"), "tx", b"\x14\x15", N_("Monitor gain")),
    # ---- CW
    _level("key_speed", N_("KEY"), "cw", b"\x14\x0c", N_("Keyer speed"), lo=6, hi=48, step=1,
           unit="wpm"),
    _level("pitch", N_("PITCH"), "cw", b"\x14\x09", N_("CW pitch"), lo=300, hi=900, step=5,
           unit="Hz"),
    _choice("break_in", N_("BK-IN"), "cw", b"\x16\x47",
            [("off", N_("off"), 0), ("semi", N_("semi"), 1), ("full", N_("full"), 2)],
            N_("Break-in (refused in receive-only mode: the key would transmit)"),
            arms_tx=("semi", "full")),
    _level("bk_delay", N_("DELAY"), "cw", b"\x14\x0f", N_("Break-in delay (dots)"),
           lo=2.0, hi=13.0, step=0.1, unit="d"),
    # ---- VFO and receiver
    _switch("split", N_("SPLIT"), "vfo", b"\x0f", N_("Split operation")),
    _switch("rit", N_("RIT"), "vfo", b"\x21\x01", N_("Receive incremental tuning")),
    IcomControl(Control("rit_offset", N_("RIT Hz"), "vfo", OFFSET, minimum=-9999, maximum=9999,
                        step=10, unit="Hz", help=N_("RIT offset")),
                b"\x21\x00", _decode_rit, _encode_rit),
    _switch("dtx", N_("XIT"), "vfo", b"\x21\x02", N_("Transmit frequency offset")),
    _choice("tuning_step", N_("TS"), "vfo", b"\x10",
            [("off", "10 Hz", 0), ("100", "100 Hz", 1), ("500", "500 Hz", 2), ("1k", "1 kHz", 3),
             ("5k", "5 kHz", 4), ("6.25k", "6.25 kHz", 5), ("8.33k", "8.33 kHz", 6),
             ("9k", "9 kHz", 7), ("10k", "10 kHz", 8), ("12.5k", "12.5 kHz", 9),
             ("20k", "20 kHz", 10), ("25k", "25 kHz", 11), ("50k", "50 kHz", 12),
             ("100k", "100 kHz", 13)], N_("Tuning step of the radio's dial"), bcd=True),
    _switch("dial_lock", N_("LOCK"), "vfo", b"\x16\x50", N_("Dial lock")),
    # ---- scope
    _choice("scope_mode", N_("MODE"), "scope", b"\x27\x14\x00",
            [("center", N_("centre"), 0), ("fixed", N_("fixed"), 1),
             ("scroll_c", N_("scroll-C"), 2), ("scroll_f", N_("scroll-F"), 3)],
            N_("Scope mode: centred on the dial or fixed edges")),
    IcomControl(Control("scope_span", N_("SPAN"), "scope", CHOICE,
                        choices=tuple((str(s), f"±{s / 1000:g} kHz") for s in SPANS),
                        help=N_("Scope span (centre mode)")),
                b"\x27\x15\x00", _decode_span, _encode_span),
    IcomControl(Control("scope_ref", N_("REF"), "scope", OFFSET, minimum=-20, maximum=20,
                        step=0.5, unit=" dB", help=N_("Scope reference level")),
                b"\x27\x19\x00", _decode_ref, _encode_ref),
    _choice("scope_speed", N_("SPEED"), "scope", b"\x27\x1a\x00",
            [("fast", N_("fast"), 0), ("mid", N_("mid"), 1), ("slow", N_("slow"), 2)],
            N_("Scope sweep speed")),
    _choice("scope_edge", N_("EDGE"), "scope", b"\x27\x16\x00",
            [("1", "1", 1), ("2", "2", 2), ("3", "3", 3), ("4", "4", 4)],
            N_("Fixed edges set (fixed mode)")),
    _switch("scope_hold", N_("HOLD"), "scope", b"\x27\x17\x00", N_("Freeze the scope")),
    _choice("scope_vbw", N_("VBW"), "scope", b"\x27\x1d\x00",
            [("nar", N_("narrow"), 0), ("wide", N_("wide"), 1)], N_("Video bandwidth")),
)

# Controls needing the rig's own logic (mode, filter, data mode, VFO): handled in IcomRig.
SPECIAL_CONTROLS = (
    Control("filter", N_("FIL"), "rx", CHOICE,
            choices=(("1", "FIL1"), ("2", "FIL2"), ("3", "FIL3")), help=N_("IF filter preset")),
    Control("filter_width", N_("WIDTH"), "rx", LEVEL, minimum=50, maximum=3600, step=50,
            unit="Hz", help=N_("IF filter width")),
    Control("data_mode", N_("DATA"), "vfo", TOGGLE, help=N_("Data mode (USB-D, LSB-D...)")),
    Control("vfo", N_("VFO"), "vfo", CHOICE, choices=(("A", "A"), ("B", "B")),
            help=N_("VFO A or B (A=B: /raw 07 a0, swap: /raw 07 b0)")),
)


def _interpolate(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


@dataclass(frozen=True)
class IcomMeter:
    meter: Meter
    command: bytes
    points: tuple[tuple[float, float], ...]

    def value(self, raw: int) -> float:
        return round(_interpolate(list(self.points), raw), 2)


IC705_METERS: tuple[IcomMeter, ...] = (
    IcomMeter(Meter("s", "S", "", 241), b"\x15\x02", ((0, 0), (241, 241))),
    IcomMeter(Meter("po", N_("Po"), "W", 10, tx_only=True), b"\x15\x11",
              ((0, 0), (21, 0.5), (43, 1), (65, 1.5), (83, 2), (95, 2.5), (105, 3), (114, 3.5),
               (124, 4), (143, 5), (183, 7.5), (213, 10), (255, 12))),
    IcomMeter(Meter("swr", "SWR", "", 3, tx_only=True), b"\x15\x12",
              ((0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (240, 6.0))),
    IcomMeter(Meter("alc", "ALC", "", 1, tx_only=True), b"\x15\x13", ((0, 0), (120, 1), (255, 2.1))),
    IcomMeter(Meter("comp", "COMP", "dB", 25.5, tx_only=True), b"\x15\x14",
              ((0, 0), (130, 15), (210, 25.5))),
    IcomMeter(Meter("vd", "Vd", "V", 16), b"\x15\x15", ((0, 0), (75, 5), (241, 16))),
    IcomMeter(Meter("id", "Id", "A", 4), b"\x15\x16", ((0, 0), (121, 2), (241, 4))),
)

# Scope data (27 00): amplitudes 0..160, 475 points per sweep on the IC-705.
SCOPE_AMPLITUDE_MAX = 160
