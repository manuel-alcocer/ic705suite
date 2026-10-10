"""Callsigns are stored upper case; names, surnames and QTHs capitalised."""

from __future__ import annotations

import sqlite3

import pytest

from ic705suite.apps.log.core.names import title_case
from ic705suite.apps.log.core.services import ContactService, OperatorService, QsoService
from ic705suite.apps.log.core.state import SessionState


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("mAnuel angel", "Manuel Angel"),
        ("MANUEL", "Manuel"),
        ("  josé   maría ", "José María"),
        ("ñoño", "Ñoño"),
        ("jean-pierre", "Jean-Pierre"),
        ("o'brien", "O'Brien"),
        # Particles stay lower case, except where the name or a part of the
        # place starts.
        ("alcalá de henares", "Alcalá de Henares"),
        ("MARÍA DE LOS ÁNGELES", "María de los Ángeles"),
        ("ortega y gasset", "Ortega y Gasset"),
        ("de la fuente", "De la Fuente"),
        ("sierra de béjar - la covatilla", "Sierra de Béjar - La Covatilla"),
        ("sevilla, el viso", "Sevilla, El Viso"),
        ("ludwig VAN beethoven", "Ludwig van Beethoven"),
        # A word with digits is a locator or a number: left as typed.
        ("madrid IN80dk", "Madrid IN80dk"),
        ("", ""),
    ],
)
def test_title_case(typed, stored):
    assert title_case(typed) == stored


def test_a_logged_qso_stores_the_call_upper_and_the_name_and_qth_capitalised(state):
    row = QsoService.log(
        {"call": "ea1abc", "name": "mAnuel angel", "qth": "SAN fernando"}, state
    )

    assert (row.call, row.name, row.qth) == ("EA1ABC", "Manuel Angel", "San Fernando")


def test_editing_a_qso_normalises_too(state):
    row = QsoService.log({"call": "EA1ABC"}, state)

    edited = QsoService.update(row.id, {"call": "ea1xyz"})

    assert edited.call == "EA1XYZ"


def test_an_address_book_entry_is_normalised(tmp_path):
    entry = ContactService.create(
        "ea7klx", first_name="mAnuel angel", last_name="alcocer JIMÉNEZ", city="sevilla"
    )

    assert (entry.callsign, entry.first_name, entry.last_name, entry.city) == (
        "EA7KLX", "Manuel Angel", "Alcocer Jiménez", "Sevilla"
    )

    edited = ContactService.update(entry.id, first_name="pepe", city="dos hermanas")
    assert (edited.first_name, edited.city) == ("Pepe", "Dos Hermanas")


def test_an_operator_is_normalised():
    operator = OperatorService.create("ea7wm", "manuel", "IM76", "sevilla")

    assert (operator.callsign, operator.name, operator.qth) == ("EA7WM", "Manuel", "Sevilla")


@pytest.mark.parametrize("old_version", [11, 12])
def test_upgrading_an_older_database_normalises_what_it_holds(tmp_path, old_version):
    """Rows written before schema 12, and the particles 12 capitalised, get
    the current treatment on start."""
    from ic705suite.apps.log.db import session as db_session

    path = tmp_path / "old.sqlite3"
    url = f"sqlite:///{path.as_posix()}"
    db_session.dispose()
    db_session.init_engine(url)
    operator = OperatorService.create("EA7WM", "Manuel")
    contact = ContactService.create("EA7KLX", first_name="Manuel")
    qso = QsoService.log({"call": "EA1ABC"}, SessionState(operator_id=operator.id))
    db_session.dispose()

    # What an older version could have stored.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE operators SET name = 'mAnuel', qth = 'Alcalá De Henares'")
        connection.execute(
            "UPDATE contacts SET callsign = 'ea7klx', first_name = 'mAnuel angel', "
            "last_name = 'alcocer', city = 'sevilla'"
        )
        connection.execute("UPDATE qsos SET call = 'ea1abc', name = 'pepe', qth = 'cádiz'")
        connection.execute("UPDATE schema_version SET version = ?", (old_version,))

    db_session.init_engine(url)

    stored = OperatorService.get(operator.id)
    assert (stored.name, stored.qth) == ("Manuel", "Alcalá de Henares")
    entry = ContactService.get(contact.id)
    assert (entry.callsign, entry.first_name, entry.last_name, entry.city) == (
        "EA7KLX", "Manuel Angel", "Alcocer", "Sevilla"
    )
    row = QsoService.get(qso.id)
    assert (row.call, row.name, row.qth) == ("EA1ABC", "Pepe", "Cádiz")


def test_a_repeater_gets_its_callsign_upper_and_keeps_its_qth():
    from ic705suite.apps.log.core.services import RepeaterService

    repeater = RepeaterService.create("ed7zab", output_hz=145_600_000, qth="Cuitu Negru 1850 m.")

    assert repeater.callsign == "ED7ZAB"
    assert repeater.qth == "Cuitu Negru 1850 m."
