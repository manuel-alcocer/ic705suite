"""The resonance in an SWR sweep: a flat dip (the IC-705's meter reads 1.00 over a broad
stretch) is placed at its middle, and a sweep good on the target but with its dip well off
it says where to centre it."""

from ic705suite.apps.swr.analysis import analyze

# The operator's 10 m sweep with a Komunica HF-PRO-2 (2026-10-17), 28.0-29.7 MHz every
# 50 kHz, tuned for the band's middle (28.850): 1.00 from 29.10 to 29.55 MHz.
SWR = [2.02, 1.94, 1.88, 1.83, 1.77, 1.7, 1.64, 1.58, 1.52, 1.46, 1.42, 1.36, 1.31, 1.27,
       1.22, 1.18, 1.14, 1.1, 1.07, 1.05, 1.03, 1.01, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
       1.0, 1.0, 1.01, 1.02, 1.02]
FREQS = [28_000_000 + 50_000 * i for i in range(len(SWR))]


def test_a_flat_dip_is_placed_at_its_middle_and_the_way_to_centre_it_is_said():
    a = analyze("swr", FREQS, SWR, 28_850_000, "10m", [])
    assert a.resonance == 29_325_000                   # 29.10-29.55: not its first point
    assert a.tuned and a.swr_at_target == 1.1          # good where it is used
    assert a.fine and a.shift == "down" and a.delta_khz == 475.0
    assert a.move == "higher"                          # the Komunica: a higher number lowers


def test_a_dip_on_the_target_needs_nothing():
    swr = [2.0, 1.6, 1.3, 1.1, 1.0, 1.1, 1.3, 1.6, 2.0]
    freqs = [28_650_000 + 50_000 * i for i in range(len(swr))]
    a = analyze("swr", freqs, swr, 28_850_000, "10m", [])
    assert a.resonance == 28_850_000 and a.tuned and not a.fine and a.move == ""
