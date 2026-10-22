"""The unified notch: device notices, notice priority and the countdown timer together.

The integration's own tests: every rule of one feature checked while the
other features are active. The timer's panel is a transient band owner and
its finished countdown awaiting OK a persistent one; conversations protect
their turn; device information refines its notice; critical notices stay
visible without losing the timer. Deterministic: `ManualScheduler`, a fake
clock for the countdown, a fake kernel for USB.

Scenario numbers refer to the M5 integration plan.
"""

from __future__ import annotations

import os
import random
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mira.domain.device_info import ChargingState, DeviceInfo, DeviceInfoUpdate, device_info_notice
from mira.domain.devices import DeviceCategory
from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticePriority, NoticeTone, notice_priority
from mira.domain.scheduler import ManualScheduler
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, device_notice
from mira.domain.timer import TimerSnapshot, TimerState
from mira.ui.notch.presentation import (
    MAX_DEFERRED_NOTICES,
    MAX_NOTICE_AGE_MS,
    ExpansionReason,
    NotchAction,
    NotchBehaviour,
    NotchPresenter,
    NoticeOutcome,
    TimerButton,
    TimerCommand,
    timer_content,
)

DURATION = 4000
PHONE_EVENT = DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Google Pixel 8", "/d/usb3/3-2",
                          category=DeviceCategory.SMARTPHONE, manufacturer="Google")
STICK_EVENT = DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "SanDisk Ultra", "/d/usb3/3-4",
                          category=DeviceCategory.STORAGE, manufacturer="SanDisk")
PHONE = device_notice(PHONE_EVENT)
PHONE_LEFT = device_notice(replace(PHONE_EVENT, change=DeviceChange.DISCONNECTED))
STICK = device_notice(STICK_EVENT)
ERROR = Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed")
ONLINE = Notice(NoticeCategory.CONNECTIVITY, NoticeTone.SUCCESS, "Back online")
DONE = Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)
RUNNING = TimerSnapshot(TimerState.RUNNING, 300, 120)
FINISHED = TimerSnapshot(TimerState.FINISHED, 300, 0)
IDLE = TimerSnapshot()


def battery(percent=84, charging=ChargingState.CHARGING, announce=True) -> Notice:
    return device_info_notice(DeviceInfoUpdate(PHONE_EVENT, DeviceInfo("Google", "Pixel 8", percent, charging),
                                               announce))


@pytest.fixture
def notch():
    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler, NotchBehaviour(notice_duration_ms=DURATION),
                               actions=frozenset(NotchAction))
    reactions = []
    presenter.subscribe_reactions(reactions.append)
    presenter.show()
    presenter.scheduler, presenter.reactions = scheduler, reactions
    return presenter


def showing(notch):
    return notch.presentation.notice


# --- Phase H scenarios ------------------------------------------------------------------------


def test_1_a_device_connecting_while_the_timer_runs_shows_then_the_countdown_returns(notch):
    notch.set_timer(RUNNING)

    assert notch.present(PHONE) is NoticeOutcome.SHOWN
    notch.scheduler.advance(DURATION)

    assert showing(notch) is None and notch.presentation.content == timer_content(RUNNING)


def test_2_the_timer_finishing_while_listening_takes_the_band_and_holds_notices(notch):
    notch.set_listening(True, True)
    notch.set_timer(FINISHED)

    shown = notch.presentation
    assert shown.timer_panel and not shown.shows_waveform  # "Timer done!" is never hidden
    assert notch.present(PHONE) is NoticeOutcome.DEFERRED
    notch.set_listening(False)
    assert showing(notch) is None  # still awaiting OK
    notch.set_timer(IDLE)
    assert showing(notch) == PHONE and notch.reactions == [PHONE]


def test_3_a_device_connecting_under_the_done_panel_waits_for_ok_and_is_greeted_once(notch):
    notch.set_timer(FINISHED)

    notch.present(PHONE)
    assert showing(notch) is None and notch.presentation.timer_panel and notch.reactions == []
    notch.set_timer(IDLE)

    assert showing(notch) == PHONE and notch.reactions == [PHONE]


def test_4_and_5_an_error_suspends_the_done_panel_which_returns_with_the_timer_intact(notch):
    notch.set_timer(FINISHED)

    assert notch.present(ERROR) is NoticeOutcome.SHOWN
    during = notch.presentation
    assert during.notice == ERROR and during.timer_panel == ()
    assert ExpansionReason.TIMER_DONE in during.reasons and during.timer.state is TimerState.FINISHED

    notch.scheduler.advance(DURATION)

    after = notch.presentation
    assert after.notice is None and after.timer_panel and after.timer.state is TimerState.FINISHED


def test_6_a_device_leaving_while_its_arrival_waits_leaves_nothing_to_show(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)

    assert notch.present(PHONE_LEFT) is NoticeOutcome.DROPPED
    notch.set_timer(IDLE)

    assert showing(notch) is None and notch.deferred == () and notch.reactions == []


