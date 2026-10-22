"""An iPhone's battery, as UPower reads it.

UPower — the power daemon every Ubuntu desktop runs — has its own iPhone
support: through libimobiledevice and usbmuxd it reads a connected, *paired*
iPhone's battery and lists it as a device of kind ``phone``. This provider
only reads that list, with the ``upower`` command-line tool:

    upower --dump        every power device UPower knows, one block each

so no iOS library is linked into MIRA and nothing talks to the phone but
UPower itself. Pairing is the phone's "Trust This Computer?" prompt; until
the user accepts it UPower lists no phone, and the query says so (and is
retried for a while).

Only blocks of kind ``phone`` are read — never ``battery`` (this computer's
own), ``line-power`` or a peripheral — and a percentage only counts together
with a charging state UPower actually determined, so a placeholder is never
shown as a reading. UPower's blocks carry serial numbers and native paths;
they are skipped while parsing and never kept or logged.

The phone in the list is matched to the USB device by elimination: this
provider is asked only about a device that offers Apple's multiplexer
(`DeviceEvent.services` has ``"usbmux"``), and only if UPower lists exactly
one phone is it taken to be that one. With two iPhones connected, nothing is
claimed for either.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

from mira.adapters.subprocesses import CancellableRunner, Cancelled
from mira.domain.device_info import ChargingState, DeviceInfo, InfoResult
from mira.domain.system_events import DeviceEvent

TIMEOUT_S = 5.0

_STATE = {
    "charging": ChargingState.CHARGING,
    "discharging": ChargingState.DISCHARGING,
    "fully-charged": ChargingState.FULL,
    "pending-charge": ChargingState.NOT_CHARGING,
}

NOT_INSTALLED = "upower is not installed"
NOT_LISTED = "UPower lists no phone: is the iPhone unlocked and has it trusted this computer?"
AMBIGUOUS = "UPower lists more than one phone; cannot tell which one this is"
TIMED_OUT = "upower did not answer in time"
FAILED = "upower failed"
NOTHING_READ = "UPower reported nothing readable for the phone"
CANCELLED = "MIRA is shutting down"

# The only keys read; serial and native-path are among those ignored.
_KEPT = frozenset({"vendor", "model", "percentage", "state", "present"})


def parse_dump(output: str) -> list[dict[str, str]]:
    """The ``phone`` blocks of ``upower --dump``, with only the keys in `_KEPT`."""

    phones, block, kind = [], {}, None

    def close():
        if kind == "phone":
            phones.append(block)

    for raw in output.splitlines():
        if raw.startswith("Device:"):
            close()
            block, kind = {}, None
            continue
        line = raw.strip()
        if not line:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            # A line of its own names the kind ("battery", "phone", …).
            if kind is None and raw.startswith("  ") and " " not in line:
                kind = line
            continue
        key = key.strip()
        if key in _KEPT:
            block[key] = value.strip()
    close()
    return phones


def read_phone(block: dict[str, str]) -> DeviceInfo:
    if block.get("present", "yes") != "yes":
        return DeviceInfo()
    charging = _STATE.get(block.get("state", ""))
    percent = None
    if charging is not None:
        try:
            value = float(block.get("percentage", "").rstrip("%").strip())
            if 0 <= value <= 100:
                percent = round(value)
        except ValueError:
            pass
    return DeviceInfo(
        manufacturer=block.get("vendor") or None,
        model=block.get("model") or None,
        battery_percent=percent,
        charging=charging if percent is not None else None,
    )


class UPowerPhoneProvider:
    """A `DeviceInfoProvider` for iPhones through UPower; see the module."""

    name = "upower"

    def __init__(self, *, which: Callable[[str], str | None] = shutil.which, run=None):
        self._which = which
        self._runner = CancellableRunner()
        self._run = self._runner if run is None else run

    def supports(self, device: DeviceEvent) -> bool:
        return "usbmux" in device.services

    def cancel(self) -> None:
        self._runner.cancel()

    def query(self, device: DeviceEvent) -> InfoResult:
        if self._runner.cancelled:
            return InfoResult(problem=CANCELLED)
        upower = self._which("upower")
        if upower is None:
            return InfoResult(problem=NOT_INSTALLED)
        try:
            completed = self._run(
                [upower, "--dump"], capture_output=True, text=True, timeout=TIMEOUT_S,
                stdin=subprocess.DEVNULL, check=False,
            )
        except Cancelled:
            return InfoResult(problem=CANCELLED)
        except subprocess.TimeoutExpired:
            return InfoResult(problem=TIMED_OUT, retry=True)
        except OSError:
            return InfoResult(problem=FAILED)
        if completed.returncode != 0:
            return InfoResult(problem=FAILED, retry=True)
        phones = parse_dump(completed.stdout or "")
        if not phones:
            return InfoResult(problem=NOT_LISTED, retry=True)
        if len(phones) > 1:
            return InfoResult(problem=AMBIGUOUS)
        info = read_phone(phones[0])
        if info.battery_percent is None:
            # Vendor and model alone say nothing the USB connection did not.
            return InfoResult(problem=NOTHING_READ, retry=True)
        return InfoResult(info)
