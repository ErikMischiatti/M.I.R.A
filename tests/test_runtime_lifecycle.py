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


@pytest.mark.parametrize(("argv", "mode", "notch_only"), [
    ([], "main window", []),
    # The notch runtime is not ended by closing its (optional) main window.
    (["--notch"], "notch", ["quit on last window closed: False"]),
])
def test_main_connects_application_shutdown_before_running(monkeypatch, tmp_path, argv, mode, notch_only):
    import mira.main as main_module

    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(tmp_path / "absent.json"))

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

        def setQuitOnLastWindowClosed(self, quits):
            events.append(f"quit on last window closed: {quits}")

        def exec(self):
            for callback in quit_callbacks:
                callback()
            return 0

    class FakeDesktop:
        def __init__(self, application, *, mode, **_notch):
            events.append(f"desktop built ({mode.value})")
            assert application is runtime

        def start(self):
            events.append("desktop.start")

        def shutdown(self):
            events.append("desktop.shutdown")

    monkeypatch.setattr(main_module, "QApplication", FakeQApplication)
    built_with = []
    monkeypatch.setattr(main_module, "build_application",
                        lambda **options: (built_with.append(sorted(options)), runtime)[1])
    monkeypatch.setattr(main_module, "DesktopShell", FakeDesktop)
    monkeypatch.setattr(main_module, "quit_on_interrupt", lambda app: events.append("interrupt handler"))

    with pytest.raises(SystemExit) as exit_info:
        main_module.main(argv)

    assert exit_info.value.code == 0
    # Both shutdowns are connected before anything starts, and on quit the
    # surfaces stop before the services they present.
    assert events == [
        *notch_only,
        f"desktop built ({mode})",
        "application.start after 2 quit callbacks",
        "desktop.start",
        "interrupt handler",
        "desktop.shutdown",
        "application.shutdown",
    ]
    # Only the notch runtime watches the system.
    assert built_with == ([["system_event_sources"]] if mode == "notch" else [[]])


def _run_main_capturing_notch_settings(monkeypatch, argv=("--notch",)):
    """Run `main` with fakes around it; return the settings handed to the shell.

    None when the shell was given no notch settings at all.
    """
    import mira.main as main_module

    captured = {}

    class FakeSignal:
        def connect(self, callback):
            pass

    class FakeQApplication:
        def __init__(self, argv):
            self.aboutToQuit = FakeSignal()

        def setQuitOnLastWindowClosed(self, quits):
            pass

        def exec(self):
            return 0

    class FakeRuntime:
        def __init__(self, **_options):
            pass

        def start(self):
            pass

        def shutdown(self):
            pass

    class FakeDesktop:
        def __init__(self, application, *, mode, notch_settings=None, notch_session=None):
            captured["settings"] = notch_settings
            captured["mode"] = mode
            # The settings window saves to the same file the settings came from.
            if notch_session is not None:
                assert notch_session.startup_settings == notch_settings

        def start(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(main_module, "QApplication", FakeQApplication)
    monkeypatch.setattr(main_module, "build_application", FakeRuntime)
    monkeypatch.setattr(main_module, "DesktopShell", FakeDesktop)
    monkeypatch.setattr(main_module, "quit_on_interrupt", lambda app: None)
    with pytest.raises(SystemExit):
        main_module.main(list(argv))
    return captured["settings"]


def test_main_opens_the_notch_with_the_saved_configuration(monkeypatch, tmp_path, capsys):
    import dataclasses

    from mira.ui.notch.config_store import NotchSettings, save_settings
    from mira.ui.notch.screen_selection import MonitorIdentity, MonitorPreference
    from mira.ui.notch.visual_config import NotchVisualConfig

    saved = NotchSettings(
        visual=dataclasses.replace(NotchVisualConfig(), transition_duration_ms=420),
        monitor=MonitorPreference(MonitorIdentity(name="DP-3")),
    )
    path = save_settings(saved, tmp_path / "notch.json")
    # The variable `MIRA-NOTCH` and `MIRA-TUNE` honour too: one config path.
    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(path))

    assert _run_main_capturing_notch_settings(monkeypatch) == saved
    assert f"[notch] configuration: {path} (saved)" in capsys.readouterr().out


def test_main_falls_back_to_the_defaults_for_an_invalid_configuration(monkeypatch, tmp_path, capsys):
    from mira.ui.notch.config_store import NotchSettings

    path = tmp_path / "notch.json"
    path.write_text("{ not json")
    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(path))

    assert _run_main_capturing_notch_settings(monkeypatch) == NotchSettings()
    output = capsys.readouterr()
    assert "(invalid fallback)" in output.out
    assert "saved configuration ignored, using production defaults" in output.err
    assert path.read_text() == "{ not json"  # never rewritten


def test_main_window_mode_never_reads_the_notch_configuration(monkeypatch, tmp_path, capsys):
    path = tmp_path / "notch.json"
    path.write_text("{ not json")
    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(path))

    assert _run_main_capturing_notch_settings(monkeypatch, argv=()) is None
    output = capsys.readouterr()
    assert "[notch]" not in output.out + output.err


def test_an_explicit_config_path_reaches_the_notch(monkeypatch, tmp_path, capsys):
    import dataclasses

    from mira.ui.notch.config_store import NotchSettings, save_settings
    from mira.ui.notch.visual_config import NotchVisualConfig

    saved = NotchSettings(visual=dataclasses.replace(NotchVisualConfig(), transition_duration_ms=300))
    path = save_settings(saved, tmp_path / "elsewhere.json")
    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(tmp_path / "absent.json"))

    assert _run_main_capturing_notch_settings(monkeypatch, argv=("--notch", "--config", str(path))) == saved


def test_main_uses_the_defaults_when_no_configuration_exists(monkeypatch, tmp_path):
    from mira.ui.notch.config_store import NotchSettings

    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(tmp_path / "absent.json"))

    assert _run_main_capturing_notch_settings(monkeypatch) == NotchSettings()


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
