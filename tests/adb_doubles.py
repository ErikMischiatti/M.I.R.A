"""A scripted ``adb`` for the adb provider: realistic output, no phone, no adb.

`FakeAdb` stands in for `subprocess.run`: it answers ``adb devices -l`` and
the provider's one shell command from canned output, refuses any other
command, and records every call so tests can check what ran.
"""

from __future__ import annotations

import subprocess

from mira.adapters.adb import PROPERTIES_AND_BATTERY
from mira.domain.devices import DeviceCategory
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind

SERIAL = "1A2B3C4D5E6F"
PIXEL = DeviceEvent(
    DeviceKind.USB, DeviceChange.CONNECTED, "Google Pixel 8", "/devices/pci0000:00/0000:00:14.0/usb3/3-2",
    category=DeviceCategory.SMARTPHONE, services=frozenset({"mtp", "adb"}),
)

BATTERY = """Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  Max charging current: 500000
  Charge counter: 3456789
  status: 2
  health: 2
  present: true
  level: 84
  scale: 100
  voltage: 4123
  temperature: 280
  technology: Li-ion
  Charging state: 1
"""


def listing(*lines: str) -> str:
    return "List of devices attached\n" + "".join(line + "\n" for line in lines) + "\n"


PIXEL_LISTED = f"{SERIAL}       device usb:3-2 product:shiba model:Pixel_8 device:shiba transport_id:7"


class FakeAdb:
    """`subprocess.run` for adb: answers by command, records every call."""

    def __init__(self, devices=listing(PIXEL_LISTED), shell=f"Google\nPixel 8\n{BATTERY}", *,
                 devices_error=None, shell_error=None):
        self.devices, self.shell = devices, shell
        self.devices_error, self.shell_error = devices_error, shell_error
        self.calls: list[list[str]] = []

    def __call__(self, command, **options):
        assert options["timeout"] > 0 and options["stdin"] is subprocess.DEVNULL
        self.calls.append(command)
        if command[1:] == ["devices", "-l"]:
            return self._answer(command, self.devices, self.devices_error)
        assert command[1] == "-t" and command[3:] == ["shell", PROPERTIES_AND_BATTERY], command
        return self._answer(command, self.shell, self.shell_error)

    @staticmethod
    def _answer(command, stdout, error):
        if isinstance(error, BaseException):
            raise error
        if error is not None:
            return subprocess.CompletedProcess(command, 1, "", error)
        return subprocess.CompletedProcess(command, 0, stdout, "")
