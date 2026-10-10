"""Plain-language interpretation of decoded CW.

CW contacts are written in abbreviations, Q codes and fixed structures rather than in
sentences ("CQ CQ DE EA4ABC EA4ABC K"). This module turns each transmission into:
  * a summary in plain language ("EA4ABC (Spain) is calling CQ: ..."), and
  * a glossary of the words it recognised.
Decoder noise (single letters, '*') is ignored. All texts are English marked with N_()
and translated when shown, so the result follows the client's language.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field

from ic705suite.apps.cw.hamdata import QCODE_BY_NAME, country_for
from ic705suite.i18n import N_, _

CALLSIGN_RE = re.compile(r"(?=[A-Z0-9]{0,3}[A-Z])[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]{1,4})?")
# Contest "cut numbers": letters sent instead of digits to save time (5NN = 599).
CUT_NUMBERS = str.maketrans({"N": "9", "T": "0", "O": "0", "A": "1", "E": "5"})
RST_RE = re.compile(r"[1-5][1-9NTEAO][1-9NTEAO]")
SILENCE_S = 5.0  # a pause this long ends a transmission

END_OVER = {"K", "PSE K"}
END_ONLY_YOU = {"KN", "("}
END_CONTACT = {"SK", "<SK>", "VA"}
END_TOKENS = END_OVER | END_ONLY_YOU | END_CONTACT | {"BK", "AR", "+"}

ABBREVIATIONS: dict[str, str] = {
    "CQ": N_("general call: anyone may answer"),
    "DE": N_("this is / from"),
    "K": N_("over: your turn"),
    "KN": N_("over: only the station called may answer"),
    "(": N_("over: only the station called may answer"),
    "BK": N_("break: quick reply without callsigns"),
    "SK": N_("end of contact"),
    "<SK>": N_("end of contact"),
    "VA": N_("end of contact"),
    "AR": N_("end of message"),
    "+": N_("end of message"),
    "=": N_("pause / new paragraph"),
    "R": N_("received: all copied"),
    "RR": N_("received: all copied"),
    "TU": N_("thank you"),
    "TY": N_("thank you"),
    "TNX": N_("thanks"),
    "TKS": N_("thanks"),
    "73": N_("best regards"),
    "72": N_("best regards (low power operators)"),
    "88": N_("love and kisses"),
    "GM": N_("good morning"),
    "GA": N_("good afternoon"),
    "GE": N_("good evening"),
    "GN": N_("good night"),
    "GD": N_("good day"),
    "UR": N_("your / you are"),
    "HR": N_("here"),
    "ES": N_("and"),
    "FB": N_("fine business: excellent"),
    "OM": N_("old man: friend (male operator)"),
    "YL": N_("young lady: female operator"),
    "XYL": N_("wife"),
    "OP": N_("operator (name follows)"),
    "NAME": N_("name (follows)"),
    "RST": N_("signal report (follows)"),
    "RIG": N_("radio equipment"),
    "TRX": N_("transceiver"),
    "ANT": N_("antenna"),
    "PWR": N_("power"),
    "W": N_("watts"),
    "WX": N_("weather"),
    "TEMP": N_("temperature"),
    "AGN": N_("again: please repeat"),
    "PSE": N_("please"),
    "PLS": N_("please"),
    "HW": N_("how do you copy?"),
    "CPY": N_("copy"),
    "CFM": N_("confirm"),
    "NR": N_("number"),
    "NW": N_("now"),
    "TEST": N_("contest call"),
    "DX": N_("distant station"),
    "POTA": N_("Parks on the Air activation"),
    "SOTA": N_("Summits on the Air activation"),
    "WWFF": N_("World Wide Flora and Fauna activation"),
    "GL": N_("good luck"),
    "CU": N_("see you"),
    "CUL": N_("see you later"),
    "SRI": N_("sorry"),
    "ABT": N_("about"),
    "BTU": N_("back to you"),
    "DR": N_("dear"),
    "GUD": N_("good"),
    "VY": N_("very"),
    "WID": N_("with"),
    "INFO": N_("information"),
    "MNI": N_("many"),
    "OK": N_("okay"),
    "NIL": N_("nothing copied"),
    "EE": N_("dit dit: friendly goodbye"),
    "?": N_("question: please repeat"),
}

# Words after which the next word is a value, not a code (NAME JOHN, QTH MADRID).
VALUE_AFTER = {"NAME": "name", "OP": "name", "QTH": "qth", "RIG": "rig", "ANT": "ant"}

R_TEXT = {"1": N_("unreadable"), "2": N_("barely readable"), "3": N_("readable with difficulty"),
          "4": N_("readable"), "5": N_("perfectly readable")}


@dataclass
class Interpretation:
    words: list[str]
    summary: list[str] = field(default_factory=list)          # translated sentences
    sender: str | None = None   # station transmitting, when it can be told
    glossary: list[tuple[str, str]] = field(default_factory=list)  # (word, translated meaning)

    @property
    def empty(self) -> bool:
        return not self.summary and not self.glossary


def station(call: str, doubtful: bool = False) -> str:
    country = country_for(call)
    mark = "?" if doubtful else ""
    return f"{call}{mark} ({_(country)})" if country else f"{call}{mark}"


def is_noise(word: str) -> bool:
    if "*" in word:
        return True
    return len(word) == 1 and word not in ABBREVIATIONS


def explain_rst(rst: str) -> str:
    digits = rst.translate(CUT_NUMBERS)
    text = _("signal report {rst}: {readability}, strength {s} of 9").format(
        rst=digits, readability=_(R_TEXT[digits[0]]), s=digits[1])
    if digits[:2] == "59":
        text += " " + _("(perfect, often sent by habit in contests)")
    return text


def gloss(word: str) -> str | None:
    """Meaning of one word, already translated, or None if unknown."""
    question = word.endswith("?") and len(word) > 1
    base = word.rstrip("?") if question else word
    if base in QCODE_BY_NAME:
        q = QCODE_BY_NAME[base]
        return _(q.question if question else q.statement)
    if base in ABBREVIATIONS:
        return _(ABBREVIATIONS[base])
    if RST_RE.fullmatch(base) and any(c.isdigit() for c in base):
        return explain_rst(base)
    if CALLSIGN_RE.fullmatch(base):
        country = country_for(base)
        return _("callsign, {country}").format(country=_(country)) if country else _("callsign")
    return None


def _distance(a: str, b: str) -> int:
    """Levenshtein distance (short strings)."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _deletions(word: str) -> set[str]:
    return {word[:i] + word[i + 1:] for i in range(len(word))}


