"""A stand-in for the direwolf program: reads the configuration file it is given (-c, in
its working folder), says what Direwolf says when ready and serves the fake AGWPE TNC on
AGWPORT. With FAKE_DIREWOLF_FAIL set it fails to open the sound card and stops, as Direwolf
does."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.packet.fake_tnc import FakeTnc  # noqa: E402


async def main() -> int:
    conf = Path(sys.argv[sys.argv.index("-c") + 1])
    settings = dict(line.split(None, 1) for line in conf.read_text().splitlines()
                    if line.strip() and not line.startswith("#"))
    print("Dire Wolf version 1.7 (fake)", flush=True)
    if os.environ.get("FAKE_DIREWOLF_FAIL"):
        print(f"Could not open audio device {settings['ADEVICE']} for input", flush=True)
        return 1
    tnc = FakeTnc()
    tnc.port = int(settings["AGWPORT"])
    await tnc.start()
    print(f"Ready to accept AGW client application 0 on port {tnc.port} ...", flush=True)
    await asyncio.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
