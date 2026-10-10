"""Repeaters: registration, session behaviour, logging and ADIF."""

from __future__ import annotations

import pytest

from ic705suite.apps.log.adif import qso_to_adif
from ic705suite.apps.log.core import repeaters, transfer
from ic705suite.apps.log.core.entry import parse
from ic705suite.apps.log.core.services import (
    OperatorService,
    ProfileService,
    QsoService,
    RepeaterService,
    ServiceError,
)
from ic705suite.apps.log.core.state import SessionState


@pytest.fixture
def repeater():
    """A typical 2 m analogue repeater with a CTCSS tone."""
    return RepeaterService.create(
        "ED7ZAE",
        name="Sevilla - Cerro del Águila",
        output_hz=145_600_000,
        ctcss_tx="88.5",
        qth="Sevilla",
    )


@pytest.fixture
def dmr_repeater():
    """A 70 cm DMR repeater with a default talkgroup."""
    return RepeaterService.create(
        "ED7ZAF",
        name="Sevilla DMR",
        output_hz=438_750_000,
        mode="DMR",
        digital_data={"talkgroup": "214", "color_code": "1", "network": "Brandmeister"},
    )


def log_line(line: str, state: SessionState, **kwargs):
    parsed = parse(line, field_order=state.field_order, mode_name=state.mode)
    return QsoService.log(parsed.fields, state, digital=parsed.digital, **kwargs)


# --------------------------------------------------------------- domain ----

@pytest.mark.parametrize(
    ("text", "expected_hz"),
    [
        ("-600", -600_000),
        ("-600 kHz", -600_000),
        ("-7.6 MHz", -7_600_000),
        ("+5 MHz", 5_000_000),
        ("0", 0),
        ("sin sentido", None),
    ],
)
def test_parse_shift(text, expected_hz):
    assert repeaters.parse_shift(text) == expected_hz


def test_tone_normalisation_accepts_what_a_radio_shows():
    assert repeaters.normalize_tone("88") == "88.5"
    assert repeaters.normalize_tone("123") == "123.0"
    assert repeaters.normalize_tone("88.5 Hz") == "88.5"
    assert repeaters.normalize_tone("") == ""
    assert not repeaters.is_standard_tone("1750.0")


# ------------------------------------------------------------ registry ----

def test_creating_a_repeater_derives_band_input_and_shift(repeater):
    """The operator types the output frequency; the rest follows the band plan."""
    assert repeater.band == "2m"
    assert repeater.shift_hz == -600_000
    assert repeater.input_hz == 145_000_000
    assert repeater.ctcss_tx == "88.5"


def test_shift_can_be_given_explicitly():
    created = RepeaterService.create(
        "ED7ZAX", output_hz=438_750_000, shift_hz=repeaters.parse_shift("-7.6 MHz")
    )
    assert created.band == "70cm"
    assert created.input_hz == 431_150_000


def test_duplicate_repeater_is_rejected(repeater):
    with pytest.raises(ServiceError, match="ya existe"):
        RepeaterService.create("ed7zae", output_hz=145_600_000)


def test_repeater_needs_an_output_frequency():
    with pytest.raises(ServiceError, match="frecuencia"):
        RepeaterService.create("ED7ZAZ")


def test_editing_the_output_keeps_input_and_band_consistent(repeater):
    updated = RepeaterService.update(repeater.id, output_hz=438_750_000, shift_hz=-7_600_000)
    assert updated.band == "70cm"
    assert updated.input_hz == 431_150_000


# ------------------------------------------------------------- session ----

def test_selecting_a_repeater_adopts_all_its_settings(state, dmr_repeater):
    RepeaterService.apply_to_state(dmr_repeater.id, state)
    assert state.via_repeater
    assert state.repeater_call == "ED7ZAF"
    assert state.band == "70cm"
    assert state.freq_hz == 438_750_000
    assert state.freq_tx_hz == 431_150_000
    assert state.mode == "DMR"
    assert state.digital_data["talkgroup"] == "214"


