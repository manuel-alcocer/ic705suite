"""Shared fixtures.

Every test gets a fresh SQLite file so module level engine state from one test
can never leak into the next.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

from ic705suite.apps.log.core import units
from ic705suite.apps.log.core.services import AntennaService, OperatorService, StationService
from ic705suite.apps.log.core.state import SessionState
from ic705suite.apps.log.db import session as db_session


@pytest.fixture(autouse=True, scope="session")
def spanish_interface():
    """The tests read the interface in Spanish, the language it was written for."""
    from ic705suite.apps.log import i18n

    i18n.set_language("es")


@pytest.fixture(autouse=True)
def default_frequency_format():
    """Frequency presentation is module-level state; reset it per test."""
    units.set_active(units.FrequencyFormat())
    yield
    units.set_active(units.FrequencyFormat())


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    """Point the application at a throwaway database.

    The URL goes through the environment rather than straight into
    ``init_engine`` so that code which re-initialises the engine on its own
    (the TUI does, on mount) resolves to the same file.
    """
    url = f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"
    postgres = os.environ.get("IC705SUITE_TEST_POSTGRES")
    if postgres:
        # Same tests on PostgreSQL: an empty schema for each one.
        url = postgres
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        engine.dispose()
    monkeypatch.setenv("HAMRLOG_HOME", str(tmp_path))
    monkeypatch.setenv("HAMRLOG_DATABASE_URL", url)
    # Most tests count rows; the catalog has its own tests.
    monkeypatch.setenv("HAMRLOG_PRESEED", "0")
    db_session.dispose()
    db_session.init_engine(url)
    yield
    db_session.dispose()


@pytest.fixture
def operator():
    return OperatorService.create("EA7WM", "Manuel", "IM76", "Sevilla")


@pytest.fixture
def station():
    created = StationService.create("HF-Casa", rig="IC-7300", power_w=100)
    antenna = AntennaService.create("Dipolo", ["40m", "20m"])
    StationService.assign_antenna(created.id, antenna.id)
    return StationService.get(created.id)


@pytest.fixture
def state(operator, station) -> SessionState:
    """A configured session: 40 m, SSB, with operator and rig selected."""
    value = SessionState(
        operator_id=operator.id, station_id=station.id, antenna_id=station.antennas[0].id
    )
    value.set_band("40m")
    value.set_mode("SSB")
    return value