def test_7_several_devices_during_a_conversation_follow_it_in_turn(notch):
    notch.set_activity(ActivityState.LISTENING)
    notch.present(PHONE)
    notch.present(STICK)
    notch.set_activity(ActivityState.THINKING)
    notch.set_activity(ActivityState.SPEAKING)
    assert showing(notch) is None

    notch.set_activity(ActivityState.IDLE)
    first = showing(notch)
    notch.scheduler.advance(DURATION)

    assert [first, showing(notch)] == [PHONE, STICK] and notch.reactions == [PHONE, STICK]


def test_8_battery_information_under_the_done_panel_joins_the_waiting_notice(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)

    assert notch.refine(battery(), announce=True) is NoticeOutcome.UPDATED
    assert showing(notch) is None
    notch.set_timer(IDLE)

    assert (showing(notch).text, showing(notch).detail) == ("Pixel 8", "84% · Charging")
    assert len(notch.reactions) == 1  # greeted once, for the arrival


def test_9_controls_opened_with_notices_pending_keep_them_pending(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)
    notch.open_controls()  # over the done panel

    assert showing(notch) is None and notch.deferred == (PHONE,)
    notch.close_controls()
    assert showing(notch) is None and notch.presentation.timer_panel  # still awaiting OK
    notch.set_timer(IDLE)
    assert showing(notch) == PHONE


def test_11_a_long_voice_session_keeps_its_deferred_notices(notch):
    # As the runtime does it: listening starts and InteractionManager attends;
    # half a minute of speech; listening ends and the activity settles.
    notch.set_timer(RUNNING)
    notch.set_listening(True, True)
    notch.set_activity(ActivityState.LISTENING)
    notch.present(PHONE)
    notch.present(ONLINE)
    notch.scheduler.advance(30_000)

    notch.set_listening(False)
    notch.set_activity(ActivityState.IDLE)

    seen = [showing(notch)]
    notch.scheduler.advance(DURATION)
    seen.append(showing(notch))
    assert seen == [PHONE, ONLINE] and notch.reactions == [PHONE, ONLINE]


def test_11_a_long_typed_turn_holds_notices_through_thinking_and_speaking(notch):
    # A typed turn: the focused input attends, Brain starts processing in the
    # same call (LISTENING straight to THINKING), a slow answer, then speech.
    seen = []
    notch.subscribe(lambda presentation: seen.append(presentation.notice))
    notch.set_activity(ActivityState.LISTENING)
    notch.present(PHONE)
    notch.set_activity(ActivityState.THINKING)
    notch.scheduler.advance(10_000)
    notch.set_activity(ActivityState.SPEAKING)
    notch.scheduler.advance(2_200)

    notch.set_activity(ActivityState.IDLE)

    assert all(notice is None for notice in seen[:-1]) and seen[-1] == PHONE  # no flash mid-turn
    assert notch.reactions == [PHONE]


def test_12_rapid_timer_and_notice_transitions_keep_every_rule():
    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler, NotchBehaviour(notice_duration_ms=DURATION),
                               actions=frozenset(NotchAction))
    reactions = []
    presenter.subscribe_reactions(reactions.append)
    presenter.show()
    rng = random.Random(5)
    phone = {"connected": False}

    def phone_arrives():
        phone["connected"] = True
        presenter.present(PHONE)

    def phone_leaves():
        phone["connected"] = False
        presenter.present(PHONE_LEFT)

    def battery_read():
        # As the information service does: only about a device still connected.
        if phone["connected"]:
            presenter.refine(battery(rng.randint(1, 100)), announce=rng.random() < 0.5)

    events = [
        lambda: presenter.present(rng.choice((STICK, ERROR, ONLINE, DONE))),
        phone_arrives,
        phone_leaves,
        battery_read,
        lambda: presenter.set_timer(rng.choice((IDLE, RUNNING, FINISHED))),
        lambda: presenter.open_timer_panel(),
        lambda: presenter.close_timer_panel(),
        lambda: presenter.toggle_controls(),
        lambda: presenter.set_listening(rng.random() < 0.4, True),
        lambda: presenter.set_activity(rng.choice(list(ActivityState))),
        lambda: scheduler.advance(rng.choice((0, 300, DURATION, 15_000, MAX_NOTICE_AGE_MS))),
    ]
    for _ in range(5000):
        rng.choice(events)()
        shown = presenter.presentation
        notice = shown.notice
        assert len(presenter.deferred) <= MAX_DEFERRED_NOTICES
        if shown.controls or shown.timer_panel:
            assert notice is None  # nothing drawn, timed or reacted to under a panel
        if notice is not None and notice_priority(notice) is NoticePriority.CRITICAL:
            assert not shown.controls and not shown.timer_panel  # an error is never covered
        if shown.timer.state is TimerState.FINISHED and notice is None:
            assert shown.timer_panel  # "Timer done!" is suspended at most, never lost
        if not phone["connected"]:
            # Nothing that says the phone is here, once it has left.
            stale = [n for n in (notice, *presenter.deferred)
                     if n is not None and n.device is not None and n.device.key == PHONE.device.key
                     and n.device.connected]
            assert stale == []
    presenter.shutdown()
    assert scheduler.pending_timers() == 0


