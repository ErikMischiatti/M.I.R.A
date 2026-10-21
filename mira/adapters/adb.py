"""Android phones' model and battery, read through the Android Debug Bridge.

Optional: it needs ``adb`` on the PATH (Ubuntu: ``sudo apt install adb``) and
a phone with *USB debugging* enabled that has *authorized* this computer.
Without either, a query says why and nothing is shown beyond what the USB
connection already said. Only phones whose USB descriptors offer the debug
interface (`DeviceEvent.services` has ``"adb"``) are asked at all, so no
command runs for a phone without debugging enabled.

Read-only, three things only, each a subprocess with a timeout:

    adb devices -l                   which phones adb sees, their state, their USB port
    adb -t <id> shell getprop ro.product.manufacturer; getprop ro.product.model;
                      dumpsys battery

The phone is matched by its **USB port** — adb's ``usb:3-2`` is the same
sysfs name as the end of `DeviceEvent.key` — and addressed by adb's
``transport_id``, so its serial number is never used, stored or logged; it
is the first column of the list and is dropped while parsing. adb's own
output never reaches a log line either (an error message can name the
serial); problems are fixed sentences. Several phones at once are told apart
by their ports.

The battery is the *phone's*: ``dumpsys battery`` runs on the phone, so the
computer's own battery can never be read by mistake. ``level``/``scale`` give
the percentage and ``status`` the charging state (Android's
``BatteryManager`` codes); a battery that is not ``present``, or a value that
does not parse, is left unread rather than guessed.

The first ``adb`` call starts adb's server on this computer if it is not
running (and it keeps running, as it does for any adb use); nothing is ever
changed on the phone.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from mira.domain.device_info import ChargingState, DeviceInfo, InfoResult
from mira.domain.system_events import DeviceEvent

LIST_TIMEOUT_S = 8.0
"""Listing may start adb's server first, which takes a few seconds."""
QUERY_TIMEOUT_S = 5.0

PROPERTIES_AND_BATTERY = "getprop ro.product.manufacturer; getprop ro.product.model; dumpsys battery"

# android.os.BatteryManager.BATTERY_STATUS_*; 1 (unknown) is left unread.
_STATUS = {
    2: ChargingState.CHARGING,
    3: ChargingState.DISCHARGING,
    4: ChargingState.NOT_CHARGING,
    5: ChargingState.FULL,
}

# Problems are fixed sentences: adb's own messages may contain the serial.
NOT_INSTALLED = "adb is not installed"
NOT_LISTED = "adb does not list the phone (yet)"
UNAUTHORIZED = "the phone has not authorized this computer: accept 'Allow USB debugging' on the phone"
OFFLINE = "adb reports the phone offline"
NO_PERMISSION = "no permission to the phone's USB device (udev rules / plugdev group)"
AMBIGUOUS = "adb lists more than one device on this USB port"
NO_TRANSPORT = "this adb is too old to address a phone without its serial (no transport_id)"
TIMED_OUT = "adb did not answer in time"
FAILED = "adb failed"
GONE = "the phone is no longer connected"
NOTHING_READ = "the phone reported nothing readable"


@dataclass(frozen=True, slots=True)
class AdbDevice:
    """One line of ``adb devices -l``, without its serial."""

    state: str
    usb_port: str | None
    transport_id: str | None
    model: str | None


def parse_devices(output: str) -> list[AdbDevice]:
    """The devices in ``adb devices -l`` output; the serial is not kept."""

    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*", "adb server", "error")):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        rest = tokens[1:]  # tokens[0] is the serial: dropped here
        if rest[:2] == ["no", "permissions"]:
            state = "no permissions"
        else:
            state = rest[0]
        fields = dict(token.split(":", 1) for token in rest if ":" in token and not token.startswith("("))
        devices.append(AdbDevice(
            state=state,
            usb_port=fields.get("usb"),
            transport_id=fields.get("transport_id"),
            model=(fields.get("model") or "").replace("_", " ") or None,
        ))
    return devices


