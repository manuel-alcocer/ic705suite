"""How much the suite itself takes: CPU and memory of this process, as `top` shows them.

%CPU is the process's CPU time over the last interval (100 % = one core busy; it can go
above with several threads). %RAM is its resident memory over the machine's total. Read from
/proc on Linux, with the standard library elsewhere (memory then unknown). One sampler for
the whole process, shared by every status line, and sampled at most once a second.
"""

from __future__ import annotations

import os
import time

from rich.text import Text

MIN_INTERVAL = 1.0


def _cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _total_ram() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


class ProcessLoad:
    def __init__(self) -> None:
        self._last = (time.monotonic(), _cpu_seconds())
        self._total = _total_ram()
        self.cpu = 0.0
        self.ram: float | None = None
        self.rss: int | None = None

    def sample(self) -> "ProcessLoad":
        now, cpu = time.monotonic(), _cpu_seconds()
        then, cpu_then = self._last
        if now - then >= MIN_INTERVAL:
            self.cpu = max(0.0, (cpu - cpu_then) / (now - then) * 100)
            self._last = (now, cpu)
            self.rss = _rss_bytes()
            self.ram = self.rss / self._total * 100 if self.rss and self._total else None
        return self


LOAD = ProcessLoad()


def load_text() -> Text:
    """'CPU 12% · RAM 1.3% 98M', coloured when the process is busy."""
    load = LOAD.sample()
    text = Text(no_wrap=True)
    text.append("CPU ", style="dim")
    text.append(f"{load.cpu:.0f}%", style="bold red" if load.cpu >= 80
                else "bold yellow" if load.cpu >= 50 else "white")
    if load.ram is not None:
        text.append(" · ", style="dim")
        text.append("RAM ", style="dim")
        text.append(f"{load.ram:.1f}%", style="bold yellow" if load.ram >= 25 else "white")
        text.append(f" {load.rss / 2**20:.0f}M", style="dim")
    return text


def with_load(text: Text, width: int) -> Text:
    """`text` with the load at the right end of a line `width` wide (dropped if it does not
    fit, so the rig information always wins)."""
    load = load_text()
    room = width - text.cell_len - load.cell_len - 1
    if room < 2:
        return text
    return Text.assemble(text, " " * room, load, " ", no_wrap=True)
