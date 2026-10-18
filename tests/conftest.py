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


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    """The suite's data folder (ft8_log.adi, FT8's settings, the antenna history...) in a
    temporary one: tests wrote their QSOs into the operator's own log."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("LOCALAPPDATA", str(data))


@pytest.fixture(autouse=True)
def no_reflector_pages(monkeypatch):
    """D-STAR's reflector status reads web pages: never from the tests (a test that wants
    some gives them to status.check)."""
    from ic705suite.apps.dstar import status

    def offline(url, *args, **kwargs):
        raise OSError(f"no network in the tests ({url})")

    monkeypatch.setattr(status, "http_get", offline)