def parse_battery(output: str) -> tuple[int | None, ChargingState | None]:
    """Percentage and charging state from ``dumpsys battery``; None where unreadable."""

    fields = {}
    for line in output.splitlines():
        key, separator, value = line.strip().partition(":")
        if separator:
            fields.setdefault(key.strip().lower(), value.strip())
    if fields.get("present", "true").lower() != "true":
        return None, None
    percent = None
    try:
        level = int(fields["level"])
        scale = int(fields.get("scale", "100"))
        if scale > 0 and 0 <= level <= scale:
            percent = round(level * 100 / scale)
    except (KeyError, ValueError):
        pass
    try:
        charging = _STATUS.get(int(fields["status"]))
    except (KeyError, ValueError):
        charging = None
    return percent, charging


def parse_properties_and_battery(output: str) -> DeviceInfo:
    """`PROPERTIES_AND_BATTERY`'s output: two property lines, then the battery."""

    lines = output.splitlines()
    if len(lines) < 2:
        return DeviceInfo()
    manufacturer, model = (line.strip() or None for line in lines[:2])
    # A property line never contains "key: value"; one that does means the
    # output is not what was asked for.
    if any(value is not None and ": " in value for value in (manufacturer, model)):
        return DeviceInfo()
    percent, charging = parse_battery("\n".join(lines[2:]))
    return DeviceInfo(manufacturer=manufacturer, model=model, battery_percent=percent, charging=charging)


def usb_port(device: DeviceEvent) -> str:
    """The sysfs name of the device's port, e.g. ``3-2``, from its devpath."""
    return device.key.rstrip("/").rsplit("/", 1)[-1]


Runner = Callable[..., subprocess.CompletedProcess]


class AdbDeviceInfoProvider:
    """A `DeviceInfoProvider` for Android phones with USB debugging; see the module."""

    name = "adb"

    def __init__(self, *, which: Callable[[str], str | None] = shutil.which, run: Runner = subprocess.run):
        self._which = which
        self._run = run

    def supports(self, device: DeviceEvent) -> bool:
        return "adb" in device.services

    def query(self, device: DeviceEvent) -> InfoResult:
        adb = self._which("adb")
        if adb is None:
            return InfoResult(problem=NOT_INSTALLED)
        listing = self._call([adb, "devices", "-l"], LIST_TIMEOUT_S)
        if isinstance(listing, InfoResult):
            return listing
        port = usb_port(device)
        matches = [entry for entry in parse_devices(listing) if entry.usb_port == port]
        if not matches:
            return InfoResult(problem=NOT_LISTED, retry=True)
        if len(matches) > 1:
            return InfoResult(problem=AMBIGUOUS)
        entry = matches[0]
        if entry.state == "unauthorized":
            return InfoResult(problem=UNAUTHORIZED, retry=True)
        if entry.state == "offline":
            return InfoResult(problem=OFFLINE, retry=True)
        if entry.state == "no permissions":
            return InfoResult(problem=NO_PERMISSION)
        if entry.state != "device":
            return InfoResult(problem=f"the phone is in {entry.state!r} mode")
        if not entry.transport_id or not entry.transport_id.isdigit():
            return InfoResult(problem=NO_TRANSPORT)
        output = self._call([adb, "-t", entry.transport_id, "shell", PROPERTIES_AND_BATTERY], QUERY_TIMEOUT_S)
        if isinstance(output, InfoResult):
            return output
        info = parse_properties_and_battery(output)
        if info.model is None and entry.model is not None:
            info = DeviceInfo(info.manufacturer, entry.model, info.battery_percent, info.charging)
        if info.is_empty:
            return InfoResult(problem=NOTHING_READ)
        return InfoResult(info)

    def _call(self, command: list[str], timeout: float) -> str | InfoResult:
        try:
            completed = self._run(
                command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False,
            )
        except subprocess.TimeoutExpired:
            return InfoResult(problem=TIMED_OUT, retry=True)
        except OSError:
            return InfoResult(problem=FAILED)
        if completed.returncode != 0:
            error = (completed.stderr or "").lower()
            if "unauthorized" in error:
                return InfoResult(problem=UNAUTHORIZED, retry=True)
            if "offline" in error:
                return InfoResult(problem=OFFLINE, retry=True)
            if "no device" in error or "not found" in error:
                return InfoResult(problem=GONE)
            return InfoResult(problem=FAILED, retry=True)
        return completed.stdout or ""
