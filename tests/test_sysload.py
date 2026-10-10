"""CPU and RAM of the suite's own process, for the status lines."""

import time

from rich.text import Text

from ic705suite.ui import sysload


def test_process_load(monkeypatch):
    monkeypatch.setattr(sysload, "MIN_INTERVAL", 0.0)
    load = sysload.ProcessLoad()
    deadline = time.process_time() + 0.2
    while time.process_time() < deadline:       # keep a core busy for a moment
        pass
    load.sample()
    assert load.cpu > 10
    assert load.rss and 0 < load.ram < 100


def test_load_goes_right_or_not_at_all():
    line = sysload.with_load(Text("IC-705 · QRG 7.030.00"), 80)
    assert line.cell_len == 80 and "CPU" in line.plain and line.plain.startswith("IC-705")
    narrow = sysload.with_load(Text("x" * 77), 80)
    assert "CPU" not in narrow.plain
