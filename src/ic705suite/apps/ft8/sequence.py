"""The FT8 QSO, as WSJT-X sequences it: which of the six standard messages goes next, from
what the other station sent us.

    Tx1  THEM ME GRID     answering their CQ
    Tx2  THEM ME -12      the report (answering their call to our CQ)
    Tx3  THEM ME R-12     their report received, and ours
    Tx4  THEM ME RR73     all received: the QSO is complete for us
    Tx5  THEM ME 73       the last word
    Tx6  CQ ME GRID       calling

A report is the other station's signal as we hear it (the SNR of the decode we answer).
The same message goes again while the other station repeats theirs (it did not hear ours),
until the tries run out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ic705suite.apps.ft8.messages import base, parse

REPORT = re.compile(r"^R?([+-]\d{2})$")
TRIES = 6                  # the same message sent this many times without an answer: stop


def report(snr: int) -> str:
    """-12, +05: two digits and the sign, within FT8's -30..+49."""
    snr = max(-30, min(49, snr))
    return f"{snr:+03d}"


@dataclass
class Qso:
    mycall: str
    mygrid: str = ""
    them: str = ""                 # the other station ("" while calling CQ)
    their_grid: str = ""
    sent_report: str = ""          # ours to them: how we hear them
    got_report: str = ""           # theirs to us
    step: str = "idle"             # cq, tx1..tx5, done, idle
    message: str = ""              # what goes next
    tries: int = 0
    log: list[str] = field(default_factory=list)   # what was sent, in order

    # ---- starting ---------------------------------------------------------------------------

    def call_cq(self) -> str:
        self.them, self.their_grid, self.sent_report, self.got_report = "", "", "", ""
        return self._go("cq", " ".join(filter(None, ["CQ", self.mycall, self.mygrid])))

    def answer(self, decode: dict) -> str:
        """Answer a station heard (its CQ, or anything it sent): Tx1, or straight to the
        report when it was already calling us."""
        self.them = decode["sender"]
        self.their_grid = decode.get("grid") or ""
        self.got_report = ""
        self.sent_report = report(decode["snr"])
        if self._for_me(decode) and decode.get("grid"):
            return self._go("tx2", self._to(self.sent_report))
        return self._go("tx1", self._to(self.mygrid))

    # ---- what comes back ----------------------------------------------------------------------

    def heard(self, decode: dict) -> bool:
        """A decode of this cycle: True when it moves the QSO on (a new message next)."""
        if self.step in ("idle", "done") or not self._for_me(decode):
            return False
        sender = base(decode["sender"])
        if self.step == "cq":
            if decode.get("kind") != "call":
                return False
            self.them = decode["sender"]
            self.their_grid = decode.get("grid") or ""
            self.sent_report = report(decode["snr"])
            if decode.get("report", "").startswith("R"):          # they skipped a step
                self.got_report = decode["report"][1:]
                self._go("tx4", self._to("RR73"))
            else:
                self._go("tx2", self._to(self.sent_report))
            return True
        if sender != base(self.them):
            return False                                          # another station: later
        their = decode.get("report", "")
        end = decode.get("end", "")
        if self.step == "tx1" and their and not their.startswith("R"):
            self.got_report = their
            self.sent_report = report(decode["snr"])
            self._go("tx3", self._to("R" + self.sent_report))
            return True
        if self.step == "tx2" and their.startswith("R"):
            self.got_report = their[1:]
            self._go("tx4", self._to("RR73"))
            return True
        if self.step in ("tx1", "tx3") and end in ("RRR", "RR73"):
            self._go("tx5", self._to("73"))
            return True
        if self.step in ("tx3", "tx4", "tx5") and end == "73":
            self.step = "done"
            return True
        if self.step == "tx1" and their.startswith("R"):          # they skipped our report
            self.got_report = their[1:]
            self._go("tx4", self._to("RR73"))
            return True
        return False

    def sent(self) -> None:
        """Our message went out: count the tries; Tx4 and Tx5 end the QSO once sent."""
        self.log.append(self.message)
        self.tries += 1
        if self.step == "tx5" or (self.step == "tx4" and self.tries >= 2):
            self.step = "done"

    @property
    def complete(self) -> bool:
        """Reports exchanged both ways: the QSO counts (for the logbook)."""
        return bool(self.them and self.sent_report and self.got_report) and \
            any(m.endswith(("RR73", " 73", "R" + self.sent_report)) for m in self.log)

    @property
    def gave_up(self) -> bool:
        return self.step not in ("cq", "done", "idle") and self.tries >= TRIES

    def stop(self) -> None:
        self.step, self.message = "idle", ""

    # ---- helpers ------------------------------------------------------------------------------

    def _for_me(self, decode: dict) -> bool:
        return decode.get("kind") == "call" and base(decode.get("to", "")) == base(self.mycall)

    def _to(self, last: str) -> str:
        return " ".join(filter(None, [self.them, self.mycall, last]))

    def _go(self, step: str, message: str) -> str:
        if message != self.message:
            self.tries = 0
        self.step, self.message = step, message
        return message


def parse_decode(text: str, snr: int = 0) -> dict:
    """A message as the service hands it over (tests and the screens' own messages)."""
    from dataclasses import asdict

    return {**asdict(parse(text)), "message": text, "snr": snr}
