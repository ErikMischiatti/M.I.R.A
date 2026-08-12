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

Every `QObject` here must also be *destroyed* on the home thread, which is a
separate obligation from being created there. `QThreadPool` auto-deletes a
finished `QRunnable` on the pool thread it ran on, and that deletion releases
whatever the runnable referenced. A sender left under Python's ownership is
therefore destroyed on a pool thread, and `~QObject` tearing down a connection
there races the home thread delivering that same connection's queued event —
observed as an intermittent SIGSEGV in `submit`, roughly once per hundred calls.
`_sender_owner` exists to close that: a parented sender is owned by C++, so a
pool thread dropping its reference cannot run the destructor, and the deferred
delete in `_forget_receiver` runs it here instead.
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
        self._timers: set[QTimer] = set()
        # Parent for every per-submit sender, built on the home thread. Giving a
        # sender a C++ parent moves its lifetime out of Python's hands, which is
        # what keeps ~QObject on this thread; see _senders below.
        self._sender_owner = QObject()
        # Receiver -> its sender. Keyed by receiver so the receiver is held for
        # exactly as long as the completion it is waiting for.
        self._senders: dict[_CompletionReceiver, _WorkerSignals] = {}

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
        # Parented, so QThreadPool auto-deleting the worker on a pool thread
        # drops a reference that no longer decides when ~QObject runs.
        signals = _WorkerSignals(self._sender_owner)
        receiver = _CompletionReceiver(self, on_complete)
        self._senders[receiver] = signals
        signals.completed.connect(receiver.handle)
        self._thread_pool.start(_Worker(work, signals))

    def _forget_timer(self, timer: QTimer) -> None:
        self._timers.discard(timer)

    def _forget_receiver(self, receiver: _CompletionReceiver) -> None:
        signals = self._senders.pop(receiver, None)
        if signals is not None:
            # Deferred, not immediate: this runs inside the slot that this very
            # sender is delivering, and posting the delete to the home thread is
            # also what keeps the destructor off any pool thread.
            signals.deleteLater()

    def pending_timers(self) -> int:
        """Timers still held alive here. The leak surface the tests assert on."""
        return len(self._timers)

    def pending_completions(self) -> int:
        """Completions awaiting delivery. The leak surface the tests assert on."""
        return len(self._senders)
