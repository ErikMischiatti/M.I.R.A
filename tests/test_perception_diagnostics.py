"""Read-only human-perception diagnostics: the snapshot and the drawer line."""

from __future__ import annotations

import itertools
import threading
import time

import pytest

from mira.core.camera_capture import CameraCapture
from mira.core.human_perception import HumanPerception
from mira.domain.perception import (
    BoundingBox,
    HumanPresence,
    PerceptionDiagnostics,
    PerceptionState,
    PerceptionStatus,
    PersonObservation,
    PresenceStabilizer,
)

from camera_doubles import FakeCameraSource
from perception_doubles import FakePersonDetector


def wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


@pytest.fixture(autouse=True)
def no_leaked_runtime_threads():
    yield
    names = {CameraCapture.THREAD_NAME, HumanPerception.THREAD_NAME}
    assert wait_until(lambda: not [t for t in threading.enumerate() if t.name in names])


def someone(count: int) -> tuple[PersonObservation, ...]:
    return tuple(PersonObservation(0.9, BoundingBox(0.1, 0.1, 0.3, 0.5), 0, 0.0) for _ in range(count))


# --- the snapshot ---------------------------------------------------------------------


def test_diagnostics_start_empty_and_disabled_without_a_detector():
    diagnostics = HumanPerception(CameraCapture(None), None).diagnostics()

    assert diagnostics == PerceptionDiagnostics(PerceptionStatus(PerceptionState.DISABLED))


def test_diagnostics_measure_throughput_and_latency_on_the_injected_clock():
    ticks = itertools.count()
    clock_lock = threading.Lock()

    def clock():
        with clock_lock:
            return next(ticks) * 0.05  # every reading advances 50 ms

    camera = CameraCapture(FakeCameraSource())
    perception = HumanPerception(
        camera,
        FakePersonDetector(default=[(0.4, 0.3, 0.6, 0.7)]),
        clock=clock,
        stabilizer_factory=lambda: PresenceStabilizer(confirm_after_s=0.0),
    )
    camera.start()
    perception.start()
    assert wait_until(lambda: perception.diagnostics().analysed_per_second is not None)
    assert wait_until(lambda: len(perception._published_at) == HumanPerception.METRICS_WINDOW)  # noqa: SLF001

    diagnostics = perception.diagnostics()
    perception.shutdown()
    camera.shutdown()

    # Each result reads the clock twice (before and after inference).
    assert diagnostics.inference_ms == pytest.approx(50.0)
    assert diagnostics.analysed_per_second == pytest.approx(10.0)
    assert diagnostics.status.state is PerceptionState.ACTIVE
    assert diagnostics.presence.present


def test_metrics_are_forgotten_when_perception_stops():
    camera = CameraCapture(FakeCameraSource())
    perception = HumanPerception(camera, FakePersonDetector())
    camera.start()
    perception.start()
    assert wait_until(lambda: perception.diagnostics().inference_ms is not None)

    perception.stop()

    diagnostics = perception.diagnostics()
    assert (diagnostics.analysed_per_second, diagnostics.inference_ms) == (None, None)
    assert diagnostics.presence == HumanPresence()
    camera.shutdown()
    perception.shutdown()


# --- the drawer line ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "diagnostics,text",
    [
        (
            PerceptionDiagnostics(PerceptionStatus(PerceptionState.DISABLED)),
            "Disabled (set MIRA_PERSON_DETECTION=1 to enable)",
        ),
        (PerceptionDiagnostics(PerceptionStatus(PerceptionState.STOPPED)), "Stopped"),
        (
            PerceptionDiagnostics(PerceptionStatus(PerceptionState.STARTING, "waiting for camera")),
            "Starting: waiting for camera",
        ),
        (
            PerceptionDiagnostics(PerceptionStatus(PerceptionState.UNAVAILABLE, "the camera is disabled")),
            "Unavailable: the camera is disabled",
        ),
        (
            PerceptionDiagnostics(PerceptionStatus(PerceptionState.FAILED, "5 consecutive detections failed")),
            "Failed: 5 consecutive detections failed",
        ),
        (
            PerceptionDiagnostics(PerceptionStatus(PerceptionState.ACTIVE)),
            "Active · nobody present",
        ),
        (
            PerceptionDiagnostics(
                PerceptionStatus(PerceptionState.ACTIVE),
                HumanPresence(True, someone(1), 1.0, 1.0),
                12.04,
                6.4,
            ),
            "Active · 1 person present · 12.0 fps · 6 ms",
        ),
        (
            PerceptionDiagnostics(
                PerceptionStatus(PerceptionState.ACTIVE), HumanPresence(True, someone(2), 1.0, 1.0)
            ),
            "Active · 2 people present",
        ),
    ],
)
def test_each_perception_state_has_a_readable_line(monkeypatch, diagnostics, text):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from mira.ui.debug_panel import describe_perception

    assert describe_perception(diagnostics) == text


def test_the_window_shows_perception_beside_the_camera(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from mira.application.composition import build_application
    from mira.domain.scheduler import ManualScheduler
    from mira.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    application = build_application(
        scheduler=ManualScheduler(),
        camera_source=FakeCameraSource(),
        person_detector=FakePersonDetector(),
    )
    window = MainWindow(application)
    try:
        panel = window.debug_panel
        assert panel.camera_status_label.text() == "Stopped"
        assert panel.perception_label.text() == "Stopped"
        assert window.perception_diagnostics() == application.perception.diagnostics()
    finally:
        window.face_widget.frame_timer.stop()
        window.close()
        application.shutdown()
