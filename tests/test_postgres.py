"""The suite's tables on PostgreSQL. Runs only with a database to spare:

    IC705SUITE_TEST_POSTGRES=postgresql+psycopg://user@host/db uv run pytest tests/test_postgres.py

(the same variable runs hamrlog's tests on it too). Everything in the database is dropped.
"""

import os

import numpy as np
import pytest
import sqlalchemy as sa

from ic705suite.apps.cw.channels import AudioSpool, ChannelBook, ChannelStore
from ic705suite.apps.swr.service import HistoryStore

URL = os.environ.get("IC705SUITE_TEST_POSTGRES", "")
pytestmark = pytest.mark.skipif(not URL, reason="IC705SUITE_TEST_POSTGRES not set")


@pytest.fixture(autouse=True)
def empty_schema():
    engine = sa.create_engine(URL)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    engine.dispose()


def test_cw_channels_on_postgres(tmp_path):
    store, spool, book = ChannelStore(URL, audio_dir=tmp_path), AudioSpool(8000), ChannelBook()
    try:
        spool.write(np.zeros(8000, np.float32), 7_007_240)
        book.tuned_rx(7_007_240, "CQ CQ DE SP3BZU SP3BZU K ", 25, 14, spool.position)
        record = book.tuned
        record.tick()
        archive_id, name = store.save(record, record.callsign, None, spool, with_audio=True)
        book.tuned_rx(7_007_240, "R TU 73 SK ", 25, 14, spool.position)
        store.save(record, None, store.by_key("SP3BZU", 7_007_400), spool, with_audio=False)
        saved = store.transcript(archive_id)
        assert name == "SP3BZU" and saved["stats"]["words"] == 10
        assert store.auto_name() == "Channel 1" and [c["name"] for c in store.list()] == ["SP3BZU"]
    finally:
        spool.close()
        store.close()


def test_antenna_history_on_postgres():
    store = HistoryStore(URL, legacy=None)
    store.add({"ts": "2026-10-10 12:04:53", "band": "40m", "kind": "final", "coil": 12.2})
    assert store.entries("40m")[0]["coil"] == 12.2 and store.entries("20m") == []
    store.close()


def test_the_logbook_survives_a_connection_the_server_dropped():
    # Overnight the server dropped the pooled connection, and the first QSO of the day
    # failed with "SSL SYSCALL error: EOF detected".
    from ic705suite.apps.log.db import session

    engine = session.init_engine(URL)
    try:
        with engine.connect() as conn:
            pid = conn.execute(sa.text("SELECT pg_backend_pid()")).scalar()
        killer = sa.create_engine(URL)
        with killer.begin() as conn:
            conn.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        killer.dispose()
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT 1")).scalar() == 1
    finally:
        session.dispose()