def test_changing_band_or_frequency_leaves_the_repeater(state, repeater):
    RepeaterService.apply_to_state(repeater.id, state)
    state.set_band("20m")
    assert not state.via_repeater
    assert state.repeater_call == ""
    assert state.freq_tx_hz is None

    RepeaterService.apply_to_state(repeater.id, state)
    state.set_frequency(145_500_000)
    assert not state.via_repeater


# ------------------------------------------------------------- logging ----

def test_contacts_record_the_repeater_and_both_frequencies(state, repeater):
    RepeaterService.apply_to_state(repeater.id, state)
    row = log_line("ea4abc,juan", state)

    assert row.via_repeater
    assert row.repeater_call == "ED7ZAE"
    assert row.freq_hz == 145_600_000
    assert row.freq_tx_hz == 145_000_000
    assert row.band == "2m"
    assert row.mode == "FM"


def test_a_frequency_on_the_entry_line_overrides_the_repeater(state, repeater):
    """Typing a frequency means simplex there, not a contradiction."""
    RepeaterService.apply_to_state(repeater.id, state)
    row = log_line("ea4abc,freq=145.500", state)

    assert not row.via_repeater
    assert row.freq_hz == 145_500_000
    assert row.freq_tx_hz is None


def test_simplex_contacts_have_no_transmit_frequency(state):
    row = log_line("ea4abc", state)
    assert not row.via_repeater
    assert row.freq_tx_hz is None


def test_deleting_a_repeater_keeps_its_contacts(state, repeater):
    RepeaterService.apply_to_state(repeater.id, state)
    row = log_line("ea4abc", state)
    RepeaterService.delete(repeater.id)

    kept = QsoService.get(row.id)
    assert kept is not None
    # The callsign survives so the log still says how the contact went out.
    assert kept.repeater_call == "ED7ZAE"


def test_profiles_store_and_restore_the_repeater(state, dmr_repeater):
    RepeaterService.apply_to_state(dmr_repeater.id, state)
    ProfileService.save_from_state("DMR-Sevilla", state)

    restored = SessionState()
    ProfileService.apply_to_state(ProfileService.list_all()[0].id, restored)
    assert restored.repeater_id == dmr_repeater.id
    assert restored.repeater_call == "ED7ZAF"
    assert restored.freq_tx_hz == 431_150_000
    assert restored.mode == "DMR"


def test_profile_with_a_deleted_repeater_falls_back_to_direct(state, repeater):
    RepeaterService.apply_to_state(repeater.id, state)
    ProfileService.save_from_state("2m-local", state)
    RepeaterService.delete(repeater.id)

    restored = SessionState()
    ProfileService.apply_to_state(ProfileService.list_all()[0].id, restored)
    assert not restored.via_repeater


# ---------------------------------------------------------------- adif ----

def test_adif_uses_split_frequencies_and_prop_mode(state, repeater):
    """ADIF FREQ is what you transmit on, FREQ_RX what you listen to."""
    RepeaterService.apply_to_state(repeater.id, state)
    record = qso_to_adif(log_line("ea4abc,juan", state))

    assert "<FREQ:10>145.000000" in record
    assert "<FREQ_RX:10>145.600000" in record
    assert "<PROP_MODE:3>RPT" in record
    assert "<APP_HAMRLOG_REPEATER:6>ED7ZAE" in record


def test_adif_omits_freq_rx_for_simplex_contacts(state):
    record = qso_to_adif(log_line("ea4abc", state))
    assert "FREQ_RX" not in record
    assert "PROP_MODE" not in record


def test_repeater_contacts_survive_an_adif_round_trip(tmp_path, state, repeater):
    RepeaterService.apply_to_state(repeater.id, state)
    log_line("ea4abc,juan", state)

    path, _ = transfer.export_adif(tmp_path / "log.adi")
    other = OperatorService.create("EA7XX", "Otro")
    report = transfer.import_adif(path, operator_id=other.id, respect_file_operator=False)
    assert report.imported == 1

    imported = QsoService.search(operator_id=other.id)[0]
    assert imported.repeater_call == "ED7ZAE"
    assert imported.freq_hz == 145_600_000
    assert imported.freq_tx_hz == 145_000_000
