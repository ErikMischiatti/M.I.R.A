"""A USB device arriving reaches the notch: configuration, symbols, the whole path.

The path under test is the production one end to end — a fake kernel (a
sysfs tree and a socket pair, `usb_doubles`) feeds the real
`LinuxUsbDeviceSource`, whose events go through the real producer and bus to
the real `DesktopShell`, its `NotchPresenter` and the shared eyes. Timers run
on a `ManualScheduler`; no USB device, display or event loop is needed.
"""

from __future__ import annotations

import pytest

from mira.application.device_config import DeviceMonitorConfiguration, parse_device_monitor_configuration
from mira.domain.devices import DeviceCategory
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent

from mira.application.composition import build_application
from mira.core.notice_producers import DEVICE_SETTLE_MS
from mira.domain.embodiment_cue import EmbodimentCue
from mira.domain.scheduler import ManualScheduler
from mira.ui.desktop_shell import DesktopShell, Surface
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import NotchContent, NotchSymbol, notice_content
from usb_doubles import ADB, GOOGLE, HID, MASS_STORAGE, MTP, FakeKernel, configure, make_device, uevent


# --- Configuration ---------------------------------------------------------------------


def test_usb_watching_is_on_and_quiet_by_default():
    assert parse_device_monitor_configuration() == DeviceMonitorConfiguration(enabled=True, debug=False)


@pytest.mark.parametrize("value", ["0", "false", "No", " OFF "])
def test_usb_watching_is_turned_off_explicitly(value):
    assert parse_device_monitor_configuration(enabled=value).enabled is False


@pytest.mark.parametrize("value", ["1", "yes", "", "maybe"])
def test_anything_else_leaves_usb_watching_on(value):
    assert parse_device_monitor_configuration(enabled=value).enabled is True


def test_debug_logging_is_opt_in():
    assert parse_device_monitor_configuration(debug="1").debug is True
    assert parse_device_monitor_configuration(debug="maybe").debug is False


# --- What the notch draws -----------------------------------------------------------


def device_notice_about(category: DeviceCategory, connected: bool = True, text: str = "Thing connected") -> Notice:
    return Notice(NoticeCategory.DEVICE, NoticeTone.INFO, text, NoticeDevice(category, connected))


@pytest.mark.parametrize(("category", "symbol"), [
    (DeviceCategory.SMARTPHONE, NotchSymbol.PHONE),
    (DeviceCategory.STORAGE, NotchSymbol.STORAGE),
    (DeviceCategory.CAMERA, NotchSymbol.CAMERA),
    (DeviceCategory.AUDIO, NotchSymbol.HEADPHONES),
    (DeviceCategory.INPUT, NotchSymbol.KEYBOARD),
    (DeviceCategory.UNKNOWN, NotchSymbol.USB),
])
def test_a_device_notice_shows_its_kind_of_device(category, symbol):
    assert notice_content(device_notice_about(category)) == NotchContent(symbol, "Thing connected")


def test_a_device_leaving_shows_the_same_kind():
    content = notice_content(device_notice_about(DeviceCategory.SMARTPHONE, False, "Pixel 8 disconnected"))

    assert content == NotchContent(NotchSymbol.PHONE, "Pixel 8 disconnected")


