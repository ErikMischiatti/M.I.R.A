"""Device queries stop at shutdown: the runner, the providers, the service, Qt.

Real child processes (``sleep``, and a fake ``adb`` that hangs) stand in for
a stuck device query; every wait is a bounded ``join`` or ``waitForDone``
that returns as soon as the work ends, never a fixed delay. No phone and no
real adb are needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mira.adapters import adb as adb_module
from mira.adapters import upower_phone
from mira.adapters.adb import LIST_TIMEOUT_S, AdbDeviceInfoProvider
from mira.adapters.subprocesses import CancellableRunner, Cancelled
from mira.adapters.upower_phone import UPowerPhoneProvider
from mira.domain.device_info import InfoResult
from mira.domain.devices import DeviceCategory
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX processes")

PIXEL = DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Google Pixel 8", "/d/usb3/3-2",
                    category=DeviceCategory.SMARTPHONE, services=frozenset({"adb", "usbmux"}))


def in_thread(work):
    """Run ``work`` on a worker thread, as `Scheduler.submit` does; its outcome lands in ``box``."""
    box = {}

    def run():
        try:
            box["result"] = work()
        except BaseException as error:  # noqa: BLE001 - reported to the test
            box["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def wait_for_child(runner: CancellableRunner, deadline_s: float = 5.0) -> None:
    """Until the runner has started its child (bounded; returns as soon as it has)."""
    end = time.monotonic() + deadline_s
    while not runner._children:
        assert time.monotonic() < end, "the child never started"
        time.sleep(0.005)


def hanging_tool(tmp_path: Path, name: str) -> str:
    script = tmp_path / name
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(0o755)
    return str(script)


# --- The runner ------------------------------------------------------------------------------


def test_the_runner_runs_a_command_like_subprocess_run():
    completed = CancellableRunner()(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout=5)

    assert (completed.returncode, completed.stdout, completed.stderr) == (3, "out\n", "err\n")


def test_cancel_stops_a_running_child_at_once_and_reaps_it():
    runner = CancellableRunner()
    thread, box = in_thread(lambda: runner(["sleep", "30"], timeout=60))
    wait_for_child(runner)
    child = next(iter(runner._children))

    started = time.monotonic()
    runner.cancel()
    thread.join(timeout=5)

    assert not thread.is_alive() and time.monotonic() - started < 5
    assert isinstance(box.get("error"), Cancelled)
    assert child.returncode is not None and runner._children == set()  # killed and reaped


def test_after_cancel_nothing_starts(tmp_path):
    runner = CancellableRunner()
    runner.cancel()
    runner.cancel()  # idempotent
    marker = tmp_path / "should-not-exist"

    with pytest.raises(Cancelled):
        runner(["touch", str(marker)], timeout=5)
    assert not marker.exists()


def test_a_timeout_still_kills_and_reaps_the_child():
    runner = CancellableRunner()

    with pytest.raises(subprocess.TimeoutExpired):
        runner(["sleep", "30"], timeout=0.2)
    assert runner._children == set()


def test_cancel_racing_many_short_calls_raises_only_cancelled_and_leaves_no_child():
    runner = CancellableRunner()
    outcomes = []
    threads = []
    for index in range(30):
        thread, box = in_thread(lambda: runner(["true"], timeout=5))
        threads.append((thread, box))
        if index == 15:
            runner.cancel()
    for thread, box in threads:
        thread.join(timeout=10)
        outcomes.append(type(box["error"]).__name__ if "error" in box else "ok")

    assert set(outcomes) <= {"ok", "Cancelled"} and "Cancelled" in outcomes
    assert runner._children == set()


def test_cancel_leaves_other_processes_alone():
    unrelated = subprocess.Popen(["sleep", "30"])  # another program's process, e.g. adb's server
    try:
        runner = CancellableRunner()
        thread, _ = in_thread(lambda: runner(["sleep", "30"], timeout=60))
        wait_for_child(runner)

        runner.cancel()
        thread.join(timeout=5)

        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


# --- The providers ---------------------------------------------------------------------------


def test_a_hung_adb_query_returns_promptly_when_cancelled(tmp_path):
    provider = AdbDeviceInfoProvider(which=lambda name: hanging_tool(tmp_path, "adb"))
    thread, box = in_thread(lambda: provider.query(PIXEL))
    wait_for_child(provider._runner)

    started = time.monotonic()
    provider.cancel()
    thread.join(timeout=5)

    assert not thread.is_alive() and time.monotonic() - started < LIST_TIMEOUT_S
    assert box["result"] == InfoResult(problem=adb_module.CANCELLED)
    assert provider.query(PIXEL) == InfoResult(problem=adb_module.CANCELLED)  # and stays stopped


def test_a_hung_upower_query_returns_promptly_when_cancelled(tmp_path):
    provider = UPowerPhoneProvider(which=lambda name: hanging_tool(tmp_path, "upower"))
    thread, box = in_thread(lambda: provider.query(PIXEL))
    wait_for_child(provider._runner)

    provider.cancel()
    thread.join(timeout=5)

    assert box["result"] == InfoResult(problem=upower_phone.CANCELLED)


def test_without_adb_cancelling_changes_nothing():
    provider = AdbDeviceInfoProvider(which=lambda name: None)

    assert provider.query(PIXEL) == InfoResult(problem=adb_module.NOT_INSTALLED)
    provider.cancel()
    assert provider.query(PIXEL).problem == adb_module.CANCELLED


# --- The running application: the real scheduler and thread pool ------------------------------


@pytest.fixture
def qt_application():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_shutdown_does_not_wait_for_a_hung_adb(qt_application, tmp_path):
    from PySide6.QtCore import QThreadPool

    from mira.application.composition import build_application
    from mira.core.notice_producers import DEVICE_CHANGED_EVENT

    provider = AdbDeviceInfoProvider(which=lambda name: hanging_tool(tmp_path, "adb"))
    application = build_application(device_info_providers=[provider])  # the production QtScheduler
    updates = []
    application.event_bus.subscribe("device_info", updates.append)
    application.start()
    application.event_bus.emit(DEVICE_CHANGED_EVENT, PIXEL)
    wait_for_child(provider._runner)

    started = time.monotonic()
    application.shutdown()
    finished = QThreadPool.globalInstance().waitForDone(5000)

    assert finished and time.monotonic() - started < LIST_TIMEOUT_S
    qt_application.processEvents()
    assert updates == []  # the cancelled answer reaches nobody


def test_a_query_answering_after_the_shell_is_destroyed_calls_nothing_dead(qt_application):
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool

    from mira.application.composition import build_application
    from mira.core.notice_producers import DEVICE_CHANGED_EVENT
    from mira.domain.device_info import DeviceInfo
    from mira.ui.desktop_shell import DesktopShell, Surface

    release = threading.Event()

    class Blocking:
        name = "blocking"
        cancelled = False

        def supports(self, device):
            return True

        def query(self, device):
            release.wait(timeout=10)  # answers only when the test says, after teardown
            return InfoResult(DeviceInfo(battery_percent=50))

        def cancel(self):
            Blocking.cancelled = True

    errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda *exc: errors.append(exc)
    try:
        application = build_application(device_info_providers=[Blocking()])
        desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
        desktop.start()
        application.start()
        application.event_bus.emit(DEVICE_CHANGED_EVENT, PIXEL)

        desktop.shutdown()
        application.shutdown()
        for obj in (desktop.notch, desktop.window, desktop.animator):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        release.set()  # now the worker answers, into a torn-down application
        assert QThreadPool.globalInstance().waitForDone(5000)
        qt_application.processEvents()
    finally:
        sys.excepthook = previous_hook

    assert Blocking.cancelled and errors == []
