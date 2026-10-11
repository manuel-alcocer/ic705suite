"""How much the suite itself takes: CPU and memory of this process, as `top` shows them.

%CPU is the process's CPU time over the last interval (100 % = one core busy; it can go
above with several threads). %RAM is its resident memory over the machine's total. Read from
/proc on Linux, from the Win32 API on Windows, with the standard library elsewhere (memory
then unknown). One sampler for the whole process, shared by every status line, and sampled
at most once a second.
"""

from __future__ import annotations

import os
import sys
import time

from rich.text import Text

MIN_INTERVAL = 1.0


def _cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system


def _rss_bytes() -> int | None:
    if sys.platform == "win32":
        return _win_rss()
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _total_ram() -> int | None:
    if sys.platform == "win32":
        return _win_total_ram()
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _win_rss() -> int | None:
    """Working set of this process (what Task Manager calls memory)."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32")    # its own prototypes, not the shared windll's
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters),
                                                 wintypes.DWORD]
    counters = Counters(cb=ctypes.sizeof(Counters))
    try:
        ok = kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                              ctypes.byref(counters), counters.cb)
    except (OSError, ctypes.ArgumentError):
        return None
    return counters.WorkingSetSize if ok else None


def _win_total_ram() -> int | None:
    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(Status)]
    status = Status(dwLength=ctypes.sizeof(Status))
    try:
        ok = kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (OSError, ctypes.ArgumentError):
        return None
    return status.ullTotalPhys if ok else None


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
