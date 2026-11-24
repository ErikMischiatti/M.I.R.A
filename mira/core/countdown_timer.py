"""The one countdown timer, announced as ``"timer_changed"`` and ``"timer_finished"``.

Deadline-based: starting or resuming records ``deadline = clock() + remaining``
on the injected monotonic clock, and the remaining time is always derived
from it, never counted down tick by tick, so nothing drifts. The scheduler
only says *when to look again*: one `call_later` for the time left. A timer
may fire early or late, so the callback re-reads the clock and arms again
if any time is left; only at or past the deadline does the countdown
finish.

Every start, pause, resume, reset and finish bumps a generation, and a
callback only acts for the generation that armed it. A callback that
outlives its cancellation can therefore never finish a newer countdown or
one that was paused or reset meanwhile, and a countdown finishes — and
``"timer_finished"`` is emitted — exactly once.

Every change of state is emitted as a `TimerSnapshot` on
``"timer_changed"``; the passing seconds are not events, they are read from
`snapshot` by whoever displays them. How the end is shown is not decided
here. A finished countdown stays FINISHED — awaiting the user — until
`acknowledge`, which returns it to idle with a plain ``"timer_changed"``:
the end was announced once and is never announced again.

`shutdown` cancels the pending callback and ignores every later command; it
must run before the scheduler shuts down.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from mira.domain.scheduler import Scheduler, TimerHandle
from mira.domain.timer import TimerSnapshot, TimerState, validate_duration
from mira.messaging.events import EventBus

TIMER_CHANGED_EVENT = "timer_changed"
TIMER_FINISHED_EVENT = "timer_finished"


class CountdownTimer:
    def __init__(self, event_bus: EventBus, scheduler: Scheduler, *, clock: Callable[[], float] = time.monotonic):
        self._bus = event_bus
        self._scheduler = scheduler
        self._clock = clock
        self._state = TimerState.IDLE
        self._duration_s = 0
        # The time left while not running; while running it is derived from
        # the deadline instead.
        self._remaining_s = 0.0
        self._deadline = 0.0
        self._handle: TimerHandle | None = None
        self._generation = 0
        self._stopped = False

    @property
    def snapshot(self) -> TimerSnapshot:
        """The timer now, with the remaining time read from the clock."""
        remaining = self._time_left() if self._state is TimerState.RUNNING else self._remaining_s
        return TimerSnapshot(self._state, self._duration_s, remaining)

    # --- Commands ------------------------------------------------------------------

    def start(self, duration_s: int) -> bool:
        """Count down ``duration_s`` from now, replacing any countdown under way.

        Raises ValueError for an invalid duration, changing nothing.
        """
        duration_s = validate_duration(duration_s)
        if self._stopped:
            return False
        self._duration_s = duration_s
        self._run(float(duration_s))
        return True

    def pause(self) -> bool:
        if self._stopped or self._state is not TimerState.RUNNING:
            return False
        remaining = self._time_left()
        if remaining <= 0:
            # The deadline passed before its callback ran: it is over.
            self._finish()
            return True
        self._disarm()
        self._state = TimerState.PAUSED
        self._remaining_s = remaining
        self._changed()
        return True

    def resume(self) -> bool:
        if self._stopped or self._state is not TimerState.PAUSED:
            return False
        self._run(self._remaining_s)
        return True

    def reset(self) -> bool:
        """Cancel any countdown and return to idle with the last duration."""
        if self._stopped or self._state is TimerState.IDLE:
            return False
        self._disarm()
        self._state = TimerState.IDLE
        self._remaining_s = float(self._duration_s)
        self._changed()
        return True

    def acknowledge(self) -> bool:
        """The user has seen the countdown finish: back to idle with its duration."""
        if self._stopped or self._state is not TimerState.FINISHED:
            return False
        return self.reset()

    def shutdown(self) -> None:
        self._disarm()
        self._stopped = True

    # --- Countdown -------------------------------------------------------------------

    def _run(self, remaining_s: float) -> None:
        self._disarm()
        self._state = TimerState.RUNNING
        self._deadline = self._clock() + remaining_s
        self._arm()
        self._changed()

    def _time_left(self) -> float:
        return max(self._deadline - self._clock(), 0.0)

    def _arm(self) -> None:
        generation = self._generation
        delay_ms = max(math.ceil(self._time_left() * 1000), 1)
        self._handle = self._scheduler.call_later(delay_ms, lambda: self._due(generation))

    def _disarm(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        self._generation += 1

    def _due(self, generation: int) -> None:
        if generation != self._generation or self._stopped or self._state is not TimerState.RUNNING:
            return
        self._handle = None
        if self._time_left() > 0:
            # Early: look again when the rest is due.
            self._arm()
            return
        self._finish()

    def _finish(self) -> None:
        self._disarm()
        self._state = TimerState.FINISHED
        self._remaining_s = 0.0
        self._changed()
        self._bus.emit(TIMER_FINISHED_EVENT, self.snapshot)

    def _changed(self) -> None:
        self._bus.emit(TIMER_CHANGED_EVENT, self.snapshot)
