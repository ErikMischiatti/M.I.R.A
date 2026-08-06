"""Qt-backed `Scheduler`: the production timing adapter.

This is the only place that knows the turn lifecycle runs on a Qt event loop.
The serialized context is the Qt main thread, delayed callbacks are `QTimer`
single shots, and submitted work runs on the global `QThreadPool` with its
result marshalled back through a queued signal.

Qt resolves an auto connection by comparing the emitting thread against the
*context object's* thread affinity — the receiver `QObject` for a bound slot,
and the sender for a plain callable. Measured, not assumed: a receiver built on
a loop-less thread is never delivered to even when both `connect` and `emit`
happen on the main thread, and the same holds for a plain callable whose sender
was built there.

`submit` creates both the sender (`_WorkerSignals`) and the receiver
(`_CompletionReceiver`) on the calling thread, so the completion is delivered on
whichever thread called `submit`, and only if that thread runs an event loop.
`_CompletionReceiver` is therefore not what makes the delivery queued — a plain
callable would land on the same thread — it exists for the `try/finally`
bookkeeping that releases it.

Both entry points assert they are called from the thread that built the
scheduler. Without that check, `submit` from a loop-less thread silently never
delivers and leaks its receiver, and a `QTimer` created there never fires —
failures whose only symptom is a wedged turn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

T = TypeVar("T")


class _QtTimerHandle:
    def __init__(self, scheduler: QtScheduler, timer: QTimer) -> None:
        self._scheduler = scheduler
        self._timer = timer

    def cancel(self) -> None:
        # stop() on a never-started or already-fired timer is a no-op.
        self._timer.stop()
        self._scheduler._forget_timer(self._timer)

    def is_pending(self) -> bool:
        return self._timer.isActive()


class _WorkerSignals(QObject):
    completed = Signal(object)


class _WorkFailed:
    """Sentinel for work that broke the port's no-raise obligation."""

    def __init__(self, error: BaseException) -> None:
        self.error = error


class _Worker(QRunnable):
    def __init__(self, work: Callable[[], object], signals: _WorkerSignals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

    def run(self) -> None:
        # The port requires work that does not raise. If that is broken anyway,
        # still complete the round trip: otherwise the receiver is never
        # released and the failure is buried on a worker thread.
        try:
            result: object = self._work()
        except BaseException as error:  # noqa: BLE001 - deliberately total
            result = _WorkFailed(error)
        self._signals.completed.emit(result)


class _CompletionReceiver(QObject):
    """Main-thread receiver; its affinity is what forces a queued delivery."""

    def __init__(self, scheduler: QtScheduler, on_complete: Callable[[object], None]) -> None:
        super().__init__()
        self._scheduler = scheduler
        self._on_complete = on_complete

    @Slot(object)
    def handle(self, result: object) -> None:
        try:
            if isinstance(result, _WorkFailed):
                # Surface the contract violation on the serialized context
                # rather than committing a turn built from nothing.
                raise result.error
            self._on_complete(result)
        finally:
            self._scheduler._forget_receiver(self)


class QtScheduler:
    """`Scheduler` implementation for the Qt main thread."""

    def __init__(self) -> None:
        self._thread_pool = QThreadPool.globalInstance()
        # The serialized context is whichever thread built this scheduler.
        self._home_thread = QThread.currentThread()
        # Qt objects are owned here; without a reference they would be
        # collected before firing or before delivering a completion.
        # _WorkerSignals is deliberately absent: the worker owns it, and Qt's
        # queued event carries its own copy of the arguments, so the signal
        # object may die once emit() has posted. Pinning it here would leak.
        self._timers: set[QTimer] = set()
        self._receivers: set[_CompletionReceiver] = set()

    def _require_home_thread(self, operation: str) -> None:
        if QThread.currentThread() is not self._home_thread:
            raise RuntimeError(
                f"QtScheduler.{operation} must be called from the thread that "
                "constructed the scheduler; elsewhere the callback would never "
                "be delivered"
            )

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> _QtTimerHandle:
        self._require_home_thread("call_later")
        timer = QTimer()
        timer.setSingleShot(True)
        self._timers.add(timer)
        # Two connections rather than one lambda: if `callback` raises, Qt still
        # runs the next slot, so the timer is released either way. Folding them
        # leaks the timer on a raising callback.
        timer.timeout.connect(callback)
        timer.timeout.connect(lambda: self._forget_timer(timer))
        timer.start(delay_ms)
        return _QtTimerHandle(self, timer)

    def submit(self, work: Callable[[], T], on_complete: Callable[[T], None]) -> None:
        self._require_home_thread("submit")
        signals = _WorkerSignals()
        receiver = _CompletionReceiver(self, on_complete)
        self._receivers.add(receiver)
        signals.completed.connect(receiver.handle)
        self._thread_pool.start(_Worker(work, signals))

    def _forget_timer(self, timer: QTimer) -> None:
        self._timers.discard(timer)

    def _forget_receiver(self, receiver: _CompletionReceiver) -> None:
        self._receivers.discard(receiver)

    def pending_timers(self) -> int:
        """Timers still held alive here. The leak surface the tests assert on."""
        return len(self._timers)

    def pending_completions(self) -> int:
        """Completions awaiting delivery. The leak surface the tests assert on."""
        return len(self._receivers)
