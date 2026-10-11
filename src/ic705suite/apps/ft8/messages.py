"""What an FT8 message says: who calls, whom, and with what (a grid, a report, RR73...).

The standard messages are three words: ``CQ [DX|NA|...] CALL GRID``, ``TO FROM GRID``,
``TO FROM -12``, ``TO FROM R-12``, ``TO FROM RRR``, ``TO FROM RR73``, ``TO FROM 73``.
Calls jt9 could not resolve come as ``<...>``; free text is anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GRID = re.compile(r"^[A-R]{2}\d{2}$")
REPORT = re.compile(r"^R?[+-]\d{2}$")
ENDINGS = ("RRR", "RR73", "73")
CALL = re.compile(r"^<?[A-Z0-9/]*\d[A-Z0-9/]*>?$|^<\.\.\.>$")   # <...>: not resolved


@dataclass
class Message:
    kind: str              # cq, call (TO FROM ...), free
    to: str = ""           # whom it is for ("" in a CQ)
    sender: str = ""       # who sends it
    grid: str = ""
    report: str = ""       # "-12", "R-12"
    end: str = ""          # RRR, RR73 or 73
    cq_to: str = ""        # CQ DX, CQ NA... (the "DX")

    def for_me(self, mycall: str) -> bool:
        return bool(mycall) and base(self.to) == base(mycall)


def base(call: str) -> str:
    """EA7KLX/P, <EA7KLX> -> EA7KLX."""
    call = call.strip("<>").upper()
    parts = [p for p in call.split("/") if p]
    return max(parts, key=lambda p: (any(c.isdigit() for c in p), len(p))) if parts else ""


def parse(text: str) -> Message:
    words = text.upper().split()
    if not words:
        return Message("free")
    if words[0] == "CQ":
        rest = words[1:]
        cq_to = ""
        if len(rest) >= 2 and not CALL.match(rest[0]):
            cq_to, rest = rest[0], rest[1:]
        if rest and CALL.match(rest[0]):
            grid = rest[1] if len(rest) > 1 and GRID.match(rest[1]) else ""
            return Message("cq", sender=rest[0].strip("<>"), grid=grid, cq_to=cq_to)
        return Message("free")
    if len(words) >= 2 and CALL.match(words[0]) and CALL.match(words[1]):
        message = Message("call", to=words[0].strip("<>"), sender=words[1].strip("<>"))
        last = words[2] if len(words) > 2 else ""
        if last in ENDINGS:             # RR73 is a grid too (in the Arctic): an ending first
            message.end = last
        elif GRID.match(last):
            message.grid = last
        elif REPORT.match(last):
            message.report = last
        return message
    return Message("free")
