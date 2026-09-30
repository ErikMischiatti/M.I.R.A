"""Camera capture ownership, threading and application lifecycle."""

from __future__ import annotations

import threading
import time

import pytest

from mira.application.camera_config import (
    CameraConfiguration,
    camera_configuration_from_environment,
    parse_camera_configuration,
)
from mira.application.composition import build_application
from mira.core.camera_capture import CameraCapture
from mira.domain.camera import CameraError, CameraSettings
from mira.domain.scheduler import ManualScheduler

from camera_doubles import FakeCameraSource, make_frame


def wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


def capture_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == CameraCapture.THREAD_NAME]


@pytest.fixture(autouse=True)
def no_leaked_capture_threads():
    yield
    assert wait_until(lambda: not capture_threads()), "a capture thread outlived its test"


# --- CameraCapture -------------------------------------------------------------


def test_a_disabled_capture_starts_nothing():
    capture = CameraCapture(None)

    capture.start()

    assert not capture.enabled
    assert not capture.running
    assert capture_threads() == []
    assert capture.stop() is True


def test_start_returns_at_once_and_the_capture_thread_owns_the_source():
    source = FakeCameraSource()
    source.hold = threading.Event()
    capture = CameraCapture(source)

    capture.start()
    assert capture.running
    assert source.read_started.wait(1.0)
    assert source.calls == ["open", "read"]

    source.hold.set()
    assert capture.stop() is True
    assert source.closed.is_set()
    assert source.threads == {CameraCapture.THREAD_NAME}
    assert not capture.running


def test_capture_reads_continuously_until_stopped():
    source = FakeCameraSource()
    capture = CameraCapture(source)

    capture.start()
    assert wait_until(lambda: source.count("read") >= 20)
    capture.stop()
    reads_at_stop = source.count("read")

    time.sleep(0.02)
    assert source.count("read") == reads_at_stop
    assert source.calls[-1] == "close"


def test_starting_twice_opens_the_device_once():
    source = FakeCameraSource()
    capture = CameraCapture(source)

    capture.start()
    capture.start()
    assert wait_until(lambda: source.count("read") > 0)
    capture.stop()

    assert source.count("open") == 1
    assert source.count("close") == 1


def test_stop_is_idempotent_and_safe_before_start():
    source = FakeCameraSource()
    capture = CameraCapture(source)

    assert capture.stop() is True
    capture.start()
    assert capture.stop() is True
    assert capture.stop() is True

    assert source.count("close") == 1


def test_a_stopped_capture_can_start_again():
    source = FakeCameraSource()
    capture = CameraCapture(source)
    capture.start()
    capture.stop()

    capture.start()
    assert wait_until(lambda: source.count("open") == 2)
    capture.stop()

    assert source.count("close") == 2


def test_shutdown_stops_and_refuses_a_later_start():
    source = FakeCameraSource()
    capture = CameraCapture(source)
    capture.start()

    assert capture.shutdown() is True
    assert capture.shutdown() is True
    capture.start()

    assert not capture.running
    assert source.count("open") == 1


@pytest.mark.parametrize(
    "error",
    [CameraError("no device"), RuntimeError("driver bug")],
    ids=["unavailable", "unexpected"],
)
def test_a_device_that_cannot_open_ends_capture_and_is_still_released(error):
    source = FakeCameraSource(open_error=error)
    capture = CameraCapture(source)

    capture.start()

    assert wait_until(lambda: not capture.running)
    assert source.calls == ["open", "close"]


def test_persistent_read_failures_end_capture_and_release_the_device():
    source = FakeCameraSource(then="fail")
    capture = CameraCapture(source)

    capture.start()

    assert wait_until(lambda: not capture.running)
    assert source.count("read") == CameraCapture.MAX_CONSECUTIVE_READ_FAILURES
    assert source.calls[-1] == "close"


