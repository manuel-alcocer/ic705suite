"""Every test keeps away from the operator's own data."""

import pytest


@pytest.fixture(autouse=True)
def private_logbook(tmp_path, monkeypatch):
    """hamrlog's database (opened by the dashboard's quick log too) in a temporary folder."""
    home = tmp_path / "hamrlog"
    home.mkdir()
    monkeypatch.setenv("HAMRLOG_HOME", str(home))
    monkeypatch.setenv("HAMRLOG_DATABASE_URL", f"sqlite:///{home / 'hamrlog.sqlite3'}")
    monkeypatch.setenv("HAMRLOG_PRESEED", "0")


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """The suite in English whatever the machine's language (a Spanish Windows, say)."""
    monkeypatch.setenv("IC705SUITE_LANG", "en")


@pytest.fixture(autouse=True)
def private_packet(tmp_path, monkeypatch):
    """hamrpkt's old settings (taken over by [packet] when it is missing) in an empty folder."""
    home = tmp_path / "hamrpkt"
    home.mkdir()
    monkeypatch.setenv("HAMRPKT_HOME", str(home))
