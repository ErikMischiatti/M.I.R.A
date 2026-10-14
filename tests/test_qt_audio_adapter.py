"""Tests for the Qt Multimedia `AudioOutput` against a real event loop.

The voice policy is covered with a recording output in
`test_voice_controller.py`; this file covers what only Qt can answer: that a
missing device is a status rather than an exception, that clips are released
when they finish, and that the thread rule is enforced.

Each case runs in a subprocess with the offscreen platform, like
`test_qt_scheduler_adapter.py`. Only silent clips are ever played, and the
device-dependent case accepts both outcomes — a machine with a sound card
plays silence, one without (CI) must refuse cleanly.
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
from mira.adapters.qt_audio import QtAudioOutput
from mira.domain.vocalization import AudioOutputError, PcmClip

app = QCoreApplication(sys.argv)
SILENCE = PcmClip(22050, bytes(2 * 2205))  # 100 ms
"""


def run_case(source: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, "-c", PREAMBLE + source],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )


def assert_ok(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr[-3000:]
    assert "TypeError" not in result.stderr, result.stderr[-3000:]
    return result.stdout


def test_construction_touches_nothing_and_a_closed_output_refuses():
    out = assert_ok(run_case("""
output = QtAudioOutput()
assert output._probed is False and output._owner is None
output.close()
output.close()
assert output.available is False
try:
    output.play(SILENCE)
except AudioOutputError:
    print("refused")
"""))
    assert "refused" in out


def test_no_device_is_unavailable_and_play_raises_the_port_error():
    out = assert_ok(run_case("""
from PySide6 import QtMultimedia
from PySide6.QtMultimedia import QAudioDevice
QtMultimedia.QMediaDevices.defaultAudioOutput = staticmethod(lambda: QAudioDevice())

output = QtAudioOutput()
assert output.available is False
assert output.unavailable_reason == "no audio output device"
try:
    output.play(SILENCE)
except AudioOutputError as error:
    print("refused:", error)
output.close()
"""))
    assert "refused: no audio output device" in out


def test_a_clip_plays_and_is_released_or_is_refused_without_a_device():
    out = assert_ok(run_case("""
output = QtAudioOutput()
if not output.available:
    try:
        output.play(SILENCE)
    except AudioOutputError:
        print("no-device-refused")
    sys.exit(0)

output.play(SILENCE)
output.play(SILENCE)  # replaces the first; never two sinks
assert output._sink is not None

def check():
    print("released" if output._sink is None else "still-held")
    output.close()
    app.quit()

QTimer.singleShot(1500, check)
QTimer.singleShot(5000, lambda: sys.exit("event loop wedged"))
app.exec()
"""))
    assert "released" in out or "no-device-refused" in out


def test_calls_from_another_thread_are_rejected():
    out = assert_ok(run_case("""
output = QtAudioOutput()
errors = []

def worker():
    try:
        output.play(SILENCE)
    except RuntimeError as error:
        errors.append(str(error))

thread = threading.Thread(target=worker)
thread.start()
thread.join()
assert errors and "constructed it" in errors[0], errors
print("rejected")
"""))
    assert "rejected" in out
