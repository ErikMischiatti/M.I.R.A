"""The countdown timer: deadline-based, exactly-once completion, no stale callbacks.

The service is driven by a `ManualScheduler` and a fake monotonic clock that
are advanced separately where a test needs the scheduler to fire early or
late relative to the clock, which is how a real `QTimer` behaves.
"""

from __future__ import annotations

import pytest

from mira.core.countdown_timer import TIMER_CHANGED_EVENT, TIMER_FINISHED_EVENT, CountdownTimer
from mira.domain.scheduler import ManualScheduler
from mira.domain.timer import (
    MAX_TIMER_SECONDS,
    TimerSnapshot,
    TimerState,
    format_remaining,
    validate_duration,
)
from mira.messaging.events import EventBus


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    """A timer on a manual scheduler and a fake clock, recording its events."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.scheduler = ManualScheduler()
        self.clock = FakeClock()
        self.timer = CountdownTimer(self.bus, self.scheduler, clock=self.clock)
        self.changes: list[TimerSnapshot] = []
        self.finished: list[TimerSnapshot] = []
        self.bus.subscribe(TIMER_CHANGED_EVENT, self.changes.append)
        self.bus.subscribe(TIMER_FINISHED_EVENT, self.finished.append)

    def elapse(self, seconds: float) -> None:
        """Real time passing: the clock and the scheduler move together."""
        self.clock.now += seconds
        self.scheduler.advance(round(seconds * 1000))

    @property
    def state(self) -> TimerState:
        return self.timer.snapshot.state


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --- Domain ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "00:00"),
        (0.2, "00:01"),
        (59.0, "00:59"),
        (60, "01:00"),
        (25 * 60, "25:00"),
        (3599, "59:59"),
        (3599.5, "01:00:00"),
        (3600, "01:00:00"),
        (3600 + 5 * 60 + 7, "01:05:07"),
        (MAX_TIMER_SECONDS, "23:59:59"),
        (-3, "00:00"),
    ],
)
def test_remaining_time_reads_as_minutes_and_seconds_or_hours(seconds, text):
    assert format_remaining(seconds) == text


@pytest.mark.parametrize("value", [0, -1, MAX_TIMER_SECONDS + 1, 1.5, "60", True, None])
def test_invalid_durations_are_rejected(value):
    with pytest.raises(ValueError):
        validate_duration(value)


def test_valid_durations_are_whole_seconds():
    assert validate_duration(1) == 1
    assert validate_duration(MAX_TIMER_SECONDS) == MAX_TIMER_SECONDS


def test_only_running_and_paused_timers_are_active():
    assert {state for state in TimerState if TimerSnapshot(state).active} == {TimerState.RUNNING, TimerState.PAUSED}


# --- Start and completion -----------------------------------------------------------------


def test_a_new_timer_is_idle_and_schedules_nothing(rig):
    assert rig.timer.snapshot == TimerSnapshot(TimerState.IDLE, 0, 0.0)
    assert rig.scheduler.pending_timers() == 0


def test_start_runs_the_countdown_from_the_full_duration(rig):
    rig.timer.start(300)

    assert rig.timer.snapshot == TimerSnapshot(TimerState.RUNNING, 300, 300.0)
    assert rig.changes == [TimerSnapshot(TimerState.RUNNING, 300, 300.0)]


def test_remaining_time_follows_the_clock(rig):
    rig.timer.start(300)

    rig.elapse(42.5)

    assert rig.timer.snapshot.remaining_s == pytest.approx(257.5)
    assert rig.state is TimerState.RUNNING


def test_the_countdown_finishes_at_its_deadline(rig):
    rig.timer.start(60)

    rig.elapse(59.9)
    assert rig.state is TimerState.RUNNING

    rig.elapse(0.1)
    assert rig.timer.snapshot == TimerSnapshot(TimerState.FINISHED, 60, 0.0)
    assert rig.finished == [TimerSnapshot(TimerState.FINISHED, 60, 0.0)]
    assert rig.changes[-1].state is TimerState.FINISHED
    assert rig.scheduler.pending_timers() == 0


def test_completion_is_announced_exactly_once(rig):
    rig.timer.start(10)

    rig.elapse(10)
    rig.elapse(600)
    rig.timer.pause()
    rig.timer.resume()

    assert len(rig.finished) == 1
    assert [change.state for change in rig.changes].count(TimerState.FINISHED) == 1


# --- Pause and resume ---------------------------------------------------------------------


def test_pause_freezes_the_remaining_time(rig):
    rig.timer.start(120)
    rig.elapse(20)

    rig.timer.pause()
    rig.elapse(1000)

    assert rig.timer.snapshot == TimerSnapshot(TimerState.PAUSED, 120, 100.0)
    assert rig.finished == []
    assert rig.scheduler.pending_timers() == 0


def test_resume_continues_from_where_it_paused(rig):
    rig.timer.start(120)
    rig.elapse(20)
    rig.timer.pause()
    rig.elapse(500)

    rig.timer.resume()
    rig.elapse(99)
    assert rig.state is TimerState.RUNNING
    assert rig.timer.snapshot.remaining_s == pytest.approx(1.0)

    rig.elapse(1)
    assert rig.state is TimerState.FINISHED


def test_pause_and_resume_only_apply_where_they_make_sense(rig):
    assert rig.timer.pause() is False
    assert rig.timer.resume() is False

    rig.timer.start(30)
    assert rig.timer.resume() is False
    assert rig.timer.pause() is True
    assert rig.timer.pause() is False
    assert rig.timer.resume() is True
    assert [change.state for change in rig.changes] == [TimerState.RUNNING, TimerState.PAUSED, TimerState.RUNNING]


def test_pausing_after_the_deadline_finishes_instead(rig):
    # The callback is late: the clock is past the deadline, the scheduler not yet.
    rig.timer.start(30)
    rig.clock.now += 31

    rig.timer.pause()

    assert rig.state is TimerState.FINISHED
    assert len(rig.finished) == 1
    rig.scheduler.advance(60_000)
    assert len(rig.finished) == 1


# --- Reset and restart ------------------------------------------------------------------------


@pytest.mark.parametrize("paused", [False, True])
def test_reset_cancels_the_countdown(rig, paused):
    rig.timer.start(90)
    rig.elapse(30)
    if paused:
        rig.timer.pause()

    assert rig.timer.reset() is True

    assert rig.timer.snapshot == TimerSnapshot(TimerState.IDLE, 90, 90.0)
    assert rig.scheduler.pending_timers() == 0
    rig.elapse(600)
    assert rig.finished == []


def test_reset_clears_a_finished_timer_and_does_nothing_when_idle(rig):
    assert rig.timer.reset() is False
    assert rig.changes == []

    rig.timer.start(5)
    rig.elapse(5)
    assert rig.timer.reset() is True
    assert rig.timer.snapshot == TimerSnapshot(TimerState.IDLE, 5, 5.0)


def test_a_finished_timer_can_be_started_again(rig):
    rig.timer.start(5)
    rig.elapse(5)

    rig.timer.start(8)
    rig.elapse(7)
    assert rig.state is TimerState.RUNNING
    rig.elapse(1)

    assert rig.state is TimerState.FINISHED
    assert [snapshot.duration_s for snapshot in rig.finished] == [5, 8]


def test_starting_again_replaces_the_running_countdown(rig):
    rig.timer.start(10)
    rig.elapse(4)

    rig.timer.start(20)
    rig.elapse(10)

    # The first countdown's deadline has passed; only the second one counts.
    assert rig.state is TimerState.RUNNING
    assert rig.timer.snapshot.remaining_s == pytest.approx(10.0)
    assert rig.scheduler.pending_timers() == 1
    rig.elapse(10)
    assert len(rig.finished) == 1


@pytest.mark.parametrize("duration", [0, -5, MAX_TIMER_SECONDS + 1, 2.5, None])
def test_an_invalid_duration_changes_nothing(rig, duration):
    rig.timer.start(60)
    before = rig.timer.snapshot

    with pytest.raises(ValueError):
        rig.timer.start(duration)

    assert rig.timer.snapshot == before
    assert rig.scheduler.pending_timers() == 1


# --- Scheduler accuracy and stale callbacks ------------------------------------------------------


def test_an_early_callback_rearms_for_the_time_left(rig):
    rig.timer.start(60)
    # The scheduler fires the deadline callback while the clock is half a second short.
    rig.clock.now += 59.5
    rig.scheduler.advance(60_000)

    assert rig.state is TimerState.RUNNING
    assert rig.finished == []
    assert rig.scheduler.pending_timers() == 1

    rig.elapse(0.5)
    assert rig.state is TimerState.FINISHED
    assert len(rig.finished) == 1


def test_a_late_callback_finishes_once_with_nothing_left(rig):
    rig.timer.start(60)
    rig.clock.now += 75
    assert rig.timer.snapshot.remaining_s == 0.0

    rig.scheduler.advance(60_000)

    assert rig.timer.snapshot == TimerSnapshot(TimerState.FINISHED, 60, 0.0)
    assert len(rig.finished) == 1


class LeakyScheduler(ManualScheduler):
    """Like a timer whose cancellation loses the race: cancelled callbacks still run."""

    def call_later(self, delay_ms, callback):
        timer = super().call_later(delay_ms, callback)
        timer.cancel = lambda: None
        return timer


def test_stale_callbacks_cannot_change_a_newer_countdown():
    bus, scheduler, clock = EventBus(), LeakyScheduler(), FakeClock()
    timer = CountdownTimer(bus, scheduler, clock=clock)
    finished = []
    bus.subscribe(TIMER_FINISHED_EVENT, finished.append)

    timer.start(10)
    timer.pause()
    timer.resume()  # a second callback, also for t+10 s
    clock.now += 2
    timer.reset()
    timer.start(30)
    clock.now += 10
    scheduler.advance(10_000)  # both stale callbacks run here

    assert timer.snapshot.state is TimerState.RUNNING
    assert timer.snapshot.remaining_s == pytest.approx(20.0)
    assert finished == []


# --- Shutdown -----------------------------------------------------------------------------------


def test_shutdown_cancels_the_countdown_and_ignores_later_commands(rig):
    rig.timer.start(10)
    rig.changes.clear()

    rig.timer.shutdown()
    rig.timer.shutdown()

    assert rig.scheduler.pending_timers() == 0
    rig.elapse(60)
    assert rig.finished == []
    assert rig.timer.pause() is False
    assert rig.timer.reset() is False
    assert rig.timer.start(5) is False
    assert rig.changes == []


def test_shutdown_before_the_scheduler_leaves_nothing_to_cancel(rig):
    rig.timer.start(10)
    rig.timer.shutdown()

    rig.scheduler.shutdown()

    assert rig.state is TimerState.RUNNING  # frozen as it was; nothing fires any more


# --- Acknowledgement ------------------------------------------------------------------------------


def test_a_finished_timer_stays_finished_until_acknowledged(rig):
    rig.timer.start(3)
    rig.elapse(3)

    rig.elapse(24 * 3600)

    assert rig.timer.snapshot == TimerSnapshot(TimerState.FINISHED, 3, 0.0)
    assert len(rig.finished) == 1


def test_acknowledging_returns_to_idle_without_finishing_again(rig):
    rig.timer.start(3)
    rig.elapse(3)
    rig.changes.clear()

    assert rig.timer.acknowledge() is True

    assert rig.timer.snapshot == TimerSnapshot(TimerState.IDLE, 3, 3.0)
    assert rig.changes == [TimerSnapshot(TimerState.IDLE, 3, 3.0)]
    assert len(rig.finished) == 1
    assert rig.scheduler.pending_timers() == 0


def test_acknowledging_again_is_harmless(rig):
    rig.timer.start(3)
    rig.elapse(3)
    rig.timer.acknowledge()
    rig.changes.clear()

    assert rig.timer.acknowledge() is False
    assert rig.timer.acknowledge() is False

    assert rig.changes == []
    assert rig.state is TimerState.IDLE


@pytest.mark.parametrize("paused", [False, True])
def test_only_a_finished_timer_can_be_acknowledged(rig, paused):
    assert rig.timer.acknowledge() is False
    rig.timer.start(30)
    if paused:
        rig.timer.pause()
    before = rig.timer.snapshot
    rig.changes.clear()

    assert rig.timer.acknowledge() is False

    assert rig.timer.snapshot == before
    assert rig.changes == []


def test_a_timer_starts_again_after_acknowledgement_and_finishes_once_more(rig):
    rig.timer.start(3)
    rig.elapse(3)
    rig.timer.acknowledge()

    rig.timer.start(4)
    rig.elapse(4)

    assert rig.state is TimerState.FINISHED
    assert [snapshot.duration_s for snapshot in rig.finished] == [3, 4]


def test_acknowledging_after_shutdown_does_nothing(rig):
    rig.timer.start(3)
    rig.elapse(3)
    rig.timer.shutdown()

    assert rig.timer.acknowledge() is False
    assert rig.state is TimerState.FINISHED
