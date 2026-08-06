"""Timing abstraction for the serialized execution context.

MIRA's turn lifecycle is a two-phase commit: an interpretation phase that may
run anywhere and has no authority, and a commitment phase that must run on one
serialized context and owns every side effect. The domain needs four timing
operations to express that, and nothing more:

- run work away from the serialized context, then deliver its result back on it
  (`submit`);
- run a callback on the serialized context after a delay (`call_later`);
- cancel a delayed callback that has not run yet (`TimerHandle.cancel`);
- ask whether a delayed callback is still outstanding (`TimerHandle.is_pending`).

No GUI or threading concept appears in this interface. `ManualScheduler` below
is the reference implementation: fully deterministic and dependency-free, driven
by an explicit clock rather than an event loop. That makes it the vehicle for
tests today; a headless runtime would additionally need a blocking drive loop,
which is deliberately not part of this port.

Error handling is deliberately absent: the caller passes work that does not
raise, so no scheduler has to decide policy for a failed turn. That obligation
is real, not decorative — implementations differ in what happens if it is
broken. `ManualScheduler` lets the exception propagate to whoever drained the
queue, while a Qt-backed one raises inside a worker thread, where the
completion is simply never delivered. `Brain._interpret_for_commit` therefore
converts any interpretation failure into a result value, and a test pins that.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class TimerHandle(Protocol):
    """A delayed callback that has not necessarily run yet."""

    def cancel(self) -> None:
        """Prevent the callback from running. Safe to call more than once."""

    def is_pending(self) -> bool:
        """True while the callback is still scheduled to run."""


@runtime_checkable
class Scheduler(Protocol):
    """Timing operations required by the turn lifecycle."""

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> TimerHandle:
        """Run `callback` on the serialized context after `delay_ms`."""

    def submit(self, work: Callable[[], T], on_complete: Callable[[T], None]) -> None:
        """Run `work` off the serialized context, then `on_complete` on it.

        `work` must not raise and must not touch state owned by the serialized
        context. `on_complete` receives whatever `work` returned.
        """


class ManualTimer:
    """A `TimerHandle` driven by `ManualScheduler`'s logical clock."""

    def __init__(self, due_ms: int, callback: Callable[[], None]) -> None:
        self.due_ms = due_ms
        self.callback = callback
        self._cancelled = False
        self._fired = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_pending(self) -> bool:
        return not self._cancelled and not self._fired

    def _fire(self) -> None:
        self._fired = True
        self.callback()


class ManualScheduler:
    """Deterministic `Scheduler` with an explicit clock and queue.

    Nothing runs until asked. Submitted work is queued rather than executed, so
    a test can interleave several turns and reproduce a stale-result race with
    no threads and no wall-clock waiting.
    """

    def __init__(self) -> None:
        self.now_ms = 0
        self._timers: list[ManualTimer] = []
        self._queue: list[tuple[Callable[[], Any], Callable[[Any], None]]] = []

    # --- Scheduler ------------------------------------------------------

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> ManualTimer:
        timer = ManualTimer(due_ms=self.now_ms + delay_ms, callback=callback)
        self._timers.append(timer)
        return timer

    def submit(self, work: Callable[[], T], on_complete: Callable[[T], None]) -> None:
        self._queue.append((work, on_complete))

    # --- inspection -----------------------------------------------------

    def pending_work(self) -> int:
        """Number of submitted units of work not yet run."""
        return len(self._queue)

    def pending_timers(self) -> int:
        """Number of timers that have neither fired nor been cancelled."""
        return sum(1 for timer in self._timers if timer.is_pending())

    # --- control --------------------------------------------------------

    def run_next(self) -> bool:
        """Run the oldest submitted work and deliver its result. True if any ran."""
        if not self._queue:
            return False
        work, on_complete = self._queue.pop(0)
        on_complete(work())
        return True

    def run_all(self) -> int:
        """Run all submitted work, including work queued while running. Returns the count."""
        ran = 0
        while self.run_next():
            ran += 1
        return ran

    def advance(self, delay_ms: int) -> int:
        """Move the clock forward, firing due timers in due order.

        A timer scheduled while advancing fires in the same call if it falls due
        inside the window, matching a real event loop. `max_iterations` turns the
        pathological case — a callback that reschedules itself with no delay —
        into a diagnosable error instead of a hang.
        """
        max_iterations = 10_000
        target = self.now_ms + delay_ms
        fired = 0
        while True:
            self._timers = [t for t in self._timers if t.is_pending()]
            due = [t for t in self._timers if t.due_ms <= target]
            if not due:
                break
            if fired >= max_iterations:
                raise RuntimeError(
                    f"advance({delay_ms}) fired {fired} timers without draining; "
                    "a callback is most likely rescheduling itself with no delay"
                )
            timer = min(due, key=lambda t: t.due_ms)
            self.now_ms = max(self.now_ms, timer.due_ms)
            timer._fire()
            fired += 1
        self.now_ms = target
        return fired
