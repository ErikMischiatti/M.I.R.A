"""The adb provider, against a scripted ``adb``: no phone and no adb needed.

Every command is answered by `FakeAdb` from realistic output, and every
test also checks the two promises that matter most: only the three
read-only commands ever run, and the serial number never leaves parsing.
"""

from __future__ import annotations

import subprocess

import pytest

from adb_doubles import BATTERY, PIXEL, PIXEL_LISTED, SERIAL, FakeAdb, listing
from mira.adapters import adb
from mira.adapters.adb import (
    PROPERTIES_AND_BATTERY,
    AdbDeviceInfoProvider,
    AdbDevice,
    parse_battery,
    parse_devices,
    parse_properties_and_battery,
)
from mira.domain.device_info import ChargingState, DeviceInfo, InfoResult
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind

def provider(fake: FakeAdb, *, installed=True) -> AdbDeviceInfoProvider:
    return AdbDeviceInfoProvider(which=lambda name: "/usr/bin/adb" if installed else None, run=fake)


def no_serial_anywhere(fake: FakeAdb, result: InfoResult) -> None:
    assert all(SERIAL not in " ".join(call) for call in fake.calls)
    assert SERIAL not in repr(result)


# --- Parsing ---------------------------------------------------------------------------


def test_the_device_list_keeps_state_port_and_transport_but_never_the_serial():
    devices = parse_devices(listing(
        "* daemon not running; starting now at tcp:5037",
        "* daemon started successfully",
        PIXEL_LISTED,
        "R58M12345 unauthorized usb:3-1 transport_id:8",
        "ABCDEF no permissions (missing udev rules? user is in the plugdev group); "
        "see [http://developer.android.com/tools/device.html] usb:1-4 transport_id:9",
        "emulator-5554 device product:sdk_gphone64 model:sdk transport_id:1",
    ))

    assert devices == [
        AdbDevice("device", "3-2", "7", "Pixel 8"),
        AdbDevice("unauthorized", "3-1", "8", None),
        AdbDevice("no permissions", "1-4", "9", None),
        AdbDevice("device", None, "1", "sdk"),
    ]
    assert SERIAL not in repr(devices) and "R58M12345" not in repr(devices)


@pytest.mark.parametrize(("status", "state"), [
    (2, ChargingState.CHARGING), (3, ChargingState.DISCHARGING),
    (4, ChargingState.NOT_CHARGING), (5, ChargingState.FULL), (1, None), (9, None),
])
def test_charging_state_comes_from_androids_status_code(status, state):
    assert parse_battery(BATTERY.replace("status: 2", f"status: {status}")) == (84, state)


@pytest.mark.parametrize(("battery", "percent"), [
    (BATTERY, 84),
    (BATTERY.replace("scale: 100", "scale: 200"), 42),
    (BATTERY.replace("level: 84", "level: abc"), None),
    (BATTERY.replace("level: 84", "level: 150"), None),
    (BATTERY.replace("scale: 100", "scale: 0"), None),
    (BATTERY.replace("  level: 84\n", ""), None),
    ("Can't find service: battery\n", None),
    ("", None),
])
def test_an_unreadable_level_is_left_unread(battery, percent):
    assert parse_battery(battery)[0] == percent


def test_a_device_without_a_battery_reports_none_at_all():
    assert parse_battery(BATTERY.replace("present: true", "present: false")) == (None, None)


def test_properties_come_first_and_unexpected_output_is_refused():
    assert parse_properties_and_battery(f"Google\nPixel 8\n{BATTERY}") == \
        DeviceInfo("Google", "Pixel 8", 84, ChargingState.CHARGING)
    assert parse_properties_and_battery(f"\n\n{BATTERY}") == DeviceInfo(None, None, 84, ChargingState.CHARGING)
    assert parse_properties_and_battery(BATTERY) == DeviceInfo()  # no property lines: not what was asked
    assert parse_properties_and_battery("") == DeviceInfo()


# --- Querying a phone ------------------------------------------------------------------


def test_an_authorized_phone_reports_model_and_battery_with_three_read_only_commands():
    fake = FakeAdb()

    result = provider(fake).query(PIXEL)

    assert result == InfoResult(DeviceInfo("Google", "Pixel 8", 84, ChargingState.CHARGING))
    assert fake.calls == [
        ["/usr/bin/adb", "devices", "-l"],
        ["/usr/bin/adb", "-t", "7", "shell", PROPERTIES_AND_BATTERY],
    ]
    no_serial_anywhere(fake, result)


