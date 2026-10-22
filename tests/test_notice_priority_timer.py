"""Notice priority together with the notch timer: the integration seam.

The timer is not a notice. Its selection panel is a *transient* band owner
(the user opened it), its finished countdown awaiting OK a *persistent* one.
Notices below CRITICAL wait under either; a critical notice closes the
panel, or suspends — never dismisses — the done panel, which returns when
the notice ends, with the countdown still FINISHED. Deterministic:
`ManualScheduler`, a fake clock.
"""

from __future__ import annotations

import os
import random
from dataclasses import replace

import pytest

from mira.domain.devices import DeviceCategory
from mira.domain.embodiment import ActivityState
from mira.domain.embodiment_cue import EmbodimentCondition, EmbodimentCue
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticePriority, NoticeTone, notice_priority
from mira.domain.scheduler import ManualScheduler
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


# --- The selection panel: a transient owner --------------------------------------------------


def test_device_notices_wait_under_the_timer_panel_and_return_when_it_closes(notch):
    notch.open_timer_panel()

    assert notch.present(PHONE) is NoticeOutcome.DEFERRED
    assert notch.presentation.notice is None and notch.reactions == []
    notch.close_timer_panel()

    assert notch.presentation.notice == PHONE and notch.reactions == [PHONE]


def test_an_error_closes_the_timer_panel_and_keeps_the_chosen_duration(notch):
    notch.open_timer_panel()
    notch.choose_timer(TimerButton(TimerCommand.LONGER))
    chosen = notch.timer_selection_s

    assert notch.present(ERROR) is NoticeOutcome.SHOWN

    assert notch.presentation.timer_panel == () and notch.presentation.notice == ERROR
    assert notch.timer_selection_s == chosen  # reopening the panel finds it as it was


def test_opening_the_timer_panel_pushes_an_error_aside_until_it_closes(notch):
    notch.present(ERROR)

    notch.open_timer_panel()
    assert notch.presentation.notice is None and notch.deferred == (ERROR,)
    notch.close_timer_panel()

    assert notch.presentation.notice == ERROR and notch.reactions == [ERROR]


# --- The done panel: a persistent owner ------------------------------------------------------


def test_device_notices_wait_for_ok_with_no_greeting_over_the_timer(notch):
    notch.set_timer(FINISHED)

    notch.present(PHONE)
    assert notch.presentation.notice is None and notch.presentation.timer_panel
    assert notch.reactions == []
    notch.set_timer(IDLE)  # OK

    assert notch.presentation.notice == PHONE and notch.reactions == [PHONE]


def test_an_error_during_the_done_panel_shows_then_the_done_panel_returns(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)  # waits for OK

    assert notch.present(ERROR) is NoticeOutcome.SHOWN
    shown = notch.presentation
    assert shown.notice == ERROR and shown.timer_panel == ()  # suspended, not dismissed
    assert ExpansionReason.TIMER_DONE in shown.reasons and shown.timer.state is TimerState.FINISHED

    notch.scheduler.advance(DURATION)

    after = notch.presentation
    assert after.notice is None and after.timer_panel  # "Timer done!" and OK again
    assert after.timer.state is TimerState.FINISHED and notch.deferred == (PHONE,)
    notch.set_timer(IDLE)
    assert notch.presentation.notice == PHONE


def test_a_showing_notice_steps_aside_when_the_timer_finishes_and_returns_after_ok(notch):
    notch.set_timer(RUNNING)
    notch.present(PHONE)
    notch.scheduler.advance(1000)

    notch.set_timer(FINISHED)
    assert notch.presentation.notice is None and notch.deferred == (PHONE,)
    notch.set_timer(IDLE)

    assert notch.presentation.notice == PHONE and notch.reactions == [PHONE]


def test_waiting_under_the_done_panel_is_not_eligible_time_but_age_still_bounds_it(notch):
    notch.set_timer(FINISHED)
    notch.present(PHONE)

    notch.scheduler.advance(30_000)  # far beyond the eligible budget: not counted here
    assert notch.deferred == (PHONE,)
    notch.scheduler.advance(MAX_NOTICE_AGE_MS - 30_000)  # nobody pressed OK for a minute
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


def test_a_random_sequence_with_the_timer_keeps_every_rule():
    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler, NotchBehaviour(notice_duration_ms=DURATION),
                               actions=frozenset(NotchAction))
    presenter.show()
    rng = random.Random(11)
    stick = replace(PHONE, text="Stick connected", device=replace(PHONE.device, key="s"))
    events = [
        lambda: presenter.present(rng.choice((PHONE, stick, ERROR))),
        lambda: presenter.present(replace(PHONE, device=replace(PHONE.device, connected=False))),
        lambda: presenter.set_timer(rng.choice((IDLE, RUNNING, FINISHED))),
        lambda: presenter.open_timer_panel(),
        lambda: presenter.close_timer_panel(),
        lambda: presenter.toggle_controls(),
        lambda: presenter.set_listening(rng.random() < 0.4, True),
        lambda: presenter.set_activity(rng.choice(list(ActivityState))),
        lambda: scheduler.advance(rng.choice((0, 500, DURATION, 15_000, MAX_NOTICE_AGE_MS))),
    ]
    for _ in range(4000):
        rng.choice(events)()
        shown = presenter.presentation
        assert len(presenter.deferred) <= MAX_DEFERRED_NOTICES
        if shown.controls or shown.timer_panel:
            assert shown.notice is None  # nothing drawn, timed or reacted to under a panel
        if shown.notice is not None and notice_priority(shown.notice) is NoticePriority.CRITICAL:
            assert not shown.controls and not shown.timer_panel  # an error is never covered
        if shown.timer.state is TimerState.FINISHED and shown.notice is None:
            assert shown.timer_panel  # the done panel is never lost, only suspended
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


def test_an_error_during_a_finished_timer_on_the_real_path_keeps_the_timer(runtime):
    application, desktop, advance = runtime
    application.timer.start(60)
    advance(60)
    application.event_bus.emit("notice", PHONE)  # waits for OK

    application.event_bus.emit("notice", ERROR)

    assert desktop.presenter.presentation.notice == ERROR
    assert desktop.controller.active_cue is EmbodimentCue.ERROR
    assert EmbodimentCondition.TIMER_FINISHED in desktop.controller.conditions  # held underneath
    advance(DURATION / 1000)
    assert desktop.presenter.presentation.timer_panel  # "Timer done!" is back
    assert desktop.presenter.presentation.notice is None  # and nothing hides under it
    assert application.timer.snapshot.state is TimerState.FINISHED

    application.timer.acknowledge()

    assert desktop.presenter.presentation.notice == PHONE
    assert EmbodimentCondition.TIMER_FINISHED not in desktop.controller.conditions
