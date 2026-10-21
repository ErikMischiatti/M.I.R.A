"""The UPower and USB storage providers, and what an update says.

No iPhone, no USB stick: a scripted ``upower`` and a temporary sysfs tree.
"""

from __future__ import annotations

import subprocess

import pytest

from mira.adapters import upower_phone
from mira.adapters.upower_phone import UPowerPhoneProvider, parse_dump, read_phone
from mira.adapters.usb_storage import UsbStorageInfoProvider
from mira.domain.device_info import (
    ChargingState,
    DeviceInfo,
    DeviceInfoUpdate,
    InfoResult,
    device_info_notice,
    device_info_text,
    format_capacity,
)
from mira.domain.devices import DeviceCategory
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind

IPHONE = DeviceEvent(
    DeviceKind.USB, DeviceChange.CONNECTED, "Apple Inc. iPhone", "/d/usb3/3-2",
    category=DeviceCategory.SMARTPHONE, manufacturer="Apple Inc.", services=frozenset({"ptp", "usbmux"}),
)

HOST_BATTERY = """Device: /org/freedesktop/UPower/devices/battery_BAT0
  native-path:          BAT0
  vendor:               SMP
  model:                DELL 7FJ9235
  serial:               4321
  power supply:         yes
  battery
    present:             yes
    state:               discharging
    percentage:          57%
    icon-name:          'battery-good-symbolic'
"""
PHONE = """Device: /org/freedesktop/UPower/devices/phone_idevice_00008110deadbeef
  native-path:          idevice-00008110-DEADBEEF
  vendor:               Apple
  model:                iPhone
  serial:               00008110-DEADBEEF
  power supply:         no
  phone
    present:             yes
    state:               charging
    percentage:          84%
    icon-name:          'phone-symbolic'
"""
DISPLAY = """Device: /org/freedesktop/UPower/devices/DisplayDevice
  power supply:         yes
  battery
    present:             yes
    state:               discharging
    percentage:          57%
"""


# --- UPower: iPhones ----------------------------------------------------------------------


def test_only_phone_blocks_are_read_and_never_their_serial():
    phones = parse_dump(HOST_BATTERY + "\n" + PHONE + "\n" + DISPLAY)

    assert phones == [{"vendor": "Apple", "model": "iPhone", "present": "yes", "state": "charging",
                       "percentage": "84%"}]
    assert "DEADBEEF" not in repr(phones)


def test_this_computers_own_battery_is_never_a_phone():
    assert parse_dump(HOST_BATTERY + "\n" + DISPLAY) == []


@pytest.mark.parametrize(("state", "percentage", "expected"), [
    ("charging", "84%", (84, ChargingState.CHARGING)),
    ("fully-charged", "100%", (100, ChargingState.FULL)),
    ("discharging", "83.6%", (84, ChargingState.DISCHARGING)),
    ("pending-charge", "80%", (80, ChargingState.NOT_CHARGING)),
    ("unknown", "0%", (None, None)),        # a placeholder, not a reading
    ("charging", "n/a", (None, None)),
    ("charging", "140%", (None, None)),
])
def test_a_percentage_counts_only_with_a_determined_state(state, percentage, expected):
    info = read_phone({"vendor": "Apple", "model": "iPhone", "state": state, "percentage": percentage})

    assert (info.battery_percent, info.charging) == expected


def test_a_phone_upower_says_is_not_present_has_no_reading():
    assert read_phone({"present": "no", "state": "charging", "percentage": "50%"}) == DeviceInfo()


class FakeUPower:
    def __init__(self, stdout="", error=None):
        self.stdout, self.error, self.calls = stdout, error, []

    def __call__(self, command, **options):
        assert options["timeout"] > 0
        self.calls.append(command)
        if isinstance(self.error, BaseException):
            raise self.error
        return subprocess.CompletedProcess(command, 1 if self.error else 0, self.stdout, self.error or "")


def upower(fake, installed=True):
    return UPowerPhoneProvider(which=lambda name: "/usr/bin/upower" if installed else None, run=fake)


def test_a_paired_iphone_reports_its_battery_with_one_read_only_command():
    fake = FakeUPower(HOST_BATTERY + PHONE)

    result = upower(fake).query(IPHONE)

    assert result == InfoResult(DeviceInfo("Apple", "iPhone", 84, ChargingState.CHARGING))
    assert fake.calls == [["/usr/bin/upower", "--dump"]] and "DEADBEEF" not in repr(result)


def test_only_devices_offering_apples_multiplexer_are_supported():
    assert upower(FakeUPower()).supports(IPHONE)
    assert not upower(FakeUPower()).supports(DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Pixel", "3-2",
                                                         services=frozenset({"adb"})))


