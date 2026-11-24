"""Notice priority on the real notch: geometry, the runtime and MIRA-TUNE.

Offscreen and deterministic: timers on a `ManualScheduler`, the window's one
transition seeked to explicit times, a fake kernel for USB (`usb_doubles`).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent

from mira.application.composition import build_application
from mira.core.notice_producers import DEVICE_SETTLE_MS
from mira.domain.devices import DeviceCategory
from mira.domain.embodiment import ActivityState
from mira.domain.embodiment_cue import EmbodimentCue
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.desktop_shell import DesktopShell, Surface
from mira.ui.notch.__main__ import build_tuning_environment
from mira.ui.notch.debug_window import DEVICE_SCENARIOS
from mira.ui.notch.presence import NotchPresence
from usb_doubles import GOOGLE, MTP, FakeKernel, make_device, uevent


@pytest.fixture(scope="module", autouse=True)
def application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def finish(notch) -> None:
    transition = notch.transition
    if transition.state() is QAbstractAnimation.State.Running:
        transition.pause()
    transition.setCurrentTime(transition.duration())


def seek(notch, ms: int) -> None:
    transition = notch.transition
    if transition.state() is QAbstractAnimation.State.Running:
        transition.pause()
    transition.setCurrentTime(ms)


def notice(text, detail=None, key="k", category=DeviceCategory.SMARTPHONE):
    return Notice(NoticeCategory.DEVICE, NoticeTone.INFO, text, NoticeDevice(category, True, key), detail=detail)


@pytest.fixture
def shell():
    application = build_application(scheduler=ManualScheduler())
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    finish(desktop.notch)
    yield desktop
    desktop.shutdown()
    application.shutdown()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)



# --- 12–13: interrupted expansion and geometry -----------------------------------------------


def test_a_notice_replaced_mid_expansion_settles_on_the_expanded_geometry(shell):
    notch, presenter = shell.notch, shell.presenter
    presenter.present(notice("Pixel 8 connected", key="p"))
    seek(notch, notch.transition.duration() // 3)

    presenter.present(Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Pixel 8 disconnected",
                             NoticeDevice(DeviceCategory.SMARTPHONE, False, "p")))
    finish(notch)

    expanded = notch.notch_geometry.expanded
    assert notch.presence is NotchPresence.EXPANDED
    assert (notch.shape.bounds.width, notch.shape.bounds.height) == (expanded.width, expanded.height)


def test_a_notice_ending_mid_expansion_collapses_back_to_the_exact_compact_shape(shell):
    notch, presenter = shell.notch, shell.presenter
    compact = notch.shape
    presenter.present(notice("Pixel 8 connected"))
    seek(notch, notch.transition.duration() // 2)

    presenter.dismiss_notice()
    finish(notch)

    assert notch.presence is NotchPresence.COMPACT and notch.shape == compact


def test_a_notice_ending_while_listening_starts_no_transition(shell):
    notch, presenter = shell.notch, shell.presenter
    presenter.set_listening(True, True)
    finish(notch)
    presenter.present(Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed"))
    finish(notch)

    presenter.dismiss_notice()

    assert notch.presence is NotchPresence.EXPANDED
    assert notch.transition.state() is QAbstractAnimation.State.Stopped
    assert presenter.presentation.shows_waveform


# --- 2, 6, 14, 15: the running application ---------------------------------------------------

PHONE = "/devices/pci0000:00/0000:00:14.0/usb3/3-2"


def listening(application, on: bool) -> None:
    application.event_bus.emit("listening_changed", SimpleNamespace(listening=on, capturing=on))


def test_a_usb_phone_plugged_in_while_mira_listens_waits_then_greets(tmp_path):
    kernel = FakeKernel(tmp_path / "sys")
    source = kernel.source()
    application = build_application(scheduler=ManualScheduler(), system_event_sources=[source])
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    application.start()
    try:
        listening(application, True)
        make_device(kernel.sysfs, PHONE, product="Pixel 8", manufacturer="Google", vendor=GOOGLE,
                    interfaces=[MTP], listed=False)
        kernel.send(uevent("add", PHONE))
        source.on_readable()
        application.scheduler.advance(DEVICE_SETTLE_MS)

        assert desktop.presenter.presentation.shows_waveform
        assert desktop.presenter.presentation.notice is None
        assert desktop.controller.active_cue is None

        listening(application, False)

        assert desktop.presenter.presentation.notice.text == "Google Pixel 8 connected"
        assert desktop.notch.presence is NotchPresence.EXPANDED
        assert desktop.controller.active_cue is EmbodimentCue.GREETING
    finally:
        desktop.shutdown()
        application.shutdown()
        kernel.close()
        for obj in (desktop.notch, desktop.window, desktop.animator):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_closing_mira_with_notices_waiting_and_showing_leaves_no_timer(tmp_path):
    application = build_application(scheduler=ManualScheduler())
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    application.event_bus.emit("notice", notice("Pixel 8 connected", key="a"))
    application.event_bus.emit("notice", notice("SanDisk connected", key="b"))
    listening(application, True)
    scheduler = application.scheduler

    desktop.shutdown()
    application.shutdown()

    assert scheduler.pending_timers() == 0 and desktop.presenter.deferred == ()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_repeated_startup_and_teardown_with_notices_in_flight():
    for round_ in range(10):
        application = build_application(scheduler=ManualScheduler())
        desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
        desktop.start()
        application.event_bus.emit("notice", notice(f"Device {round_}", key=str(round_)))
        listening(application, round_ % 2 == 0)
        application.event_bus.emit("notice", notice("Other", key="other"))
        desktop.shutdown()
        application.shutdown()
        assert application.scheduler.pending_timers() == 0
        for obj in (desktop.notch, desktop.window, desktop.animator):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- MIRA-TUNE: the scenarios behave as their names promise ----------------------------------


@pytest.fixture
def tune(tmp_path):
    from mira.ui.notch.config_store import ConfigSession

    environment = build_tuning_environment(ConfigSession(tmp_path / "notch.json"), scheduler=ManualScheduler())
    finish(environment.notch)
    yield environment
    environment.shutdown()
    environment.notch.transition.stop()
    environment.notch.hide()
    for widget in (environment.debug, environment.notch, environment.animator):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def play(tune, name):
    tune.debug.preview_buttons[f"scenario {name}"].click()


def shown(tune):
    notice_ = tune.presenter.presentation.notice
    return None if notice_ is None else (notice_.text, notice_.detail)


def test_every_scenario_is_offered(tune):
    for name in DEVICE_SCENARIOS:
        assert tune.debug.preview_buttons[f"scenario {name}"].isEnabled()


def test_scenario_connect_then_info_grows_one_notice_and_greets_once(tune):
    play(tune, "Connect, then info")
    tune.scheduler.advance(0)
    assert shown(tune) == ("Google Pixel 8 connected", None)
    assert tune.controller.active_cue is EmbodimentCue.GREETING
    tune.controller.clear_cue()

    tune.scheduler.advance(1200)

    assert shown(tune) == ("Pixel 8", "84% · Charging") and tune.controller.active_cue is None


def test_scenario_device_while_listening_waits_for_the_conversation(tune):
    play(tune, "Device while listening")
    tune.scheduler.advance(800 + 1200)
    assert shown(tune) is None and tune.presenter.presentation.activity is ActivityState.LISTENING

    tune.scheduler.advance(2500)

    assert shown(tune) == ("Pixel 8", "84% · Charging")
    assert tune.controller.active_cue is EmbodimentCue.GREETING


def test_scenario_two_devices_shows_both_in_turn(tune):
    play(tune, "Two devices")
    tune.scheduler.advance(300)
    assert shown(tune) == ("Google Pixel 8 connected", None)

    tune.scheduler.advance(tune.presenter.behaviour.notice_duration_ms)

    assert shown(tune) == ("SanDisk Ultra connected", None)


def test_scenario_battery_after_expiry_reopens_once_without_a_greeting(tune):
    play(tune, "Battery after expiry")
    tune.scheduler.advance(tune.presenter.behaviour.notice_duration_ms)
    assert shown(tune) is None
    tune.controller.clear_cue()

    tune.scheduler.advance(5000 - tune.presenter.behaviour.notice_duration_ms)

    assert shown(tune) == ("Pixel 8", "84% · Charging") and tune.controller.active_cue is None


def test_scenario_connect_then_unplug_replaces_the_arrival(tune):
    play(tune, "Connect, then unplug")
    tune.scheduler.advance(1500)

    assert shown(tune) == ("Google Pixel 8 disconnected", None)


def test_scenario_long_name_keeps_the_reading_whole(tune):
    play(tune, "Long name")
    tune.scheduler.advance(1000)

    layout = tune.notch.content_layout(tune.presenter.presentation.content)
    assert layout.detail == "57% · Discharging" and layout.label.endswith("…")


def test_replaying_a_scenario_cancels_the_one_still_running(tune):
    play(tune, "Battery after expiry")
    play(tune, "Connect, then unplug")
    tune.scheduler.advance(10_000)

    assert tune.scheduler.pending_timers() == 0 and shown(tune) is None