def test_an_unclassified_device_notice_keeps_the_generic_device_symbol():
    assert notice_content(Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Output: Speaker")).symbol \
        is NotchSymbol.DEVICE


def test_a_tone_still_says_more_than_the_kind_of_device():
    warning = Notice(NoticeCategory.DEVICE, NoticeTone.WARNING, "Overcurrent",
                     NoticeDevice(DeviceCategory.STORAGE, connected=True))

    assert notice_content(warning).symbol is NotchSymbol.WARNING


# --- From the kernel to the notch ---------------------------------------------------------

PHONE = "/devices/pci0000:00/0000:00:14.0/usb3/3-2"


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime(qt_application, tmp_path):
    """The production graph and notch shell, watching a fake kernel."""
    kernel = FakeKernel(tmp_path / "sys")
    source = kernel.source()
    application = build_application(scheduler=ManualScheduler(), system_event_sources=[source])
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    application.start()
    runtime = type("Runtime", (), {})()
    runtime.kernel, runtime.source, runtime.application, runtime.desktop = kernel, source, application, desktop

    def deliver(*messages):
        kernel.send(*messages)
        source.on_readable()

    runtime.deliver = deliver
    runtime.settle = lambda: application.scheduler.advance(DEVICE_SETTLE_MS)
    yield runtime
    desktop.shutdown()
    application.shutdown()
    kernel.close()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def presentation(runtime):
    return runtime.desktop.presenter.presentation


def test_a_phone_plugged_in_expands_the_notch_with_its_name_and_the_eyes_greet_it(runtime):
    device = make_device(runtime.kernel.sysfs, PHONE, product="Pixel 8", manufacturer="Google", vendor=GOOGLE,
                         configured=False, listed=False)
    runtime.deliver(uevent("add", PHONE))
    configure(device, [MTP, ADB])
    runtime.deliver(uevent("bind", PHONE))
    assert presentation(runtime).notice is None  # still settling

    runtime.settle()

    shown = presentation(runtime)
    assert shown.content == NotchContent(NotchSymbol.PHONE, "Google Pixel 8 connected")
    assert shown.notice.device == NoticeDevice(DeviceCategory.SMARTPHONE, connected=True)
    assert runtime.desktop.notch.presence is NotchPresence.EXPANDED
    assert runtime.desktop.controller.active_cue is EmbodimentCue.GREETING


def test_the_notch_returns_to_how_it_was_after_the_notice_duration(runtime):
    make_device(runtime.kernel.sysfs, PHONE, product="Stick", interfaces=[MASS_STORAGE], listed=False)
    before = runtime.desktop.notch.presence
    runtime.deliver(uevent("add", PHONE))
    runtime.settle()
    assert runtime.desktop.notch.presence is NotchPresence.EXPANDED

    runtime.application.scheduler.advance(runtime.desktop.presenter.behaviour.notice_duration_ms)

    assert presentation(runtime).notice is None
    assert runtime.desktop.notch.presence is before


def test_a_device_unplugged_while_its_arrival_shows_replaces_it_without_a_greeting(runtime):
    make_device(runtime.kernel.sysfs, PHONE, product="Keyboard", interfaces=[HID], listed=False)
    runtime.deliver(uevent("add", PHONE))
    runtime.settle()
    runtime.desktop.controller.clear_cue()

    runtime.deliver(uevent("remove", PHONE))
    runtime.settle()

    assert presentation(runtime).content == NotchContent(NotchSymbol.KEYBOARD, "Keyboard disconnected")
    assert runtime.desktop.controller.active_cue is None


def test_a_quick_plug_and_unplug_shows_nothing(runtime):
    make_device(runtime.kernel.sysfs, PHONE, product="Flaky", interfaces=[HID], listed=False)

    runtime.deliver(uevent("add", PHONE), uevent("remove", PHONE))
    runtime.settle()

    assert presentation(runtime).notice is None
    assert runtime.desktop.controller.active_cue is None


def test_a_composite_device_shows_one_notice(runtime):
    notices = []
    runtime.application.event_bus.subscribe("notice", notices.append)
    device = make_device(runtime.kernel.sysfs, PHONE, product="Pixel 8", configured=False, listed=False)

    runtime.deliver(uevent("add", PHONE))
    configure(device, [MTP, ADB])
    runtime.deliver(
        uevent("add", f"{PHONE}/3-2:1.0", DEVTYPE="usb_interface"),
        uevent("add", f"{PHONE}/3-2:1.1", DEVTYPE="usb_interface"),
        uevent("bind", f"{PHONE}/3-2:1.0", DEVTYPE="usb_interface"),
        uevent("bind", PHONE),
        uevent("bind", PHONE),
    )
    runtime.settle()
    runtime.application.scheduler.advance(10_000)

    assert [notice.text for notice in notices] == ["Pixel 8 connected"]


def test_shutdown_stops_watching_the_kernel(runtime):
    assert runtime.source.watching

    runtime.application.shutdown()

    assert not runtime.source.watching
