import numpy as np
import pytest

from ic705suite.apps.cw.decoder import CwDecoder, detect_cw
from ic705suite.apps.cw.synth import cw_signal, noise

FS = 8000
MESSAGE = "CQ CQ DE EA4ABC EA4ABC PSE K"


def decode(x: np.ndarray, pitch: float = 600, wpm_hint: float = 18,
           tail: np.ndarray | None = None) -> tuple[str, CwDecoder]:
    out: list[str] = []
    dec = CwDecoder(FS, pitch, wpm=wpm_hint, on_text=out.append)
    if tail is None:
        tail = np.zeros(FS * 2, np.float32)
    x = np.concatenate([x, tail])
    for i in range(0, len(x), 160):  # the radio delivers ~20 ms blocks
        dec.process(x[i:i + 160])
    return "".join(out).strip(), dec


@pytest.mark.parametrize("wpm", [12, 20, 30, 40])
@pytest.mark.parametrize("noise_rms", [0.01, 0.2])
def test_decodes_speeds_and_noise(wpm, noise_rms):
    rng = np.random.default_rng(wpm)
    sig = cw_signal(MESSAGE, wpm, 600, FS, 0.5, lead=1.0)
    text, dec = decode(sig + noise(len(sig), noise_rms, rng))
    # The first character may be lost while the speed estimate adapts.
    assert text.endswith(MESSAGE[3:])
    assert abs(dec.wpm - wpm) / wpm < 0.15


def test_afc_follows_offset_tone():
    sig = cw_signal(MESSAGE, 20, 680, FS, 0.5, lead=1.0)
    text, dec = decode(sig)
    assert abs(dec.tone - 680) < 10
    assert text.endswith("DE EA4ABC EA4ABC PSE K")


def test_afc_stays_off_neighbouring_station():
    # A QSO partner 150 Hz away must not pull the decoder off the tuned station.
    rng = np.random.default_rng(5)
    tuned = cw_signal("CQ DE EA4ABC K", 22, 600, FS, 0.3, lead=0.5, tail=6.0)
    other = cw_signal("EA4ABC DE DL1XYZ K", 25, 750, FS, 0.6, lead=4.0, tail=0.5)
    n = max(len(tuned), len(other))
    x = np.zeros(n, np.float32)
    x[:len(tuned)] += tuned
    x[:len(other)] += other
    text, dec = decode(x + noise(n, 0.02, rng))
    assert abs(dec.tone - 600) < 20
    assert "DE EA4ABC K" in text
    assert "DL1XYZ" not in text


def test_quiet_on_noise_and_carrier():
    rng = np.random.default_rng(3)
    carrier = 0.3 * np.sin(2 * np.pi * 600 * np.arange(FS * 8) / FS)
    # Flush with band noise: a carrier dropping to digital silence is one long mark.
    text, _ = decode((carrier + noise(FS * 8, 0.1, rng)).astype(np.float32),
                     tail=noise(FS * 2, 0.1, rng))
    assert text == ""
    text, _ = decode(noise(FS * 8, 0.1, rng))
    assert len(text) <= 2


def test_detect_cw_finds_keyed_tone_and_rejects_carrier():
    rng = np.random.default_rng(4)
    keyed = cw_signal("CQ CQ DE EA4ABC", 20, 820, FS, 0.1, lead=0, tail=0)[:int(1.5 * FS)]
    found, tone, _, _ = detect_cw(keyed + noise(len(keyed), 0.1, rng), FS, 150, 1050)
    assert found and abs(tone - 820) < 10
    carrier = (0.3 * np.sin(2 * np.pi * 700 * np.arange(int(1.5 * FS)) / FS)).astype(np.float32)
    found, _, _, _ = detect_cw(carrier + noise(len(carrier), 0.1, rng), FS, 150, 1050)
    assert not found


def test_skimmer_finds_each_station_and_validates_callsigns():
    from ic705suite.apps.cw.skimmer import Skimmer

    rng = np.random.default_rng(7)
    seconds = 40
    n = FS * seconds
    x = noise(n, 0.03, rng)
    stations = [(700, "CQ DE EA1AAA EA1AAA K", 20, 0.3),
                (1150, "TEST DL2BBB DL2BBB", 26, 0.2),
                (1700, "OK3CCC DE G4DDD G4DDD K", 16, 0.15)]
    for tone, text, wpm, amp in stations:
        sig = cw_signal(text, wpm, tone, FS, amp, lead=0.5, tail=1.5)
        reps = np.tile(sig, n // len(sig) + 1)[:n]
        x += reps
    spots = []
    sk = Skimmer(FS, on_callsign=lambda ch: spots.append((round(ch.tone), ch.callsign)))
    for i in range(0, n, 160):
        sk.process(x[i:i + 160])
    assert sorted(round(ch.tone / 10) * 10 for ch in sk.channels) == [700, 1150, 1700]
    assert {call for _, call in spots} == {"EA1AAA", "DL2BBB", "G4DDD"}


def test_bayesian_decoder_beats_classic_on_human_keying():
    from ic705suite.apps.cw import bench
    from ic705suite.apps.cw.bayes import BayesDecoder

    rng = np.random.default_rng(3)
    classic, bayes = [], []
    for _ in range(8):
        text, audio = bench.make_case(rng, bench.FALLBACK_CALLS, 8.0, float(rng.uniform(16, 30)),
                                      jitter=0.15, fading=0.5, crashes=0.5)
        classic.append(bench.cer(text, bench.run_decoder(
            lambda cb: CwDecoder(FS, 600, on_text=cb), audio)))
        bayes.append(bench.cer(text, bench.run_decoder(
            lambda cb: BayesDecoder(FS, 600, on_text=cb), audio)))
    assert np.mean(bayes) < 0.10
    assert np.mean(bayes) < np.mean(classic)
