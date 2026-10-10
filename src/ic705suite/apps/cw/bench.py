"""Decoder benchmark: realistic synthetic QSOs with known text, character error rate.

    python -m cwic705.bench                    # compare the decoders over a SNR sweep
    python -m cwic705.bench --snr 6 --cases 20

The text is built like real contacts (CQ calls, answers, reports, names, contest exchanges)
with callsigns from MASTER.SCP when available, keyed with a human-like irregular fist and
received through noise, fading (QSB) and static crashes (QRN).
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from ic705suite.apps.cw.config import DEFAULT_SCP_PATH
from ic705suite.apps.cw.synth import keying_envelope, qrn, qsb

FS = 8000
TONE = 600.0
NAMES = ["JOHN", "PEPE", "HANS", "ANNA", "LUIS", "MIKE", "JAN", "MARIA", "TOM", "OLE"]
PLACES = ["MADRID", "BERLIN", "PARIS", "ROMA", "WIEN", "OSLO", "LYON", "SEVILLA", "PRAHA"]
FALLBACK_CALLS = ["EA4ABC", "DL1XYZ", "G4ABC", "F5XYZ", "SP3BZU", "OK1ABC", "I2ABC", "ON4XX",
                  "PA3ABC", "OE1ABC", "HB9ABC", "EI2ABC", "OZ3ABC", "SM5ABC", "K1ABC"]


def load_calls() -> list[str]:
    if DEFAULT_SCP_PATH.exists():
        from ic705suite.apps.cw.skimmer import load_scp

        calls = sorted(c for c in load_scp(DEFAULT_SCP_PATH) if "/" not in c)
        if calls:
            return calls
    return FALLBACK_CALLS


def make_text(rng: np.random.Generator, calls: list[str]) -> str:
    a, b = (calls[i] for i in rng.choice(len(calls), 2, replace=False))
    rst = rng.choice(["599", "579", "559", "449", "5NN"])
    templates = [
        f"CQ CQ CQ DE {a} {a} K",
        f"{a} DE {b} {b} K",
        f"{b} DE {a} GM OM TNX FER CALL UR RST {rst} {rst} NAME {rng.choice(NAMES)} "
        f"QTH {rng.choice(PLACES)} HW? {b} DE {a} KN",
        f"R TU FB 73 GL {a} DE {b} SK",
        f"TEST {a} {a}",
        f"{b} 5NN {rng.integers(1, 40)} TU",
        f"CQ POTA DE {a} {a} K",
    ]
    return str(rng.choice(templates))


def make_case(rng: np.random.Generator, calls: list[str], snr_db: float, wpm: float,
              jitter: float, fading: float, crashes: float) -> tuple[str, np.ndarray]:
    """Text and audio. snr_db is the tone power against noise in a 500 Hz bandwidth."""
    text = make_text(rng, calls)
    env = keying_envelope(text, wpm, FS, jitter=jitter, rng=rng)
    lead, tail = np.zeros(int(1.5 * FS), np.float32), np.zeros(int(1.5 * FS), np.float32)
    env = np.concatenate([lead, env, tail])
    n = len(env)
    t = np.arange(n) / FS
    amp = 0.3
    sig = amp * env * np.sin(2 * np.pi * TONE * t + rng.uniform(0, 6.3))
    if fading:
        sig *= qsb(n, FS, fading, rng=rng)
    # Tone power amp^2/2 vs noise power in 500 Hz: sigma^2 * 500 / (FS/2).
    sigma = np.sqrt((amp ** 2 / 2) / 10 ** (snr_db / 10) * (FS / 2) / 500)
    audio = sig + rng.standard_normal(n) * sigma
    if crashes:
        audio += qrn(n, FS, crashes, sigma * 8, rng=rng)
    return text, audio.astype(np.float32)


def cer(reference: str, decoded: str) -> float:
    """Character error rate (Levenshtein distance / reference length), spaces normalised."""
    ref, hyp = " ".join(reference.split()), " ".join(decoded.split())
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1] / max(1, len(ref))


def run_decoder(factory, audio: np.ndarray) -> str:
    out: list[str] = []
    decoder = factory(out.append)
    for i in range(0, len(audio), FS // 50):
        decoder.process(audio[i:i + FS // 50])
    if hasattr(decoder, "flush"):
        decoder.flush()
    return "".join(out)


def decoders() -> dict:
    from ic705suite.apps.cw.decoder import CwDecoder

    found = {"classic": lambda cb: CwDecoder(FS, TONE, on_text=cb)}
    try:
        from ic705suite.apps.cw.bayes import BayesDecoder

        found["bayes"] = lambda cb: BayesDecoder(FS, TONE, on_text=cb)
    except ImportError:
        pass
    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m cwic705.bench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snr", type=float, nargs="*", default=[20, 12, 8, 5, 3, 1])
    p.add_argument("--cases", type=int, default=12)
    p.add_argument("--jitter", type=float, default=0.15)
    p.add_argument("--fading", type=float, default=0.5)
    p.add_argument("--crashes", type=float, default=0.5, help="static crashes per second")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--show", action="store_true", help="print each decoded text")
    args = p.parse_args(argv)
    calls = load_calls()
    found = decoders()
    print(f"{args.cases} QSO fragments per SNR, 14-32 wpm, jitter {args.jitter}, fading "
          f"{args.fading}, {args.crashes} crashes/s; SNR in 500 Hz")
    print("SNR dB " + "".join(f"{name:>12}" for name in found) + "   (character error rate)")
    for snr in args.snr:
        rng = np.random.default_rng(args.seed)
        errors = {name: [] for name in found}
        started = time.time()
        for _ in range(args.cases):
            wpm = float(rng.uniform(14, 32))
            text, audio = make_case(rng, calls, snr, wpm, args.jitter, args.fading, args.crashes)
            for name, factory in found.items():
                decoded = run_decoder(factory, audio)
                errors[name].append(cer(text, decoded))
                if args.show:
                    print(f"  [{name:7s}] {text}\n            {' '.join(decoded.split())}")
        print(f"{snr:6.1f} " + "".join(f"{100 * np.mean(e):11.1f}%" for e in errors.values())
              + f"   ({time.time() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
