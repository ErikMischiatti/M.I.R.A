"""Ctrl+C and SIGTERM on the running application: one clean shutdown, no stray windows.

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

import sys
import mira.main
from mira.application.composition import Application
from mira.ui.desktop_shell import DesktopShell

start, desktop_shutdown, application_shutdown = (
    DesktopShell.start, DesktopShell.shutdown, Application.shutdown)

def reporting_start(self):
    start(self)
    def ready():
        import os
        children = open(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read().split()
        print("CHILDREN", *children, flush=True)
        print("READY", flush=True)
    QTimer.singleShot(0, ready)

def reporting_desktop_shutdown(self):
    desktop_shutdown(self)
    shown = lambda surface: None if surface is None else surface.isVisible()
    print("desktop.shutdown", "window", shown(self.window), "notch", shown(self.notch),
          "animator", self.animator.is_running(), flush=True)

def reporting_application_shutdown(self):
    application_shutdown(self)
    print("application.shutdown", flush=True)

DesktopShell.start = reporting_start
DesktopShell.shutdown = reporting_desktop_shutdown
Application.shutdown = reporting_application_shutdown
mira.main.main(sys.argv[1:])
"""


def run_and_interrupt(tmp_path, *arguments: str, sig=signal.SIGINT) -> tuple[int, str, str]:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "MIRA_NOTCH_CONFIG": str(tmp_path / "notch.json"),
    }
    for name in ("MIRA_CAMERA", "MIRA_INTENT_ENGINE"):
        env.pop(name, None)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", DRIVER, *arguments],
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
        process.send_signal(sig)
        out, err = process.communicate(timeout=20)
    except BaseException:
        process.kill()
        process.communicate()
        raise
    return process.returncode, "".join(lines) + out, err


MODES = {"MIRA": (), "MIRA-NOTCH": ("--notch",)}
SIGNALS = {"SIGINT": signal.SIGINT, "SIGTERM": signal.SIGTERM}


@pytest.mark.parametrize("sig", list(SIGNALS))
@pytest.mark.parametrize("launcher", list(MODES))
def test_a_signal_shuts_the_application_down_cleanly(tmp_path, launcher, sig):
    returncode, out, err = run_and_interrupt(tmp_path, *MODES[launcher], sig=SIGNALS[sig])

    assert returncode == 0, err
    assert "Traceback" not in err
    assert "KeyboardInterrupt" not in err
    assert "application.shutdown" in out


@pytest.mark.parametrize("sig", list(SIGNALS))
@pytest.mark.parametrize(("launcher", "surfaces"), [
    ("MIRA", "window False notch None"),
    ("MIRA-NOTCH", "window None notch False"),
])
def test_a_signal_stops_the_surfaces_before_the_services(tmp_path, launcher, surfaces, sig):
    _, out, _ = run_and_interrupt(tmp_path, *MODES[launcher], sig=SIGNALS[sig])

    reported = [line for line in out.splitlines() if line.endswith("shutdown") or "shutdown " in line]
    assert reported == [
        f"desktop.shutdown {surfaces} animator False",
        "application.shutdown",
    ]


def test_sigterm_releases_the_runtime_and_leaves_no_child_process(tmp_path):
    returncode, out, err = run_and_interrupt(tmp_path, "--notch", sig=signal.SIGTERM)

    assert returncode == 0, err
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "mira"
    assert not (runtime / "runtime.sock").exists()  # the server closed its socket
    children = next(line for line in out.splitlines() if line.startswith("CHILDREN")).split()[1:]
    assert [pid for pid in children if Path(f"/proc/{pid}").exists()] == []


def test_a_second_sigterm_still_ends_a_shutdown_that_hangs(tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "MIRA_NOTCH_CONFIG": str(tmp_path / "notch.json")}
    hanging = DRIVER.replace(
        "def reporting_application_shutdown(self):\n",
        "def reporting_application_shutdown(self):\n    print('HANGING', flush=True); import time; time.sleep(60)\n",
    )
    process = subprocess.Popen([sys.executable, "-u", "-c", hanging, "--notch"], cwd=REPO_ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in process.stdout:
            if line.strip() == "READY":
                break
        process.send_signal(signal.SIGTERM)
        for line in process.stdout:
            if line.strip() == "HANGING":
                break
        process.send_signal(signal.SIGTERM)  # the default action again: it ends now
        process.communicate(timeout=20)
    except BaseException:
        process.kill()
        process.communicate()
        raise

    assert process.returncode == -signal.SIGTERM