def test_only_phones_offering_the_debug_bridge_are_supported():
    adb_provider = provider(FakeAdb())

    assert adb_provider.supports(PIXEL)
    assert not adb_provider.supports(DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Pixel 8", "3-2",
                                                 services=frozenset({"mtp"})))


def test_without_adb_nothing_runs():
    fake = FakeAdb()

    result = provider(fake, installed=False).query(PIXEL)

    assert result == InfoResult(problem=adb.NOT_INSTALLED) and fake.calls == []


@pytest.mark.parametrize(("line", "problem", "retry"), [
    (f"{SERIAL} unauthorized usb:3-2 transport_id:7", adb.UNAUTHORIZED, True),
    (f"{SERIAL} offline usb:3-2 transport_id:7", adb.OFFLINE, True),
    (f"{SERIAL} no permissions (missing udev rules?); see [http://x] usb:3-2 transport_id:7", adb.NO_PERMISSION, False),
    (f"{SERIAL} recovery usb:3-2 transport_id:7", "the phone is in 'recovery' mode", False),
    (f"{SERIAL} device usb:3-2 model:Pixel_8", adb.NO_TRANSPORT, False),
])
def test_a_phone_adb_cannot_use_says_why_and_is_not_asked_further(line, problem, retry):
    fake = FakeAdb(devices=listing(line))

    result = provider(fake).query(PIXEL)

    assert result == InfoResult(problem=problem, retry=retry)
    assert len(fake.calls) == 1
    no_serial_anywhere(fake, result)


def test_a_phone_adb_does_not_list_yet_is_retried():
    fake = FakeAdb(devices=listing())

    assert provider(fake).query(PIXEL) == InfoResult(problem=adb.NOT_LISTED, retry=True)


def test_several_phones_are_told_apart_by_their_usb_port():
    fake = FakeAdb(devices=listing(
        "OTHERSERIAL device usb:3-1 product:a54x model:SM_A546B transport_id:4",
        PIXEL_LISTED,
        "THIRD device usb:1-4 model:moto_g transport_id:5",
    ))

    result = provider(fake).query(PIXEL)

    assert result.info.model == "Pixel 8"
    assert fake.calls[1][:3] == ["/usr/bin/adb", "-t", "7"]
    assert all("OTHERSERIAL" not in " ".join(call) for call in fake.calls)


def test_two_entries_on_one_port_are_not_guessed_between():
    fake = FakeAdb(devices=listing(PIXEL_LISTED, "OTHER device usb:3-2 transport_id:8"))

    assert provider(fake).query(PIXEL) == InfoResult(problem=adb.AMBIGUOUS)


@pytest.mark.parametrize(("where", "error", "problem", "retry"), [
    ("devices", subprocess.TimeoutExpired("adb", 8), adb.TIMED_OUT, True),
    ("shell", subprocess.TimeoutExpired("adb", 5), adb.TIMED_OUT, True),
    ("devices", FileNotFoundError("adb"), adb.FAILED, False),
    ("shell", f"error: device unauthorized.\nThis adb server's $ADB_VENDOR_KEYS is not set", adb.UNAUTHORIZED, True),
    ("shell", "error: device offline", adb.OFFLINE, True),
    ("shell", "error: no device with transport id '7'", adb.GONE, False),
    ("shell", f"error: device '{SERIAL}' not found", adb.GONE, False),
    ("devices", "cannot connect to daemon", adb.FAILED, True),
])
def test_a_failing_or_slow_adb_is_a_problem_never_an_exception(where, error, problem, retry):
    fake = FakeAdb(**{f"{where}_error": error})

    result = provider(fake).query(PIXEL)

    assert result == InfoResult(problem=problem, retry=retry)
    no_serial_anywhere(fake, result)


def test_invalid_battery_output_keeps_what_was_read():
    fake = FakeAdb(shell="Google\nPixel 8\nCan't find service: battery\n")

    assert provider(fake).query(PIXEL) == InfoResult(DeviceInfo("Google", "Pixel 8", None, None))


def test_output_that_says_nothing_is_nothing_read():
    fake = FakeAdb(devices=listing(f"{SERIAL} device usb:3-2 transport_id:7"), shell="")

    assert provider(fake).query(PIXEL) == InfoResult(problem=adb.NOTHING_READ)


def test_the_listed_model_stands_in_when_the_property_is_empty():
    fake = FakeAdb(shell=f"Google\n\n{BATTERY}")

    assert provider(fake).query(PIXEL).info.model == "Pixel 8"
