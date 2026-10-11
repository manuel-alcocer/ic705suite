"""Direwolf run by the core ([packet] tnc = "direwolf"): its configuration written from the
suite's settings, the program started, watched (its output kept, the errors noted) and
started again if it stops, and the Digirig found among the machine's devices.

Direwolf (GPLv2) stays a program of its own: the suite only starts it and talks to it
over its AGWPE port.

The process is read by threads, not asyncio subprocesses: the graphical client's event
loop (qasync) cannot run those everywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_LINES = 300
RESTART_DELAYS = (3, 5, 10, 30, 60)      # seconds, growing while it keeps failing
STABLE = 60                               # running this long resets the delays
CM108_VENDOR = "0d8c"                     # C-Media: the Digirig's sound chip (CM108B)
CP210X = "10c4:ea60"                      # Silicon Labs CP210x: its serial port
READY = re.compile(r"Ready to accept AGW client", re.IGNORECASE)
ERRORS = re.compile(r"could not open|error|bind failed|failed to|no such|permission denied|"
                    r"not found|unable to", re.IGNORECASE)


@dataclass
class Settings:
    """What direwolf.conf says, from [packet]."""
    mycall: str
    audio: str                 # ADEVICE: "plughw:Device,0", "USB Audio Device"...
    ptt: str = ""              # "/dev/ttyUSB0 RTS", "COM5 RTS", "CM108", "" (VOX: none)
    modem: int = 1200
    txdelay: int = 30          # tens of ms
    txtail: int = 10
    agw_port: int = 8000
    kiss_port: int = 0         # 0: no KISS over TCP


def config_text(settings: Settings) -> str:
    ptt = f"PTT {settings.ptt}\n" if settings.ptt else \
        "# No PTT line: the radio's VOX keys it\n"
    return (
        "# Written by ic705suite from [packet] in its configuration: changes here are\n"
        "# lost the next time it starts Direwolf.\n"
        f"ADEVICE {settings.audio}\n"
        "ARATE 48000\n"
        "ACHANNELS 1\n"
        "CHANNEL 0\n"
        f"MYCALL {settings.mycall}\n"
        f"MODEM {settings.modem}\n"
        f"{ptt}"
        f"TXDELAY {settings.txdelay}\n"
        f"TXTAIL {settings.txtail}\n"
        f"AGWPORT {settings.agw_port}\n"
        f"KISSPORT {settings.kiss_port}\n"
    )


def find_direwolf(configured: str = "") -> str | None:
    """The program: [packet] direwolf if given, else on the PATH (or where its Windows
    installer leaves it)."""
    if configured:
        path = shutil.which(configured) or (configured if Path(configured).is_file() else None)
        return path
    found = shutil.which("direwolf")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in (Path("C:/direwolf/direwolf.exe"),
                          Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                          / "direwolf" / "direwolf.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


# ---- the Digirig ---------------------------------------------------------------------------

@dataclass
class Device:
    """A Digirig (or another CM108 sound card with a CP210x next to it) as Direwolf takes it."""
    audio: str                 # ADEVICE
    ptt: str                   # PTT
    description: str


def _usb_path(sysfs: Path) -> str:
    """The USB port a device hangs from, e.g. "1-1.1" for ".../1-1.1.2:1.0"."""
    try:
        real = os.path.realpath(sysfs)
    except OSError:
        return ""
    ports = re.findall(r"/(\d+-[\d.]+)(?=[/:])", real)
    return ports[-1] if ports else ""


def _parent(port: str) -> str:
    return port.rsplit(".", 1)[0] if "." in port else port


def _linux_devices(asound: Path = Path("/proc/asound"), sys_root: Path = Path("/sys"),
                   by_id: Path = Path("/dev/serial/by-id")) -> list[Device]:
    cards = []
    for card in sorted(asound.glob("card[0-9]*")):
        usbid = (card / "usbid").read_text().strip() if (card / "usbid").exists() else ""
        if not usbid.startswith(CM108_VENDOR + ":"):
            continue
        card_id = (card / "id").read_text().strip()
        port = _usb_path(sys_root / "class" / "sound" / card.name / "device")
        cards.append((card_id, port))
    serials = []
    if by_id.is_dir():
        for link in sorted(by_id.iterdir()):
            if "CP210" not in link.name.upper():
                continue
            name = os.path.basename(os.path.realpath(link))
            port = _usb_path(sys_root / "class" / "tty" / name / "device")
            serials.append((str(link), port))
    devices = []
    for card_id, port in cards:
        # The serial port on the same hub as the sound card (a Digirig is both).
        serial = next((s for s, p in serials if p and port and _parent(p) == _parent(port)),
                      serials[0][0] if len(cards) == 1 and serials else "")
        tty = os.path.basename(os.path.realpath(serial)) if serial else ""
        devices.append(Device(f"plughw:{card_id},0", f"{serial} RTS" if serial else "CM108",
                              f"sound card {card_id}" + (f" + {tty}" if tty else "")))
    return devices


def _windows_devices() -> list[Device]:
    """The CP210x COM port by its name in the device list; Direwolf finds the sound card by
    a piece of its name."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'CP210' } | "
             "ForEach-Object { $_.Name }"],
            capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ports = re.findall(r"\((COM\d+)\)", out)
    return [Device("USB Audio Device", f"{port} RTS", f"USB Audio Device + {port}")
            for port in ports[:1]]


