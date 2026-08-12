"""Tests for the Qt scheduler adapter against a real event loop.

`ManualScheduler` covers the lifecycle logic deterministically; this file
covers the one thing only Qt can answer: that `call_later` fires on the event
loop and that `submit` runs work off the main thread and delivers the result
back *on* it. That main-thread guarantee is the whole reason the adapter exists,
so it is asserted directly by comparing thread identities.

Each case runs in a subprocess with the offscreen platform, so a stalled event
loop fails on a timeout instead of wedging the suite.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"
)

PREAMBLE = """
import sys, threading
from PySide6.QtCore import QCoreApplication, QTimer
from mira.adapters.qt_scheduler import QtScheduler

app = QCoreApplication(sys.argv)
scheduler = QtScheduler()
main_thread = threading.current_thread().ident
observed = {}


def finish():
    app.quit()


# Hard stop so a stalled loop fails loudly rather than hanging.
QTimer.singleShot(15000, app.quit)
"""

CALL_LATER_CASE = PREAMBLE + """
def on_timer():
    observed["fired_on"] = threading.current_thread().ident
    finish()


handle = scheduler.call_later(50, on_timer)
observed["pending_before"] = handle.is_pending()
app.exec()

assert observed.get("pending_before") is True, "timer should be pending before firing"
assert observed.get("fired_on") == main_thread, "call_later must fire on the main thread"
print("OK")
"""

SUBMIT_CASE = PREAMBLE + """
def work():
    observed["work_on"] = threading.current_thread().ident
    return "computed"


def on_complete(result):
    observed["complete_on"] = threading.current_thread().ident
    observed["result"] = result
    finish()


scheduler.submit(work, on_complete)
app.exec()

assert observed.get("result") == "computed"
assert observed.get("work_on") is not None, "work never ran"
assert observed["work_on"] != main_thread, "work must run off the main thread"
assert observed["complete_on"] == main_thread, "completion must run on the main thread"
print("OK")
"""

CANCEL_CASE = PREAMBLE + """
fired = []
handle = scheduler.call_later(50, lambda: fired.append(1))
handle.cancel()
assert handle.is_pending() is False

QTimer.singleShot(300, app.quit)
app.exec()

assert fired == [], "a cancelled timer must not fire"
print("OK")
"""

# This case deliberately discards its handles, which is what production does.
# It is therefore the only test that would catch QtScheduler dropping its
# internal timer reference; do not "tidy" it by keeping the handles.
ORDERING_CASE = PREAMBLE + """
order = []


def make(name):
    def run():
        order.append(name)
        if len(order) == 3:
            finish()
    return run


scheduler.call_later(150, make("third"))
scheduler.call_later(50, make("first"))
scheduler.call_later(100, make("second"))
app.exec()

assert order == ["first", "second", "third"], order
print("OK")
"""


# A contract violation must still release the receiver and surface the error on
# the serialized context, rather than leaking and stalling the turn silently.
WORK_RAISES_CASE = PREAMBLE + """
completed = []


def bad():
    raise ValueError("contract violation")


scheduler.submit(bad, completed.append)
QTimer.singleShot(600, app.quit)
app.exec()

assert completed == [], "on_complete must not run for failed work"
assert scheduler.pending_completions() == 0, "receiver leaked on the failure path"
print("OK")
"""


# submit/call_later from a loop-less thread would silently never deliver, so the
# adapter must reject it rather than wedge the turn.
WRONG_THREAD_CASE = PREAMBLE + """
import threading

errors = []


def from_worker():
    for op, call in (
        ("submit", lambda: scheduler.submit(lambda: 1, lambda r: None)),
        ("call_later", lambda: scheduler.call_later(10, lambda: None)),
    ):
        try:
            call()
            errors.append(f"{op} did not raise")
        except RuntimeError as exc:
            assert op in str(exc), exc

t = threading.Thread(target=from_worker)
t.start()
t.join()

# No app.exec() here: the guard raises synchronously, so running the loop would
# only wait out the hard-stop timer.
assert errors == [], errors
assert scheduler.pending_completions() == 0, "receiver leaked from the rejected call"
print("OK")
"""


# One submit per event-loop cycle, repeated. This is the sequence that made the
# crash reproducible: a single completion delivered, then the loop exits while
# the pool thread that ran the work is still retiring its sender. At 500 cycles
# the unfixed adapter segfaulted in essentially every process, against roughly
# 1% for a single submit, so this is the end-to-end guard.
REPEATED_SUBMIT_CASE = PREAMBLE + """
CYCLES = 500

for i in range(CYCLES):
    got = []

    def on_complete(result, got=got):
        assert threading.current_thread().ident == main_thread, "completion off main"
        got.append(result)
        app.quit()

    scheduler.submit(lambda: "x", on_complete)
    app.exec()
    assert got == ["x"], f"cycle {i} delivered {got}"

