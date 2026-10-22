"""`NotchPresenter`: what the notch presents, resolved in one place, Qt-free.

Driven by `ManualScheduler`, so notice lifetimes are exact and no test waits.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.notch import presentation as presentation_module
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import (
    DEFAULT_NOTICE_DURATION_MS,
    ExpansionReason,
    NotchAction,
    NotchBehaviour,
    NotchContent,
    NotchPresentation,
    NotchPresenter,
    NotchSymbol,
)


H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED
DURATION = 3000

WARNING = Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low disk")
DEVICE = Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Headphones")
TASK_DONE = Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def presenter(scheduler):
    presenter = NotchPresenter(scheduler, NotchBehaviour(notice_duration_ms=DURATION))
    presenter.show()
    return presenter


def presences(presenter: NotchPresenter) -> list[NotchPresence]:
    """The presence changes listeners see, in order (content changes omitted)."""
    seen: list[NotchPresence] = []

    def record(presentation: NotchPresentation):
        if (seen[-1] if seen else presenter_start) is not presentation.presence:
            seen.append(presentation.presence)

    presenter_start = presenter.presentation.presence
    presenter.subscribe(record)
    return seen


# --- Defaults ------------------------------------------------------------------


def test_the_default_presentation_is_hidden_idle_and_empty(scheduler):
    presenter = NotchPresenter(scheduler)

    assert presenter.presentation == NotchPresentation()
    assert presenter.presentation.presence is H
    assert presenter.presentation.content is None
    assert presenter.presentation.reasons == frozenset()
    assert presenter.presentation.actions == frozenset()


def test_showing_is_compact_until_a_reason_expands_it(presenter):
    assert presenter.presentation.presence is C


def test_the_production_behaviour_is_conservative():
    behaviour = NotchBehaviour()

    assert behaviour.expand_on_hover
    assert behaviour.notice_duration_ms == DEFAULT_NOTICE_DURATION_MS
    assert behaviour.expands_for(NoticeCategory.ALERT)
    assert behaviour.expands_for(NoticeCategory.DEVICE)
    assert not behaviour.expands_for(NoticeCategory.TASK)


@pytest.mark.parametrize("duration", [0, 999, 30001, 1.5, True])
def test_a_notice_duration_outside_the_safe_limits_is_rejected(duration):
    with pytest.raises(ValueError):
        NotchBehaviour(notice_duration_ms=duration)


def test_actions_are_presented_as_given(scheduler):
    actions = frozenset({NotchAction.OPEN_MAIN_WINDOW})
    presenter = NotchPresenter(scheduler, actions=actions)

    assert presenter.presentation.actions == actions


# --- Activity ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("activity", "content"),
    [
        (ActivityState.IDLE, None),
        (ActivityState.LISTENING, NotchContent(NotchSymbol.LISTENING, "Listening")),
        (ActivityState.THINKING, NotchContent(NotchSymbol.THINKING, "Thinking")),
        (ActivityState.SPEAKING, NotchContent(NotchSymbol.SPEAKING, "Speaking")),
    ],
)
def test_every_activity_has_its_content(presenter, activity, content):
    presenter.set_activity(activity)

    assert presenter.presentation.activity is activity
    assert presenter.presentation.content == content


def test_every_activity_is_mapped():
    for activity in ActivityState:
        presenter = NotchPresenter(ManualScheduler())
        presenter.set_activity(activity)
        presenter.presentation.content  # never raises


@pytest.mark.parametrize("activity", list(ActivityState))
def test_activity_alone_never_expands_the_notch(presenter, activity):
    presenter.set_activity(activity)

    assert presenter.presentation.presence is C


def test_activity_shows_once_expanded_by_hover(presenter):
    presenter.set_activity(ActivityState.THINKING)
    presenter.set_hovered(True)

    assert presenter.presentation.presence is E
    assert presenter.presentation.content == NotchContent(NotchSymbol.THINKING, "Thinking")


# --- Notice content ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("notice", "content"),
    [
        (Notice(NoticeCategory.TASK, NoticeTone.SUCCESS), NotchContent(NotchSymbol.SUCCESS, "Done")),
        (Notice(NoticeCategory.TASK, NoticeTone.ERROR), NotchContent(NotchSymbol.ERROR, "Error")),
        (Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low disk"), NotchContent(NotchSymbol.WARNING, "Low disk")),
        (Notice(NoticeCategory.ALERT, NoticeTone.INFO), NotchContent(NotchSymbol.WARNING, None)),
        (Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Headphones"), NotchContent(NotchSymbol.DEVICE, "Headphones")),
        (Notice(NoticeCategory.DEVICE, NoticeTone.ERROR, "Mic lost"), NotchContent(NotchSymbol.ERROR, "Mic lost")),
        (Notice(NoticeCategory.TASK, NoticeTone.INFO, "  "), NotchContent(NotchSymbol.TASK, None)),
    ],
)
def test_notice_content_follows_tone_then_category(presenter, notice, content):
    presenter.present(notice)

    assert presenter.presentation.content == content


def test_a_notice_wins_over_the_activity_and_the_activity_returns_after_it(presenter, scheduler):
    presenter.set_activity(ActivityState.LISTENING)
    presenter.present(WARNING)
    assert presenter.presentation.content.symbol is NotchSymbol.WARNING

    scheduler.advance(DURATION)

    assert presenter.presentation.content.symbol is NotchSymbol.LISTENING


# --- Lifetime and replacement ------------------------------------------------------------


def test_an_automatic_notice_expands_then_expires_back_to_compact(presenter, scheduler):
    seen = presences(presenter)

    presenter.present(WARNING)
    assert presenter.presentation.presence is E
    assert presenter.presentation.reasons == {ExpansionReason.NOTICE}

    scheduler.advance(DURATION - 1)
    assert presenter.presentation.notice == WARNING

    scheduler.advance(1)
    assert presenter.presentation.notice is None
    assert presenter.presentation.presence is C
    assert seen == [E, C]
    assert scheduler.pending_timers() == 0


def test_the_latest_notice_replaces_the_previous_one_with_a_full_duration(presenter, scheduler):
    # The same subject (device changes, no device key): the latest replaces.
    presenter.present(Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Keyboard"))
    scheduler.advance(2000)

    presenter.present(DEVICE)
    assert presenter.presentation.notice == DEVICE
    assert scheduler.pending_timers() == 1

    scheduler.advance(DURATION - 1)  # beyond the first notice's lifetime
    assert presenter.presentation.notice == DEVICE
    assert presenter.presentation.presence is E

    scheduler.advance(1)
    assert presenter.presentation.notice is None
    assert presenter.presentation.presence is C


def test_replacing_does_not_flicker_through_compact(presenter):
    seen = presences(presenter)

    presenter.present(WARNING)
    presenter.present(DEVICE)

    assert seen == [E]


def test_the_same_notice_again_restarts_its_lifetime(presenter, scheduler):
    presenter.present(WARNING)
    scheduler.advance(2000)
    presenter.present(WARNING)

    scheduler.advance(2000)
    assert presenter.presentation.notice == WARNING
    scheduler.advance(1000)
    assert presenter.presentation.notice is None


def test_dismissing_ends_the_notice_and_its_timer(presenter, scheduler):
    presenter.present(WARNING)

    presenter.dismiss_notice()

    assert presenter.presentation.presence is C
    assert scheduler.pending_timers() == 0


def test_a_new_duration_applies_from_the_next_notice(presenter, scheduler):
    presenter.present(WARNING)
    presenter.set_behaviour(NotchBehaviour(notice_duration_ms=10000))

    scheduler.advance(DURATION)
    assert presenter.presentation.notice is None

    presenter.present(WARNING)
    scheduler.advance(9999)
    assert presenter.presentation.notice == WARNING
    scheduler.advance(1)
    assert presenter.presentation.notice is None


# --- Preferences ------------------------------------------------------------------------


def test_a_notice_whose_category_does_not_expand_stays_compact(presenter, scheduler):
    presenter.present(TASK_DONE)

    assert presenter.presentation.presence is C
    assert presenter.presentation.reasons == frozenset()
    # Still the content for its lifetime, visible if opened another way.
    presenter.set_hovered(True)
    assert presenter.presentation.content.symbol is NotchSymbol.SUCCESS

    presenter.set_hovered(False)
    scheduler.advance(DURATION)
    assert presenter.presentation.notice is None


def test_enabling_a_category_expands_for_its_notices(scheduler):
    behaviour = NotchBehaviour(automatic_expansion=frozenset({NoticeCategory.TASK}), notice_duration_ms=DURATION)
    presenter = NotchPresenter(scheduler, behaviour)
    presenter.show()

    presenter.present(TASK_DONE)
    assert presenter.presentation.presence is E
    presenter.present(WARNING)
    assert presenter.presentation.presence is C


def test_disabling_a_category_mid_notice_collapses_at_once(presenter):
    presenter.present(WARNING)

    presenter.set_behaviour(NotchBehaviour(automatic_expansion=frozenset(), notice_duration_ms=DURATION))

    assert presenter.presentation.presence is C


def test_hover_does_not_expand_when_hover_expansion_is_off(scheduler):
    presenter = NotchPresenter(scheduler, NotchBehaviour(expand_on_hover=False))
    presenter.show()

    presenter.set_hovered(True)
    assert presenter.presentation.presence is C

    presenter.set_behaviour(NotchBehaviour(expand_on_hover=True))
    assert presenter.presentation.presence is E


# --- Expansion reasons --------------------------------------------------------------


def test_hover_expands_and_leaving_collapses(presenter):
    seen = presences(presenter)

    presenter.set_hovered(True)
    assert presenter.presentation.reasons == {ExpansionReason.HOVER}
    presenter.set_hovered(False)

    assert seen == [E, C]


def test_hover_ending_while_a_notice_shows_stays_expanded_until_it_expires(presenter, scheduler):
    presenter.set_hovered(True)
    presenter.present(WARNING)
    presenter.set_hovered(False)
    assert presenter.presentation.presence is E
    assert presenter.presentation.reasons == {ExpansionReason.NOTICE}

    scheduler.advance(DURATION)

    assert presenter.presentation.presence is C


def test_a_notice_expiring_while_hovered_stays_expanded_until_leaving(presenter, scheduler):
    presenter.present(WARNING)
    presenter.set_hovered(True)
    scheduler.advance(DURATION)
    assert presenter.presentation.presence is E
    assert presenter.presentation.reasons == {ExpansionReason.HOVER}

    presenter.set_hovered(False)

    assert presenter.presentation.presence is C


def test_pinning_is_its_own_reason(presenter, scheduler):
    presenter.set_pinned(True)
    presenter.present(WARNING)
    presenter.set_hovered(True)
    assert presenter.presentation.reasons == {ExpansionReason.PINNED, ExpansionReason.NOTICE, ExpansionReason.HOVER}

    presenter.set_hovered(False)
    scheduler.advance(DURATION)
    assert presenter.presentation.presence is E

    presenter.set_pinned(False)
    assert presenter.presentation.presence is C


def test_only_the_last_reason_to_end_collapses(presenter, scheduler):
    seen = presences(presenter)

    presenter.set_hovered(True)
    presenter.set_pinned(True)
    presenter.present(WARNING)
    presenter.set_hovered(False)
    presenter.set_pinned(False)
    scheduler.advance(DURATION)

    assert seen == [E, C]


def test_hidden_wins_over_every_reason(presenter):
    presenter.set_pinned(True)
    presenter.present(WARNING)

    presenter.hide()

    assert presenter.presentation.presence is H
    assert presenter.presentation.reasons == {ExpansionReason.PINNED, ExpansionReason.NOTICE}


def test_hiding_forgets_the_hover(presenter):
    presenter.set_hovered(True)
    presenter.hide()
    presenter.show()

    assert not presenter.hovered
    assert presenter.presentation.presence is C


# --- Notification and shutdown ---------------------------------------------------------


def test_listeners_hear_only_real_changes(presenter):
    heard = []
    presenter.subscribe(heard.append)

    presenter.set_hovered(False)
    presenter.set_activity(ActivityState.IDLE)
    presenter.show()
    assert heard == []

    presenter.set_activity(ActivityState.THINKING)
    assert [p.activity for p in heard] == [ActivityState.THINKING]


def test_shutdown_cancels_the_timer_and_stops_notifying(presenter, scheduler):
    heard = []
    presenter.subscribe(heard.append)
    presenter.present(WARNING)
    heard.clear()

    presenter.shutdown()
    presenter.shutdown()
    scheduler.advance(DURATION)
    presenter.present(DEVICE)
    presenter.set_hovered(True)

    assert heard == []
    assert scheduler.pending_timers() == 0


def test_a_late_expiry_never_ends_a_newer_notice(presenter, scheduler):
    presenter.present(WARNING)
    stale = presenter._expiry
    presenter.present(DEVICE)

    stale._fire()  # a timer the scheduler failed to cancel

    assert presenter.presentation.notice == DEVICE


# --- Boundary ----------------------------------------------------------------------------


@pytest.mark.parametrize("module", [presentation_module.__file__, "mira/domain/notice.py"])
def test_the_presentation_model_imports_no_toolkit_or_service(module):
    path = Path(module)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    imported = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = ("PySide6", "mira.adapters", "mira.core", "mira.cognition", "mira.actions", "mira.application")
    assert not [name for name in imported if name.startswith(forbidden)]


# --- Integrated controls ---------------------------------------------------------------


def controls_presenter():
    from mira.ui.notch.presentation import NotchAction

    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler, actions=frozenset(NotchAction))
    presenter.show()
    return presenter, scheduler


def test_controls_are_timer_settings_and_quit_never_the_main_window():
    from mira.ui.notch.presentation import ExpansionReason, NotchAction

    presenter, _ = controls_presenter()
    presenter.toggle_controls()

    assert presenter.presentation.controls == (NotchAction.TIMER, NotchAction.OPEN_SETTINGS, NotchAction.QUIT)
    assert ExpansionReason.CONTROLS in presenter.presentation.reasons


def test_a_notch_without_control_actions_opens_no_controls():
    from mira.ui.notch.presentation import NotchAction

    presenter = NotchPresenter(ManualScheduler(), actions=frozenset({NotchAction.OPEN_MAIN_WINDOW}))
    presenter.show()
    presenter.open_controls()

    assert presenter.presentation.controls == () and not presenter.controls_open


def test_a_notice_steps_aside_for_the_controls_and_hiding_closes_them():
    from mira.domain.notice import Notice, NoticeCategory, NoticeTone

    presenter, _ = controls_presenter()
    low_battery = Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low battery")
    presenter.present(low_battery)
    presenter.open_controls()
    # Drawn under the controls it could not be seen: it waits instead.
    assert presenter.presentation.controls and presenter.presentation.notice is None
    assert presenter.deferred == (low_battery,)

    presenter.hide()
    presenter.show()

    assert presenter.presentation.controls == ()
    assert presenter.presentation.notice == low_battery


def test_the_linger_timer_is_cancelled_by_shutdown():
    presenter, scheduler = controls_presenter()
    presenter.set_hovered(True)
    presenter.open_controls()
    presenter.set_hovered(False)
    assert scheduler.pending_timers() == 1

    presenter.shutdown()

    assert scheduler.pending_timers() == 0
