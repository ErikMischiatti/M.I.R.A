"""Notice priority together with the notch timer: the integration seam.

The timer is not a notice: its panel and its finished countdown awaiting OK
take the content band. These tests pin that both are band owners for the
presenter's floor — notices wait under them instead of expiring unseen, the
eyes do not greet over the timer's held eyes, and everything comes back
once the user has answered. Deterministic: `ManualScheduler`, a fake clock.
"""

from __future__ import annotations

import os
import random
from dataclasses import replace

import pytest

from mira.domain.devices import DeviceCategory
from mira.domain.embodiment import ActivityState
from mira.domain.embodiment_cue import EmbodimentCondition, EmbodimentCue
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.domain.timer import TimerSnapshot, TimerState
from mira.ui.notch.presentation import (
    DEFERRED_NOTICE_TTL_MS,
    MAX_DEFERRED_NOTICES,
    NotchAction,
    NotchBehaviour,
    NotchPresenter,
    NoticeOutcome,
)

DURATION = 4000
PHONE = Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Pixel 8 connected",
               NoticeDevice(DeviceCategory.SMARTPHONE, True, "/d/usb3/3-2"))
ERROR = Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed")
RUNNING = TimerSnapshot(TimerState.RUNNING, 300, 120)
FINISHED = TimerSnapshot(TimerState.FINISHED, 300, 0)
IDLE = TimerSnapshot()


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


def test_while_the_timer_panel_is_open_notices_wait_and_return_when_it_closes(notch):
    notch.open_timer_panel()

    assert notch.present(PHONE) is NoticeOutcome.DEFERRED
    assert notch.presentation.notice is None and notch.reactions == []

    notch.close_timer_panel()

    assert notch.presentation.notice == PHONE and notch.reactions == [PHONE]


def test_a_finished_timer_awaiting_ok_holds_device_notices_back_until_answered(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)
    assert notch.presentation.notice is None and notch.presentation.timer_panel
    assert notch.reactions == []  # no greeting over the timer's held eyes

    notch.set_timer(IDLE)  # the user pressed OK

    assert notch.presentation.notice == PHONE and notch.reactions == [PHONE]


def test_a_showing_notice_steps_aside_when_the_timer_finishes(notch):
    notch.set_timer(RUNNING)
    notch.present(PHONE)
    notch.scheduler.advance(1000)

    notch.set_timer(FINISHED)
    assert notch.presentation.notice is None and notch.deferred == (PHONE,)
    notch.set_timer(IDLE)

    assert notch.presentation.notice == PHONE and notch.reactions == [PHONE]


def test_an_error_waits_behind_the_done_panel_and_is_dropped_if_ok_comes_too_late(notch):
    # The done panel is drawn over every notice, so an error under it could
    # not be seen either: it waits, and like any waiting notice it expires.
    notch.set_timer(FINISHED)
    notch.present(ERROR)
    assert notch.presentation.notice is None

    notch.scheduler.advance(DEFERRED_NOTICE_TTL_MS)
    notch.set_timer(IDLE)

    assert notch.presentation.notice is None and notch.deferred == ()


def test_a_running_countdown_is_not_a_band_owner(notch):
    notch.set_timer(RUNNING)

    assert notch.present(PHONE) is NoticeOutcome.SHOWN


def test_shutdown_with_the_timer_done_and_notices_waiting_leaves_no_timer(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)
    notch.present(ERROR)

    notch.shutdown()

    assert notch.scheduler.pending_timers() == 0 and notch.deferred == ()


def test_a_random_sequence_with_the_timer_keeps_every_band_owner_clear():
    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler, NotchBehaviour(notice_duration_ms=DURATION),
                               actions=frozenset(NotchAction))
    presenter.show()
    rng = random.Random(11)
    events = [
        lambda: presenter.present(rng.choice((PHONE, ERROR, replace(PHONE, text="Stick connected",
                                                                   device=replace(PHONE.device, key="s"))))),
        lambda: presenter.set_timer(rng.choice((IDLE, RUNNING, FINISHED))),
        lambda: presenter.open_timer_panel(),
        lambda: presenter.close_timer_panel(),
        lambda: presenter.toggle_controls(),
        lambda: presenter.set_listening(rng.random() < 0.4, True),
        lambda: presenter.set_activity(rng.choice(list(ActivityState))),
        lambda: scheduler.advance(rng.choice((0, 500, DURATION, DEFERRED_NOTICE_TTL_MS))),
    ]
    for _ in range(3000):
        rng.choice(events)()
        shown = presenter.presentation.notice
        band = presenter.presentation.controls or presenter.presentation.timer_panel
        assert len(presenter.deferred) <= MAX_DEFERRED_NOTICES
        if band:
            assert shown is None  # nothing is drawn, or timed, under a panel
    presenter.shutdown()
    assert scheduler.pending_timers() == 0


# --- The running application -------------------------------------------------------------


@pytest.fixture
def runtime():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from mira.application.composition import build_application
    from mira.ui.desktop_shell import DesktopShell, Surface

    clock = [1000.0]
    scheduler = ManualScheduler()
    application = build_application(scheduler=scheduler, clock=lambda: clock[0])
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    application.start()

    def advance(seconds: float) -> None:
        clock[0] += seconds
        scheduler.advance(int(seconds * 1000))

    yield application, desktop, advance
    desktop.shutdown()
    application.shutdown()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_device_during_the_finished_timer_waits_for_ok_on_the_real_path(runtime):
    application, desktop, advance = runtime
    application.timer.start(60)
    advance(60)
    assert application.timer.snapshot.state is TimerState.FINISHED
    assert EmbodimentCondition.TIMER_FINISHED in desktop.controller.conditions

    application.event_bus.emit("notice", PHONE)
    assert desktop.presenter.presentation.notice is None
    assert desktop.controller.active_cue is None

    application.timer.acknowledge()

    assert desktop.presenter.presentation.notice == PHONE
    assert desktop.controller.active_cue is EmbodimentCue.GREETING
    assert EmbodimentCondition.TIMER_FINISHED not in desktop.controller.conditions
