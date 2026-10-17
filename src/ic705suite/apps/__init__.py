"""The applications of the suite, as the dashboard lists them.

Applications that need the radio run on the shared station of the core (local or on a
server); packet runs on the core too, with its own TNC, so it works without the radio; the
logbook works on its own and runs on this machine.
"""

from __future__ import annotations

from dataclasses import dataclass

from ic705suite.i18n import N_
from ic705suite.rig.base import FEATURE_AUDIO, FEATURE_DSTAR, FEATURE_SWR_METER, RigModel


@dataclass(frozen=True)
class AppInfo:
    id: str
    key: str                 # dashboard shortcut
    name: str                # English, marked with N_(); "{model}" is the rig model name
    description: str
    feature: str | None      # rig feature it needs, None if any rig (or none) will do
    needs_rig: bool
    ready: bool = True


APPS = (
    AppInfo("view", "F2", N_("{model} View"),
            N_("Rig control: frequency, band, mode, power, meters"), None, True),
    AppInfo("cw", "F3", N_("CW"),
            N_("Decoder, skimmer and keyer; channels and plain-language translation"),
            FEATURE_AUDIO, True),
    AppInfo("dstar", "F4", N_("D-STAR"),
            N_("Terminal mode, built-in gateway, reflectors and D-PRS (QDStar)"),
            FEATURE_DSTAR, True, ready=False),
    AppInfo("swr", "F5", N_("Antenna"),
            N_("SWR and noise sweeps, which way to move the coil; phone page"),
            FEATURE_SWR_METER, True),
    AppInfo("log", "F6", N_("Logbook"),
            N_("hamrlog: contacts, ADIF, repeaters, address book"), None, False),
    AppInfo("packet", "F7", N_("Packet"),
            N_("BayCom-style terminal: 8 AX.25 channels and the monitor, over Direwolf"),
            None, True),
    AppInfo("ft8", "F8", N_("FT8"),
            N_("The band's activity, decoded every 15 s (WSJT-X's jt9)"), FEATURE_AUDIO, True),
    AppInfo("phone", "F10", N_("Phone"),
            N_("Talk: PTT with the computer's or the radio's microphone, bands, modes, "
               "memories and the quick log (in the terminal, the rig view)"), None, True),
)
BY_ID = {app.id: app for app in APPS}


def catalog(model: RigModel | None) -> list[dict]:
    """The applications and whether this rig can run them (sent in the welcome event)."""
    return [{"id": a.id, "key": a.key, "name": a.name, "description": a.description,
             "needs_rig": a.needs_rig, "ready": a.ready,
             "available": a.feature is None or (model is not None and model.has(a.feature))}
            for a in APPS]
