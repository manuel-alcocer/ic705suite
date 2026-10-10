"""Morse code tables, text normalization and timing helpers."""

from __future__ import annotations

import re

MORSE: dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "/": "-..-.",
    "(": "-.--.", ")": "-.--.-", ":": "---...", "=": "-...-", "+": ".-.-.",
    "-": "-....-", '"': ".-..-.", "@": ".--.-.",
}

# Patterns that only make sense on receive (not sendable via CI-V as single chars).
_DECODE_EXTRA: dict[str, str] = {
    "...-.-": "<SK>",
    ".-...": "<AS>",
    "-.-.--": "!",
    "........": "<HH>",
    "...---...": "<SOS>",
    "-.-.-": "<KA>",
    "...-.": "<SN>",
}

DECODE: dict[str, str] = {v: k for k, v in MORSE.items()} | _DECODE_EXTRA

# Prosigns accepted in the TX text, mapped to the IC-705 keyer syntax.
# The keyer joins the characters following '^' without inter-character gap.
PROSIGNS_TX: dict[str, str] = {
    "<AR>": "+",
    "<BT>": "=",
    "<KN>": "(",
    "<SK>": "^SK",
    "<AS>": "^AS",
    "<BK>": "^BK",
    "<HH>": "^HH",
    "<KA>": "^KA",
    "<SN>": "^SN",
}

ICOM_CW_MAX_CHARS = 30
_ICOM_ALLOWED = set(MORSE) | {" ", "^"}


def normalize_tx(text: str) -> tuple[str, str]:
    """Convert free text to IC-705 keyer syntax.

    Returns (normalized_text, dropped_chars). Unsupported characters are dropped.
    """
    text = text.upper()
    for token, repl in PROSIGNS_TX.items():
        text = text.replace(token, repl)
    text = re.sub(r"\s+", " ", text).strip()
    kept, dropped = [], []
    for ch in text:
        (kept if ch in _ICOM_ALLOWED else dropped).append(ch)
    return re.sub(r" +", " ", "".join(kept)).strip(), "".join(sorted(set(dropped)))


def chunk_for_icom(text: str, size: int = ICOM_CW_MAX_CHARS) -> list[str]:
    """Split normalized text into chunks the radio accepts (<= size chars).

    Splits on word boundaries; never breaks a '^XY' prosign group.
    """
    chunks: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # Leading space keeps the word gap between consecutive chunks.
        word = " " + word if chunks else word
        while len(word) > size:
            cut = size
            # Avoid splitting a '^' group (the '^' plus the two letters after it).
            caret = word.rfind("^", max(0, cut - 2), cut)
            if caret != -1:
                cut = caret if caret > 0 else cut
            chunks.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        chunks.append(current)
    return chunks


def char_units(ch: str) -> int:
    """Duration in dit units of a single character including the following char gap."""
    if ch == " ":
        return 4  # word gap is 7 units: 3 already counted after the previous char
    code = MORSE.get(ch)
    if code is None:
        return 0
    return sum(1 if e == "." else 3 for e in code) + (len(code) - 1) + 3


def text_units(text: str) -> int:
    """Duration in dit units of a normalized text ('^' groups counted as plain letters)."""
    return sum(char_units(ch) for ch in text if ch != "^")


def dit_seconds(wpm: float) -> float:
    """PARIS standard: dit length in seconds for a given speed."""
    return 1.2 / wpm


def text_duration(text: str, wpm: float) -> float:
    return text_units(text) * dit_seconds(wpm)


def elements(text: str) -> list[tuple[bool, int]]:
    """Expand text into keying segments (key_down, units). Used by the simulator and tests."""
    out: list[tuple[bool, int]] = []
    for word_index, word in enumerate(text.upper().split()):
        if word_index:
            out.append((False, 7))
        for char_index, ch in enumerate(word):
            code = MORSE.get(ch)
            if code is None:
                continue
            if char_index:
                out.append((False, 3))
            for i, e in enumerate(code):
                if i:
                    out.append((False, 1))
                out.append((True, 1 if e == "." else 3))
    return out