_scp_index: tuple[int, dict[str, set[str]]] | None = None


def _index_for(scp: set[str]) -> dict[str, set[str]]:
    """Map every call and its one-letter deletions to the calls they come from, so the
    callsigns one edit away are found without scanning the whole list (built once)."""
    global _scp_index
    if _scp_index is None or _scp_index[0] != id(scp):
        index: dict[str, set[str]] = {}
        for call in scp:
            for key in _deletions(call) | {call}:
                index.setdefault(key, set()).add(call)
        _scp_index = (id(scp), index)
    return _scp_index[1]


def snap_to_scp(call: str, scp: set[str]) -> str:
    """A callsign missing from MASTER.SCP with exactly one known callsign one letter away
    is taken to be that one (SL2NFC -> DL2NFC): decoders get single letters wrong."""
    if not scp or call in scp:
        return call
    index = _index_for(scp)
    candidates = set()
    for key in _deletions(call) | {call}:
        candidates |= index.get(key, set())
    near = [k for k in candidates if _distance(call, k) == 1]
    return near[0] if len(near) == 1 else call


def merge_variants(calls: list[str], scp: set[str]) -> Counter:
    """Fold decoding variants (EP3BZU, IP3BZU) into the likeliest callsign (SP3BZU)."""
    counts = Counter(calls)
    ranked = sorted(counts, key=lambda c: (c in scp, counts[c], len(c)), reverse=True)
    merged: Counter = Counter()
    for call in ranked:
        target = next((k for k in merged if _distance(call, k) <= 1), None)
        merged[target or call] += counts[call]
    return merged