def test_occasional_failures_and_read_errors_do_not_end_capture():
    failures = CameraCapture.MAX_CONSECUTIVE_READ_FAILURES - 1
    source = FakeCameraSource(
        [None] * (failures - 1) + [OSError("glitch"), make_frame(0), None, make_frame(1)]
    )
    capture = CameraCapture(source)

    capture.start()
    assert wait_until(lambda: source.count("read") > failures + 5)

    assert capture.running
    capture.stop()


def test_stop_during_an_in_flight_read_releases_the_device_when_the_read_returns():
    source = FakeCameraSource()
    source.hold = threading.Event()
    capture = CameraCapture(source, join_timeout_s=0.05)
    capture.start()
    assert source.read_started.wait(1.0)

    assert capture.stop() is False
    assert capture.running
    assert not source.closed.is_set()

    capture.start()  # must not start a second reader on the busy device
    assert source.count("open") == 1

    source.hold.set()
    assert source.closed.wait(1.0)
    assert wait_until(lambda: not capture.running)
    assert capture.stop() is True
    assert source.count("read") == 1


# --- application ownership ------------------------------------------------------


def test_building_the_application_opens_no_camera():
    source = FakeCameraSource()

    application = build_application(scheduler=ManualScheduler(), camera_source=source)

    assert application.camera.enabled
    assert source.calls == []
    assert capture_threads() == []


def test_application_start_captures_and_shutdown_releases_the_camera():
    source = FakeCameraSource()
    scheduler = ManualScheduler()
    application = build_application(scheduler=scheduler, camera_source=source)

    application.start()
    assert wait_until(lambda: source.count("read") > 0)
    application.shutdown()
    application.shutdown()

    assert source.closed.is_set()
    assert source.count("close") == 1
    assert not application.camera.running
    assert scheduler.pending_timers() == 0
    application.start()
    assert not application.camera.running


def test_an_unavailable_camera_leaves_the_application_usable():
    scheduler = ManualScheduler()
    application = build_application(
        scheduler=scheduler, camera_source=FakeCameraSource(open_error=CameraError("busy"))
    )
    responses = []

    application.start()
    application.brain.process_text_async("che ore sono", responses.append)
    scheduler.advance(600)
    scheduler.run_all()

    assert len(responses) == 1
    assert wait_until(lambda: not application.camera.running)
    application.shutdown()


def test_the_camera_is_disabled_unless_the_environment_enables_it():
    application = build_application(scheduler=ManualScheduler())

    application.start()

    assert not application.camera.enabled
    assert capture_threads() == []
    application.shutdown()


def test_enabling_the_camera_selects_the_opencv_adapter(monkeypatch):
    monkeypatch.setenv("MIRA_CAMERA", "1")
    monkeypatch.setenv("MIRA_CAMERA_DEVICE", "/dev/video7")

    application = build_application(scheduler=ManualScheduler())

    source = application.camera._source  # noqa: SLF001 - adapter choice is the subject
    assert type(source).__name__ == "OpenCVCameraSource"
    assert source._settings.device == "/dev/video7"  # noqa: SLF001
    assert not application.camera.running


# --- configuration --------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_camera_enabling_values(value):
    assert parse_camera_configuration(enabled=value).enabled


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "maybe"])
def test_camera_disabling_values(value):
    assert not parse_camera_configuration(enabled=value).enabled


def test_camera_configuration_parses_every_setting():
    configuration = parse_camera_configuration(
        enabled="1", device=" 2 ", width="1280", height="720", fps="15"
    )

    assert configuration == CameraConfiguration(
        enabled=True, settings=CameraSettings(device="2", width=1280, height=720, fps=15.0)
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"device": "   "},
        {"width": "wide"},
        {"width": "0"},
        {"height": "-480"},
        {"fps": "fast"},
        {"fps": "nan"},
        {"fps": "0"},
    ],
)
def test_invalid_camera_settings_fall_back_to_defaults(raw):
    assert parse_camera_configuration(**raw) == CameraConfiguration()


def test_camera_configuration_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MIRA_CAMERA", "on")
    monkeypatch.setenv("MIRA_CAMERA_WIDTH", "640")

    configuration = camera_configuration_from_environment()

    assert configuration.enabled
    assert configuration.settings == CameraSettings(width=640)
