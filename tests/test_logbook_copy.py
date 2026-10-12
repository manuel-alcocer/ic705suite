"""A logbook copied whole into another, empty database (to the core's PostgreSQL)."""

import pytest

from ic705suite import cli
from ic705suite.logbook_copy import CopyError, copy_logbook


def make_logbook(url: str) -> None:
    from ic705suite.apps.log.core.services import OperatorService, QsoService, SettingsService
    from ic705suite.apps.log.db import session

    session.dispose()
    session.init_engine(url)
    operator = OperatorService.create("EA7KLX", "Manuel")
    state = SettingsService.load_state()
    state.operator_id = operator.id
    SettingsService.save_state(state)
    for call in ("F5RRS", "M7MOK", "MW3FLI"):
        QsoService.log({"call": call, "mode": "FT8", "band": "20m"}, state)
    session.dispose()


def test_a_logbook_copied_whole(tmp_path, capsys):
    source, target = f"sqlite:///{tmp_path / 'pc.sqlite3'}", f"sqlite:///{tmp_path / 'core.db'}"
    make_logbook(source)
    assert cli.main(["--copy-logbook", source, target]) == 0
    assert "logbook copied: 3 QSOs" in capsys.readouterr().out

    from ic705suite.apps.log.core.services import QsoService
    from ic705suite.apps.log.db import session

    session.init_engine(target)
    try:
        assert [q.call for q in QsoService.recent()] == ["F5RRS", "M7MOK", "MW3FLI"]
        assert QsoService.recent()[0].operator_callsign == "EA7KLX"
    finally:
        session.dispose()
    with pytest.raises(CopyError, match="must be an empty logbook"):
        copy_logbook(source, target)                           # never over a logbook
