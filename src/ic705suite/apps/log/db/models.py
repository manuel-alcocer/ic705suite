"""SQLAlchemy models.

Design notes:

* Frequencies are integer hertz; reports (RST) are text because digital modes
  use dB values such as ``-12``.
* Timestamps are naive UTC. SQLite has no timezone storage, so normalising on
  write keeps SQLite and PostgreSQL behaviour identical.
* ``Qso.entry_mode`` drives the edit rules: an automatically timestamped QSO
  only allows the callsign to be corrected, a manually dated one allows
  everything.
* Anything the model has no column for lands in the ``extra`` JSON blob, which
  keeps ADIF imports lossless.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from ..core.callsign import normalize as normalize_call
from ..core.names import title_case


def _callsign(value: str | None) -> str | None:
    """Callsigns are stored upper case, however they were typed."""
    return normalize_call(value) if value else value


def _capitalised(value: str | None) -> str | None:
    """Names, surnames and QTHs are stored with each word capitalised."""
    return title_case(value) if value else value


def utcnow() -> dt.datetime:
    """Current UTC time as a naive datetime, truncated to the second."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


class Base(DeclarativeBase):
    """Declarative base with a shared JSON type map."""

    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


class EntryMode(str, enum.Enum):
    """How a QSO acquired its timestamp."""

    AUTO = "AUTO"
    MANUAL = "MANUAL"


class Operator(Base):
    """A person logging QSOs. Local identity only, no password."""

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    callsign: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    gridsquare: Mapped[str] = mapped_column(String(12), default="")
    qth: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    qsos: Mapped[list[Qso]] = relationship(back_populates="operator")

    @validates("callsign")
    def _validate_callsign(self, _key: str, value: str | None) -> str | None:
        return _callsign(value)

    @validates("name", "qth")
    def _validate_capitalised(self, _key: str, value: str | None) -> str | None:
        return _capitalised(value)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Operator {self.callsign}>"

    @property
    def display(self) -> str:
        return f"{self.callsign} ({self.name})" if self.name else self.callsign


#: Which frequency ranges a station covers. A station may have several types:
#: an IC-705 is HF, VHF and UHF at once.
station_type_links = Table(
    "station_type_links",
    Base.metadata,
    Column("station_id", ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True),
    Column("type_id", ForeignKey("station_types.id", ondelete="CASCADE"), primary_key=True),
)