@pytest.mark.parametrize(("fake", "installed", "expected"), [
    (FakeUPower(HOST_BATTERY), True, InfoResult(problem=upower_phone.NOT_LISTED, retry=True)),
    (FakeUPower(PHONE + PHONE), True, InfoResult(problem=upower_phone.AMBIGUOUS)),
    (FakeUPower(PHONE.replace("charging", "unknown")), True,
     InfoResult(problem=upower_phone.NOTHING_READ, retry=True)),
    (FakeUPower(error=subprocess.TimeoutExpired("upower", 5)), True,
     InfoResult(problem=upower_phone.TIMED_OUT, retry=True)),
    (FakeUPower(error="failed to connect"), True, InfoResult(problem=upower_phone.FAILED, retry=True)),
    (FakeUPower(PHONE), False, InfoResult(problem=upower_phone.NOT_INSTALLED)),
])
def test_without_a_trustworthy_reading_nothing_is_claimed(fake, installed, expected):
    assert upower(fake, installed).query(IPHONE) == expected


# --- USB storage: sysfs only ---------------------------------------------------------------

STICK = DeviceEvent(
    DeviceKind.USB, DeviceChange.CONNECTED, "SanDisk Ultra", "/devices/pci0/usb3/3-2",
    category=DeviceCategory.STORAGE,
)


def disk(sysfs, sectors: int, name="sdb", lun="6:0:0:0"):
    size = sysfs / "devices/pci0/usb3/3-2/3-2:1.0/host6/target6:0:0" / lun / "block" / name / "size"
    size.parent.mkdir(parents=True)
    size.write_text(f"{sectors}\n")


def test_a_sticks_capacity_is_read_from_sysfs(tmp_path):
    disk(tmp_path, 125_045_424)  # 64 GB

    result = UsbStorageInfoProvider(sysfs=tmp_path).query(STICK)

    assert result == InfoResult(DeviceInfo(capacity_bytes=125_045_424 * 512))


def test_storage_capacity_is_reported_only_when_it_is_certain(tmp_path):
    storage = UsbStorageInfoProvider(sysfs=tmp_path)
    assert storage.query(STICK) == InfoResult(problem="the storage device has no disk yet", retry=True)

    disk(tmp_path, 0)  # a card reader without a card
    assert storage.query(STICK).problem == "the storage device reports no medium"

    disk(tmp_path, 1000, name="sdc", lun="6:0:0:1")
    disk(tmp_path, 2000, name="sdd", lun="6:0:0:2")
    assert storage.query(STICK).problem == "the storage device has several disks"


def test_only_usb_storage_is_supported(tmp_path):
    storage = UsbStorageInfoProvider(sysfs=tmp_path)

    assert storage.supports(STICK) and not storage.supports(IPHONE)


@pytest.mark.parametrize(("size", "text"), [
    (64_023_257_088, "64 GB"), (31_914_983_424, "32 GB"), (1_000_204_886_016, "1 TB"),
    (1_500_301_910_016, "1.5 TB"), (2_000_000_000, "2 GB"), (512_000_000, "512 MB"),
])
def test_capacity_is_written_as_sold(size, text):
    assert format_capacity(size) == text


# --- What an update says ---------------------------------------------------------------------


@pytest.mark.parametrize(("info", "text"), [
    (DeviceInfo("Google", "Pixel 8", 84, ChargingState.CHARGING), "Pixel 8 — Battery 84% — Charging"),
    (DeviceInfo(battery_percent=84), "iPhone — Battery 84%"),                  # the maker goes, for room
    (DeviceInfo(capacity_bytes=64_000_000_000), "Apple Inc. iPhone — 64 GB"),  # without a battery it stays
    (DeviceInfo(model="Pixel 8"), "Pixel 8"),
    (DeviceInfo(battery_percent=100, charging=ChargingState.FULL), "iPhone — Battery 100% — Full"),
])
def test_an_update_says_only_what_was_read(info, text):
    assert device_info_text(IPHONE, info) == text


def test_a_name_that_does_not_start_with_its_maker_is_kept_whole():
    from dataclasses import replace

    device = replace(IPHONE, name="SanDisk Ultra", manufacturer="Western Digital")

    assert device_info_text(device, DeviceInfo(capacity_bytes=64_000_000_000)) == "SanDisk Ultra — 64 GB"


def test_an_update_is_a_notice_about_the_same_device():
    notice = device_info_notice(DeviceInfoUpdate(IPHONE, DeviceInfo(battery_percent=84), announce=True))

    assert notice == Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "iPhone — Battery 84%",
                            NoticeDevice(DeviceCategory.SMARTPHONE, connected=True, key=IPHONE.key))
