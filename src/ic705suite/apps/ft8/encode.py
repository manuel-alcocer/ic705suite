"""An FT8 message as audio: its 79 tones from WSJT-X's ft8code (the same packing and LDPC as
WSJT-X; a program of its own, which the suite only runs), then the waveform: 8-tone
continuous-phase FSK, 6.25 baud and 6.25 Hz apart, the frequency changes smoothed by a
Gaussian (BT 2), as WSJT-X and ft8_lib make it.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SYMBOLS = 79
BAUD = 6.25                # symbols per second, and Hz between tones
BT = 2.0                   # the Gaussian's bandwidth x symbol time
RATE = 12_000


class EncodeError(Exception):
    pass


def find_ft8code(configured_jt9: str = "") -> str | None:
    """ft8code, beside jt9 (both come with WSJT-X)."""
    found = shutil.which("ft8code")
    if found:
        return found
    if configured_jt9:
        beside = Path(configured_jt9).with_name("ft8code" + (".exe" if sys.platform == "win32"
                                                              else ""))
        if beside.is_file():
            return str(beside)
    from ic705suite.apps.ft8.jt9 import find_jt9

    jt9 = find_jt9()
    if jt9:
        beside = Path(jt9).with_name(Path(jt9).name.replace("jt9", "ft8code"))
        if beside.is_file():
            return str(beside)
    return None


def normalise(message: str) -> str:
    return " ".join(message.upper().split())


def tones(program: str, message: str) -> list[int]:
    """The message's 79 tones (0-7); EncodeError when FT8 cannot carry it as written."""
    message = normalise(message)
    if not message:
        raise EncodeError("empty message")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run([program, message], capture_output=True, text=True, timeout=10,
                            check=False, creationflags=flags)
    lines = result.stdout.splitlines()
    header = next((line for line in lines if "Message" in line and "Decoded" in line), "")
    row = next((line for line in lines if line.strip().startswith("1.")), "")
    if not header or not row:
        raise EncodeError(f"ft8code did not take {message!r}")
    start, end = header.index("Decoded"), header.index("Err")
    decoded = normalise(row[start:end])
    if decoded != message:
        raise EncodeError(f"FT8 sends it as {decoded!r}")
    index = next((i for i, line in enumerate(lines) if line.startswith("Channel symbols")), -1)
    digits = "".join(ch for ch in "".join(lines[index + 2:index + 3]) if ch.isdigit())
    if index < 0 or len(digits) != SYMBOLS:
        raise EncodeError("ft8code gave no tones")
    return [int(d) for d in digits]


def _pulse(samples_per_symbol: int) -> np.ndarray:
    """The Gaussian-smoothed frequency pulse, three symbols long (ft8_lib's gfsk_pulse)."""
    c = math.pi * math.sqrt(2 / math.log(2))
    t = np.arange(3 * samples_per_symbol) / samples_per_symbol - 1.5
    erf = np.vectorize(math.erf)
    return (erf(c * BT * (t + 0.5)) - erf(c * BT * (t - 0.5))) / 2


def waveform(symbols: list[int], frequency: float, rate: int = RATE) -> np.ndarray:
    """The audio of the tones: the lowest tone at `frequency` Hz, 12.64 s at `rate`."""
    n = round(rate / BAUD)                       # samples per symbol (1920 at 12 kHz)
    peak = 2 * math.pi / n                       # one tone step per symbol, in phase
    dphi = np.full((len(symbols) + 2) * n, 2 * math.pi * frequency / rate)
    pulse = _pulse(n)
    for i, tone in enumerate(symbols):
        dphi[i * n:i * n + 3 * n] += peak * tone * pulse
    # The first and the last symbol carry on into the extra ones at each end.
    dphi[:2 * n] += peak * symbols[0] * pulse[n:]
    dphi[len(symbols) * n:] += peak * symbols[-1] * pulse[:2 * n]
    phase = np.cumsum(dphi[n:(len(symbols) + 1) * n]) - dphi[n]
    audio = np.sin(phase)
    ramp = n // 8                                # soft start and end: no clicks
    shape = (1 - np.cos(np.pi * np.arange(ramp) / ramp)) / 2
    audio[:ramp] *= shape
    audio[-ramp:] *= shape[::-1]
    return audio.astype(np.float32)


def audio(program: str, message: str, frequency: float, rate: int = RATE) -> np.ndarray:
    return waveform(tones(program, message), frequency, rate)
