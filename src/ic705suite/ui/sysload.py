"""How much the suite itself takes: CPU and memory of this process, as `top` shows them, and
how warm the machine's CPU is.

%CPU is the process's CPU time over the last interval (100 % = one core busy; it can go
above with several threads). %RAM is its resident memory over the machine's total. Read from
/proc on Linux, from the Win32 API on Windows, with the standard library elsewhere (memory
then unknown). One sampler for the whole process, shared by every status line, and sampled
at most once a second. The temperature comes from Linux's sensors (none elsewhere).

With the radio's core on another machine (a remote link), the status lines show its load
too: the core sends it with the radio's state (`host`), and the link keeps it here (CORE).
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

from rich.text import Text

MIN_INTERVAL = 1.0
STALE = 10.0                 # seconds: an older core load is not shown
# Sensors of the CPU, in order of preference: hwmon chips (a PC), then thermal zones.
HWMON_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal")
THERMAL_ZONES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal", "TCPU",
                 "acpitz")


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


def _sensor(root: Path = Path("/sys/class")) -> Path | None:
    """The file with the CPU's temperature in millidegrees, if Linux has one."""
    found: dict[str, Path] = {}
    for chip in (root / "hwmon").glob("hwmon*"):
        try:
            name = (chip / "name").read_text().strip()
        except OSError:
            continue
        if name in HWMON_CHIPS and (chip / "temp1_input").exists():
            found.setdefault(name, chip / "temp1_input")
    for name in HWMON_CHIPS:
        if name in found:
            return found[name]
    for zone in (root / "thermal").glob("thermal_zone*"):
        try:
            kind = (zone / "type").read_text().strip()
        except OSError:
            continue
        if kind in THERMAL_ZONES:
            found.setdefault(kind, zone / "temp")
    return next((found[kind] for kind in THERMAL_ZONES if kind in found), None)


def _temperature(sensor: Path | None) -> float | None:
    if sensor is None:
        return None
    try:
        return int(sensor.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None


def host_name() -> str:
    return socket.gethostname().split(".")[0][:12]


class ProcessLoad:
    def __init__(self) -> None:
        self._last = (time.monotonic(), _cpu_seconds())
        self._total = _total_ram()
        self.cpu = 0.0
        self.ram: float | None = None
        self.rss: int | None = None
        self.temp: float | None = None
        self._sensor = _sensor() if sys.platform.startswith("linux") else None

    def sample(self) -> ProcessLoad:
        now, cpu = time.monotonic(), _cpu_seconds()
        then, cpu_then = self._last
        if now - then >= MIN_INTERVAL:
            self.cpu = max(0.0, (cpu - cpu_then) / (now - then) * 100)
            self._last = (now, cpu)
            self.rss = _rss_bytes()
            self.ram = self.rss / self._total * 100 if self.rss and self._total else None
            self.temp = _temperature(self._sensor)
        return self

    def as_dict(self) -> dict:
        """For the clients of a core (`host` in the radio's state)."""
        self.sample()
        return {"name": host_name(), "cpu": round(self.cpu, 1),
                "ram": None if self.ram is None else round(self.ram, 1), "rss": self.rss,
                "temp": None if self.temp is None else round(self.temp, 1)}


LOAD = ProcessLoad()
# The radio's core's load when it runs on another machine: (its `host` dict, when it came).
CORE: dict = {}


def set_core(host: dict | None) -> None:
    if host:
        CORE.update(host, received=time.monotonic())


def core_load() -> dict | None:
    if CORE and time.monotonic() - CORE.get("received", 0) < STALE:
        return CORE
    return None


def cpu_style(cpu: float) -> str:
    return "bold red" if cpu >= 80 else "bold yellow" if cpu >= 50 else "white"


def temp_style(temp: float) -> str:
    return "bold red" if temp >= 80 else "bold yellow" if temp >= 70 else "white"


def _load_part(text: Text, cpu: float, ram: float | None, rss: int | None,
               temp: float | None) -> None:
    text.append("CPU ", style="dim")
    text.append(f"{cpu:.0f}%", style=cpu_style(cpu))
    if ram is not None:
        text.append(" · ", style="dim")
        text.append("RAM ", style="dim")
        text.append(f"{ram:.1f}%", style="bold yellow" if ram >= 25 else "white")
        if rss:
            text.append(f" {rss / 2**20:.0f}M", style="dim")
    if temp is not None:
        text.append(" · ", style="dim")
        text.append(f"{temp:.0f}°C", style=temp_style(temp))


def load_text() -> Text:
    """'CPU 12% · RAM 1.3% 98M · 52°C', coloured when the process is busy or the machine
    warm; with the core on another machine, its load too, each after its machine's name."""
    load = LOAD.sample()
    core = core_load()
    text = Text(no_wrap=True)
    if core is not None:
        text.append(host_name() + " ", style="bold cyan")
    _load_part(text, load.cpu, load.ram, load.rss, load.temp)
    if core is not None:
        text.append(" │ ", style="dim")
        text.append(str(core.get("name") or "core") + " ", style="bold cyan")
        _load_part(text, float(core.get("cpu") or 0), core.get("ram"), core.get("rss"),
                   core.get("temp"))
    return text


def version_text() -> Text:
    from ic705suite import __version__

    return Text(f"v{__version__}", style="bold cyan", no_wrap=True)


def with_load(text: Text, width: int) -> Text:
    """`text` with the load and the suite's version at the right end of a line `width`
    wide. The version is always there: the load goes first when there is no room, and the
    line's own end makes way for the version when even that does not fit."""
    version = version_text()
    load = Text.assemble(load_text(), (" · ", "dim"), version)
    for right in (load, version):
        room = width - text.cell_len - right.cell_len - 1
        if room >= 2:
            return Text.assemble(text, " " * room, right, " ", no_wrap=True)
    keep = max(0, width - version.cell_len - 3)
    head = text.copy()
    head.truncate(keep, overflow="ellipsis")
    return Text.assemble(head, "  ", version, " ", no_wrap=True)
