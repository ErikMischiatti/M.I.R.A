"""Application-level ownership and shutdown characterization."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mira.application.composition import build_application
from mira.domain.scheduler import ManualScheduler


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_application_shutdown_owns_scheduler_cleanup():
    scheduler = ManualScheduler()
    application = build_application(scheduler=scheduler)
    called: list[str] = []

    scheduler.call_later(10, lambda: called.append("timer"))
    scheduler.submit(lambda: "result", lambda _: called.append("complete"))

    application.shutdown()
    application.shutdown()

    assert scheduler.pending_timers() == 0
    assert scheduler.pending_work() == 0
    assert scheduler.advance(100) == 0
    assert scheduler.run_all() == 0
    assert called == []


def test_main_connects_application_shutdown_before_running(monkeypatch):
    import mira.main as main_module

    events: list[str] = []
    quit_callbacks: list = []

    class FakeSignal:
        def connect(self, callback):
            quit_callbacks.append(callback)

    class FakeApplicationRuntime:
        def start(self):
            events.append(f"application.start after {len(quit_callbacks)} quit callbacks")

        def shutdown(self):
            events.append("application.shutdown")

    runtime = FakeApplicationRuntime()

    class FakeQApplication:
        def __init__(self, argv):
            self.aboutToQuit = FakeSignal()

        def exec(self):
            for callback in quit_callbacks:
                callback()
            return 0

    class FakeDesktop:
        def __init__(self, application):
            events.append("desktop built")
            assert application is runtime

        def start(self):
            events.append("desktop.start")

        def shutdown(self):
            events.append("desktop.shutdown")

    monkeypatch.setattr(main_module, "QApplication", FakeQApplication)
    monkeypatch.setattr(main_module, "build_application", lambda: runtime)
    monkeypatch.setattr(main_module, "DesktopShell", FakeDesktop)

    with pytest.raises(SystemExit) as exit_info:
        main_module.main()

    assert exit_info.value.code == 0
    # Both shutdowns are connected before anything starts, and on quit the
    # surfaces stop before the services they present.
    assert events == [
        "desktop built",
        "application.start after 2 quit callbacks",
        "desktop.start",
        "desktop.shutdown",
        "application.shutdown",
    ]


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 not installed",
)
def test_window_close_shuts_down_pending_runtime_resources():
    source = """
import sys, threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from mira.application.composition import build_application
from mira.domain.models import IntentResult
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.main_window import MainWindow

started = threading.Event()
release = threading.Event()
responses = []

class BlockingIntentEngine:
    def infer(self, user_input):
        started.set()
        assert release.wait(5), "shutdown never released inference"
        return IntentResult(intent="greeting", confidence=1.0)

class RecordingWindow(MainWindow):
    def on_brain_response(self, response):
        responses.append(response)
        super().on_brain_response(response)

app = QApplication(sys.argv)
application = build_application()
application.brain.intent_engine = BlockingIntentEngine()
application.brain.listening_delay_ms = 0
app.aboutToQuit.connect(application.shutdown)
app.aboutToQuit.connect(release.set)
window = RecordingWindow(application, FaceAnimator(FaceController()))
window.show()

callbacks = []
timer = application.scheduler.call_later(60000, lambda: callbacks.append("timer"))
window.on_user_message_submitted("ciao")

def close_when_started():
    if not started.is_set():
        return
    poll.stop()
    window.close()

poll = QTimer()
poll.timeout.connect(close_when_started)
poll.start(1)
app.exec()

assert started.is_set(), "intent worker did not start"
assert timer.is_pending() is False
assert application.scheduler.pending_timers() == 0
assert application.scheduler.pending_completions() == 0
assert callbacks == []
assert responses == [], "a bound window callback ran after close"
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip().endswith("OK")
    assert "Signal source has been deleted" not in result.stderr
