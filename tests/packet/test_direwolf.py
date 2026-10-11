"""Direwolf's configuration and the Digirig found among the devices (a copy of the
Raspberry Pi's /proc and /sys, where it was tried)."""

from __future__ import annotations

import os
import sys

import pytest

from ic705suite.apps.packet.direwolf import Settings, _linux_devices, config_text


def test_the_configuration_written():
    text = config_text(Settings("EA7KLX-3", "plughw:Device,0", "/dev/ttyUSB0 RTS",
                                agw_port=8010))
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert lines == ["ADEVICE plughw:Device,0", "ARATE 48000", "ACHANNELS 1", "CHANNEL 0",
                     "MYCALL EA7KLX-3", "MODEM 1200", "PTT /dev/ttyUSB0 RTS", "TXDELAY 30",
                     "TXTAIL 10", "AGWPORT 8010", "KISSPORT 0"]
    assert "PTT" not in config_text(Settings("EA7KLX-3", "pulse")).replace("No PTT", "")


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links, as Linux has them")
def test_the_digirig_on_the_raspberry_pi(tmp_path):
    usb = tmp_path / "sys/devices/platform/fd500000.pcie/pci0000:00/0000:01:00.0/usb1/1-1/1-1.1"
    asound = tmp_path / "proc/asound"
    (asound / "card0").mkdir(parents=True)
    (asound / "card0" / "id").write_text("vc4hdmi0\n")
    (asound / "card2").mkdir()
    (asound / "card2" / "id").write_text("Device\n")
    (asound / "card2" / "usbid").write_text("0d8c:0012\n")
    sound = usb / "1-1.1.2" / "1-1.1.2:1.0" / "sound" / "card2"
    serial = usb / "1-1.1.1" / "1-1.1.1:1.0" / "ttyUSB0"
    sound.mkdir(parents=True)
    serial.mkdir(parents=True)
    classes = tmp_path / "sys/class"
    (classes / "sound" / "card2").mkdir(parents=True)
    os.symlink(sound.parent.parent, classes / "sound" / "card2" / "device")
    (classes / "tty" / "ttyUSB0").mkdir(parents=True)
    os.symlink(serial.parent, classes / "tty" / "ttyUSB0" / "device")
    dev = tmp_path / "dev"
    (dev / "serial/by-id").mkdir(parents=True)
    (dev / "ttyUSB0").write_text("")
    link = dev / "serial/by-id" / "usb-Silicon_Labs_CP2102N_8665-if00-port0"
    os.symlink("../../ttyUSB0", link)

    devices = _linux_devices(asound, tmp_path / "sys", dev / "serial/by-id")
    assert len(devices) == 1
    assert devices[0].audio == "plughw:Device,0"
    assert devices[0].ptt == f"{link} RTS"
    assert devices[0].description == "sound card Device + ttyUSB0"

    # A CM108 card with no serial port by it: PTT through the card itself.
    os.remove(link)
    assert _linux_devices(asound, tmp_path / "sys", dev / "serial/by-id")[0].ptt == "CM108"