assert scheduler.pending_completions() == 0, "receivers leaked across cycles"
print("OK")
"""


# Creating a QObject on the home thread is only half the obligation; it must be
# destroyed there too. The sender is referenced by the QRunnable, which
# QThreadPool auto-deletes on a pool thread, so if that deletion is what destroys
# the sender then ~QObject tears down the connection off the home thread and
# races the delivery of its own queued event. Asserting on the destructor's
# thread pins that invariant directly rather than hunting an intermittent crash.
SENDER_LIFETIME_CASE = PREAMBLE + """
from PySide6.QtCore import Qt
from mira.adapters import qt_scheduler

destroyed_on = []
original_init = qt_scheduler._WorkerSignals.__init__


def recording_init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    # Direct, so the handler runs inside ~QObject on the thread destroying it.
    self.destroyed.connect(
        lambda *_: destroyed_on.append(threading.current_thread().ident),
        Qt.ConnectionType.DirectConnection,
    )


qt_scheduler._WorkerSignals.__init__ = recording_init

CYCLES = 50
for i in range(CYCLES):
    got = []

    def on_complete(result, got=got):
        got.append(result)
        app.quit()

    scheduler.submit(lambda: "x", on_complete)
    app.exec()
    assert got == ["x"], f"cycle {i} delivered {got}"

# Drain the deferred deletes the completions queued.
QTimer.singleShot(200, app.quit)
app.exec()

# Non-vacuous on purpose: an adapter that simply kept every sender alive would
# record nothing here and satisfy an "all on the main thread" assertion
# trivially, while still growing without bound.
assert len(destroyed_on) == CYCLES, f"only {len(destroyed_on)}/{CYCLES} senders destroyed"
off_main = [t for t in destroyed_on if t != main_thread]
assert off_main == [], f"{len(off_main)} of {CYCLES} senders destroyed off the main thread"
print("OK")
"""


# --- call_later timer lifetime ------------------------------------------
#
# PySide holds a strong reference to every callable connected to a signal, in
# bookkeeping owned by the emitter. `call_later` used to connect a lambda closing
# over its own QTimer, which made timer and callable refer to each other through
# a hop on the C++ side that Python's collector cannot see, so no timer was ever
# reclaimed. `pending_timers()` reported zero throughout, which is why these
# cases count live QTimer objects instead of trusting that number.
#
# TOLERANCE covers the QTimer objects the harness itself has in flight
# (`QTimer.singleShot` for the hard stop and the drain step). The defect these
# cases guard against grew one QTimer per call — 500 at COUNT below, two orders
# of magnitude above the tolerance — so it cannot hide inside it.
TIMER_LIFETIME_PREAMBLE = PREAMBLE + """
import gc

COUNT = 500
TOLERANCE = 10


def live_timers():
    gc.collect()
    return sum(1 for obj in gc.get_objects() if isinstance(obj, QTimer))


def owned_timers():
    # C++ children of the scheduler's owner: exact, with no ambient timers.
    return [c for c in scheduler._qt_owner.children() if isinstance(c, QTimer)]
"""


TIMER_FIRED_LIFETIME_CASE = TIMER_LIFETIME_PREAMBLE + """
fired = []
baseline = live_timers()

for _ in range(COUNT):
    scheduler.call_later(0, lambda: fired.append(1))

observed["owned_while_pending"] = len(owned_timers())


def measure():
    # Recorded rather than asserted: PySide prints a traceback for an exception
    # raised inside a slot but the process still exits 0, so an assertion here
    # would be a false pass. Every case in this file therefore asserts after
    # app.exec() has returned.
    observed["fired"] = len(fired)
    observed["pending"] = scheduler.pending_timers()
    observed["owned"] = len(owned_timers())
    observed["grew"] = live_timers() - baseline
    app.quit()


# Drain the deferred deletes the released timers queued, then measure.
QTimer.singleShot(500, measure)
app.exec()

assert observed.get("owned_while_pending") == COUNT, observed.get("owned_while_pending")
assert "grew" in observed, "the drain step never ran"
assert observed["fired"] == COUNT, f"only {observed['fired']}/{COUNT} callbacks ran"
assert observed["pending"] == 0, f"{observed['pending']} still pending"
assert observed["owned"] == 0, f"{observed['owned']} timers still owned by the scheduler"
assert observed["grew"] <= TOLERANCE, (
    f"live QTimer count grew by {observed['grew']} over {COUNT} fired timers"
)
print("OK")
"""


TIMER_CANCELLED_LIFETIME_CASE = TIMER_LIFETIME_PREAMBLE + """
fired = []
baseline = live_timers()

handles = [scheduler.call_later(60000, lambda: fired.append(1)) for _ in range(COUNT)]
observed["owned_while_pending"] = len(owned_timers())
observed["all_pending"] = all(h.is_pending() for h in handles)

for handle in handles:
    handle.cancel()

observed["pending_after_cancel"] = scheduler.pending_timers()
observed["none_pending"] = all(h.is_pending() is False for h in handles)

# Idempotent: further cancels must neither raise nor resurrect anything.
for handle in handles:
    handle.cancel()
    handle.cancel()
observed["idempotent"] = all(h.is_pending() is False for h in handles)

handles.clear()