#: Configurations offered under each station. Assigned by hand, so
#: two stations with the same rig can offer different configurations, and one
#: configuration can be reused by several stations.
station_profile_links = Table(
    "station_profile_links",
    Base.metadata,
    Column("station_id", ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True),
    Column("profile_id", ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
)


#: Antennas that can be connected to each station. Assigned by hand: a
#: handheld's own antenna only fits that handheld, whatever its bands.
station_antenna_links = Table(
    "station_antenna_links",
    Base.metadata,
    Column("station_id", ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True),
    Column("antenna_id", ForeignKey("antennas.id", ondelete="CASCADE"), primary_key=True),
)


class Antenna(Base):
    """An antenna and the amateur bands it works on.

    Stations list the antennas they can use, and an antenna only
    offers the configurations on one of its bands.
    """

    __tablename__ = "antennas"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The operator's identifier, E0001 / A0001 / S0001 (see db/codes.py).
    #: Unique; separate from the internal autoincrement id.
    code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    #: ADIF band names, e.g. ["2m", "70cm"]. Empty means not stated.
    bands: Mapped[list[str]] = mapped_column(default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    #: Manufacturer, used to filter the lists.
    brand: Mapped[str] = mapped_column(String(80), default="", index=True)
    #: From the bundled catalog (preseed): read-only, only used in sets.
    preset: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Antenna {self.name}>"

    @property
    def band_names(self) -> str:
        return ", ".join(self.bands or [])

    def covers_band(self, band: str | None) -> bool:
        """Whether the antenna works on a band.

        An antenna with no bands stated, or a configuration with no band, has
        nothing to check against, so both are accepted.
        """
        if not band or not self.bands:
            return True
        return band.lower() in {name.lower() for name in self.bands}


class StationType(Base):
    """A class of station defined by the frequencies it covers: HF, VHF, CB...

    The range decides which configurations a station can be given: one tuned
    outside every type of the station would be a configuration it cannot use.
    """

    __tablename__ = "station_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # BigInteger: UHF ends at 3 GHz, past a 32-bit PostgreSQL integer.
    min_hz: Mapped[int] = mapped_column(BigInteger)
    max_hz: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StationType {self.name}>"

    def contains(self, freq_hz: int) -> bool:
        return self.min_hz <= freq_hz <= self.max_hz


class Station(Base):
    """A rig, used together with one of its antennas."""

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The operator's identifier, E0001 / A0001 / S0001 (see db/codes.py).
    #: Unique; separate from the internal autoincrement id.
    code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    rig: Mapped[str] = mapped_column(String(120), default="")
    #: Legacy: the antenna as free text, before schema 5 made antennas their
    #: own table. Only read by the upgrade; no longer written.
    antenna: Mapped[str] = mapped_column(String(120), default="")
    power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    #: Manufacturer, used to filter the lists.
    brand: Mapped[str] = mapped_column(String(80), default="", index=True)
    #: From the bundled catalog (preseed): read-only, only used in sets.
    preset: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    types: Mapped[list[StationType]] = relationship(
        secondary=station_type_links, order_by="StationType.min_hz"
    )
    profiles: Mapped[list[Profile]] = relationship(
        secondary=station_profile_links, order_by="Profile.name", back_populates="stations"
    )
    antennas: Mapped[list[Antenna]] = relationship(
        secondary=station_antenna_links, order_by="Antenna.name"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Station {self.name}>"

    @property
    def type_names(self) -> str:
        return ", ".join(station_type.name for station_type in self.types)

    def covers(self, freq_hz: int | None) -> bool:
        """Whether a frequency falls in the range of any of the station's types.

        A station with no types, or a configuration with no frequency, has
        nothing to check against, so both are accepted.
        """
        if freq_hz is None or not self.types:
            return True
        return any(station_type.contains(freq_hz) for station_type in self.types)

    def summary(self, antenna: Antenna | None = None) -> str:
        """Rig, antenna in use and power, for the status line."""
        parts = [self.rig or self.name]
        if antenna is not None:
            parts.append(antenna.name)
        if self.power_w:
            parts.append(f"{self.power_w} W")
        return " / ".join(parts)


class PowerSupply(Base):
    """A power supply: a mains unit, a battery, a solar charger..."""

    __tablename__ = "power_supplies"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The operator's identifier, E0001 / A0001 / S0001 (see db/codes.py).
    #: Unique; separate from the internal autoincrement id.
    code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    voltage_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    #: Manufacturer, used to filter the lists.
    brand: Mapped[str] = mapped_column(String(80), default="", index=True)
    #: From the bundled catalog (preseed): read-only, only used in sets.
    preset: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PowerSupply {self.name}>"


#: What each equipment set is made of. Radios, antennas and supplies are
#: registered on their own and can belong to several sets.
equipment_station_links = Table(
    "equipment_station_links",
    Base.metadata,
    Column("equipment_id", ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True),
    Column("station_id", ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True),
)
equipment_antenna_links = Table(
    "equipment_antenna_links",
    Base.metadata,
    Column("equipment_id", ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True),
    Column("antenna_id", ForeignKey("antennas.id", ondelete="CASCADE"), primary_key=True),
)
equipment_supply_links = Table(
    "equipment_supply_links",
    Base.metadata,
    Column("equipment_id", ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "supply_id", ForeignKey("power_supplies.id", ondelete="CASCADE"), primary_key=True
    ),
)


class Equipment(Base):
    """A set the operator works with: one or more radios (``Station``, shown
    as «emisora»), plus the antennas and power supplies that go with them.

    Shown to the operator as an «equipo». It must hold at least one radio;
    the service enforces it.
    """

    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    stations: Mapped[list[Station]] = relationship(
        secondary=equipment_station_links, order_by="Station.name"
    )
    antennas: Mapped[list[Antenna]] = relationship(
        secondary=equipment_antenna_links, order_by="Antenna.name"
    )
    supplies: Mapped[list[PowerSupply]] = relationship(
        secondary=equipment_supply_links, order_by="PowerSupply.name"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Equipment {self.name}>"

    def fits(self, freq_hz: int | None, band: str | None) -> bool:
        """Whether a QSO on this frequency and band could be made with the set.

        Some radio must tune the frequency and, when the set lists antennas,
        some antenna must work the band. A QSO with no frequency or band has
        nothing to check against.
        """
        radio_ok = freq_hz is None or any(s.covers(freq_hz) for s in self.stations)
        antenna_ok = not band or not self.antennas or any(
            a.covers_band(band) for a in self.antennas
        )
        return radio_ok and antenna_ok

    def parts_for(
        self, freq_hz: int | None, band: str | None
    ) -> tuple[Station | None, Antenna | None]:
        """The radio and antenna of the set a QSO most likely used.

        The first that suits the frequency and band, or the first one when
        none does: the QSO is then flagged, but still says what it was made
        with.
        """
        station = next(
            (s for s in self.stations if freq_hz is None or s.covers(freq_hz)),
            self.stations[0] if self.stations else None,
        )
        antenna = next(
            (a for a in self.antennas if a.covers_band(band)),
            self.antennas[0] if self.antennas else None,
        )
        return station, antenna


class Contact(Base):
    """An address book entry.

    Separate from ``Qso``: the log records what happened on the air, while
    this is who somebody is. Entries usually arrive in bulk from a DMR user
    list, so the table is indexed for lookups by callsign and by DMR ID and
    carries no foreign keys.
    """

    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_country_call", "country", "callsign"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    callsign: Mapped[str] = mapped_column(String(32), index=True)
    #: Callsign without portable prefixes/suffixes, used for lookups.
    base_call: Mapped[str] = mapped_column(String(32), index=True, default="")
    #: DMR radio ID. Unique when present; a person may hold none.
    dmr_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True, index=True)

    first_name: Mapped[str] = mapped_column(String(80), default="")
    last_name: Mapped[str] = mapped_column(String(80), default="")

    city: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(120), default="")
    country: Mapped[str] = mapped_column(String(80), default="")
    gridsquare: Mapped[str] = mapped_column(String(12), default="")

    email: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    #: Where the entry came from: an import format name, "manual" or "log".
    source: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @validates("callsign")
    def _validate_callsign(self, _key: str, value: str | None) -> str | None:
        return _callsign(value)

    #: The city is the contact's QTH.
    @validates("first_name", "last_name", "city")
    def _validate_capitalised(self, _key: str, value: str | None) -> str | None:
        return _capitalised(value)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Contact {self.callsign} {self.dmr_id}>"

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    @property
    def display(self) -> str:
        return f"{self.callsign} · {self.full_name}" if self.full_name else self.callsign


class Repeater(Base):
    """A repeater the operator works through.

    A repeater is used in split: you listen on ``output_hz`` and transmit on
    ``input_hz``, which is ``output_hz + shift_hz``. Analogue repeaters need a
    CTCSS tone to open them; digital ones carry their parameters in
    ``digital_data`` (color code, talkgroup, reflector, room).

    The callsign is not unique: one club often runs several repeaters under
    the same one, on different bands or modes. Callsign and output frequency
    tell them apart.
    """

    __tablename__ = "repeaters"

    id: Mapped[int] = mapped_column(primary_key=True)
    callsign: Mapped[str] = mapped_column(String(32), index=True)
    #: Who runs it: the club or URE section.
    name: Mapped[str] = mapped_column(String(120), default="")
    #: The number the URE gives it, «R5» or «R73»; empty when it has none.
    ure_number: Mapped[str] = mapped_column(String(8), default="")
    #: IARU channel of its output, «RV58» or «RU698».
    channel: Mapped[str] = mapped_column(String(8), default="")

    band: Mapped[str] = mapped_column(String(16), default="")
    #: Repeater output: what you tune and listen to.
    output_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Repeater input: what you actually transmit on.
    input_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Signed offset from output to input, negative below.
    shift_hz: Mapped[int] = mapped_column(Integer, default=0)

    mode: Mapped[str] = mapped_column(String(24), default="")
    #: Sub-audible tone sent to open the repeater, as text ("88.5").
    ctcss_tx: Mapped[str] = mapped_column(String(8), default="")
    #: Tone expected on the output, when it differs from the transmit one.
    ctcss_rx: Mapped[str] = mapped_column(String(8), default="")
    #: Digital coded squelch, as an alternative to CTCSS.
    dcs: Mapped[str] = mapped_column(String(8), default="")

    qth: Mapped[str] = mapped_column(String(120), default="")
    gridsquare: Mapped[str] = mapped_column(String(12), default="")

    #: Mode specific defaults: color_code, talkgroup, reflector, room, network.
    digital_data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: From the URE list: shown and used, never changed or deleted.
    preset: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    # The QTH is left as written: it describes the site, often with its
    # height ("Cuitu Negru 1850 m."), and the URE list already spells it.
    @validates("callsign")
    def _validate_callsign(self, _key: str, value: str | None) -> str | None:
        return _callsign(value)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Repeater {self.callsign} {self.output_hz}>"

    @property
    def display(self) -> str:
        return f"{self.callsign} · {self.name}" if self.name else self.callsign


class Profile(Base):
    """A profile: what every QSO logged while it is active inherits.

    Managed in the profiles view (F4) and activated there with Enter, with
    Ctrl+0 to Ctrl+9 when it holds one of the ten ``slot`` keys, or with
    /profile. The default one is activated on start.
    """

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)

    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), nullable=True)
    #: Legacy: the single station a profile carried before schema 4. Only read
    #: by the upgrade that turns it into an assignment; no longer written.
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"), nullable=True)
    repeater_id: Mapped[int | None] = mapped_column(ForeignKey("repeaters.id"), nullable=True)
    #: The equipment set («setup») the QSOs are made with.
    equipment_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True
    )
    #: 0 to 9: one of the ten main profiles, activated with Ctrl+<slot>.
    #: Unique among profiles; the service enforces it.
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    band: Mapped[str] = mapped_column(String(16), default="")
    freq_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(24), default="")
    power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    digital_data: Mapped[dict[str, Any]] = mapped_column(default=dict)

    # Fast entry behaviour, so a contest profile can differ from a ragchew one.
    field_order: Mapped[list[str]] = mapped_column(default=list)
    separator: Mapped[str] = mapped_column(String(4), default=",")

    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    operator: Mapped[Operator | None] = relationship()
    repeater: Mapped[Repeater | None] = relationship()
    equipment: Mapped[Equipment | None] = relationship()
    stations: Mapped[list[Station]] = relationship(
        secondary=station_profile_links, order_by="Station.name", back_populates="profiles"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Profile {self.name}>"


class Qso(Base):
    """A logged contact."""

    __tablename__ = "qsos"
    __table_args__ = (
        Index("ix_qsos_operator_time", "operator_id", "qso_utc"),
        Index("ix_qsos_base_call_band", "base_call", "band"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"), nullable=True)
    antenna_id: Mapped[int | None] = mapped_column(ForeignKey("antennas.id"), nullable=True)
    repeater_id: Mapped[int | None] = mapped_column(ForeignKey("repeaters.id"), nullable=True)
    #: The equipment set it was made with, assigned when editing it.
    equipment_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True
    )
    #: Repeater callsign copied here so the contact keeps its history even if
    #: the repeater is later deleted from the list.
    repeater_call: Mapped[str] = mapped_column(String(32), default="", index=True)

    call: Mapped[str] = mapped_column(String(32), index=True)
    #: Callsign without portable prefixes/suffixes, used for duplicate checks.
    base_call: Mapped[str] = mapped_column(String(32), index=True, default="")
    name: Mapped[str] = mapped_column(String(80), default="")

    qso_utc: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    band: Mapped[str] = mapped_column(String(16), default="")
    #: Frequency as the operator tunes it: the repeater output when working
    #: through one, the single working frequency otherwise.
    freq_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Actual transmit frequency when it differs from ``freq_hz``, that is the
    #: repeater input. Exported as ADIF FREQ, with ``freq_hz`` as FREQ_RX.
    freq_tx_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(24), default="")

    rst_sent: Mapped[str] = mapped_column(String(16), default="")
    rst_rcvd: Mapped[str] = mapped_column(String(16), default="")

    qth: Mapped[str] = mapped_column(String(120), default="")
    gridsquare: Mapped[str] = mapped_column(String(12), default="")
    country: Mapped[str] = mapped_column(String(80), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)

    entry_mode: Mapped[EntryMode] = mapped_column(
        Enum(EntryMode, native_enum=False, length=8), default=EntryMode.AUTO, index=True
    )

    #: Mode specific values: talkgroup, reflector, room, network, ...
    digital_data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: ADIF fields with no dedicated column, preserved on import/export.
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    operator: Mapped[Operator] = relationship(back_populates="qsos")
    station: Mapped[Station | None] = relationship()
    antenna: Mapped[Antenna | None] = relationship()
    repeater: Mapped[Repeater | None] = relationship()
    equipment: Mapped[Equipment | None] = relationship()

    @validates("call", "repeater_call")
    def _validate_callsign(self, _key: str, value: str | None) -> str | None:
        return _callsign(value)

    @validates("name", "qth")
    def _validate_capitalised(self, _key: str, value: str | None) -> str | None:
        return _capitalised(value)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Qso {self.call} {self.qso_utc:%Y-%m-%d %H:%M} {self.band} {self.mode}>"

    @property
    def is_manual(self) -> bool:
        return self.entry_mode is EntryMode.MANUAL

    @property
    def via_repeater(self) -> bool:
        return bool(self.repeater_call)


class Setting(Base):
    """Application level key/value settings shared by every operator."""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", name="uq_settings_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SchemaVersion(Base):
    """Single-row table recording the schema revision for future migrations."""

    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    applied_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


#: Bumped whenever models change in a way that needs a data migration.
#: 2 added the repeaters table and the repeater columns on qsos and profiles.
#: 3 added the contacts address book.
#: 4 added station types and the station to configuration assignments.
#: 5 made antennas a table of their own, assigned to stations, with bands.
#: 6 added power supplies, equipment sets of radios, antennas and supplies,
#:   the read-only catalog (preset) of radios, antennas and supplies, and
#:   the brand of each.
#: 7 stores country names in English, as ADIF does; they are translated when shown.
#: 8 gives radios, antennas and supplies their own unique code (E0001, A0001, S0001).
#: 9 lets a QSO record the equipment set it was made with.
#: 10 gives profiles a setup, a power and one of the ten Ctrl+digit keys.
#: 11 lets repeaters share a callsign, gives them the URE number and channel,
#:   and adds the read-only catalog of the URE repeater list.
#: 12 stores callsigns upper case and names, surnames and QTHs capitalised.
#: 13 keeps particles lower case in them ("Alcalá de Henares"), so it runs the
#:   normalisation of 12 again.
CURRENT_SCHEMA_VERSION = 13
