"""Camera status: the application can always say what its camera is doing."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from mira.adapters.opencv_camera import OpenCVCameraSource
from mira.application.composition import build_application
from mira.core.camera_capture import CameraCapture
from mira.domain.camera import CameraError, CameraInfo, CameraState, CameraStatus
from mira.domain.scheduler import ManualScheduler

from camera_doubles import FakeCameraSource


def wait_for_state(capture: CameraCapture, state: CameraState, timeout_s: float = 2.0) -> CameraStatus:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = capture.status
        if status.state is state:
            return status
        time.sleep(0.001)
    raise AssertionError(f"camera stayed {capture.status} instead of {state}")


@pytest.fixture(autouse=True)
def no_leaked_capture_threads():
    yield
    deadline = time.monotonic() + 2.0
    while any(t.name == CameraCapture.THREAD_NAME for t in threading.enumerate()):
        assert time.monotonic() < deadline, "a capture thread outlived its test"
        time.sleep(0.001)


def test_a_capture_without_a_source_is_disabled_whatever_is_asked_of_it():
    capture = CameraCapture(None)

    capture.start()
    capture.stop()

    assert capture.status == CameraStatus(CameraState.DISABLED)


def test_a_configured_camera_is_stopped_until_started():
    assert CameraCapture(FakeCameraSource()).status == CameraStatus(CameraState.STOPPED)


def test_opening_then_active_with_what_the_device_delivers():
    source = FakeCameraSource(info=CameraInfo("cam0", 640, 480, 30.0))
    source.open_hold = threading.Event()
    capture = CameraCapture(source)

    capture.start()
    assert capture.status == CameraStatus(CameraState.OPENING)

    source.open_hold.set()
    active = wait_for_state(capture, CameraState.ACTIVE)
    capture.stop()

    assert active.info == CameraInfo("cam0", 640, 480, 30.0)
    assert active.detail == "cam0 640x480"


def test_a_normal_stop_reports_stopped():
    capture = CameraCapture(FakeCameraSource())
    capture.start()
    wait_for_state(capture, CameraState.ACTIVE)

    capture.stop()

    assert capture.status == CameraStatus(CameraState.STOPPED)


@pytest.mark.parametrize(
    "message",
    ["no camera at index 0", "device busy", "permission denied"],
    ids=["no-device", "busy", "not-permitted"],
)
def test_a_camera_that_cannot_be_opened_is_unavailable_with_its_reason(message):
    capture = CameraCapture(FakeCameraSource(open_error=CameraError(message)))

    capture.start()

    assert wait_for_state(capture, CameraState.UNAVAILABLE).detail == message


def test_an_unexpected_open_error_is_a_failure():
    capture = CameraCapture(FakeCameraSource(open_error=RuntimeError("driver bug")))

    capture.start()

    assert "driver bug" in wait_for_state(capture, CameraState.FAILED).detail


def test_persistent_read_failures_are_a_failure():
    capture = CameraCapture(FakeCameraSource(then="fail"))

    capture.start()

    status = wait_for_state(capture, CameraState.FAILED)
    assert status.detail == f"{CameraCapture.MAX_CONSECUTIVE_READ_FAILURES} consecutive reads failed"


def test_a_failure_reason_survives_the_application_stopping_the_camera():
    capture = CameraCapture(FakeCameraSource(open_error=CameraError("device busy")))
    capture.start()
    wait_for_state(capture, CameraState.UNAVAILABLE)

    capture.shutdown()

    assert capture.status == CameraStatus(CameraState.UNAVAILABLE, "device busy")


def test_a_failed_camera_can_be_started_again():
    source = FakeCameraSource([None] * CameraCapture.MAX_CONSECUTIVE_READ_FAILURES)
    capture = CameraCapture(source)
    capture.start()
    wait_for_state(capture, CameraState.FAILED)
    while capture.running:
        time.sleep(0.001)

    capture.start()

    wait_for_state(capture, CameraState.ACTIVE)
    capture.stop()


def test_a_failure_racing_a_stop_reports_stopped():
    source = FakeCameraSource(open_error=CameraError("late"))
    source.open_hold = threading.Event()
    capture = CameraCapture(source, join_timeout_s=0.02)
    capture.start()

    capture.stop()
    source.open_hold.set()
    assert source.closed.wait(1.0)

    assert capture.status == CameraStatus(CameraState.STOPPED)


def test_missing_opencv_is_reported_through_the_real_adapter(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    capture = CameraCapture(OpenCVCameraSource())

    capture.start()

    assert "OpenCV is not installed" in wait_for_state(capture, CameraState.UNAVAILABLE).detail


def test_the_application_reports_camera_status_and_keeps_working_without_one():
    scheduler = ManualScheduler()
    application = build_application(
        scheduler=scheduler, camera_source=FakeCameraSource(open_error=CameraError("no device"))
    )
    responses = []

    application.start()
    wait_for_state(application.camera, CameraState.UNAVAILABLE)
    application.brain.process_text_async("ciao", responses.append)
    scheduler.advance(600)
    scheduler.run_all()
    application.shutdown()

    assert [response.text for response in responses] != []
    assert application.camera.status.state is CameraState.UNAVAILABLE


def test_status_values_are_immutable():
    status = CameraStatus(CameraState.ACTIVE, "cam", CameraInfo("cam", 1, 1))

    assert status == CameraStatus(CameraState.ACTIVE, "cam", CameraInfo("cam", 1, 1))
    with pytest.raises(AttributeError):
        status.state = CameraState.FAILED  # type: ignore[misc]
