"""USB transport (planned for the next version).

Over USB the IC-705 exposes two CDC-ACM serial ports (the first one carries
CI-V at up to 115200 baud) and a USB audio codec visible to ALSA. This
transport will open the serial port for CI-V and capture the USB audio device;
the rest of the suite (IcomRig, station, applications, scanner, UI) already works with any
Transport, so nothing else needs to change.
"""

from __future__ import annotations

from ic705suite.rig.transport import Transport, TransportError


class UsbTransport(Transport):
    name = "usb"

    def __init__(self, serial_device: str, audio_device: str | None = None,
                 sample_rate: int = 8000, baudrate: int = 115200) -> None:
        super().__init__(sample_rate)
        self.serial_device = serial_device
        self.audio_device = audio_device
        self.baudrate = baudrate

    async def connect(self) -> None:
        raise TransportError("USB connection is not implemented yet (planned for the next "
                             "version); use WiFi (--host) or the simulator (--sim)")

    async def close(self) -> None:
        pass

    def send_civ(self, frame: bytes) -> None:
        raise TransportError("not connected")