# Words that may follow the last CQ of a call ("DE EA7XYZ EA7XYZ PSE K"); any more and
# someone has answered, or the caller has moved on to a contact.
CQ_TAIL = 6
# A contest station alternates its call with quick exchanges (5NN 14 TU): one that called
# within this many words is still running the contest.
CONTEST_WINDOW = 30
CONTEST_WORDS = {"TEST", "CONTEST"}


def _calls(words: list[str]) -> list[tuple[int, bool]]:
    """Where each general call is, and whether it is a contest one. "TEST" counts as the
    contest CQ only before a callsign or DE ("TEST EA7XYZ")."""
    found = []
    for i, word in enumerate(words):
        following = words[i + 1:i + 3]
        if word == "CQ":
            found.append((i, bool(set(following) & CONTEST_WORDS)))
        elif word in CONTEST_WORDS and (words[i - 1:i] == ["CQ"] or any(
                w == "DE" or CALLSIGN_RE.fullmatch(w) for w in following)):
            found.append((i, True))
    return found


def calling_cq(text: str) -> bool:
    """Whether the station is calling CQ right now: its latest words are a general call."""
    words = text.upper().split()
    calls = _calls(words)
    return bool(calls) and len(words) - calls[-1][0] - 1 <= CQ_TAIL


def call_kind(text: str) -> str | None:
    """"contest" for a station running a contest (calling or in a quick exchange), "cq" for
    one calling CQ to anyone, None otherwise."""
    words = text.upper().split()
    if any(contest and len(words) - i <= CONTEST_WINDOW for i, contest in _calls(words)):
        return "contest"
    return "cq" if calling_cq(text) else None


