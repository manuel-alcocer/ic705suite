"""The super-automatic mode: the core works the band by itself until it is turned off.

Each cycle heard, with nothing under way, it answers the best station there is:

    1. a station calling us
    2. a CQ from an entity never worked (new DXCC), then one new on this band
    3. a CQ from the entity worked least
    4. a CQ from our own country, last

never one worked on this band before, nor one called lately without an answer, nor a CQ
meant for others (CQ NA from Europe). Each station gets TRIES_EACH calls. After STATIONS
stations without a QSO (or with nobody to call) it calls CQ CQS times; nobody answering,
it goes back to answering. A QSO made starts the count again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ic705suite import geo
from ic705suite.apps.ft8.messages import base

TRIES_EACH = 3             # calls to one station without an answer
STATIONS = 6               # stations called without a QSO: then CQ
CQS = 6                    # CQs with nobody answering: back to answering
RETRY_AFTER = 15 * 60.0    # seconds before a station that did not answer is called again
QUIET_CYCLES = 2           # cycles (both turns) with nobody to call: then CQ


@dataclass
class Auto:
    on: bool = False
    phase: str = "hunt"                 # hunt (answering), cq
    missed: int = 0                     # stations called without a QSO
    quiet: int = 0                      # cycles in a row with nobody to call
    tried: dict[str, float] = field(default_factory=dict)   # call -> when it was called

    def start(self) -> None:
        self.on, self.phase, self.missed, self.quiet = True, "hunt", 0, 0

    def stop(self) -> None:
        self.on = False

    def worked_one(self) -> None:
        self.phase, self.missed, self.quiet = "hunt", 0, 0

    def missed_one(self, call: str, now: float) -> None:
        self.tried[base(call)] = now
        self.missed += 1

    def cq_done(self) -> None:
        self.phase, self.missed, self.quiet = "hunt", 0, 0

    def wants_cq(self) -> bool:
        return self.missed >= STATIONS or self.quiet >= QUIET_CYCLES

    def pick(self, decodes: list[dict], mycall: str, now: float) -> dict | None:
        """The station to answer among a cycle's decodes, or None."""
        mine = geo.entity_for(mycall)
        home = mine.name if mine is not None else ""
        continent = mine.continent if mine is not None else ""
        for old in [c for c, when in self.tried.items() if now - when > RETRY_AFTER]:
            del self.tried[old]
        best, best_rank = None, None
        for d in decodes:
            rank = self._rank(d, mycall, home, continent)
            if rank is not None and (best_rank is None or rank < best_rank):
                best, best_rank = d, rank
        return best

    def _rank(self, d: dict, mycall: str, home: str, continent: str) -> tuple | None:
        sender = d.get("sender") or ""
        if not sender or sender.startswith("<") or "." in sender or \
                base(sender) == base(mycall):
            return None
        snr = -int(d.get("snr") or 0)
        if d.get("kind") == "call" and base(d.get("to") or "") == base(mycall):
            # Calling us with its grid: answered first, worked before or not.
            return (0, 0, snr) if d.get("grid") else None
        if d.get("kind") != "cq" or d.get("b4") or base(sender) in self.tried:
            return None
        if not self._for_us(d.get("cq_to") or "", d.get("continent") or "", continent):
            return None
        country, dxcc = d.get("country") or "", d.get("dxcc") or ""
        if country and country == home:
            return (4, 0, snr)
        if dxcc == "new":
            return (1, 0, snr)
        if dxcc == "new_band":
            return (2, 0, snr)
        return (3, int(d.get("dxcc_count") or 0), snr)

    @staticmethod
    def _for_us(cq_to: str, theirs: str, ours: str) -> bool:
        """A plain CQ, CQ DX from another continent, CQ to our continent (CQ EU); not CQ
        POTA, CQ TEST... (an activity we are not in)."""
        if not cq_to:
            return True
        if cq_to == "DX":
            return bool(theirs and ours and theirs != ours)
        return cq_to == ours
