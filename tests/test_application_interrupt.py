"""Ctrl+C on the running application: one clean shutdown, no stray windows.

Each case runs the real `mira.main` in a subprocess on the offscreen
platform, with the shell and application shutdowns wrapped only to report
what they did. SIGINT is restored to its default in the child, because a
parent that ignores it (a background job) would otherwise pass that on.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"
)

REPO_ROOT = Path(__file__).resolve().parents[1]

DRIVER = """
from PySide6.QtCore import QTimer

import mira.main
from mira.application.composition import Application
from mira.ui.desktop_shell import DesktopShell

start, desktop_shutdown, application_shutdown = (
    DesktopShell.start, DesktopShell.shutdown, Application.shutdown)

def reporting_start(self):
    start(self)
    QTimer.singleShot(0, lambda: print("READY", flush=True))

def reporting_desktop_shutdown(self):
    desktop_shutdown(self)
    print("desktop.shutdown", "window", self.window.isVisible(), "notch", self.notch.isVisible(),
          "animator", self.animator.is_running(), flush=True)

def reporting_application_shutdown(self):
    application_shutdown(self)
    print("application.shutdown", flush=True)

DesktopShell.start = reporting_start
DesktopShell.shutdown = reporting_desktop_shutdown
Application.shutdown = reporting_application_shutdown
mira.main.main()
"""


def run_and_interrupt(tmp_path) -> tuple[int, str, str]:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "MIRA_NOTCH_CONFIG": str(tmp_path / "notch.json"),
    }
    for name in ("MIRA_CAMERA", "MIRA_INTENT_ENGINE"):
        env.pop(name, None)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", DRIVER],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    try:
        lines = []
        for line in process.stdout:
            lines.append(line)
            if line.strip() == "READY":
                break
        else:
            pytest.fail(f"never became ready: {''.join(lines)}{process.stderr.read()}")
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=20)
    except BaseException:
        process.kill()
        process.communicate()
        raise
    return process.returncode, "".join(lines) + out, err


def test_ctrl_c_shuts_the_application_down_cleanly(tmp_path):
    returncode, out, err = run_and_interrupt(tmp_path)

    assert returncode == 0, err
    assert "Traceback" not in err
    assert "KeyboardInterrupt" not in err


def test_ctrl_c_stops_the_surfaces_before_the_services(tmp_path):
    _, out, _ = run_and_interrupt(tmp_path)

    reported = [line for line in out.splitlines() if line.endswith("shutdown") or "shutdown " in line]
    assert reported == [
        "desktop.shutdown window False notch False animator False",
        "application.shutdown",
    ]
