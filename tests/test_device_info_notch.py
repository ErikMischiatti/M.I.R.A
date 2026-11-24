"""Device information from the kernel to the notch, end to end.

The production path with fakes only at the edges: a fake kernel (sysfs and a
socket pair) feeds the real USB source, producer, information service and
providers, whose ``adb`` is scripted (`adb_doubles`) and whose storage sizes
come from the same fake sysfs; the real desktop shell and notch presenter
show the result. A `ManualScheduler` runs every timer and every off-thread
query exactly when the test says.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent

from adb_doubles import PIXEL_LISTED, FakeAdb, listing
from mira.adapters.adb import AdbDeviceInfoProvider
from mira.adapters.usb_storage import UsbStorageInfoProvider
from mira.application.composition import build_application, desktop_device_info_providers
from mira.application.device_config import DeviceMonitorConfiguration
from mira.core.device_info import RETRY_DELAYS_MS
from mira.core.notice_producers import DEVICE_SETTLE_MS, USB_REENUMERATION_MS
from mira.domain.embodiment_cue import EmbodimentCue
from mira.domain.scheduler import ManualScheduler
from mira.ui.desktop_shell import DesktopShell, Surface
from mira.ui.notch.presence import NotchPresence
from usb_doubles import ADB, GOOGLE, MASS_STORAGE, MTP, FakeKernel, make_device, uevent, unplug

PHONE = "/devices/pci0000:00/0000:00:14.0/usb3/3-2"  # adb lists it as usb:3-2


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime(qt_application, tmp_path):
    kernel = FakeKernel(tmp_path / "sys")
    source = kernel.source()
    fake_adb = FakeAdb()
    installed = {"adb": True}
    providers = [
        AdbDeviceInfoProvider(which=lambda name: "/usr/bin/adb" if installed["adb"] else None, run=fake_adb),
        UsbStorageInfoProvider(sysfs=kernel.sysfs),
    ]
    application = build_application(
        scheduler=ManualScheduler(), system_event_sources=[source], device_info_providers=providers,
    )
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    application.start()

    class Runtime:
        pass

    runtime = Runtime()
    runtime.kernel, runtime.application, runtime.desktop = kernel, application, desktop
    runtime.adb, runtime.installed, runtime.scheduler = fake_adb, installed, application.scheduler

    def deliver(*messages):
        kernel.send(*messages)
        source.on_readable()

    runtime.deliver = deliver

    def text():
        notice = desktop.presenter.presentation.notice
        return None if notice is None else notice.text

    def detail():
        notice = desktop.presenter.presentation.notice
        return None if notice is None else notice.detail

    runtime.text, runtime.detail = text, detail
    yield runtime
    desktop.shutdown()
    application.shutdown()
    kernel.close()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def plug_pixel(runtime, interfaces=(MTP, ADB)):
    make_device(runtime.kernel.sysfs, PHONE, product="Pixel 8", manufacturer="Google", vendor=GOOGLE,
                interfaces=list(interfaces), listed=False)
    runtime.deliver(uevent("add", PHONE))
    runtime.scheduler.advance(DEVICE_SETTLE_MS)


def test_a_phone_with_debugging_grows_its_notice_with_model_and_battery(runtime):
    plug_pixel(runtime)
    assert runtime.text() == "Google Pixel 8 connected"
    assert runtime.desktop.controller.active_cue is EmbodimentCue.GREETING
    runtime.desktop.controller.clear_cue()

    runtime.scheduler.run_all()  # the adb query, off the UI thread in production

    assert (runtime.text(), runtime.detail()) == ("Pixel 8", "84% · Charging")
    assert runtime.desktop.notch.presence is NotchPresence.EXPANDED
    assert runtime.desktop.controller.active_cue is None  # greeted once, at the arrival
    runtime.scheduler.advance(runtime.desktop.presenter.behaviour.notice_duration_ms)
    assert runtime.text() is None


def test_nothing_is_asked_before_the_arrival_is_announced(runtime):
    make_device(runtime.kernel.sysfs, PHONE, product="Pixel 8", manufacturer="Google", vendor=GOOGLE,
                interfaces=[MTP, ADB], listed=False)
    runtime.deliver(uevent("add", PHONE))

    assert runtime.scheduler.pending_work() == 0
    runtime.deliver(uevent("remove", PHONE))  # a bounce
    runtime.scheduler.advance(10_000)
    assert runtime.adb.calls == []


def test_a_phone_without_debugging_runs_no_command(runtime):
    plug_pixel(runtime, interfaces=(MTP,))
    runtime.scheduler.run_all()

    assert runtime.adb.calls == [] and runtime.text() == "Google Pixel 8 connected"


def test_without_adb_the_notice_says_only_what_the_connection_said(runtime):
    runtime.installed["adb"] = False

    plug_pixel(runtime)
    runtime.scheduler.run_all()

    assert runtime.text() == "Google Pixel 8 connected" and runtime.adb.calls == []


def test_a_phone_unplugged_during_the_query_gets_no_late_information(runtime):
    plug_pixel(runtime)

    runtime.deliver(uevent("remove", PHONE))
    runtime.scheduler.run_all()  # the query answers after the phone left
    runtime.scheduler.advance(USB_REENUMERATION_MS)

    assert runtime.text() == "Google Pixel 8 disconnected"
    assert runtime.application.device_info.tracked() == {}


def test_a_phone_authorized_after_a_moment_is_announced_then(runtime):
    runtime.adb.devices = listing(PIXEL_LISTED.replace(" device ", " unauthorized "))
    plug_pixel(runtime)
    runtime.scheduler.run_all()
    runtime.scheduler.advance(runtime.desktop.presenter.behaviour.notice_duration_ms)
    assert runtime.text() is None

    runtime.adb.devices = listing(PIXEL_LISTED)  # the user tapped "Allow"
    runtime.scheduler.advance(RETRY_DELAYS_MS[0])
    runtime.scheduler.run_all()

    assert (runtime.text(), runtime.detail()) == ("Pixel 8", "84% · Charging")
    assert runtime.desktop.notch.presence is NotchPresence.EXPANDED


def test_a_usb_sticks_capacity_joins_its_notice(runtime):
    device = make_device(runtime.kernel.sysfs, PHONE, product="Ultra", manufacturer="SanDisk", vendor="0781",
                         interfaces=[MASS_STORAGE], listed=False)
    runtime.deliver(uevent("add", PHONE))
    runtime.scheduler.advance(DEVICE_SETTLE_MS)
    size = device / "3-2:1.0/host6/target6:0:0/6:0:0:0/block/sdb/size"
    size.parent.mkdir(parents=True)
    size.write_text("125045424\n")

    runtime.scheduler.run_all()

    assert (runtime.text(), runtime.detail()) == ("SanDisk Ultra", "64 GB")


def test_closing_mira_during_a_query_shows_nothing_more(runtime):
    plug_pixel(runtime)

    runtime.desktop.shutdown()
    runtime.application.shutdown()

    assert runtime.scheduler.pending_work() == 0 and runtime.adb.calls == []


def test_repeated_connections_leave_nothing_tracked(runtime):
    for _ in range(5):
        plug_pixel(runtime)
        runtime.scheduler.run_all()
        runtime.deliver(uevent("remove", PHONE))
        runtime.scheduler.advance(USB_REENUMERATION_MS)
        unplug(runtime.kernel.sysfs, PHONE)

    assert runtime.application.device_info.tracked() == {}
    assert len(runtime.adb.calls) == 10  # two read-only commands per connection, no more


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop providers")
def test_device_information_can_be_turned_off():
    assert desktop_device_info_providers(DeviceMonitorConfiguration(info=False)) == []
    assert [type(provider).__name__ for provider in desktop_device_info_providers(DeviceMonitorConfiguration())] == [
        "AdbDeviceInfoProvider", "UPowerPhoneProvider", "UsbStorageInfoProvider",
    ]