def find_devices() -> list[Device]:
    """The Digirigs plugged in here (best effort: an empty list when none is recognised)."""
    try:
        if sys.platform.startswith("linux"):
            return _linux_devices()
        if sys.platform == "win32":
            return _windows_devices()
    except OSError as exc:
        log.info("looking for a Digirig: %s", exc)
    return []


# ---- the process ---------------------------------------------------------------------------

class Direwolf:
    """Starts Direwolf and keeps it running until stop(). Its state: stopped, starting,
    running (ready for AGWPE clients), waiting (to start again after it stopped), missing
    (no program) or failed (it keeps stopping)."""

    def __init__(self, command: list[str], conf: Path, text: str,
                 on_change: Callable[[], None] = lambda: None) -> None:
        self.command = command
        self.conf = conf
        self.text = text
        self.on_change = on_change
        self.lines: deque[str] = deque(maxlen=KEEP_LINES)
        self.state = "stopped"
        self.error = ""
        self.pid = 0
        self.restarts = 0
        self._asked = False                # restart() asked for: no wait, not a failure
        self._process: subprocess.Popen | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self._task is None:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(self._end)
        self._set("stopped")

    def restart(self) -> None:
        """Start it again now (its settings may have changed)."""
        process = self._process
        if process is not None and process.poll() is None:
            self._asked = True
            process.terminate()

    def _set(self, state: str, error: str | None = None) -> None:
        self.state = state
        if error is not None:
            self.error = error
        self.on_change()

    async def _run(self) -> None:
        failures = 0
        while True:
            started = time.monotonic()
            try:
                self.conf.parent.mkdir(parents=True, exist_ok=True)
                self.conf.write_text(self.text, encoding="utf-8")
                self._spawn()
            except OSError as exc:
                self._set("missing" if isinstance(exc, FileNotFoundError) else "failed",
                          str(exc))
                return
            code = await asyncio.to_thread(self._process.wait)
            self.pid = 0
            if time.monotonic() - started > STABLE:
                failures = 0
            if self._asked:
                self._asked = False
                self._line("*** Direwolf stopped to start again")
                continue
            delay = RESTART_DELAYS[min(failures, len(RESTART_DELAYS) - 1)]
            failures += 1
            self.restarts += 1
            self._line(f"*** Direwolf stopped (exit code {code}); starting it again in "
                       f"{delay} s")
            self._set("failed" if failures >= len(RESTART_DELAYS) else "waiting",
                      self.error or f"exit code {code}")
            await asyncio.sleep(delay)

    def _spawn(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.error = ""
        # Run in the file's folder and given its bare name: Direwolf cuts long paths short
        # (and then runs with its defaults, AGWPE 8000 and KISS 8001).
        self._process = subprocess.Popen(
            [*self.command, "-t", "0", "-c", self.conf.name], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(self.conf.parent),
            creationflags=flags)
        self.pid = self._process.pid
        self._line(f"*** Started {' '.join(self.command)} (pid {self.pid})")
        self._set("starting")
        threading.Thread(target=self._read, args=(self._process,), daemon=True,
                         name="direwolf-output").start()

    def _read(self, process: subprocess.Popen) -> None:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line and self._loop is not None and not self._loop.is_closed():
                with contextlib.suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(self._output, line)

    def _output(self, line: str) -> None:
        self._line(line)
        if READY.search(line):
            self._set("running")
        elif ERRORS.search(line):
            self.error = line.strip()
            self.on_change()

    def _line(self, line: str) -> None:
        self.lines.append(line)
        log.debug("direwolf: %s", line)

    def _end(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
