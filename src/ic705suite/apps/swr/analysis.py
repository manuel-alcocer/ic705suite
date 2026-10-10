"""Find the antenna resonance in a sweep and work out which way to move the coil."""

from __future__ import annotations

from dataclasses import asdict, dataclass

# On the Komunica HF-PRO-2 a higher coil number lowers the resonant frequency.
COIL_SIGN = -1
# Approximate kHz per coil unit, used until the history gives a better slope.
DEFAULT_KHZ_PER_UNIT = {"40m": -150.0}


@dataclass
class Analysis:
    resonance: int            # Hz
    best_value: float         # minimum SWR, or peak S-meter (raw)
    at_edge: str              # "", "low" or "high": extreme sits at the sweep edge
    delta_khz: float          # resonance - target
    confident: bool           # noise sweeps: the peak stands out from the floor
    centered: bool            # resonance close enough to the target
    tuned: bool               # centered and (for SWR sweeps) SWR <= 1.5
    far: bool                 # SWR sweeps: SWR above 3 everywhere (meter saturated)
    move: str                 # "", "lower" or "higher" coil number
    units: float | None       # estimated coil units to move
    khz_per_unit: float | None
    bw_15: tuple[int, int] | None  # frequency range with SWR <= 1.5
    bw_20: tuple[int, int] | None
    swr_at_target: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


TUNED_SWR = 1.5


def _value_at(freqs: list[int], values: list[float], hz: int) -> float | None:
    """Linear interpolation of the sweep at hz (None outside the sweep)."""
    for (f0, y0), (f1, y1) in zip(zip(freqs, values), zip(freqs[1:], values[1:])):
        if f0 <= hz <= f1:
            return y0 if f1 == f0 else y0 + (y1 - y0) * (hz - f0) / (f1 - f0)
    return None


def tolerance_hz(target: int) -> int:
    return max(10_000, round(target * 0.0025))


def _median3(values: list[float]) -> list[float]:
    """Running median: removes single-point spikes (a carrier) but keeps a broad resonance."""
    if len(values) < 3:
        return list(values)
    return [values[0]] + [sorted(values[i - 1:i + 2])[1] for i in range(1, len(values) - 1)] \
        + [values[-1]]


def _smooth(values: list[float]) -> list[float]:
    values = _median3(values)
    if len(values) < 3:
        return list(values)
    out = [values[0]]
    for i in range(1, len(values) - 1):
        out.append((values[i - 1] + values[i] + values[i + 1]) / 3)
    out.append(values[-1])
    return out


def _vertex(freqs: list[int], values: list[float], i: int) -> float:
    """Parabola through the extreme and its neighbours: sub-step resonance estimate."""
    if i <= 0 or i >= len(freqs) - 1:
        return float(freqs[i])
    f0, f1, f2 = freqs[i - 1:i + 2]
    y0, y1, y2 = values[i - 1:i + 2]
    denom = y0 - 2 * y1 + y2
    if denom == 0:
        return float(f1)
    step = (f2 - f0) / 2
    offset = 0.5 * (y0 - y2) / denom
    return f1 + max(-1.0, min(1.0, offset)) * step


def _range_below(freqs: list[int], swr: list[float], limit: float, i: int) -> tuple[int, int] | None:
    if swr[i] > limit:
        return None

    def cross(a: int, b: int) -> int:
        ya, yb = swr[a], swr[b]
        if yb == ya:
            return freqs[a]
        return round(freqs[a] + (freqs[b] - freqs[a]) * (limit - ya) / (yb - ya))

    lo = i
    while lo > 0 and swr[lo - 1] <= limit:
        lo -= 1
    hi = i
    while hi < len(freqs) - 1 and swr[hi + 1] <= limit:
        hi += 1
    low = cross(lo, lo - 1) if lo > 0 else freqs[0]
    high = cross(hi, hi + 1) if hi < len(freqs) - 1 else freqs[-1]
    return low, high


MIN_PROMINENCE = 6.0  # S-meter raw units (about half an S unit)


def _peak_stands_out(values: list[float], smooth: list[float], i: int) -> bool:
    """The noise peak must fall off on both sides and rise clearly above the median floor and
    above point-to-point jitter. A maximum at the sweep edge is just the noise slope."""
    if i == 0 or i == len(values) - 1:
        return False
    median = sorted(smooth)[len(smooth) // 2]
    jitter = sorted(abs(v - s) for v, s in zip(values, smooth))[len(values) // 2]
    prominence = smooth[i] - median
    return prominence >= max(MIN_PROMINENCE, 3 * jitter)


def khz_per_unit(history: list[dict], band: str) -> float | None:
    """Least-squares slope (kHz per coil unit) from this band's final coil positions."""
    pts = []
    for h in history:
        if h.get("kind") != "final" or h.get("band") != band or h.get("at_edge"):
            continue
        try:
            pts.append((float(str(h.get("coil")).replace(",", ".")), h["resonance"] / 1000))
        except (TypeError, ValueError):
            continue
    if len({c for c, _ in pts}) >= 2:
        n = len(pts)
        mx = sum(c for c, _ in pts) / n
        my = sum(f for _, f in pts) / n
        sxx = sum((c - mx) ** 2 for c, _ in pts)
        slope = sum((c - mx) * (f - my) for c, f in pts) / sxx
        if slope * COIL_SIGN > 0:  # only trust slopes with the physically expected sign
            return round(slope, 1)
    return DEFAULT_KHZ_PER_UNIT.get(band)


def analyze(kind: str, freqs: list[int], values: list[float], target: int,
            band: str, history: list[dict]) -> Analysis | None:
    """kind 'swr': values are SWR (minimum = resonance); 'noise': S-meter (maximum)."""
    if len(freqs) < 2:
        return None
    if kind == "swr":
        i = min(range(len(values)), key=values.__getitem__)
        resonance = _vertex(freqs, values, i)
        best = values[i]
    else:
        smooth = _smooth(values)
        i = max(range(len(smooth)), key=smooth.__getitem__)
        resonance = _vertex(freqs, smooth, i)
        best = values[i]
    confident = True
    if kind != "swr":
        confident = _peak_stands_out(values, smooth, i)
    at_edge = "low" if i == 0 else "high" if i == len(freqs) - 1 else ""
    delta = resonance - target
    centered = confident and abs(delta) <= tolerance_hz(target) and not at_edge
    tuned = centered and (kind != "swr" or best <= 1.5)
    at_target = _value_at(freqs, values, target) if kind == "swr" else None
    if at_target is not None and at_target <= TUNED_SWR:
        # What matters on the air is the SWR on the operating frequency, not the exact dip.
        centered = tuned = True
    move = ""
    if confident and not centered:
        # Resonance below the target -> raise frequency -> COIL_SIGN tells which way the number goes.
        raise_freq = delta < 0
        move = "higher" if (raise_freq == (COIL_SIGN > 0)) else "lower"
    slope = khz_per_unit(history, band)
    units = None
    # From the sweep edge the true distance is unknown: give a direction only.
    if slope and confident and not centered and not at_edge:
        units = round(abs(delta / 1000 / slope) * 4) / 4  # quarter-unit resolution
    bw_15 = _range_below(freqs, values, 1.5, i) if kind == "swr" else None
    bw_20 = _range_below(freqs, values, 2.0, i) if kind == "swr" else None
    result = Analysis(round(resonance), best, at_edge, round(delta / 1000, 1), confident, centered, tuned, kind == "swr" and best >= 3.0, move,
                    units, slope, bw_15, bw_20)
    if at_target is not None:
        result.swr_at_target = round(at_target, 2)
    return result
