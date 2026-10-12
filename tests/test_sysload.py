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


def test_the_cpu_temperature(tmp_path):
    """A PC's package sensor before the ACPI zone; a Raspberry Pi's cpu-thermal zone."""
    from ic705suite.ui.sysload import _sensor, _temperature

    def chip(name: str, milli: int, kind: str = "hwmon") -> None:
        folder = tmp_path / "hwmon" / f"hwmon{len(list((tmp_path / 'hwmon').glob('*')))}" \
            if kind == "hwmon" else tmp_path / "thermal" / f"thermal_zone{name}"
        folder.mkdir(parents=True)
        if kind == "hwmon":
            (folder / "name").write_text(name + "\n")
            (folder / "temp1_input").write_text(f"{milli}\n")
        else:
            (folder / "type").write_text(name + "\n")
            (folder / "temp").write_text(f"{milli}\n")

    (tmp_path / "hwmon").mkdir()
    assert _sensor(tmp_path) is None and _temperature(None) is None
    chip("acpitz", 40000, "zone")
    assert _temperature(_sensor(tmp_path)) == 40.0
    chip("cpu-thermal", 61348, "zone")
    assert _temperature(_sensor(tmp_path)) == 61.348
    chip("nvme", 36850)
    chip("coretemp", 85000)
    assert _temperature(_sensor(tmp_path)) == 85.0


def test_the_cores_machine_too(monkeypatch):
    """With the radio's core elsewhere, both machines' load, each by its name; not when the
    core's news is old."""
    from ic705suite.ui import sysload

    monkeypatch.setattr(sysload, "CORE", {})
    monkeypatch.setattr(sysload, "host_name", lambda: "dune")
    assert "│" not in sysload.load_text().plain
    sysload.set_core({"name": "wpsd", "cpu": 18.2, "ram": 4.2, "rss": 41 * 2**20, "temp": 61.3})
    text = sysload.load_text().plain
    assert text.startswith("dune CPU") and "│ wpsd CPU 18% · RAM 4.2% 41M · 61°C" in text
    sysload.CORE["received"] -= sysload.STALE + 1
    assert "wpsd" not in sysload.load_text().plain
