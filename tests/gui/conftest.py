"""The graphical client's tests: offscreen Qt on the same asyncio loop as the core (qasync).
Skipped without the "gui" extra."""

import asyncio
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("qasync")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from ic705suite.gui import theme

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


@pytest.fixture(scope="session")
def qloop(qapp):
    import qasync

    loop = qasync.QEventLoop(qapp)
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def close_windows(qapp):
    """Each test starts with no window of an earlier one left (and its key filter)."""
    yield
    from PySide6.QtWidgets import QApplication

    for widget in QApplication.topLevelWidgets():
        qapp.removeEventFilter(widget)
        widget.close()
        widget.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def run(qloop):
    """Runs a coroutine on the Qt loop: run(scenario())."""
    def runner(coroutine):
        asyncio.set_event_loop(qloop)
        return qloop.run_until_complete(asyncio.wait_for(coroutine, 60))
    return runner
