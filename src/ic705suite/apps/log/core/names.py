"""Capitalisation of personal names and places.

Names, surnames and QTHs are stored with every word capitalised, however they
were typed: ``mAnuel angel`` becomes ``Manuel Angel``. Particles stay lower
case inside a name: ``María de los Ángeles``, ``Alcalá de Henares``. Callsigns
go the other way, all upper case (see :func:`hamrlog.core.callsign.normalize`).
"""

from __future__ import annotations

import re

# A run of letters: a hyphen or an apostrophe starts a new one, so
# "jean-pierre" and "o'brien" give "Jean-Pierre" and "O'Brien".
_LETTERS = re.compile(r"[^\W\d_]+")

# Lower case unless they open the name: "de la Fuente" in the middle of a
# full name, "De la Fuente" alone in the surname field.
_PARTICLES = frozenset(
    {"de", "del", "la", "las", "los", "el", "y", "e", "da", "di", "du", "van", "von", "der", "den"}
)

# A word ending in one of these, or with no letters (a lone dash), opens a
# new part of a place: "Sierra de Béjar - La Covatilla".
_PHRASE_BREAKS = ",;:-/("


def title_case(text: str) -> str:
    """Capitalise each word of a name or a place and lower-case the rest.

    Particles (``de``, ``la``, ``y``…) go lower case except at the start.
    Surrounding and repeated spaces go. A word with a digit in it is left as
    typed, so a locator or a street number in a QTH keeps its letters
    (``IN80dk``, ``3B``).
    """
    words = []
    opens_phrase = True
    for word in text.split():
        if any(char.isdigit() for char in word):
            words.append(word)
        elif not opens_phrase and word.lower() in _PARTICLES:
            words.append(word.lower())
        else:
            words.append(_LETTERS.sub(lambda match: match.group(0).capitalize(), word))
        opens_phrase = not any(char.isalnum() for char in word) or word[-1] in _PHRASE_BREAKS
    return " ".join(words)
