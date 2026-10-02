"""The PipeWire monitor survives its child dying, and stops when told to.

A script stands in for ``pw-dump``; a recording ``call_later`` stands in for
the restart timer, so backoff is asserted exactly and nothing waits for real
seconds. The tracker's reconciliation is checked on its own.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

from mira.adapters.pipewire_audio import (
    RESTART_INITIAL_MS,
    RESTART_MAX_MS,
    STABLE_MS,
    PipeWireAudioSource,
    PipeWireTracker,
)
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind


@pytest.fixture(scope="module", autouse=True)
def application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def node(object_id, media_class, name, description):
    return {"id": object_id, "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": media_class, "node.name": name, "node.description": description}}}


SPEAKER = node(66, "Audio/Sink", "alsa_output.speaker", "Speaker")
HEADPHONES = node(91, "Audio/Sink", "bluez_output.1", "Headphones")
MIC = node(128, "Audio/Source", "alsa_input.mic", "Microphones")


def default(sink):
    return {"id": 40, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
            "metadata": [{"key": "default.audio.sink", "value": {"name": sink}}]}


class Timers:
    """A recording `call_later`: delays are kept, firing is the test's call."""

    def __init__(self):
        self.scheduled: list[tuple[int, object]] = []

    def __call__(self, delay_ms, callback):
        timers = self

        class Handle:
            cancelled = False

            def cancel(self):
                self.cancelled = True

        handle = Handle()
        self.scheduled.append((delay_ms, callback, handle))
        return handle

    @property
    def delays(self):
        return [delay for delay, _, _ in self.scheduled]

    def fire_last(self):
        delay, callback, handle = self.scheduled[-1]
        if not handle.cancelled:
            callback()


def script(tmp_path, name, *dumps, then="exit", code=3):
    path = tmp_path / name
    lines = [f"#!{sys.executable}", "import sys, time"]
    for dump in dumps:
        lines.append(f"print({json.dumps(dump)!r}, flush=True)")
    lines.append("time.sleep(60)" if then == "wait" else f"time.sleep(0.05); sys.exit({code})")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def spin_until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    assert condition(), "condition never held"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); a reaped child does not exist.
    with open(f"/proc/{pid}/stat") as handle:
        return handle.read().split()[2] != "Z"


@pytest.fixture
def cleanup():
    sources = []
    yield sources.append
    for source in sources:
        source.shutdown()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- Restart and backoff ----------------------------------------------------------------


def test_an_unexpected_exit_schedules_a_restart_that_resumes_monitoring(tmp_path, cleanup, capsys):
    dying = script(tmp_path, "dying", [SPEAKER])
    timers = Timers()
    events = []
    source = PipeWireAudioSource(which=lambda _: str(dying), call_later=timers)
    cleanup(source)

    assert source.start(events.append)
    spin_until(lambda: timers.scheduled)

    assert timers.delays == [RESTART_INITIAL_MS]
    assert source.process is None and source.restart_pending
    assert "retrying in 1 s" in capsys.readouterr().err

    # The next monitor works: one with the speaker plus newly arrived headphones.
    source._program = str(script(tmp_path, "working", [SPEAKER, HEADPHONES], then="wait"))
    timers.fire_last()
    spin_until(lambda: events)

    assert source.restarts == 1 and source.process is not None and not source.restart_pending
    # Only what changed while it was down — never "Speaker connected" again.
    assert events == [DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.CONNECTED, "Headphones", "bluez_output.1")]
    assert "pw-dump recovered" in capsys.readouterr().out


def test_repeated_exits_back_off_up_to_a_bound(tmp_path, cleanup):
    dying = script(tmp_path, "dying", [SPEAKER])
    timers = Timers()
    source = PipeWireAudioSource(which=lambda _: str(dying), call_later=timers)
    cleanup(source)
    source.start(lambda event: None)

    for attempt in range(8):
        spin_until(lambda: len(timers.scheduled) == attempt + 1)
        timers.fire_last()

    assert timers.delays[:6] == [1000, 2000, 4000, 8000, 16000, 30000]
    assert max(timers.delays) == RESTART_MAX_MS
    assert source.restarts == 8


def test_a_monitor_that_ran_long_enough_restarts_quickly_again(tmp_path, cleanup):
    dying = script(tmp_path, "dying", [SPEAKER])
    timers = Timers()
    now = [0.0]
    source = PipeWireAudioSource(which=lambda _: str(dying), call_later=timers, clock=lambda: now[0])
    cleanup(source)
    source.start(lambda event: None)
    spin_until(lambda: len(timers.scheduled) == 1)
    timers.fire_last()
    spin_until(lambda: len(timers.scheduled) == 2)
    assert timers.delays == [1000, 2000]

    timers.fire_last()
    now[0] += STABLE_MS / 1000 + 1  # this one ran for over a minute
    spin_until(lambda: len(timers.scheduled) == 3)

    assert timers.delays[-1] == RESTART_INITIAL_MS


