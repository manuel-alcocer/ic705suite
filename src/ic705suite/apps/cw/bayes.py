"""Probabilistic (Bayesian) CW decoder.

The classic decoder takes hard decisions: every pulse is a dot or a dash by a threshold,
every pause an element, letter or word gap, and one wrong call spoils the whole letter.
This decoder keeps many interpretations alive and scores each one with

  * the timing likelihood of every pulse and pause (log-normal around 1, 3 and 7 dits,
    tolerant of a human fist), including the chance that a pulse is just noise or that a
    pause is a fading dropout inside a pulse;
  * the clarity of the pulse (how far above the noise it was);
  * a CW language model: letter-pair frequencies and a bonus for words that exist on the
    air (abbreviations, Q codes, reports, numbers and callsigns, MASTER.SCP ones above all).

A beam search keeps the best interpretations; a word is written out once every likely
interpretation agrees on it, so the text appears with a delay of about one word.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace

from ic705suite.apps.cw.decoder import HOP_RATE, CwDecoder
from ic705suite.apps.cw.hamdata import QCODE_BY_NAME
from ic705suite.apps.cw.interpret import ABBREVIATIONS, CALLSIGN_RE, RST_RE
from ic705suite.morse import DECODE  # noqa: F401  (CODES below is the allowed subset)

BEAM = 48
PRUNE_MARGIN = 25.0     # drop interpretations this much less likely than the best (log)
COMMIT_MARGIN = 8.0     # interpretations within this margin must agree before writing
FLUSH_DITS = 9         # a pause this long (in dits, at least FLUSH_MIN_S) ends everything
FLUSH_MIN_S = 0.6
MAX_PENDING_WORDS = 1   # latency cap: older finished words follow the best interpretation

SIGMA_MARK = 0.28       # log-normal spread of pulse lengths
SIGMA_GAP = 0.38        # pauses vary more than pulses with a hand key
P_NOISE = 0.03
P_DROPOUT = 0.02
LM_WEIGHT = 0.5
WORD_SCALE = 1.0        # scales the word bonuses of the language model
PROSIGN_COST = -4.0
WORD_PENALTY = -1.0     # every word boundary costs this: splitting must be earned
SIGMA_WORD = 0.3
P_WORD_GAP = 0.1

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/?.=+ "
# Characters this decoder may produce: rare punctuation (: ; " ! ' , ( )) is left out,
# since on the air it is far more often a garbled letter than the real thing.
CODES = {code: ch for code, ch in DECODE.items()
         if ch in ALPHABET or ch in ("<SK>", "<AS>", "<KA>", "<HH>")}
# Every beginning of an allowed code: anything else can never become a character.
PREFIXES = {code[:i] for code in CODES for i in range(1, len(code) + 1)}
LONG_WORD = 7           # unknown words longer than this are usually noise run together

WORDS = set(ABBREVIATIONS) | set(QCODE_BY_NAME) | {
    "CQ", "DE", "TEST", "DX", "QRZ?", "QRL?", "HW?", "TNX", "TU", "FER", "CALL", "NAME", "OP",
    "QTH", "RIG", "ANT", "WX", "HR", "UR", "ES", "FB", "OM", "PSE", "AGN", "BK", "KN", "SK",
    "GM", "GA", "GE", "GN", "73", "88", "5NN", "599", "579", "559", "449", "339", "ENN", "TU",
    "EE", "R", "K", "IS", "THE", "AND", "FOR", "YOU", "MY", "NR", "QSL", "VIA", "BURO", "LOTW",
    "EQSL", "WATTS", "W", "DIPOLE", "VERT", "YAGI", "SUNNY", "CLOUDY", "RAIN", "COLD", "WARM",
}
# One-letter words are almost always pieces of longer ones, except these.
WORDS = {w for w in WORDS if len(w) > 1 or w in ("K", "R")}


def _corpus(callsigns: list[str]) -> list[str]:
    """Text used to learn letter-pair frequencies: CW words plus callsigns."""
    text = []
    for word in sorted(WORDS):
        text += [word] * (8 if len(word) <= 3 else 3)
    text += callsigns[:4000] or ["EA4ABC", "DL1XYZ", "G4ABC", "K1ABC", "SP3BZU", "OK1ABC"]
    text += [str(n) for n in range(100)]
    return text


class LanguageModel:
    def __init__(self, scp: set[str] | None = None) -> None:
        self.scp = scp or set()
        pairs: Counter = Counter()
        firsts: Counter = Counter()
        for word in _corpus(sorted(self.scp)):
            chain = " " + word + " "
            for a, b in zip(chain, chain[1:]):
                pairs[a, b] += 1
                firsts[a] += 1
        k = 0.5  # add-k smoothing over the alphabet
        # Scores are relative to a uniform guess: likely letters add, unlikely ones subtract,
        # so finishing letters is not penalised in itself (a beam would then avoid it).
        uniform = math.log(1 / len(ALPHABET))
        self._log: dict[tuple[str, str], float] = {}
        for a in ALPHABET:
            total = firsts[a] + k * len(ALPHABET)
            for b in ALPHABET:
                self._log[a, b] = math.log((pairs[a, b] + k) / total) - uniform

    def char(self, prev: str, ch: str) -> float:
        return self._log.get((prev, ch), -3.0)

    @staticmethod
    def unknown(length: int) -> float:
        """Score of an unknown word of this length, charged while the word is being built."""
        return -1.0 - 0.7 * max(0, length - LONG_WORD)

    def bonus(self, word: str) -> float:
        """What a finished word earns over the unknown-word score already charged."""
        return self.word(word) - self.unknown(len(word))

    def word(self, word: str) -> float:
        """Score (log) of a finished word, by how plausible it is on the air."""
        if not word:
            return 0.0
        if "*" in word:
            return -6.0
        if word in WORDS:
            return 3.0
        if CALLSIGN_RE.fullmatch(word):
            if word in self.scp:
                return 4.0
            return 1.5 if len(word) >= 4 else 0.0
        if RST_RE.fullmatch(word):
            return 2.5
        if word.isdigit() and len(word) <= 3:
            return 1.0
        if len(word) == 1:
            return -3.0
        return self.unknown(len(word))


def _lognorm(d: float, mean: float, sigma: float) -> float:
    x = (math.log(max(d, 1e-4)) - math.log(mean)) / sigma
    return -0.5 * x * x - math.log(sigma)


def _clip(p: float) -> float:
    return min(0.98, max(0.02, p))


@dataclass(slots=True)
class Hyp:
    score: float = 0.0
    text: str = ""          # finished, not yet written words (each followed by a space)
    word: str = ""          # letters of the current word
    elements: str = ""      # dots and dashes of the current letter
    has_prev: bool = False  # an element has been accepted (so `gap` follows something)
    gap: float = 0.0        # pause since the last accepted element, before `mark`
    gap_conf: float = 1.0
    mark: float = 0.0       # candidate pulse not yet classified (0 = none)
    mark_conf: float = 1.0
    after: float = 0.0      # pause after the candidate pulse
    after_conf: float = 1.0


class BayesDecoder(CwDecoder):
    """Drop-in replacement for CwDecoder with probabilistic decoding."""

    def __init__(self, sample_rate: int, pitch: float = 600.0, wpm: float = 18.0,
                 on_text: Callable[[str], None] | None = None, scp: set[str] | None = None) -> None:
        self.lm = LanguageModel(scp)
        super().__init__(sample_rate, pitch, wpm, on_text)

    def reset(self, pitch: float | None = None) -> None:
        super().reset(pitch)
        self._beam = [Hyp()]
        self._run_conf = 0.0
        self._run_down = False
        self._run_len = 0
        self._idle = 0

    # ---- segmentation: runs of key-down / key-up with their clarity ----------------------

    def _step(self, key: bool) -> None:
        span = max(self.signal - self.noise, 1e-9)
        p_down = 1.0 / (1.0 + math.exp(-(self.level - (self.noise + 0.5 * span)) / (0.12 * span)))
        if key != self._run_down and self._run_len:
            duration = self._run_len / HOP_RATE
            conf = self._run_conf / self._run_len
            if self._run_down:
                self._on_mark(duration, conf)
            else:
                self._on_space(duration, conf)
            self._run_len = 0
            self._run_conf = 0.0
        self._run_down = key
        self._run_len += 1
        self._run_conf += p_down if key else 1.0 - p_down
        if not key:
            limit = max(FLUSH_MIN_S, FLUSH_DITS * self.dit)
            if self._run_len / HOP_RATE > limit and not self._idle:
                self._on_space(self._run_len / HOP_RATE, self._run_conf / self._run_len)
                self._run_len = 0
                self._run_conf = 0.0
                self.flush()
                self._idle = 1
        else:
            self._idle = 0

    # ---- beam search ------------------------------------------------------------------------

    def _on_space(self, d: float, conf: float) -> None:
        for h in self._beam:
            if h.mark:
                h.after_conf = (h.after_conf * h.after + conf * d) / (h.after + d) if h.after else conf
                h.after += d
            else:
                h.gap_conf = (h.gap_conf * h.gap + conf * d) / (h.gap + d) if h.gap else conf
                h.gap += d

    def _on_mark(self, d: float, conf: float) -> None:
        if conf > 0.7 and d > 0.4 * self.dit:  # fairly clear pulses update the speed estimate
            self._marks.append(d)
            self._update_timing(d)
        dit = self.dit
        out: list[Hyp] = []
        for h in self._beam:
            if not h.mark:
                out.append(replace(h, mark=d, mark_conf=conf, after=0.0, after_conf=1.0))
                continue
            # (1) the candidate pulse was noise: everything around it is one pause
            n = replace(h)
            n.score += self._noise_cost(h.mark, h.mark_conf, dit)
            n.gap, n.gap_conf = h.gap + h.mark + h.after, h.gap_conf
            n.mark, n.mark_conf, n.after, n.after_conf = d, conf, 0.0, 1.0
            out.append(n)
            # (2) the pause after it was a dropout: the two pulses are one
            if h.after:
                m = replace(h)
                m.score += (math.log(P_DROPOUT) + _lognorm(h.after, 0.15 * dit, 0.8)
                            + math.log(_clip(1.0 - h.after_conf)))
                m.mark = h.mark + h.after + d
                m.mark_conf = (h.mark_conf + conf) / 2
                m.after = 0.0
                out.append(m)
            # (3) the candidate is a real dot or dash
            for symbol, units in ((".", 1), ("-", 3)):
                base = h.score + _lognorm(h.mark, units * dit, SIGMA_MARK) + math.log(0.5) \
                    + math.log(_clip(h.mark_conf))
                for g in self._accept(h, symbol, base, dit):
                    g.gap, g.gap_conf = h.after, h.after_conf
                    g.mark, g.mark_conf, g.after, g.after_conf = d, conf, 0.0, 1.0
                    out.append(g)
        self._prune(out)

    def _noise_cost(self, d: float, conf: float, dit: float) -> float:
        return math.log(P_NOISE) + _lognorm(d, 0.15 * dit, 0.8) + math.log(_clip(1.0 - conf))

    def _accept(self, h: Hyp, symbol: str, score: float, dit: float) -> list[Hyp]:
        """Interpretations of the pause before an accepted element, then the element."""
        if not h.has_prev:
            if h.elements + symbol not in PREFIXES:
                return []
            return [replace(h, score=score, elements=h.elements + symbol, has_prev=True)]
        real = math.log(_clip(h.gap_conf))
        results = []
        for kind, units, sigma, prior in (("elem", 1, SIGMA_GAP, 0.6),
                                          ("char", 3, SIGMA_GAP, 0.4 - P_WORD_GAP),
                                          ("word", 7, SIGMA_WORD, P_WORD_GAP)):
            gap = min(h.gap, 10 * dit) if kind == "word" else h.gap
            s = score + _lognorm(gap, units * dit, sigma) + math.log(prior) + real
            g = replace(h, score=s)
            if kind != "elem" and not self._finish_char(g):
                continue
            if kind == "word":
                self._finish_word(g)
            g.elements += symbol
            if g.elements in PREFIXES:
                results.append(g)
        return results

    def _finish_char(self, h: Hyp) -> bool:
        if not h.elements:
            return True
        ch = CODES.get(h.elements)
        if ch is None:
            ch = "*"
            h.score -= 9.0
        prev = h.word[-1] if h.word else " "
        if len(ch) == 1:
            h.score += LM_WEIGHT * self.lm.char(prev, ch)
        else:
            h.score += PROSIGN_COST  # <HH>, <SK>...: rare, and often two letters run together
        # Charge the unknown-word score as the word grows, so an interpretation that runs
        # words together pays now, not only when the word finally ends (beams are greedy).
        before = self.lm.unknown(len(h.word)) if h.word else 0.0
        h.word += ch
        h.score += LM_WEIGHT * WORD_SCALE * (self.lm.unknown(len(h.word)) - before)
        h.elements = ""
        return True

    def _finish_word(self, h: Hyp) -> None:
        if not h.word:
            return
        last = h.word[-1] if len(h.word[-1]) == 1 else " "
        h.score += LM_WEIGHT * (WORD_SCALE * self.lm.bonus(h.word) + self.lm.char(last, " ")) \
            + WORD_PENALTY
        h.text += h.word + " "
        h.word = ""

    def _prune(self, hyps: list[Hyp]) -> None:
        if not hyps:
            self._beam = [Hyp()]
            return
        # Identical states keep only their best history.
        best: dict[tuple, Hyp] = {}
        for h in hyps:
            key = (h.text[-12:], h.word, h.elements, h.has_prev, round(h.gap, 3), round(h.mark, 3))
            if key not in best or h.score > best[key].score:
                best[key] = h
        ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
        top = ranked[0].score
        self._beam = [h for h in ranked[:BEAM] if h.score > top - PRUNE_MARGIN]
        self._commit()

    def _commit(self) -> None:
        """Write out the words every likely interpretation agrees on."""
        top = self._beam[0].score
        likely = [h.text for h in self._beam if h.score > top - COMMIT_MARGIN]
        prefix = likely[0]
        for text in likely[1:]:
            i = 0
            while i < min(len(prefix), len(text)) and prefix[i] == text[i]:
                i += 1
            prefix = prefix[:i]
        cut = prefix.rfind(" ") + 1  # whole words only
        best = self._beam[0].text
        if cut == 0 and best.count(" ") > MAX_PENDING_WORDS:
            cut = best.find(" ") + 1
            prefix = best
        if cut:
            written = prefix[:cut]
            self._beam = [replace(h, text=h.text[cut:]) for h in self._beam if h.text.startswith(written)]
            self._write(written)

    def _write(self, text: str) -> None:
        for ch in text:
            self._emit(ch)

    def flush(self) -> None:
        """End of a transmission: settle every pending decision and write the best text."""
        finished: list[Hyp] = []
        dit = self.dit
        for h in self._beam:
            options = []
            if h.mark:
                n = replace(h)
                n.score += self._noise_cost(h.mark, h.mark_conf, dit)
                options.append(n)
                for symbol, units in ((".", 1), ("-", 3)):
                    base = h.score + _lognorm(h.mark, units * dit, SIGMA_MARK) + math.log(0.5) \
                        + math.log(_clip(h.mark_conf))
                    options += self._accept(h, symbol, base, dit)
            else:
                options.append(replace(h))
            for o in options:
                self._finish_char(o)
                self._finish_word(o)
                o.mark, o.gap, o.after, o.has_prev = 0.0, 0.0, 0.0, False
                finished.append(o)
        best = max(finished, key=lambda h: h.score) if finished else Hyp()
        if best.text:
            self._write(best.text)
        self._beam = [Hyp()]
