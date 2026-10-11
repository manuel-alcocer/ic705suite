"""What the rig keys do, worked out without a screen: the terminal's applications and the
graphical client both call these, so PgUp/PgDn, F3, F4/F5, F6 and F7/F8 act the same in
either."""

from __future__ import annotations

from ic705suite.rig import bands

STEPS = (10, 100, 1_000, 10_000, 100_000)
CYCLE_MODES = ("CW", "USB", "LSB", "FM", "AM", "DV")
SIGNAL_GUARD = 150      # Hz: the signal already tuned is not the next one


def tune_target(frequency: int, step: int, direction: int) -> int:
    """PgUp/PgDn: up to the next multiple of the step, down to the previous one."""
    if direction > 0:
        return (frequency // step + 1) * step
    return (frequency - 1) // step * step


def next_step(step: int) -> int:
    return STEPS[(STEPS.index(step) + 1) % len(STEPS)] if step in STEPS else STEPS[1]


def next_mode(mode: str | None) -> str:
    """F3: the next of the usual modes."""
    index = CYCLE_MODES.index(mode) + 1 if mode in CYCLE_MODES else 0
    return CYCLE_MODES[index % len(CYCLE_MODES)]


def band_beside(frequency: int | None, direction: int) -> str | None:
    """F4/F5: the band below or above the one tuned (or the frequency, between bands)."""
    frequency = frequency or 0
    names = [b.name for b in bands.BANDS]
    current = bands.band_for(frequency)
    if current is not None:
        index = names.index(current.name) + direction
    else:
        index = next((i for i, b in enumerate(bands.BANDS) if b.start > frequency), len(names))
        index = index if direction > 0 else index - 1
    return names[index] if 0 <= index < len(names) else None


def next_choice(control: dict, current) -> object:
    """F6 and the like: the next choice of a control."""
    keys = [k for k, _ in control["choices"]]
    return keys[(keys.index(current) + 1) % len(keys)] if current in keys else keys[0]


def signal_beside(dial: int, signals: list[int], direction: int) -> int | None:
    """F7/F8: the nearest signal above (or below) the dial, rounded to 10 Hz."""
    ahead = [f for f in signals if f > dial + SIGNAL_GUARD] if direction > 0 \
        else [f for f in reversed(signals) if f < dial - SIGNAL_GUARD]
    return round(ahead[0] / 10) * 10 if ahead else None