def interpret(words: list[str], scp: set[str] | None = None,
              heard: Counter | None = None) -> Interpretation:
    """`heard` counts exact callsign decodes over the whole session (see Interpreter)."""
    scp = scp or set()
    heard = heard or Counter()
    words = [w.upper() for w in words if w and not is_noise(w.upper())]
    result = Interpretation(words)
    if not words:
        return result
    local = Counter(w for w in words if CALLSIGN_RE.fullmatch(w))
    # A callsign decoded identically twice is real even if it never does contests: only
    # one-off decodes are corrected towards MASTER.SCP, and the correction stays doubtful.
    snapped = {w: (w if local[w] + heard[w] >= 2 else snap_to_scp(w, scp))
               for w in local}
    guessed = {new for old, new in snapped.items() if new != old}
    words = [snapped.get(w, w) for w in words]
    raw_calls = [w for w in words if CALLSIGN_RE.fullmatch(w.rstrip("?"))]
    counts = merge_variants(raw_calls, scp)
    # Map every variant to the callsign it was merged into, and use that from now on.
    canonical = {c: next((k for k in counts if _distance(c, k) <= 1), c) for c in set(raw_calls)}
    words = [canonical.get(w, w) for w in words]
    calls = [canonical[c] for c in raw_calls]
    after_de = next((words[i + 1] for i, w in enumerate(words[:-1])
                     if w == "DE" and CALLSIGN_RE.fullmatch(words[i + 1])), None)
    sender = after_de or (counts.most_common(1)[0][0] if counts else None)
    if sender and (after_de or counts[sender] >= 2 or (sender in scp and sender not in guessed)):
        result.sender = sender  # only when it is reasonably sure
    summary = result.summary

    if "CQ" in words or "TEST" in words:  # "TEST" alone is the contest form of CQ
        who = station(sender) if sender else _("Someone")
        if "TEST" in words:
            summary.append(_("{station} is calling CQ in a contest: any station may answer "
                             "with a quick exchange").format(station=who))
        elif "DX" in words:
            summary.append(_("{station} is calling CQ looking for distant stations (DX)")
                           .format(station=who))
        elif "POTA" in words or "SOTA" in words or "WWFF" in words:
            summary.append(_("{station} is calling CQ from a park or summit activation")
                           .format(station=who))
        else:
            summary.append(_("{station} is calling CQ: looking for anyone to talk to")
                           .format(station=who))
    elif after_de:
        before = [c for c in calls if c != after_de]
        if before:
            summary.append(_("{sender} answers {other}").format(
                sender=station(after_de), other=station(before[0])))
        else:
            summary.append(_("{sender} identifies").format(sender=station(after_de)))
    elif any(w.startswith("QRZ") for w in words):
        summary.append(_("Asks who is calling"))
    elif len(counts) == 1:
        summary.append(_("{sender} gives their callsign (calling or answering)").format(
            sender=station(sender, doubtful=sender in guessed
                           or (counts[sender] < 2 and sender not in scp))))
    elif len(counts) >= 2:
        # Heard once and unknown to MASTER.SCP: probably a decoding error, shown with '?'.
        summary.append(_("Callsigns heard: {calls}").format(calls=", ".join(
            station(c, doubtful=c in guessed or (counts[c] < 2 and c not in scp))
            for c, _n in counts.most_common())))

    if "QRL?" in words:
        summary.append(_("Asks whether the frequency is in use"))
    rst = next((w for w in words if RST_RE.fullmatch(w) and any(c.isdigit() for c in w)), None)
    if rst:
        text = explain_rst(rst)
        summary.append(text[0].upper() + text[1:])
        index = words.index(rst)
        if index + 1 < len(words) and words[index + 1].translate(CUT_NUMBERS).isdigit():
            summary.append(_("Contest exchange: {number} (serial number or zone)").format(
                number=words[index + 1].translate(CUT_NUMBERS)))
    for i, word in enumerate(words[:-1]):
        kind = VALUE_AFTER.get(word)
        value = words[i + 1]
        if value == "IS" and i + 2 < len(words):  # NAME IS PEPE, QTH IS MADRID
            value = words[i + 2]
        if kind == "name" and value not in ABBREVIATIONS and value != "IS":
            summary.append(_("Operator name: {name}").format(name=value.title()))
        elif kind == "qth" and value not in ABBREVIATIONS and value != "IS":
            summary.append(_("Location: {place}").format(place=value.title()))
    if any(w in ("TU", "TNX", "TKS", "TY") for w in words):
        summary.append(_("Thanks the other station"))
    if any(w in ("73", "GL", "CUL", "CU", "EE") for w in words):
        summary.append(_("Says goodbye"))
    if "AGN" in words or "?" in words:
        summary.append(_("Asks to repeat"))
    # The closing word may be followed by courtesies ("SK EE"): use the last closing word.
    last = next((w for w in reversed(words) if w in END_TOKENS), "")
    if last in END_OVER:
        summary.append(_("Over: waiting for an answer"))
    elif last in END_ONLY_YOU:
        summary.append(_("Over: only the station called may answer"))
    elif last in END_CONTACT:
        summary.append(_("End of the contact"))

    seen = set()
    for word in words:
        if word in seen:
            continue
        seen.add(word)
        meaning = gloss(word)
        if meaning:
            result.glossary.append((word, meaning))
    return result


class Interpreter:
    """Streaming wrapper: feed decoded characters, collect finished transmissions."""

    def __init__(self, clock=time.monotonic, scp: set[str] | None = None) -> None:
        self._clock = clock
        self.scp = scp or set()
        self.heard: Counter = Counter()  # exact callsign decodes in this conversation
        self._words: list[str] = []
        self._current = ""
        self._last_input = clock()

    def feed(self, text: str) -> list[Interpretation]:
        done: list[Interpretation] = []
        self._last_input = self._clock()
        for ch in text:
            if ch != " ":
                self._current += ch
                continue
            if self._current:
                word, self._current = self._current, ""
                self._words.append(word)
                if word.upper() in END_TOKENS:
                    done.append(self._flush())
        return [d for d in done if not d.empty]

    def tick(self) -> Interpretation | None:
        """Call periodically: a long pause also ends a transmission."""
        if (self._words or self._current) and self._clock() - self._last_input > SILENCE_S:
            if self._current:
                self._words.append(self._current)
                self._current = ""
            result = self._flush()
            return None if result.empty else result
        return None

    def _flush(self) -> Interpretation:
        words, self._words = self._words, []
        result = interpret(words, self.scp, self.heard)
        self.heard.update(w.upper() for w in words if CALLSIGN_RE.fullmatch(w.upper()))
        return result
