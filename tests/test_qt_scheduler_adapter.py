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
    ],
)
def test_qt_scheduler(source):
    result = run_case(source)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip().endswith("OK")