def test_there_is_never_more_than_one_child(tmp_path, cleanup):
    dying = script(tmp_path, "dying", [SPEAKER])
    timers = Timers()
    source = PipeWireAudioSource(which=lambda _: str(dying), call_later=timers)
    cleanup(source)
    source.start(lambda event: None)
    first = source.process.processId()
    spin_until(lambda: timers.scheduled)

    source._program = str(script(tmp_path, "working", [SPEAKER], then="wait"))
    timers.fire_last()
    timers.fire_last()  # a stray second firing must not spawn another

    assert source.process is not None and source.process.processId() != first
    assert source.restarts == 1
    assert not alive(first)


def test_a_child_that_cannot_start_is_retried_too(tmp_path, cleanup):
    timers = Timers()
    source = PipeWireAudioSource(which=lambda _: str(tmp_path / "gone" / "pw-dump"), call_later=timers)
    cleanup(source)

    assert source.start(lambda event: None)
    spin_until(lambda: timers.scheduled)
    assert timers.delays == [RESTART_INITIAL_MS]


# --- Intentional shutdown ------------------------------------------------------------------


def test_shutdown_ends_the_child_and_never_restarts_it(tmp_path, cleanup):
    working = script(tmp_path, "working", [SPEAKER], then="wait")
    timers = Timers()
    source = PipeWireAudioSource(which=lambda _: str(working), call_later=timers)
    cleanup(source)
    source.start(lambda event: None)
    spin_until(lambda: source.process is not None and source.process.processId() > 0)
    pid = source.process.processId()

    source.shutdown()
    for _ in range(5):
        QCoreApplication.processEvents()

    assert not alive(pid)
    assert timers.scheduled == [] and not source.restart_pending


def test_shutdown_cancels_a_pending_restart(tmp_path, cleanup):
    dying = script(tmp_path, "dying", [SPEAKER])
    timers = Timers()
    source = PipeWireAudioSource(which=lambda _: str(dying), call_later=timers)
    cleanup(source)
    source.start(lambda event: None)
    spin_until(lambda: timers.scheduled)

    source.shutdown()
    timers.fire_last()  # even if the timer fired anyway

    assert timers.scheduled[-1][2].cancelled
    assert source.process is None and source.restarts == 0


def test_no_pw_dump_means_unavailable_and_no_retry():
    timers = Timers()
    source = PipeWireAudioSource(which=lambda _: None, call_later=timers)

    assert source.start(lambda event: None) is False
    assert timers.scheduled == []


# --- Reconciliation -------------------------------------------------------------------------


def test_a_restarted_monitor_reports_only_what_changed_while_it_was_down():
    tracker = PipeWireTracker()
    tracker.feed([SPEAKER, MIC, default("alsa_output.speaker")])

    tracker.rebase()
    # PipeWire itself restarted too: every object has a new id.
    renumbered = [node(201, "Audio/Sink", "alsa_output.speaker", "Speaker"),
                  node(202, "Audio/Sink", "bluez_output.1", "Headphones"),
                  default("alsa_output.speaker")]
    events = tracker.feed(renumbered)

    assert events == [
        DeviceEvent(DeviceKind.AUDIO_INPUT, DeviceChange.DISCONNECTED, "Microphones", "alsa_input.mic"),
        DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.CONNECTED, "Headphones", "bluez_output.1"),
    ]
    # And it is tracked under its new id afterwards.
    assert tracker.feed([{"id": 202, "info": None}]) == [
        DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.DISCONNECTED, "Headphones", "bluez_output.1")
    ]


def test_an_unchanged_topology_after_a_restart_is_silent():
    tracker = PipeWireTracker()
    tracker.feed([SPEAKER, MIC, default("alsa_output.speaker")])
    tracker.rebase()

    assert tracker.feed([SPEAKER, MIC, default("alsa_output.speaker")]) == []


def test_a_default_that_moved_while_down_is_reported_once():
    tracker = PipeWireTracker()
    tracker.feed([SPEAKER, HEADPHONES, default("alsa_output.speaker")])
    tracker.rebase()

    assert tracker.feed([SPEAKER, HEADPHONES, default("bluez_output.1")]) == [
        DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.DEFAULT_CHANGED, "Headphones", "bluez_output.1")
    ]
