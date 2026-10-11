"""WSJT-X's decoder, jt9, run on one FT8 cycle at a time.

The radio's audio of a 15-second cycle is written as a 12 kHz, 16-bit mono WAV whose name
carries the cycle's UTC time (jt9 reads it from there) and `jt9 -8` decodes it. jt9 (GPL)
stays a program of its own: the suite only runs it and reads what it prints.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 12_000              # what jt9 takes
CYCLE = 15                 # seconds of an FT8 cycle

#: ``170615 -11  0.0 1497 ~  OE8PPL IK1MJK JN35          ? a1``
LINE = re.compile(r"^(?P<time>\d{6})\s+(?P<snr>-?\d+)\s+(?P<dt>-?\d+\.\d)\s+(?P<freq>\d+)\s+~\s+"
                  r"(?P<message>.*?)\s*(?P<ap>\?\s*a\d+)?\s*$")


@dataclass
class Decode:
    time: str              # HHMMSS (UTC) of the cycle
    snr: int               # dB
    dt: float              # seconds off the cycle's start
    freq: int              # audio Hz
    message: str
    doubtful: bool = False  # decoded with a priori help (jt9's "? a1")


def find_jt9(configured: str = "") -> str | None:
    """jt9: [ft8] jt9 if given, else on the PATH or where WSJT-X installs it."""
    if configured:
        return shutil.which(configured) or (configured if Path(configured).is_file() else None)
    found = shutil.which("jt9")
    if found:
        return found
    candidates = []
    if sys.platform == "win32":
        candidates = [Path("C:/WSJT/wsjtx/bin/jt9.exe"),
                      Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "wsjtx" /
                      "bin" / "jt9.exe"]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/wsjtx.app/Contents/MacOS/jt9")]
    return next((str(c) for c in candidates if c.is_file()), None)


def resample(samples: np.ndarray, rate: int, to: int = RATE) -> np.ndarray:
    """Samples at `rate` -> at `to` (by the spectrum: no aliasing up to the lower Nyquist)."""
    if rate == to or not len(samples):
        return samples.astype(np.float32)
    n_out = int(round(len(samples) * to / rate))
    spectrum = np.fft.rfft(samples)
    bins = n_out // 2 + 1
    if bins > len(spectrum):
        spectrum = np.concatenate([spectrum, np.zeros(bins - len(spectrum), spectrum.dtype)])
    else:
        spectrum = spectrum[:bins]
    return (np.fft.irfft(spectrum, n_out) * (n_out / len(samples))).astype(np.float32)


def cycle_name(start: float) -> str:
    """The WAV's name: jt9 takes the cycle's time from it."""
    return time.strftime("%y%m%d_%H%M%S", time.gmtime(start)) + ".wav"


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """One cycle at 12 kHz, normalised (jt9 wants a healthy level, not clipping)."""
    y = resample(samples, rate)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y / peak * 0.7
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((y * 32767).astype("<i2").tobytes())


def parse(output: str) -> list[Decode]:
    decodes = []
    for line in output.splitlines():
        match = LINE.match(line.strip())
        if match:
            decodes.append(Decode(match["time"], int(match["snr"]), float(match["dt"]),
                                  int(match["freq"]), " ".join(match["message"].split()),
                                  bool(match["ap"])))
    return decodes


def decode(program: str, wav: Path, depth: int = 3, timeout: float = 30.0,
           band: tuple[int, int] | None = None) -> list[Decode]:
    """Run jt9 on one cycle's WAV (in its folder, where it leaves its own files); `band`
    (lowest, highest Hz) limits it to part of the passband, much faster."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    limits = ["-L", str(band[0]), "-H", str(band[1])] if band else []
    result = subprocess.run([program, "-8", "-d", str(depth), *limits, wav.name],
                            cwd=str(wav.parent),
                            capture_output=True, text=True, timeout=timeout, check=False,
                            creationflags=flags)
    return parse(result.stdout)
