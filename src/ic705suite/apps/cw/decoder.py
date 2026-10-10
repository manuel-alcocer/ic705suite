"""CW (Morse) decoder working on receive audio.

Pipeline: quadrature mix at the tone frequency -> two cascaded boxcar low-pass
filters -> magnitude envelope at 250 Hz -> adaptive threshold with hysteresis
-> mark/space timing -> adaptive dit estimate -> Morse table lookup.
An optional AFC follows the strongest keyed tone near the radio's CW pitch.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

import numpy as np

from ic705suite.morse import DECODE

HOP_RATE = 250  # envelope samples per second
MIN_WPM, MAX_WPM = 5.0, 60.0
SQUELCH_OPEN = 6.0   # peak/noise envelope ratio
SQUELCH_CLOSE = 4.8
AFC_RANGE_HZ = 100.0  # only fine-tune: wider ranges jump between stations of a QSO
# Detection filter length per boxcar stage, as a fraction of a dit: longer windows
# reject more noise and neighbouring stations, but must stay shorter than a dit.
WINDOW_DITS = 0.5
WINDOW_MIN_S, WINDOW_MAX_S = 0.006, 0.025


class ToneEnvelope:
    """Streaming narrow-band magnitude detector centred on `freq`."""

    def __init__(self, sample_rate: int, freq: float, window: float = 0.008) -> None:
        self.sample_rate = sample_rate
        self.freq = freq
        self.hop = sample_rate // HOP_RATE
        self._phase = 0.0
        self._offset = 0
        # Recent mixed-down input, so the window can change without a glitch.
        self._recent = np.zeros(2 * int(WINDOW_MAX_S * sample_rate) + 2, np.complex128)
        self.set_window(window)

    def set_window(self, window: float) -> None:
        """Length of each boxcar stage in seconds; filter state is rebuilt from recent input."""
        self.window = window
        self._len = max(2, int(window * self.sample_rate))
        tail = np.zeros(self._len - 1, np.complex128)
        y, self._tail1 = self._boxcar(self._recent, tail)
        _, self._tail2 = self._boxcar(y, tail)

    def _boxcar(self, x: np.ndarray, tail: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        buf = np.concatenate([tail, x])
        c = np.concatenate([[0], np.cumsum(buf)])
        return (c[self._len:] - c[:-self._len]) / self._len, buf[-(self._len - 1):]

    def process(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.zeros(0)
        w = 2 * np.pi * self.freq / self.sample_rate
        z = x * np.exp(-1j * (self._phase + w * np.arange(len(x))))
        self._phase = (self._phase + w * len(x)) % (2 * np.pi)
        self._recent = np.concatenate([self._recent, z])[-len(self._recent):]
        y, self._tail1 = self._boxcar(z, self._tail1)
        y, self._tail2 = self._boxcar(y, self._tail2)
        out = y[self._offset::self.hop]
        self._offset = (self._offset - len(y)) % self.hop
        return np.abs(out) * 2.0  # sine of amplitude A -> ~A


class CwDecoder:
    def __init__(self, sample_rate: int, pitch: float = 600.0, wpm: float = 18.0,
                 on_text: Callable[[str], None] | None = None) -> None:
        self.sample_rate = sample_rate
        self.pitch = pitch            # radio CW pitch (AFC searches around it)
        self.on_text = on_text
        self.afc = True
        self.afc_range = AFC_RANGE_HZ
        self.muted = False
        self.env = ToneEnvelope(sample_rate, pitch)
        self.dit = 1.2 / wpm
        self._history: deque[float] = deque(maxlen=HOP_RATE * 3)
        self.noise = 1e-6
        self.peak = 1e-6
        self.level = 0.0
        self._marks: deque[float] = deque(maxlen=16)
        self._afc_buf = np.zeros(sample_rate, np.float32)
        self._afc_count = 0
        self.reset()

    # ---- public properties ---------------------------------------------------

    @property
    def tone(self) -> float:
        return self.env.freq

    @tone.setter
    def tone(self, freq: float) -> None:
        self.env.freq = float(freq)

    @property
    def wpm(self) -> float:
        return 1.2 / self.dit

    @property
    def snr_db(self) -> float:
        return 20 * np.log10(max(self.peak, 1e-9) / max(self.noise, 1e-9))

    @property
    def squelched(self) -> bool:
        return not self._squelch_open

    def reset(self, pitch: float | None = None) -> None:
        """Forget the current signal (call after retuning)."""
        if pitch is not None:
            self.pitch = pitch
        self.env.freq = self.pitch
        self._history.clear()
        self._marks.clear()
        self._down = False
        self._run = 0
        self._space_before = 0
        self._mark_pending = 0
        self._elements = ""
        self._word_done = True
        self._squelch_open = False
        self._key = False
        self._level_sum, self._level_n = 0.0, 0
        self._mark_levels: deque[float] = deque(maxlen=8)
        self.signal = 1e-6
        self._afc_buf[:] = 0
        self._afc_count = 0

    def set_pitch(self, pitch: float) -> None:
        self.pitch = pitch
        self.env.freq = pitch

    # ---- processing ---------------------------------------------------------------

    def process(self, samples: np.ndarray) -> None:
        if self.afc:
            self._afc_feed(samples)
        mags = self.env.process(samples)
        if len(mags) == 0:
            return
        self._history.extend(mags.tolist())
        hist = np.fromiter(self._history, float)
        self.noise = max(float(np.percentile(hist, 20)), 1e-9)
        self.peak = float(np.percentile(hist, 97))
        # Noise alone (Rayleigh envelope) gives p97/p20 ~ 4, so open well above that.
        ratio = self.peak / self.noise
        if ratio > SQUELCH_OPEN and len(hist) >= HOP_RATE // 2:  # not on start-up transients
            self._squelch_open = True
        elif ratio < SQUELCH_CLOSE:
            self._squelch_open = False
        # Signal level: the median level of the last real pulses follows fading (QSB) pulse
        # by pulse, where a percentile over seconds would still remember the strong part.
        self.signal = self.peak
        if len(self._mark_levels) >= 3:
            self.signal = min(self.peak, float(np.median(self._mark_levels)))
        span = max(self.signal - self.noise, 1e-9)
        hi = self.noise + 0.55 * span
        lo = self.noise + 0.40 * span
        squelched = self.squelched or self.muted
        for m in mags:
            self.level = m
            key = not squelched and (m > (lo if self._key else hi))
            if key:
                self._level_sum += m
                self._level_n += 1
            elif self._key:
                if self._level_n / HOP_RATE >= 0.6 * self.dit:
                    self._mark_levels.append(self._level_sum / self._level_n)
                self._level_sum, self._level_n = 0.0, 0
            self._key = key
            self._step(key)
        self._adapt_window()

    def _adapt_window(self) -> None:
        target = min(WINDOW_MAX_S, max(WINDOW_MIN_S, WINDOW_DITS * self.dit))
        if abs(target - self.env.window) > 0.25 * self.env.window:
            self.env.set_window(target)

    def _step(self, key: bool) -> None:
        dt = 1.0 / HOP_RATE
        min_len = min(0.025, max(0.008, 0.3 * self.dit))
        if self._down and not key:
            self._down = False
            mark = self._run
            self._run = 0
            if mark * dt < min_len:
                self._run = self._space_before + mark  # glitch: still in the space
            else:
                self._mark_pending = mark
        elif not self._down and key:
            self._down = True
            if self._mark_pending and self._run * dt < min_len:
                self._run = self._mark_pending + self._run  # short dropout inside a mark
                self._mark_pending = 0
            else:
                self._space_before = self._run
                self._run = 0
        self._run += 1
        if self._down:
            return
        space = self._run * dt
        if self._mark_pending and space >= min_len:
            self._add_mark(self._mark_pending * dt)
            self._mark_pending = 0
        if self._elements and not self._mark_pending and space > 2.0 * self.dit:
            self._emit(DECODE.get(self._elements, "*"))
            self._elements = ""
            self._word_done = False
        if not self._word_done and not self._elements and space > 5.0 * self.dit:
            self._emit(" ")
            self._word_done = True

    def _add_mark(self, duration: float) -> None:
        # Marks far shorter than a dit are mostly noise; letting them into the speed
        # estimate drags it towards the maximum and then every dit reads as a dah.
        if duration >= 0.5 * self.dit or len(self._marks) < 4:
            self._marks.append(duration)
            self._update_timing(duration)
        self._elements += "." if duration < 2.0 * self.dit else "-"
        if len(self._elements) > 9:
            self._elements = self._elements[-9:]

    def _update_timing(self, duration: float) -> None:
        marks = np.fromiter(self._marks, float)
        if len(marks) < 4:
            estimate = duration if duration < 2 * self.dit else duration / 3
        elif marks.max() > 2.2 * marks.min():
            short, long_ = marks.min(), marks.max()
            for _ in range(8):
                threshold = (short + long_) / 2
                dits, dahs = marks[marks < threshold], marks[marks >= threshold]
                short, long_ = dits.mean(), dahs.mean()
            if 2.0 <= long_ / short <= 4.5:
                estimate = (dits.sum() + dahs.sum() / 3) / len(marks)
            else:
                estimate = short
        else:
            mean = marks.mean()
            estimate = mean if mean < 2 * self.dit else mean / 3
        self.dit = 0.6 * self.dit + 0.4 * estimate
        self.dit = min(1.2 / MIN_WPM, max(1.2 / MAX_WPM, self.dit))

    def _emit(self, text: str) -> None:
        if self.on_text:
            self.on_text(text)

    # ---- AFC --------------------------------------------------------------------------

    def _afc_feed(self, samples: np.ndarray) -> None:
        n = len(samples)
        if n >= len(self._afc_buf):
            self._afc_buf[:] = samples[-len(self._afc_buf):]
        else:
            self._afc_buf = np.roll(self._afc_buf, -n)
            self._afc_buf[-n:] = samples
        self._afc_count += n
        if self._afc_count >= self.sample_rate // 2:
            self._afc_count = 0
            self._afc_update()

    def _afc_update(self) -> None:
        lo, hi = self.pitch - self.afc_range, self.pitch + self.afc_range
        tone, score = find_tone(self._afc_buf, self.sample_rate, lo, hi)
        if tone is None or score < 6.0:
            return
        # A much stronger station just outside the range leaks sidelobes into it: only
        # follow peaks that are a real signal compared with the strongest one nearby.
        _, wide_score = find_tone(self._afc_buf, self.sample_rate, lo - 200, hi + 200)
        if wide_score > 4 * score:
            return
        delta = tone - self.env.freq
        if abs(delta) > 30:
            self.env.freq = tone
        elif abs(delta) > 4:
            self.env.freq += 0.5 * delta


def find_tone(samples: np.ndarray, sample_rate: int, f_lo: float, f_hi: float
              ) -> tuple[float | None, float]:
    """Strongest spectral peak between f_lo and f_hi. Returns (freq, peak/median)."""
    if len(samples) < 256:
        return None, 0.0
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1 / sample_rate)
    band = np.nonzero((freqs >= max(f_lo, 50)) & (freqs <= min(f_hi, sample_rate / 2 - 50)))[0]
    if len(band) < 3:
        return None, 0.0
    i = int(np.argmax(spectrum[band]))
    if i in (0, len(band) - 1):
        return None, 0.0  # rising towards the band edge: the real peak lies outside
    k = band[i]
    median = np.median(spectrum[band]) or 1e-12
    if 0 < k < len(spectrum) - 1:
        a, b, c = spectrum[k - 1], spectrum[k], spectrum[k + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if denom else 0.0
    else:
        shift = 0.0
    return float(freqs[k] + shift * (freqs[1] - freqs[0])), float(spectrum[k] / median)


def detect_cw(samples: np.ndarray, sample_rate: int, f_lo: float, f_hi: float
              ) -> tuple[bool, float | None, float, float]:
    """Look for a keyed (on/off) narrow tone in the band.

    Returns (found, tone_freq, snr, keying_ratio). Steady carriers (birdies) are
    rejected because their level does not vary over time.
    """
    frame = int(0.032 * sample_rate)
    hop = frame // 2
    if len(samples) < frame * 4:
        return False, None, 0.0, 0.0
    count = (len(samples) - frame) // hop + 1
    idx = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    mag = np.abs(np.fft.rfft(samples[idx] * np.hanning(frame), axis=1))
    freqs = np.fft.rfftfreq(frame, 1 / sample_rate)
    band = (freqs >= max(f_lo, 50)) & (freqs <= min(f_hi, sample_rate / 2 - 50))
    if band.sum() < 3:
        return False, None, 0.0, 0.0
    mag_b = mag[:, band]
    noise = float(np.median(mag_b)) or 1e-12
    p90 = np.percentile(mag_b, 90, axis=0)
    p20 = np.percentile(mag_b, 20, axis=0)
    k = int(np.argmax(p90))
    snr = float(p90[k] / noise)
    keying = float(p90[k] / max(p20[k], noise * 0.5))
    found = snr > 4.0 and keying > 3.0
    tone, _ = find_tone(samples, sample_rate, freqs[band][k] - 40, freqs[band][k] + 40)
    return found, tone, snr, keying
