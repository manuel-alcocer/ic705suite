"""Icom radios the suite knows. Add a model here to support another CI-V radio."""

from __future__ import annotations

from ic705suite.rig.base import (
    CONNECTION_LAN,
    CONNECTION_SIM,
    CONNECTION_USB,
    FEATURE_AUDIO,
    FEATURE_CW_KEYER,
    FEATURE_DATA_MODE,
    FEATURE_DSTAR,
    FEATURE_GPS,
    FEATURE_SCOPE,
    FEATURE_SWR_METER,
    RigModel,
)

IC705 = RigModel(
    id="ic705",
    name="IC-705",
    manufacturer="Icom",
    family="icom",
    connections=(CONNECTION_LAN, CONNECTION_USB, CONNECTION_SIM),
    features=frozenset({FEATURE_CW_KEYER, FEATURE_AUDIO, FEATURE_DSTAR, FEATURE_GPS,
                        FEATURE_SCOPE, FEATURE_SWR_METER, FEATURE_DATA_MODE}),
    ranges=((30_000, 199_999_999), (400_000_000, 470_000_000)),
    max_power_w=10.0,
    modes=("LSB", "USB", "AM", "CW", "CW-R", "RTTY", "RTTY-R", "FM", "WFM", "DV"),
    params={"civ_address": 0xA4},
)

MODELS = (IC705,)
