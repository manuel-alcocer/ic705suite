"""Smoke test of the Windows executable: the server with the simulator (or, with --gui, the
graphical client, offscreen) starts, and Ctrl+Break stops it by itself and cleanly (the core
closed, exit code 0), as Ctrl+C does in a console.

    python packaging/windows/smoke.py dist/ic705suite.exe [--gui]
"""

import os
import signal
import subprocess
import sys
import time

START_S = 8
STOP_S = 30


def main(exe: str, gui: bool = False) -> int:
    sys.stdout.reconfigure(errors="replace")
    command = [exe, "--gui", "--sim"] if gui else \
        [exe, "--sim", "--serve", "--listen", "127.0.0.1:7399", "--token", "smoke"]
    server = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"},
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    time.sleep(START_S)
    if server.poll() is not None:
        print(server.stdout.read())
        print(f"the server stopped by itself with code {server.returncode}")
        return 1
    server.send_signal(signal.CTRL_BREAK_EVENT)
    try:
        output, _ = server.communicate(timeout=STOP_S)
    except subprocess.TimeoutExpired:
        server.kill()
        output, _ = server.communicate()
        print(output)
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
