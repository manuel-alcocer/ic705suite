"""Run the CW decoder over a recorded WAV file (e.g. one made with `ic705suite --record`)."""

from __future__ import annotations

import argparse

import numpy as np

from ic705suite.apps.cw.decoder import CwDecoder, find_tone
from ic705suite.apps.cw.playback import load_wav


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ic705suite-replay", description=__doc__)
    p.add_argument("wav")
    p.add_argument("--pitch", type=float, default=600, help="CW pitch of the radio (Hz)")
    p.add_argument("--wpm", type=float, default=18, help="initial speed estimate")
    p.add_argument("--no-afc", action="store_true")
    p.add_argument("--stats", action="store_true", help="print decoder state every second")
    p.add_argument("--skim", action="store_true",
                   help="run the multi-signal skimmer instead of the single decoder")
    args = p.parse_args(argv)

    samples, rate = load_wav(args.wav)
    if args.skim:
        return skim(samples, rate)
    tone, score = find_tone(samples, rate, 200, 1500)
    print(f"{len(samples) / rate:.1f} s at {rate} Hz; strongest tone {tone or 0:.0f} Hz "
          f"(peak/median {score:.1f})")
    out: list[str] = []
    decoder = CwDecoder(rate, args.pitch, args.wpm, on_text=out.append)
    decoder.afc = not args.no_afc
    for i in range(0, len(samples), rate // 50):
        decoder.process(samples[i:i + rate // 50])
        if args.stats and (i // (rate // 50)) % 50 == 0:
            print(f"[{i / rate:6.1f}s] tone={decoder.tone:.0f} wpm={decoder.wpm:.1f} "
                  f"snr={decoder.snr_db:.1f}dB squelch={'closed' if decoder.squelched else 'open'}"
                  f" | {''.join(out)[-50:]}")
    decoder.process(np.zeros(rate * 2, np.float32))
    print("".join(out).strip())
    return 0


def skim(samples: np.ndarray, rate: int) -> int:
    """Print each callsign as the skimmer validates it, then every channel's text."""
    from ic705suite.config import DEFAULT_SCP_PATH
    from ic705suite.apps.cw.skimmer import Skimmer, load_scp

    scp = load_scp(DEFAULT_SCP_PATH) if DEFAULT_SCP_PATH.exists() else set()
    texts: dict[int, str] = {}

    def spot(ch) -> None:
        known = "  (MASTER.SCP)" if ch.callsign in scp else ""
        print(f"[{sk._clock:6.1f}s] {ch.tone:5.0f} Hz  {ch.wpm:4.1f} wpm  {ch.callsign}{known}")

    sk = Skimmer(rate, scp=scp, on_callsign=spot)
    print(f"{len(samples) / rate:.1f} s at {rate} Hz; {len(scp)} callsigns in MASTER.SCP\n"
          "-- callsigns validated:")
    step = rate // 50
    for i in range(0, len(samples), step):
        sk.process(samples[i:i + step])
        for ch in sk.channels:
            texts[id(ch)] = f"{ch.tone:5.0f} Hz | {' '.join(ch.text.split())}"
    print("-- text of the channels opened (last 100 characters each):")
    for line in texts.values():
        head, _, text = line.partition(" | ")
        if len(text) > 20:
            print(f"{head} | {text[-100:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
