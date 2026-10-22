"""Notice priority in the notch presenter: preemption, deferral, coalescing,
expiration, restoration, refinement and reactions.

Qt-free and deterministic: a `ManualScheduler` runs every notice timer, so a
notice's lifetime, a deferred notice's expiry and the end of a conversation
happen exactly when the test says.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from mira.domain.device_info import ChargingState, DeviceInfo, DeviceInfoUpdate, device_info_notice
from mira.domain.devices import DeviceCategory
from mira.domain.embodiment import ActivityState, EmbodimentIntent
from mira.domain.embodiment_cue import EmbodimentCue
from mira.domain.micro_motion import MicroMotions
from mira.domain.notice import Notice, NoticeCategory, NoticePriority, NoticeTone, notice_priority
from mira.domain.scheduler import ManualScheduler
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, device_notice
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import (
    DEFERRED_NOTICE_TTL_MS,
    MAX_DEFERRED_NOTICES,
    NotchAction,
    NotchBehaviour,
    NotchPresenter,
    NoticeOutcome,
)
from mira.ui.notch.startup import deliver_notice, react_to_notice

DURATION = 4000
E, C = NotchPresence.EXPANDED, NotchPresence.COMPACT

PHONE = DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Google Pixel 8", "/d/usb3/3-2",
                    category=DeviceCategory.SMARTPHONE, manufacturer="Google")
STICK = DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "SanDisk Ultra", "/d/usb3/3-4",
                    category=DeviceCategory.STORAGE, manufacturer="SanDisk")
CHARGING = DeviceInfo("Google", "Pixel 8", 84, ChargingState.CHARGING)

ERROR = Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed")
ALERT = Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low disk")
DONE = Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)
ONLINE = Notice(NoticeCategory.CONNECTIVITY, NoticeTone.SUCCESS, "Back online")


def arrived(device=PHONE) -> Notice:
    return device_notice(device)


def left(device=PHONE) -> Notice:
    return device_notice(replace(device, change=DeviceChange.DISCONNECTED))


def informed(device=PHONE, info=CHARGING, announce=True) -> Notice:
    return device_info_notice(DeviceInfoUpdate(device, info, announce))


class Recorder:
    """A presenter with its scheduler, its presences and its reactions recorded."""

    def __init__(self):
        self.scheduler = ManualScheduler()
        self.presenter = NotchPresenter(
            self.scheduler, NotchBehaviour(notice_duration_ms=DURATION), actions=frozenset(NotchAction),
        )
        self.reactions: list[Notice] = []
        self.presenter.subscribe(self._presented)
        self.presenter.subscribe_reactions(self.reactions.append)
        self.presenter.show()
        # Presence changes from here on (the compact notch is the baseline).
        self._last = self.presenter.presentation.presence
        self.presences: list[NotchPresence] = []

    def _presented(self, presentation):
        if presentation.presence is not getattr(self, "_last", None):
            self._last = presentation.presence
            if hasattr(self, "presences"):
                self.presences.append(presentation.presence)

    @property
    def showing(self) -> Notice | None:
        return self.presenter.presentation.notice

    def advance(self, ms: int) -> None:
        self.scheduler.advance(ms)


@pytest.fixture
def notch():
    return Recorder()


# --- The vocabulary -------------------------------------------------------------------------


@pytest.mark.parametrize(("notice", "priority"), [
    (ERROR, NoticePriority.CRITICAL),
    (ALERT, NoticePriority.CRITICAL),
    (DONE, NoticePriority.FEEDBACK),
    (arrived(), NoticePriority.PASSIVE),
    (ONLINE, NoticePriority.PASSIVE),
    (Notice(NoticeCategory.CONNECTIVITY, NoticeTone.ERROR, "Network failed"), NoticePriority.CRITICAL),
])
def test_every_notice_has_a_priority_and_only_critical_ones_outrank_a_conversation(notice, priority):
    assert notice_priority(notice) is priority
    assert (priority > NoticePriority.INTERACTION) == (priority is NoticePriority.CRITICAL)


# --- 1–3: idle, listening, speaking ---------------------------------------------------------


def test_a_device_connecting_while_idle_is_shown_at_once_and_reacted_to_once(notch):
    outcome = notch.presenter.present(arrived())

    assert outcome is NoticeOutcome.SHOWN and notch.showing == arrived()
    assert notch.presenter.presentation.presence is E and notch.reactions == [arrived()]


def test_a_device_connecting_while_listening_waits_and_never_covers_the_waveform(notch):
    notch.presenter.set_listening(True, True)

    outcome = notch.presenter.present(arrived())

    assert outcome is NoticeOutcome.DEFERRED
    assert notch.presenter.presentation.shows_waveform and notch.reactions == []
    assert notch.presenter.deferred == (arrived(),)


def test_when_listening_ends_the_waiting_notice_is_shown_without_collapsing_first(notch):
    notch.presenter.set_listening(True, True)
    notch.presenter.present(arrived())
    notch.presences.clear()

    notch.presenter.set_listening(False)

    assert notch.showing == arrived() and notch.reactions == [arrived()]
    assert notch.presences == []  # expanded throughout: LISTENING handed over to NOTICE
    notch.advance(DURATION)
    assert notch.presences == [C]


@pytest.mark.parametrize("activity", [ActivityState.SPEAKING, ActivityState.LISTENING])
def test_a_device_connecting_while_mira_speaks_waits_for_the_answer_to_end(notch, activity):
    notch.presenter.set_activity(activity)

    assert notch.presenter.present(arrived()) is NoticeOutcome.DEFERRED
    notch.presenter.set_activity(ActivityState.THINKING)  # still busy, but not talking with the user

    assert notch.showing == arrived()


def test_a_device_notice_showing_steps_aside_when_listening_starts_and_comes_back(notch):
    notch.presenter.present(arrived())
    notch.advance(1000)

    notch.presenter.set_listening(True, True)
    assert notch.showing is None and notch.presenter.presentation.shows_waveform
    notch.presenter.set_listening(False)

    assert notch.showing == arrived() and notch.reactions == [arrived()]  # greeted once


def test_task_feedback_waits_for_a_conversation_but_an_error_interrupts_it(notch):
    notch.presenter.set_listening(True, True)

    assert notch.presenter.present(DONE) is NoticeOutcome.DEFERRED
    assert notch.presenter.present(ERROR) is NoticeOutcome.SHOWN
    assert not notch.presenter.presentation.shows_waveform and notch.reactions == [ERROR]


# --- The controls take the band ------------------------------------------------------------


@pytest.mark.parametrize("incoming", [arrived(), ERROR])
def test_while_the_controls_are_open_every_notice_waits(notch, incoming):
    notch.presenter.open_controls()

    assert notch.presenter.present(incoming) is NoticeOutcome.DEFERRED
    assert notch.showing is None and notch.reactions == []  # nothing drawn under the controls

    notch.presenter.close_controls()

    assert notch.showing == incoming and notch.reactions == [incoming]


def test_a_showing_notice_steps_aside_for_the_controls_and_returns_without_a_second_reaction(notch):
    notch.presenter.present(arrived())
    notch.advance(3000)

    notch.presenter.open_controls()
    assert notch.showing is None and notch.presenter.deferred == (arrived(),)
    notch.advance(DURATION)  # it does not expire under the controls
    notch.presenter.close_controls()

    assert notch.showing == arrived() and notch.reactions == [arrived()]
    notch.advance(DURATION - 1)
    assert notch.showing == arrived()  # its full time again, now that it can be seen


def test_the_controls_closing_by_themselves_bring_the_waiting_notice_back(notch):
    notch.presenter.set_hovered(True)
    notch.presenter.open_controls()
    notch.presenter.present(arrived())
    notch.presenter.set_hovered(False)

    notch.advance(1500)  # CONTROLS_LINGER_MS after the pointer left

    assert notch.presenter.presentation.controls == () and notch.showing == arrived()


# --- 4–5: several devices, rapid changes ----------------------------------------------------


def test_two_devices_connecting_together_are_both_seen_in_turn(notch):
    notch.presenter.present(arrived(PHONE))
    notch.advance(300)

    assert notch.presenter.present(arrived(STICK)) is NoticeOutcome.DEFERRED
    assert notch.showing == arrived(PHONE)
    notch.advance(DURATION - 300)

    assert notch.showing == arrived(STICK) and notch.presences == [E]  # never collapsed in between
    assert notch.reactions == [arrived(PHONE), arrived(STICK)]
    notch.advance(DURATION)
    assert notch.showing is None and notch.presences == [E, C]


def test_a_device_leaving_replaces_its_own_arrival_at_once(notch):
    notch.presenter.present(arrived())
    notch.advance(1500)

    assert notch.presenter.present(left()) is NoticeOutcome.SHOWN
    assert notch.showing == left() and notch.reactions == [arrived(), left()]
    notch.advance(DURATION - 1)
    assert notch.showing == left()


def test_a_device_that_arrives_and_leaves_while_waiting_shows_nothing(notch):
    notch.presenter.set_listening(True, True)
    notch.presenter.present(arrived())

    assert notch.presenter.present(left()) is NoticeOutcome.DROPPED
    notch.presenter.set_listening(False)

    assert notch.showing is None and notch.presenter.deferred == () and notch.reactions == []


def test_rapid_flapping_while_listening_leaves_at_most_the_final_state(notch):
    notch.presenter.set_listening(True, True)
    for _ in range(10):
        notch.presenter.present(arrived())
        notch.presenter.present(left())
    notch.presenter.present(arrived())

    assert notch.presenter.deferred == (arrived(),)
    notch.presenter.set_listening(False)
    assert notch.showing == arrived() and len(notch.reactions) == 1


# --- 6–7: device information ----------------------------------------------------------------


def test_information_arriving_while_the_arrival_shows_grows_it_without_a_second_greeting(notch):
    notch.presenter.present(arrived())
    notch.advance(3000)

    outcome = notch.presenter.refine(informed(), announce=True)

    assert outcome is NoticeOutcome.UPDATED
    assert (notch.showing.text, notch.showing.detail) == ("Pixel 8", "84% · Charging")
    assert notch.reactions == [arrived()]
    notch.advance(DURATION - 1)
    assert notch.showing is not None  # the first information gets its full time


def test_information_about_a_device_that_left_is_never_shown(notch):
    notch.presenter.present(left())

    assert notch.presenter.refine(informed(), announce=True) is NoticeOutcome.DROPPED
    assert notch.showing == left()

    notch.presenter.set_listening(True, True)
    notch.presenter.present(left(STICK))
    assert notch.presenter.refine(informed(STICK, DeviceInfo(capacity_bytes=64_000_000_000)),
                                  announce=True) is NoticeOutcome.DROPPED


def test_information_for_a_waiting_arrival_joins_it_and_it_is_greeted_once_when_shown(notch):
    notch.presenter.set_listening(True, True)
    notch.presenter.present(arrived())

    assert notch.presenter.refine(informed(), announce=True) is NoticeOutcome.UPDATED
    notch.presenter.set_listening(False)

    assert (notch.showing.text, notch.showing.detail) == ("Pixel 8", "84% · Charging")
    assert len(notch.reactions) == 1


def test_late_battery_information_after_the_notice_expired_is_shown_on_its_own(notch):
    notch.presenter.present(arrived())
    notch.advance(DURATION)
    assert notch.showing is None

    assert notch.presenter.refine(informed(), announce=True) is NoticeOutcome.SHOWN

    assert notch.showing.detail == "84% · Charging" and notch.reactions == [arrived()]


def test_an_insignificant_late_update_does_not_reopen_the_notch(notch):
    notch.presenter.present(arrived())
    notch.advance(DURATION)
    notch.presences.clear()

    moved = informed(info=replace(CHARGING, battery_percent=85), announce=False)
    assert notch.presenter.refine(moved, announce=False) is NoticeOutcome.DROPPED
    assert notch.presences == []


def test_a_battery_that_moves_while_shown_updates_in_place_and_keeps_the_time(notch):
    notch.presenter.present(informed())
    notch.advance(3000)

    notch.presenter.refine(informed(info=replace(CHARGING, battery_percent=85)), announce=False)

    assert notch.showing.detail == "85% · Charging"
    notch.advance(1000)
    assert notch.showing is None


def test_repeated_identical_information_changes_nothing(notch):
    notch.presenter.present(informed())
    seen = list(notch.presences)

    assert notch.presenter.refine(informed(), announce=True) is NoticeOutcome.UNCHANGED
    assert notch.presences == seen


# --- 8–10: lifetimes, priority order, deferred expiry ---------------------------------------


def test_a_notice_expires_after_its_duration(notch):
    notch.presenter.present(arrived())
    notch.advance(DURATION - 1)
    assert notch.showing is not None

    notch.advance(1)

    assert notch.showing is None and notch.presenter.presentation.presence is C


def test_a_critical_notice_is_never_overwritten_by_a_passive_one(notch):
    notch.presenter.present(ALERT)

    assert notch.presenter.present(arrived()) is NoticeOutcome.DEFERRED
    assert notch.showing == ALERT
    notch.advance(DURATION)
    assert notch.showing == arrived()


def test_a_preempted_device_notice_returns_after_the_error_without_a_second_greeting(notch):
    notch.presenter.present(arrived())
    notch.advance(1000)

    notch.presenter.present(ERROR)
    assert notch.showing == ERROR and notch.presenter.deferred == (arrived(),)
    notch.advance(DURATION)

    assert notch.showing == arrived()
    assert notch.reactions == [arrived(), ERROR]


def test_waiting_notices_are_restored_highest_priority_first_then_oldest(notch):
    notch.presenter.set_listening(True, True)
    for notice in (arrived(PHONE), DONE, arrived(STICK)):
        notch.presenter.present(notice)

    notch.presenter.set_listening(False)
    order = [notch.showing]
    for _ in range(2):
        notch.advance(DURATION)
        order.append(notch.showing)

    assert order == [DONE, arrived(PHONE), arrived(STICK)]


def test_a_waiting_notice_expires_when_it_is_no_longer_news(notch):
    notch.presenter.set_listening(True, True)
    notch.presenter.present(arrived())

    notch.advance(DEFERRED_NOTICE_TTL_MS)
    notch.presenter.set_listening(False)

    assert notch.presenter.deferred == () and notch.showing is None and notch.reactions == []


def test_the_waiting_queue_is_bounded_and_drops_the_least_important_first(notch):
    notch.presenter.set_listening(True, True)
    devices = [replace(PHONE, key=f"/d/usb3/3-{n}") for n in range(MAX_DEFERRED_NOTICES + 1)]

    notch.presenter.present(arrived(devices[0]))
    notch.presenter.present(DONE)
    for device in devices[1:]:
        notch.presenter.present(arrived(device))

    assert len(notch.presenter.deferred) == MAX_DEFERRED_NOTICES
    assert DONE in notch.presenter.deferred and arrived(devices[0]) not in notch.presenter.deferred
    assert notch.scheduler.pending_timers() == MAX_DEFERRED_NOTICES  # no timer left behind


# --- 11: duplicates ---------------------------------------------------------------------------


def test_the_same_notice_again_only_restarts_its_time(notch):
    notch.presenter.present(arrived())
    notch.advance(3000)
    seen = list(notch.presences)

    assert notch.presenter.present(arrived()) is NoticeOutcome.REFRESHED
    notch.advance(DURATION - 1)

    assert notch.showing == arrived() and notch.presences == seen and len(notch.reactions) == 1


def test_a_duplicate_waiting_notice_is_merged(notch):
    notch.presenter.set_listening(True, True)
    for _ in range(3):
        notch.presenter.present(ONLINE)

    assert notch.presenter.deferred == (ONLINE,)


# --- 14–15: shutdown and repetition -----------------------------------------------------------


def test_shutdown_cancels_every_timer_and_silences_everything(notch):
    notch.presenter.present(arrived())
    notch.presenter.set_listening(True, True)
    notch.presenter.present(arrived(STICK))
    notch.presenter.open_controls()
    reactions, presences = len(notch.reactions), len(notch.presences)

    notch.presenter.shutdown()
    notch.presenter.shutdown()

    assert notch.scheduler.pending_timers() == 0 and notch.presenter.deferred == ()
    assert notch.presenter.present(ERROR) is NoticeOutcome.DROPPED
    assert notch.presenter.refine(informed(), announce=True) is NoticeOutcome.DROPPED
    notch.presenter.set_listening(False)
    notch.advance(60_000)
    assert len(notch.reactions) == reactions and len(notch.presences) == presences


def test_a_long_random_sequence_keeps_the_presenter_consistent():
    notch = Recorder()
    rng = random.Random(7)
    events = [
        lambda: notch.presenter.present(arrived(rng.choice((PHONE, STICK)))),
        lambda: notch.presenter.present(left(rng.choice((PHONE, STICK)))),
        lambda: notch.presenter.present(rng.choice((ERROR, DONE, ONLINE))),
        lambda: notch.presenter.refine(informed(), announce=rng.random() < 0.5),
        lambda: notch.presenter.set_listening(rng.random() < 0.5, True),
        lambda: notch.presenter.set_activity(rng.choice(list(ActivityState))),
        lambda: notch.presenter.open_controls(),
        lambda: notch.presenter.close_controls(),
        lambda: notch.advance(rng.choice((0, 100, 1000, DURATION, DEFERRED_NOTICE_TTL_MS))),
    ]
    for _ in range(2000):
        rng.choice(events)()
        presenter = notch.presenter
        assert len(presenter.deferred) <= MAX_DEFERRED_NOTICES
        shown = presenter.presentation.notice
        if shown is not None:
            assert notch.scheduler.pending_timers() >= 1  # it will expire
        interacting = presenter.presentation.listening or \
            presenter.presentation.activity in (ActivityState.LISTENING, ActivityState.SPEAKING)
        if interacting and shown is not None:
            assert notice_priority(shown) is NoticePriority.CRITICAL  # nothing else covers a conversation
        if presenter.presentation.controls:
            assert shown is None  # nothing is drawn, or timed, under the controls
        if shown is None and not interacting and not presenter.presentation.controls:
            assert presenter.deferred == ()  # nothing waits without a reason
    notch.presenter.set_listening(False)
    notch.presenter.set_activity(ActivityState.IDLE)
    notch.presenter.close_controls()
    notch.advance(10 * DEFERRED_NOTICE_TTL_MS)
    assert notch.showing is None and notch.presenter.deferred == ()
    notch.presenter.shutdown()
    assert notch.scheduler.pending_timers() == 0


# --- 18: the eyes -------------------------------------------------------------------------------


@pytest.fixture
def eyes():
    controller = FaceController(MicroMotions(random.Random(3)))
    controller.set_intent(EmbodimentIntent(ActivityState.IDLE))
    return controller


def test_the_eyes_react_only_when_a_notice_is_shown(notch, eyes):
    notch.presenter.subscribe_reactions(lambda notice: react_to_notice(notice, eyes))
    notch.presenter.set_listening(True, True)

    deliver_notice(arrived(), presenter=notch.presenter, controller=eyes)
    assert eyes.active_cue is None  # MIRA listens: no greeting over the listening eyes

    notch.presenter.set_listening(False)
    assert eyes.active_cue is EmbodimentCue.GREETING


def test_a_device_leaving_gets_no_cue(notch, eyes):
    notch.presenter.subscribe_reactions(lambda notice: react_to_notice(notice, eyes))

    deliver_notice(left(), presenter=notch.presenter, controller=eyes)

    assert notch.showing == left() and eyes.active_cue is None


def test_an_error_reacts_even_during_a_conversation(notch, eyes):
    notch.presenter.subscribe_reactions(lambda notice: react_to_notice(notice, eyes))
    notch.presenter.set_listening(True, True)

    deliver_notice(ERROR, presenter=notch.presenter, controller=eyes)

    assert eyes.active_cue is EmbodimentCue.ERROR


def test_a_greeting_never_replaces_a_stronger_cue(notch, eyes):
    notch.presenter.subscribe_reactions(lambda notice: react_to_notice(notice, eyes))
    eyes.show_cue(EmbodimentCue.WARNING)

    deliver_notice(arrived(), presenter=notch.presenter, controller=eyes)

    assert eyes.active_cue is EmbodimentCue.WARNING


@pytest.mark.parametrize(("activity", "notice", "cue"), [
    (ActivityState.LISTENING, arrived(), None),
    (ActivityState.SPEAKING, DONE, None),
    (ActivityState.LISTENING, ERROR, EmbodimentCue.ERROR),
    (ActivityState.IDLE, arrived(), EmbodimentCue.GREETING),
    (ActivityState.THINKING, DONE, EmbodimentCue.SUCCESS),
])
def test_without_a_notch_the_eyes_follow_the_same_priority(eyes, activity, notice, cue):
    eyes.set_intent(EmbodimentIntent(activity))

    deliver_notice(notice, presenter=None, controller=eyes)

    assert eyes.active_cue is cue
