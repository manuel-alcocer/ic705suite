"""Multi-signal CW skimmer: finds every keyed signal in the passband and decodes each one.

Like CW Skimmer, raw text is unreliable on weak or crowded signals, so stations are
reported by callsign only once the callsign is validated: seen repeatedly on the same
signal, or present in the Super Check Partial database (MASTER.SCP).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ic705suite.apps.cw.decoder import CwDecoder, find_tone
from ic705suite.apps.cw.interpret import snap_to_scp

log = logging.getLogger(__name__)

DETECT_EVERY_S = 1.0
DETECT_WINDOW_S = 3.0
DETECT_SCORE = 15.0         # (peak / noise) * keying ratio, see _detect()
MIN_SPACING_HZ = 50.0       # two channels closer than this are the same signal
CHANNEL_AFC_HZ = 25.0       # per-channel AFC range, keeps channels apart
CHANNEL_TIMEOUT_S = 30.0    # drop a channel after this long without signal
TEXT_KEEP = 300
SCP_HITS = 2       # decodes needed to report a callsign present in MASTER.SCP
SCP_LONG = 5       # ...but one is enough from this length on: noise rarely spells those
NEW_STATION_GAP_S = 8.0
NOISE_LETTERS = set("EISHT5")  # what noise and crashes decode as  # a channel back after this much silence may be another station
UNKNOWN_HITS = 3   # decodes needed for a callsign not in MASTER.SCP
MAX_CHANNELS = 16
GHOST_RATIO = 15.0          # >23 dB under a signal within GHOST_SPAN_HZ: its key clicks
GHOST_SPAN_HZ = 400.0

# Prefix with at least one letter, a digit, a 1-4 letter suffix, optional /portable part.
CALLSIGN_RE = re.compile(r"\b(?=[A-Z0-9]{0,3}[A-Z])[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]{1,4})?\b")


def load_scp(path: Path) -> set[str]:
    calls = set()
    with path.open(encoding="ascii", errors="ignore") as fh:
        for line in fh:
            line = line.strip().upper()
            if line and not line.startswith(("#", "!")):
                calls.add(line)
    return calls


@dataclass
class Channel:
    tone: float
    decoder: CwDecoder
    created: float
    last_seen: float
    text: str = ""
    calls: Counter = field(default_factory=Counter)
    callsign: str | None = None   # best validated callsign

    @property
    def wpm(self) -> float:
        return self.decoder.wpm

    @property
    def snr_db(self) -> float:
        return self.decoder.snr_db


class Skimmer:
    def __init__(self, sample_rate: int, f_lo: float = 150.0, f_hi: float = 3400.0,
                 scp: set[str] | None = None,
                 on_callsign: Callable[[Channel], None] | None = None,
                 decoder_class: type[CwDecoder] = CwDecoder,
                 on_channel_text: Callable[[Channel, str], None] | None = None) -> None:
        self.decoder_class = decoder_class
        self.on_channel_text = on_channel_text
        self.sample_rate = sample_rate
        self.f_lo = f_lo
        self.f_hi = min(f_hi, sample_rate / 2 - 100)
        self.scp = scp or set()
        self.on_callsign = on_callsign
        self.channels: list[Channel] = []
        self.calls: Counter = Counter()  # callsign decodes across all channels
        self._buf = np.zeros(int(DETECT_WINDOW_S * sample_rate), np.float32)
        self._since_detect = 0
        self._candidates: list[float] = []  # tones detected in the previous round only
        self._filled = 0  # real samples in the detection buffer since the last reset
        # Audio band that actually carries signal/noise (the radio's IF filter as heard),
        # measured on every detection round; None until the buffer is full.
        self.passband: tuple[float, float] | None = None
        self._clock = 0.0  # seconds of audio processed (deterministic, used for timeouts)

    def reset(self) -> None:
        """Forget all signals (call after retuning)."""
        self.channels.clear()
        self.calls.clear()
        self._candidates = []
        self._filled = 0
        self.passband = None
        self._buf[:] = 0
        self._since_detect = 0

    # ---- processing -------------------------------------------------------------------

    def process(self, samples: np.ndarray) -> None:
        n = len(samples)
        if n >= len(self._buf):
            self._buf[:] = samples[-len(self._buf):]
        elif n:
            self._buf = np.roll(self._buf, -n)
            self._buf[-n:] = samples
        self._clock += n / self.sample_rate
        self._filled = min(len(self._buf), self._filled + n)
        for ch in self.channels:
            ch.decoder.process(samples)
        self._since_detect += n
        # A partly empty buffer has a near-zero noise floor, so everything would score
        # as a signal: wait until it holds DETECT_WINDOW_S of real audio.
        if self._filled == len(self._buf) and self._since_detect >= DETECT_EVERY_S * self.sample_rate:
            self._since_detect = 0
            self._update_channels()

    def _update_channels(self) -> None:
        now = self._clock
        tones = self._detect(self._buf)
        for ch in self.channels:
            if any(abs(t - ch.tone) < MIN_SPACING_HZ for t in tones):
                if now - ch.last_seen > NEW_STATION_GAP_S and ch.callsign:
                    # In a QSO or pileup the next signal on this spot is often someone
                    # else: the callsign must be earned again.
                    ch.calls.clear()
                    ch.callsign = None
                ch.last_seen = now
            ch.tone = ch.decoder.tone
        new = [t for t in tones if all(abs(t - ch.tone) >= MIN_SPACING_HZ for ch in self.channels)]
        for tone in new:
            # Noise crosses the threshold now and then, but rarely twice in a row at the
            # same place: require two consecutive detections before opening a channel.
            if len(self.channels) < MAX_CHANNELS and \
                    any(abs(tone - c) < MIN_SPACING_HZ / 2 for c in self._candidates):
                self._add_channel(tone, now)
        self._candidates = new
        # Drop silent channels and duplicates that drifted onto the same signal.
        kept: list[Channel] = []
        for ch in sorted(self.channels, key=lambda c: c.created):
            if now - ch.last_seen > CHANNEL_TIMEOUT_S:
                continue
            if any(abs(ch.tone - k.tone) < MIN_SPACING_HZ / 2 for k in kept):
                continue
            kept.append(ch)
        self.channels = sorted(kept, key=lambda c: c.tone)

    def _add_channel(self, tone: float, now: float) -> None:
        # Detection bins are 31 Hz wide; refine on the 3 s buffer (0.3 Hz resolution),
        # since a channel 15-20 Hz off loses ~6 dB in the narrow tone detector.
        fine, _ = find_tone(self._buf, self.sample_rate, tone - 25, tone + 25)
        if fine is not None:
            tone = fine
        ch = Channel(tone=tone, decoder=None, created=now, last_seen=now)  # type: ignore[arg-type]
        kwargs = {"scp": self.scp} if self.decoder_class is not CwDecoder else {}
        decoder = self.decoder_class(self.sample_rate, tone,
                                     on_text=lambda t, c=ch: self._on_text(c, t), **kwargs)
        decoder.afc_range = CHANNEL_AFC_HZ
        ch.decoder = decoder
        # Pre-roll: decode the audio that made us notice the signal.
        for i in range(0, len(self._buf), self.sample_rate // 50):
            decoder.process(self._buf[i:i + self.sample_rate // 50])
        self.channels.append(ch)
        log.debug("skimmer: new channel at %.0f Hz", tone)

    def shift(self, delta_tone: float) -> None:
        """The dial moved: keep the stations, whose audio tones move by -delta_tone Hz.
        Stations that fall outside the analysed band are dropped."""
        kept = []
        for ch in self.channels:
            tone = ch.tone - delta_tone
            if self.f_lo <= tone <= self.f_hi:
                ch.tone = tone
                ch.decoder.set_pitch(tone)
                kept.append(ch)
        self.channels = kept
        # The detection buffer holds audio of the old tuning.
        self._buf[:] = 0
        self._filled = 0
        self._candidates = []
        self.passband = None

    def _on_text(self, ch: Channel, text: str) -> None:
        if self.on_channel_text:
            self.on_channel_text(ch, text)
        ch.text = (ch.text + text)[-TEXT_KEEP:]
        if text != " ":
            return
        # A word just ended: look for a callsign in it.
        words = ch.text.split()
        if not words:
            return
        word = words[-1]
        if CALLSIGN_RE.fullmatch(word):
            # "<them> DE <me>": the callsign after DE is the station transmitting here.
            ch.calls[word] += 3 if len(words) > 1 and words[-2] == "DE" else 1
            self.calls[word] += 1  # global: survives the channel being dropped and recreated
            self._validate(ch)

    def _validate(self, ch: Channel) -> None:
        best = None
        taken = {c.callsign for c in self.channels if c is not ch and c.callsign}
        for call, own in ch.calls.most_common():
            # A callsign heard once on this signal may be the station being called
            # ("EA4SIM DE DL9SIM"): the channel's own call repeats, follows DE, or is a
            # known contester that is not already another channel's station.
            if call in taken or (own < 2 and call not in self.scp):
                continue
            if call not in self.scp:
                near = snap_to_scp(call, self.scp)
                if near != call and self.calls[near]:
                    continue  # EK6LBT while IK6LBT is also being heard: a misread of it
            # Decoding noise produces short "callsigns" often enough that a single hit,
            # even in MASTER.SCP, is not proof; a known callsign needs less repetition.
            if set(call) <= NOISE_LETTERS:
                needed = UNKNOWN_HITS  # EE5I: noise decodes as strings of dots
            elif call in self.scp:
                needed = 1 if len(call) >= SCP_LONG else SCP_HITS
            else:
                needed = UNKNOWN_HITS
            if self.calls[call] >= needed:
                best = call
                break
        if best and best != ch.callsign:
            ch.callsign = best
            log.info("skimmer: %s at %.0f Hz (%.0f wpm)", best, ch.tone, ch.wpm)
            if self.on_callsign:
                self.on_callsign(ch)

    # ---- detection ----------------------------------------------------------------------

    def _detect(self, x: np.ndarray) -> list[float]:
        """Tones of keyed signals: strong over the noise AND varying in time (not carriers)."""
        n = 256
        hop = n // 2
        frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
        mag = np.abs(np.fft.rfft(frames, axis=1))
        freqs = np.fft.rfftfreq(n, 1 / self.sample_rate)
        band = (freqs >= self.f_lo) & (freqs <= self.f_hi)
        # Local noise floor (+-250 Hz): outside the radio's IF filter the audio is nearly
        # silent, so a passband-wide median would make any in-filter noise look like a signal.
        floor = np.median(mag, axis=0)
        half = max(1, int(250 / (freqs[1] - freqs[0])))
        noise = np.array([np.median(floor[max(0, k - half):k + half + 1]) for k in range(len(floor))])
        noise = np.maximum(noise, 1e-12)
        # Bins whose noise floor is >20 dB under the loudest region are outside the IF filter.
        band &= noise > 0.1 * noise[band].max()
        if band.any():
            inside = freqs[band]
            self.passband = (float(inside.min()), float(inside.max()))
        p90 = np.percentile(mag, 90, axis=0)
        p20 = np.percentile(mag, 20, axis=0)
        score = np.where(band, (p90 / noise) * np.minimum(p90 / np.maximum(p20, 1e-12), 10), 0)
        span = max(1, int(GHOST_SPAN_HZ / (freqs[1] - freqs[0])))
        tones: list[float] = []
        for k in np.argsort(score)[::-1]:
            if score[k] < DETECT_SCORE:
                break
            if not 0 < k < len(p90) - 1 or p90[k] < max(p90[k - 1], p90[k + 1]):
                continue  # not a local maximum: skirt of a neighbouring signal
            if p90[max(0, k - span):k + span + 1].max() > GHOST_RATIO * p90[k]:
                continue  # keying sidebands (clicks) of a much stronger nearby signal
            a, b, c = p90[k - 1], p90[k], p90[k + 1]
            denom = a - 2 * b + c
            tone = freqs[k] + (0.5 * (a - c) / denom if denom else 0.0) * (freqs[1] - freqs[0])
            if all(abs(tone - t) >= MIN_SPACING_HZ for t in tones):
                tones.append(float(tone))
        return tones