def measure():
    observed["fired"] = len(fired)
    observed["owned"] = len(owned_timers())
    observed["grew"] = live_timers() - baseline
    app.quit()


QTimer.singleShot(500, measure)
app.exec()

assert observed["owned_while_pending"] == COUNT, observed["owned_while_pending"]
assert observed["all_pending"] is True, "a long-delay timer should be pending"
assert observed["pending_after_cancel"] == 0, observed["pending_after_cancel"]
assert observed["none_pending"] is True, "a cancelled timer must not be pending"
assert observed["idempotent"] is True, "cancel is not idempotent"
assert "grew" in observed, "the drain step never ran"
assert observed["fired"] == 0, "a cancelled timer must not fire"
assert observed["owned"] == 0, f"{observed['owned']} cancelled timers still owned"
assert observed["grew"] <= TOLERANCE, (
    f"live QTimer count grew by {observed['grew']} over {COUNT} cancelled timers"
)
print("OK")
"""


# A QTimer must be destroyed on its home thread, not merely created there. The
# submit path had exactly this fault; asserting the destructor's thread here pins
# the same invariant for timers, and asserting that destructions were *observed*
# is what stops a return to never destroying them at all.
TIMER_DESTRUCTION_THREAD_CASE = TIMER_LIFETIME_PREAMBLE + """
from PySide6.QtCore import Qt

destroyed_on = []
original_init = QTimer.__init__


def recording_init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    # Direct, so the handler runs inside ~QObject on the destroying thread.
    self.destroyed.connect(
        lambda *_: destroyed_on.append(threading.current_thread().ident),
        Qt.ConnectionType.DirectConnection,
    )


QTimer.__init__ = recording_init

fired = []
# Half fire, half are cancelled: both release paths must destroy on this thread.
for _ in range(COUNT // 2):
    scheduler.call_later(0, lambda: fired.append(1))
for handle in [scheduler.call_later(60000, lambda: None) for _ in range(COUNT // 2)]:
    handle.cancel()


def measure():
    observed["fired"] = len(fired)
    observed["destroyed"] = len(destroyed_on)
    observed["off_main"] = len([t for t in destroyed_on if t != main_thread])
    app.quit()


QTimer.singleShot(500, measure)
app.exec()

assert "destroyed" in observed, "the drain step never ran"
assert observed["fired"] == COUNT // 2, f"only {observed['fired']}/{COUNT // 2} fired"
# Non-vacuous on purpose: never destroying a timer would satisfy an
# "all on the home thread" assertion trivially, which is the leak this fixes.
assert observed["destroyed"] >= COUNT, (
    f"only {observed['destroyed']} of {COUNT} timers were destroyed at all"
)
assert observed["off_main"] == 0, (
    f"{observed['off_main']} timers destroyed off the home thread"
)
print("OK")
"""


# Cancelling from a foreign thread would stop a timer across threads and destroy
# it off its home thread, so it is rejected like submit and call_later are.
CANCEL_WRONG_THREAD_CASE = PREAMBLE + """
import threading

handle = scheduler.call_later(60000, lambda: None)
errors = []


def from_worker():
    try:
        handle.cancel()
        errors.append("cancel did not raise")
    except RuntimeError as exc:
        assert "cancel" in str(exc), exc


t = threading.Thread(target=from_worker)
t.start()
t.join()

assert errors == [], errors
# The rejected call must have changed nothing.
assert handle.is_pending() is True, "the timer was cancelled by the rejected call"
assert scheduler.pending_timers() == 1, scheduler.pending_timers()

# Cancelling from the home thread still works.
handle.cancel()
assert handle.is_pending() is False
assert scheduler.pending_timers() == 0
print("OK")
"""


def run_case(source: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(CALL_LATER_CASE, id="call_later-fires-on-main-thread"),
        pytest.param(SUBMIT_CASE, id="submit-runs-off-main-completes-on-main"),
        pytest.param(CANCEL_CASE, id="cancel-prevents-firing"),
        pytest.param(ORDERING_CASE, id="timers-fire-in-delay-order"),
        pytest.param(WORK_RAISES_CASE, id="failed-work-releases-receiver"),
        pytest.param(WRONG_THREAD_CASE, id="rejects-calls-from-a-foreign-thread"),
        pytest.param(REPEATED_SUBMIT_CASE, id="repeated-submits-survive-many-loop-cycles"),
        pytest.param(SENDER_LIFETIME_CASE, id="sender-is-destroyed-on-the-main-thread"),
        pytest.param(TIMER_FIRED_LIFETIME_CASE, id="fired-timers-are-released"),
        pytest.param(TIMER_CANCELLED_LIFETIME_CASE, id="cancelled-timers-are-released"),
        pytest.param(TIMER_DESTRUCTION_THREAD_CASE, id="timers-are-destroyed-on-the-main-thread"),
        pytest.param(CANCEL_WRONG_THREAD_CASE, id="rejects-cancel-from-a-foreign-thread"),
    ],
)
def test_qt_scheduler(source):
    result = run_case(source)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip().endswith("OK")
