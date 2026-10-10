"""What every rig offers the rest of the suite, whatever its make or protocol.

A rig model (:class:`RigModel`) describes a radio: how it connects, what it can do and
which commands its protocol needs. A :class:`Rig` is a connected instance of a model.
Applications only use this interface and look at ``model.features`` before offering
something (D-STAR, audio over the network, the CW keyer...), so adding a radio means
adding a model and, for a new protocol family, a Rig subclass; nothing else changes.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass, field

from ic705suite.rig.transport import Transport


class RigError(Exception):
    """The radio rejected a command or did not answer."""


class TxDisabledError(RigError):
    """A command able to key the transmitter was refused: the suite is receive-only."""


# Features a model may have. Applications check them before offering a function.
FEATURE_CW_KEYER = "cw_keyer"        # internal keyer fed with text
FEATURE_AUDIO = "audio"              # receive audio reaches the computer
FEATURE_DSTAR = "dstar"              # D-STAR digital voice and terminal mode
FEATURE_GPS = "gps"
FEATURE_SCOPE = "scope"              # band scope data
FEATURE_SWR_METER = "swr_meter"
FEATURE_DATA_MODE = "data_mode"

CONNECTION_LAN = "lan"
CONNECTION_USB = "usb"
CONNECTION_SIM = "sim"


@dataclass(frozen=True)
class RigModel:
    id: str                          # "ic705": the value of [rig] model in the config
    name: str                        # "IC-705"
    manufacturer: str                # "Icom"
    family: str                      # protocol family: "icom" (CI-V)
    connections: tuple[str, ...]     # supported links, first one is the default
    features: frozenset[str]
    ranges: tuple[tuple[int, int], ...]   # receive coverage, Hz
    max_power_w: float
    modes: tuple[str, ...]
    # Protocol details (for Icom: the default CI-V address).
    params: dict = field(default_factory=dict, hash=False, compare=False)

    def has(self, feature: str) -> bool:
        return feature in self.features

    def covers(self, hz: int) -> bool:
        return any(lo <= hz <= hi for lo, hi in self.ranges)


@dataclass
class RigState:
    frequency: int | None = None
    mode: str | None = None
    filter: int | None = None
    smeter: int = 0            # raw 0..255 (120 = S9, 241 = S9+60 dB)
    pitch: int = 600           # CW pitch, Hz
    key_wpm: int = 20
    power: int | None = None   # percent
    transmitting: bool = False
    controls: dict = field(default_factory=dict)   # control id -> value (see rig.controls)
    meters: dict = field(default_factory=dict)     # meter id -> value in its unit


@dataclass(frozen=True)
class Meter:
    id: str              # "s", "po", "swr", "alc", "comp", "vd", "id"
    label: str           # N_() marked
    unit: str
    maximum: float
    tx_only: bool = False


@dataclass
class ScopeFrame:
    """One sweep of the band scope: amplitudes 0..1 from `start` to `end` Hz."""
    start: int
    end: int
    amplitudes: list[float]
    center_mode: bool = True       # centred on the dial (False: fixed edges)
    out_of_range: bool = False


def smeter_text(raw: int) -> str:
    if raw <= 120:
        return f"S{round(raw / 120 * 9)}"
    return f"S9+{round((raw - 120) / 121 * 60)}"


class Rig(abc.ABC):
    """A connected radio. Methods are coroutines; they raise RigError on failure.

    Receive-only by default: unless ``tx_enabled`` is set, no command that can key the
    transmitter leaves the computer (``TxDisabledError``).
    """

    def __init__(self, model: RigModel, transport: Transport) -> None:
        self.model = model
        self.transport = transport
        self.tx_enabled = False
        self.state = RigState()
        self.on_state: Callable[[], None] | None = None
        self.on_scope: Callable[[ScopeFrame], None] | None = None

    @property
    def name(self) -> str:
        return self.transport.radio_name or self.model.name

    async def connect(self) -> None:
        await self.transport.connect()

    async def close(self) -> None:
        await self.transport.close()

    def _changed(self) -> None:
        if self.on_state:
            self.on_state()

    # ---- receive side (every rig) -----------------------------------------------------

    @abc.abstractmethod
    async def read_frequency(self) -> int: ...

    @abc.abstractmethod
    async def set_frequency(self, hz: int) -> None: ...

    @abc.abstractmethod
    async def read_mode(self) -> str: ...

    @abc.abstractmethod
    async def set_mode(self, mode: str) -> None: ...

    @abc.abstractmethod
    async def read_smeter(self) -> int: ...

    async def read_power(self) -> int:
        raise RigError(f"{self.model.name}: reading the RF power is not supported")

    async def set_power(self, percent: int) -> None:
        raise RigError(f"{self.model.name}: setting the RF power is not supported")

    async def read_filter_width(self) -> int:
        raise RigError(f"{self.model.name}: filter width is not supported")

    async def set_filter_width(self, hz: int) -> None:
        raise RigError(f"{self.model.name}: filter width is not supported")

    async def raw(self, data: bytes) -> bytes:
        """Send a protocol command as is (debugging) and return the answer."""
        raise RigError(f"{self.model.name}: raw commands are not supported")

    # ---- front panel ----------------------------------------------------------------------

    def controls(self) -> list:
        """The rig.controls.Control descriptions this rig can read and set."""
        return []

    async def read_control(self, control_id: str):
        raise RigError(f"{self.model.name}: no control {control_id}")

    async def set_control(self, control_id: str, value) -> None:
        raise RigError(f"{self.model.name}: no control {control_id}")

    def meters(self) -> list[Meter]:
        return []

    async def read_meter(self, meter_id: str) -> float:
        raise RigError(f"{self.model.name}: no meter {meter_id}")

    async def scope_output(self, on: bool) -> None:
        """Start or stop the band scope data (frames arrive through on_scope)."""
        raise RigError(f"{self.model.name}: has no band scope")

    # ---- CW (models with FEATURE_CW_KEYER) ----------------------------------------------

    async def read_pitch(self) -> int:
        return self.state.pitch

    async def set_pitch(self, hz: int) -> None:
        raise RigError(f"{self.model.name}: CW pitch is not supported")

    async def read_key_speed(self) -> int:
        return self.state.key_wpm

    async def set_key_speed(self, wpm: int) -> None:
        raise RigError(f"{self.model.name}: keyer speed is not supported")

    async def set_break_in(self, semi: bool = True) -> None:
        raise RigError(f"{self.model.name}: break-in is not supported")

    async def send_cw(self, text: str, on_chunk: Callable[[str], None] | None = None) -> None:
        raise RigError(f"{self.model.name}: has no CW keyer")

    async def stop_cw(self) -> None:
        pass

    # ---- transmit and meters (antenna tuning) --------------------------------------------

    async def ptt(self, on: bool) -> None:
        raise RigError(f"{self.model.name}: PTT is not supported")

    async def read_swr(self) -> float:
        raise RigError(f"{self.model.name}: has no SWR meter")

    async def read_rf_watts(self) -> float:
        raise RigError(f"{self.model.name}: has no power meter")

    async def save_settings(self) -> object:
        """Operating settings to put back with restore_settings() after a measurement."""
        return (await self.read_frequency(), await self.read_mode())

    async def restore_settings(self, saved: object) -> None:
        frequency, mode = saved  # type: ignore[misc]
        await self.set_frequency(frequency)
        await self.set_mode(mode)
