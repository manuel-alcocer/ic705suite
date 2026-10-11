"""Smoke test of the Windows executable: the server with the simulator (or, with --gui, the
graphical client, offscreen) starts, and Ctrl+Break stops it by itself and cleanly (the core
closed, exit code 0), as Ctrl+C does in a console.

Ctrl+Break is sent once the suite's log says it is ready: a first start can take a while (the
bundle unpacks, the antivirus looks at it) and a signal before then would just kill it.

    python packaging/windows/smoke.py dist/ic705suite.exe [--gui]
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

START_S = 120          # at most, for the log to say the suite is ready
STOP_S = 30
READY = {False: "listening on", True: "graphical client ready"}


def log_file() -> Path:
    """The suite's log on Windows (ic705suite.paths.state_dir)."""
    return Path(os.environ["LOCALAPPDATA"]) / "ic705suite" / "logs" / "ic705suite.log"


def ready(gui: bool, before: int) -> bool:
    try:
        return READY[gui] in log_file().read_text(encoding="utf-8", errors="replace")[before:]
    except OSError:
        return False


def kill(process: subprocess.Popen) -> str:
    """The whole tree: the bundle's executable runs the suite in a child process, which
    would keep the output pipe open."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    try:
        return process.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        return ""


def main(exe: str, gui: bool = False) -> int:
    sys.stdout.reconfigure(errors="replace")
    try:
        before = len(log_file().read_text(encoding="utf-8", errors="replace"))
    except OSError:
        before = 0
    command = [exe, "--gui", "--sim"] if gui else \
        [exe, "--sim", "--serve", "--listen", "127.0.0.1:7399", "--token", "smoke"]
    server = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"},
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    deadline = time.monotonic() + START_S
    while not ready(gui, before) and server.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    if server.poll() is None and not ready(gui, before):
        print(kill(server))
        print(f"the suite did not say it was ready within {START_S} s")
        return 1
    time.sleep(2)                     # running for a moment, not just started
    if server.poll() is not None:
        print(server.stdout.read())
        print(f"the server stopped by itself with code {server.returncode}")
        return 1
    server.send_signal(signal.CTRL_BREAK_EVENT)
    try:
        output, _ = server.communicate(timeout=STOP_S)
    except subprocess.TimeoutExpired:
        print(kill(server))
        print(f"the server did not stop within {STOP_S} s of Ctrl+Break")
        return 1
    print(output)
    if server.returncode != 0 or "Traceback" in output:
        print(f"the server stopped with code {server.returncode}")
        return 1
    print("server started and stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], gui="--gui" in sys.argv[2:]))
