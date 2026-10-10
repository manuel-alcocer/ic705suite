"""Front-panel controls, described independently of any radio.

A rig model lists the controls it has; the rig driver knows how to read and set each one.
Screens only need these descriptions to draw a knob, a switch or a selector, so a control
added to a driver shows up in the rig view without touching the interface.

Values travel as plain JSON: levels and offsets as numbers, switches as booleans, selectors
as the chosen option's key.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ic705suite.i18n import N_

LEVEL = "level"      # 0..100 % (or minimum..maximum in `unit`)
TOGGLE = "toggle"    # on / off
CHOICE = "choice"    # one of `choices`
OFFSET = "offset"    # signed value (RIT, PBT), minimum..maximum in `unit`

# Groups, in the order the rig view shows them.
GROUPS = (
    ("rx", N_("Receive")),
    ("dsp", N_("DSP")),
    ("tx", N_("Transmit")),
    ("cw", N_("CW")),
    ("vfo", N_("VFO")),
    ("scope", N_("Scope")),
)


@dataclass(frozen=True)
class Control:
    id: str
    label: str                       # short, English, N_() marked: shown on the panel
    group: str
    kind: str
    choices: tuple[tuple[str, str], ...] = ()   # (key, label) for CHOICE
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    unit: str = "%"
    help: str = ""                   # one line, English, N_() marked

    def as_dict(self) -> dict:
        data = asdict(self)
        data["choices"] = [list(c) for c in self.choices]
        return data

    def clamp(self, value: float) -> float:
        return max(self.minimum, min(self.maximum, value))

    def next_choice(self, current: str | None, direction: int = 1) -> str:
        keys = [k for k, _ in self.choices]
        if current not in keys:
            return keys[0]
        return keys[(keys.index(current) + direction) % len(keys)]

    def parse(self, text: str):
        """A value typed by the operator ("50", "on", "fast") for this control."""
        text = text.strip().lower()
        if self.kind == TOGGLE:
            if text in ("on", "1", "yes", "true", "si", "sí"):
                return True
            if text in ("off", "0", "no", "false"):
                return False
            raise ValueError(text)
        if self.kind == CHOICE:
            for key, label in self.choices:
                if text in (key.lower(), label.lower()):
                    return key
            raise ValueError(text)
        return self.clamp(float(text.rstrip("%").replace(",", ".")))