# --- The band rules with the timer ----------------------------------------------------------


def test_an_error_closes_the_timer_panel_and_keeps_the_chosen_duration(notch):
    notch.open_timer_panel()
    notch.choose_timer(TimerButton(TimerCommand.LONGER))
    chosen = notch.timer_selection_s

    assert notch.present(ERROR) is NoticeOutcome.SHOWN
    assert notch.presentation.timer_panel == () and notch.timer_selection_s == chosen


def test_opening_the_timer_panel_pushes_a_notice_aside_until_it_closes(notch):
    notch.present(ERROR)

    notch.open_timer_panel()
    assert showing(notch) is None and notch.deferred == (ERROR,)
    notch.close_timer_panel()

    assert showing(notch) == ERROR and notch.reactions == [ERROR]


def test_a_showing_notice_steps_aside_when_the_timer_finishes(notch):
    notch.set_timer(RUNNING)
    notch.present(PHONE)

    notch.set_timer(FINISHED)
    assert showing(notch) is None and notch.deferred == (PHONE,)
    notch.set_timer(IDLE)

    assert showing(notch) == PHONE and notch.reactions == [PHONE]


def test_under_the_done_panel_waiting_is_not_eligible_time_but_age_still_bounds_it(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)

    notch.scheduler.advance(30_000)
    assert notch.deferred == (PHONE,)
    notch.scheduler.advance(MAX_NOTICE_AGE_MS - 30_000)
    notch.set_timer(IDLE)

    assert showing(notch) is None and notch.deferred == ()


# --- The real runtime: scenarios 3, 8 and 10 end to end ---------------------------------------

PHONE_PATH = "/devices/pci0000:00/0000:00:14.0/usb3/3-2"


@pytest.fixture
def runtime(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from adb_doubles import FakeAdb
    from mira.adapters.adb import AdbDeviceInfoProvider
    from mira.application.composition import build_application
    from mira.ui.desktop_shell import DesktopShell, Surface
    from usb_doubles import FakeKernel

    kernel = FakeKernel(tmp_path / "sys")
    source = kernel.source()
    clock = [1000.0]
    scheduler = ManualScheduler()
    adb = FakeAdb()
    application = build_application(
        scheduler=scheduler, clock=lambda: clock[0], system_event_sources=[source],
        device_info_providers=[AdbDeviceInfoProvider(which=lambda name: "/usr/bin/adb", run=adb)],
    )
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    application.start()

    def advance(seconds):
        clock[0] += seconds
        scheduler.advance(int(seconds * 1000))

    def plug():
        from usb_doubles import ADB, GOOGLE, MTP, make_device, uevent

        make_device(kernel.sysfs, PHONE_PATH, product="Pixel 8", manufacturer="Google", vendor=GOOGLE,
                    interfaces=[MTP, ADB], listed=False)
        kernel.send(uevent("add", PHONE_PATH))
        source.on_readable()

    env = SimpleNamespace(application=application, desktop=desktop, advance=advance, plug=plug,
                          scheduler=scheduler, source=source, kernel=kernel)
    yield env
    desktop.shutdown()
    application.shutdown()
    kernel.close()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_runtime_a_phone_and_its_battery_during_timer_done_appear_after_ok_greeted_once(runtime):
    from mira.core.notice_producers import DEVICE_SETTLE_MS
    from mira.domain.embodiment_cue import EmbodimentCondition, EmbodimentCue

    runtime.application.timer.start(60)
    runtime.advance(60)
    assert EmbodimentCondition.TIMER_FINISHED in runtime.desktop.controller.conditions

    runtime.plug()
    runtime.advance(DEVICE_SETTLE_MS / 1000)
    runtime.scheduler.run_all()  # the adb query answers while "Timer done!" shows
    presented = runtime.desktop.presenter.presentation
    assert presented.notice is None and presented.timer_panel
    assert runtime.desktop.controller.active_cue is None  # no greeting over the timer's eyes

    runtime.application.timer.acknowledge()

    notice = runtime.desktop.presenter.presentation.notice
    assert (notice.text, notice.detail) == ("Pixel 8", "84% · Charging")
    assert runtime.desktop.controller.active_cue is EmbodimentCue.GREETING


def test_runtime_shutdown_with_the_timer_running_and_the_device_monitor_active(runtime):
    from mira.core.notice_producers import DEVICE_SETTLE_MS

    runtime.application.timer.start(300)
    runtime.application.event_bus.emit("listening_changed", SimpleNamespace(listening=True, capturing=True))
    runtime.plug()
    runtime.advance(DEVICE_SETTLE_MS / 1000)  # announced, waiting behind the conversation; query pending
    socket = runtime.source._socket

    runtime.desktop.shutdown()
    runtime.application.shutdown()

    assert runtime.scheduler.pending_timers() == 0 and runtime.scheduler.pending_work() == 0
    assert runtime.desktop.presenter.deferred == ()
    assert socket.fileno() == -1 and not runtime.source.watching
    assert runtime.application.device_info.tracked() == {}
