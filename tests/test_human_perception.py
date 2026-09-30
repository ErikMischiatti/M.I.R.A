"""The person-perception runtime: its thread, its frames and its lifecycle."""

from __future__ import annotations

import threading
import time

import pytest

from mira.application.composition import build_application
from mira.application.perception_config import (
    PersonDetectionConfiguration,
    parse_person_detection_configuration,
    person_detection_configuration_from_environment,
)
from mira.core.camera_capture import CameraCapture
from mira.core.human_perception import HumanPerception
from mira.domain.camera import CameraError
from mira.domain.perception import PerceptionError, PerceptionState, PerceptionStatus
from mira.domain.scheduler import ManualScheduler

from camera_doubles import FakeCameraSource
from perception_doubles import FakePersonDetector


RUNTIME_THREADS = {CameraCapture.THREAD_NAME, HumanPerception.THREAD_NAME}


def wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


def runtime_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name in RUNTIME_THREADS]


@pytest.fixture(autouse=True)
def no_leaked_runtime_threads():
    yield
    assert wait_until(lambda: not runtime_threads()), f"threads outlived the test: {runtime_threads()}"


def running_pair(detector=None, source=None, **options):
    source = source or FakeCameraSource()
    camera = CameraCapture(source)
    perception = HumanPerception(camera, detector or FakePersonDetector(), **options)
    camera.start()
    perception.start()
    return camera, perception, source


def stop_pair(camera, perception):
    perception.shutdown()
    camera.shutdown()


class SlowDetector(FakePersonDetector):
    def __init__(self, delay_s: float, **options):
        super().__init__(**options)
        self.delay_s = delay_s

    def detect(self, frame):
        time.sleep(self.delay_s)
        return super().detect(frame)


# --- lifecycle --------------------------------------------------------------------


def test_without_a_detector_perception_is_disabled_and_starts_nothing():
    camera = CameraCapture(FakeCameraSource())
    perception = HumanPerception(camera, None)

    perception.start()

    assert not perception.enabled
    assert perception.status == PerceptionStatus(PerceptionState.DISABLED)
    assert runtime_threads() == []


def test_without_an_enabled_camera_perception_is_unavailable_and_starts_nothing():
    perception = HumanPerception(CameraCapture(None), FakePersonDetector())

    perception.start()

    assert perception.status == PerceptionStatus(PerceptionState.UNAVAILABLE, "the camera is disabled")
    assert not perception.running


def test_the_detector_runs_on_its_own_thread_and_never_reads_the_camera():
    detector = FakePersonDetector()
    camera, perception, source = running_pair(detector)

    assert wait_until(lambda: len(detector.seen) >= 5)
    stop_pair(camera, perception)

    assert detector.loads == 1
    assert detector.threads == {HumanPerception.THREAD_NAME}
    assert source.threads == {CameraCapture.THREAD_NAME}


def test_first_frame_and_then_only_newer_frames_are_analysed():
    detector = FakePersonDetector()
    camera, perception, _ = running_pair(detector)

    assert wait_until(lambda: len(detector.seen) >= 20)
    stop_pair(camera, perception)

    assert detector.seen == sorted(set(detector.seen))
    assert perception.status == PerceptionStatus(PerceptionState.STOPPED)


def test_a_slow_detector_skips_stale_frames_instead_of_queueing_them():
    detector = SlowDetector(0.02)
    camera, perception, _ = running_pair(detector)

    assert wait_until(lambda: len(detector.seen) >= 8)
    captured = camera.latest_frame().sequence
    stop_pair(camera, perception)

    gaps = [later - earlier for earlier, later in zip(detector.seen, detector.seen[1:])]
    assert all(gap >= 1 for gap in gaps)
    assert max(gaps) > 1
    assert len(detector.seen) < captured / 2


def test_the_camera_keeps_capturing_while_the_detector_is_busy():
    detector = FakePersonDetector()
    detector.hold = threading.Event()
    camera, perception, _ = running_pair(detector)
    assert detector.detect_started.wait(1.0)
    first = camera.latest_frame().sequence

    assert wait_until(lambda: camera.latest_frame().sequence > first + 10)
    assert len(detector.seen) == 1

    detector.hold.set()
    stop_pair(camera, perception)


def test_starting_twice_loads_the_detector_once():
    detector = FakePersonDetector()
    camera, perception, _ = running_pair(detector)
    perception.start()

    assert wait_until(lambda: detector.seen)
    stop_pair(camera, perception)
    assert detector.loads == 1


def test_stop_and_shutdown_are_idempotent_and_shutdown_refuses_a_restart():
    detector = FakePersonDetector()
    camera, perception, _ = running_pair(detector)
    assert wait_until(lambda: detector.seen)

    assert perception.stop() is True
    assert perception.stop() is True
    perception.start()
    assert wait_until(lambda: detector.loads == 2)
    assert perception.shutdown() is True
    assert perception.shutdown() is True
    perception.start()

    assert not perception.running
    camera.shutdown()


def test_shutdown_during_an_inference_waits_then_lets_it_finish_alone():
    detector = FakePersonDetector()
    detector.hold = threading.Event()
    camera, perception, _ = running_pair(detector, join_timeout_s=0.05)
    assert detector.detect_started.wait(1.0)

    assert perception.shutdown() is False
    assert perception.running
    perception.start()  # must not start a second detector thread
    assert len([n for n in runtime_threads() if n == HumanPerception.THREAD_NAME]) == 1

    detector.hold.set()
    assert wait_until(lambda: not perception.running)
    assert detector.seen == [detector.seen[0]]
    camera.shutdown()


def test_perception_keeps_up_when_the_camera_restarts_its_sequence():
    detector = FakePersonDetector()
    camera, perception, _ = running_pair(detector)
    assert wait_until(lambda: len(detector.seen) >= 30)

    camera.stop()
    before_restart = len(detector.seen)
    camera.start()

    assert wait_until(lambda: len(detector.seen) > before_restart + 5)
    stop_pair(camera, perception)
    assert min(detector.seen[before_restart:]) < max(detector.seen[:before_restart])


# --- failures -------------------------------------------------------------------------


def test_an_occasional_detector_exception_is_isolated():
    detector = FakePersonDetector(script={2: RuntimeError("inference glitch")})
    camera, perception, _ = running_pair(detector)

    assert wait_until(lambda: len(detector.seen) >= 10)
    assert perception.status.state is PerceptionState.ACTIVE
    stop_pair(camera, perception)


def test_persistent_detector_exceptions_end_perception_as_failed():
    class Broken(FakePersonDetector):
        def detect(self, frame):
            super().detect(frame)
            raise RuntimeError("model crashed")

    detector = Broken()
    camera, perception, _ = running_pair(detector)

    assert wait_until(lambda: not perception.running)
    status = perception.status
    assert status.state is PerceptionState.FAILED
    assert "model crashed" in status.detail
    assert len(detector.seen) == HumanPerception.MAX_CONSECUTIVE_DETECTION_FAILURES
    camera.shutdown()
    perception.shutdown()
    assert perception.status.state is PerceptionState.FAILED


def test_a_detector_returning_the_wrong_type_is_a_failed_detection():
    class Garbage(FakePersonDetector):
        def detect(self, frame):
            super().detect(frame)
            return ["a person, honestly"]

    camera, perception, _ = running_pair(Garbage())

    assert wait_until(lambda: perception.status.state is PerceptionState.FAILED)
    stop_pair(camera, perception)


@pytest.mark.parametrize(
    "error,state",
    [
        (PerceptionError("model not found: /x.xml"), PerceptionState.UNAVAILABLE),
        (RuntimeError("driver bug"), PerceptionState.FAILED),
    ],
    ids=["model-unavailable", "unexpected"],
)
def test_a_detector_that_cannot_load_reports_why_and_ends(error, state):
    camera, perception, _ = running_pair(FakePersonDetector(load_error=error))

    assert wait_until(lambda: not perception.running)
    assert perception.status.state is state
    assert str(error) in perception.status.detail or repr(error) in perception.status.detail
    assert camera.running
    stop_pair(camera, perception)


def test_an_unavailable_camera_leaves_perception_waiting_without_spinning():
    detector = FakePersonDetector()
    source = FakeCameraSource(open_error=CameraError("no device"))
    camera, perception, _ = running_pair(detector, source=source)

    assert wait_until(lambda: perception.status == PerceptionStatus(PerceptionState.STARTING, "waiting for camera"))
    time.sleep(0.1)
    assert detector.seen == []
    assert perception.running
    stop_pair(camera, perception)


# --- application ownership -----------------------------------------------------------


def test_the_application_starts_perception_after_the_camera_and_stops_it_first():
    detector = FakePersonDetector()
    source = FakeCameraSource()
    application = build_application(
        scheduler=ManualScheduler(), camera_source=source, person_detector=detector
    )
    assert detector.loads == 0 and source.calls == []

    application.start()
    assert wait_until(lambda: len(detector.seen) >= 3)
    application.shutdown()
    application.shutdown()

    assert not application.perception.running and not application.camera.running
    assert source.closed.is_set()


def test_detection_is_disabled_unless_the_environment_enables_it():
    application = build_application(scheduler=ManualScheduler(), camera_source=FakeCameraSource())

    assert application.perception.status.state is PerceptionState.DISABLED


def test_enabling_detection_selects_the_cascade_adapter_with_its_settings(monkeypatch):
    monkeypatch.setenv("MIRA_PERSON_DETECTION", "1")
    monkeypatch.setenv("MIRA_PERSON_DETECTION_MODEL", "/models/upper.xml")
    monkeypatch.setenv("MIRA_PERSON_DETECTION_CONFIDENCE", "0.6")

    application = build_application(scheduler=ManualScheduler(), camera_source=FakeCameraSource())

    detector = application.perception._detector  # noqa: SLF001 - adapter choice is the subject
    assert type(detector).__name__ == "OpenCVCascadePersonDetector"
    assert (detector._model_path, detector._min_confidence) == ("/models/upper.xml", 0.6)  # noqa: SLF001


def test_detection_enabled_without_the_camera_is_unavailable(monkeypatch):
    monkeypatch.setenv("MIRA_PERSON_DETECTION", "yes")
    application = build_application(scheduler=ManualScheduler())

    application.start()

    assert application.perception.status == PerceptionStatus(
        PerceptionState.UNAVAILABLE, "the camera is disabled"
    )
    application.shutdown()


# --- configuration ---------------------------------------------------------------------


def test_detection_configuration_parses_every_setting():
    assert parse_person_detection_configuration(
        enabled="on", model=" /m.xml ", confidence="0.25"
    ) == PersonDetectionConfiguration(enabled=True, model_path="/m.xml", min_confidence=0.25)


@pytest.mark.parametrize("confidence", ["high", "-0.1", "1.5", "nan", ""])
def test_invalid_confidence_falls_back_to_the_detector_default(confidence):
    assert parse_person_detection_configuration(confidence=confidence).min_confidence is None


@pytest.mark.parametrize("enabled", [None, "", "0", "off", "sometimes"])
def test_detection_stays_disabled_for_other_values(enabled):
    assert not parse_person_detection_configuration(enabled=enabled).enabled


def test_detection_configuration_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MIRA_PERSON_DETECTION", "TRUE")

    assert person_detection_configuration_from_environment().enabled


# --- detections data plane ------------------------------------------------------------


def test_there_are_no_detections_before_the_first_analysed_frame():
    detector = FakePersonDetector()
    detector.hold = threading.Event()
    camera, perception, _ = running_pair(detector)
    assert detector.detect_started.wait(1.0)

    assert perception.latest_detections() is None
    assert perception.wait_for_detections(timeout_s=0.02) is None

    detector.hold.set()
    stop_pair(camera, perception)


def test_each_result_describes_one_frame_and_everyone_in_it():
    two_people = [(0.1, 0.2, 0.3, 0.6), (0.6, 0.1, 0.9, 0.7)]
    detector = FakePersonDetector(default=two_people)
    camera, perception, _ = running_pair(detector)

    result = perception.wait_for_detections(timeout_s=1.0)
    stop_pair(camera, perception)

    assert result.count == 2
    assert result.frame_sequence in detector.seen
    assert all(seen.frame_sequence == result.frame_sequence for seen in result.persons)
    assert all(seen.observed_at == result.observed_at for seen in result.persons)


def test_a_frame_with_nobody_in_it_is_still_a_result():
    camera, perception, _ = running_pair(FakePersonDetector(default=[]))

    result = perception.wait_for_detections(timeout_s=1.0)
    stop_pair(camera, perception)

    assert result is not None and result.persons == () and result.count == 0


def test_versions_increase_by_one_and_frames_move_forward():
    camera, perception, _ = running_pair(FakePersonDetector())
    results = [perception.wait_for_detections(timeout_s=1.0)]
    for _ in range(15):
        results.append(perception.wait_for_detections(after=results[-1], timeout_s=1.0))
    stop_pair(camera, perception)

    versions = [result.version for result in results]
    assert versions == sorted(set(versions))
    assert all(later - earlier >= 1 for earlier, later in zip(versions, versions[1:]))
    frames = [result.frame_sequence for result in results]
    assert frames == sorted(set(frames))


def test_waiting_for_a_newer_result_times_out_while_the_detector_is_busy():
    detector = FakePersonDetector()
    camera, perception, _ = running_pair(detector)
    first = perception.wait_for_detections(timeout_s=1.0)
    detector.hold = threading.Event()
    detector.detect_started.clear()
    assert detector.detect_started.wait(1.0)
    held = perception.latest_detections()

    assert perception.wait_for_detections(after=held, timeout_s=0.03) is None
    assert perception.latest_detections() is held and held.version >= first.version

    detector.hold.set()
    stop_pair(camera, perception)


def test_versions_keep_increasing_across_a_restart():
    camera, perception, _ = running_pair(FakePersonDetector())
    before = perception.wait_for_detections(timeout_s=1.0)
    perception.stop()
    assert perception.latest_detections() is None

    perception.start()
    after = perception.wait_for_detections(timeout_s=1.0)
    stop_pair(camera, perception)

    assert after.version > before.version


def test_no_results_are_available_or_published_after_shutdown():
    detector = FakePersonDetector()
    camera, perception, _ = running_pair(detector, join_timeout_s=0.02)
    assert perception.wait_for_detections(timeout_s=1.0) is not None
    detector.hold = threading.Event()
    detector.detect_started.clear()
    assert detector.detect_started.wait(1.0)

    perception.shutdown()  # the in-flight inference has not returned yet
    assert perception.latest_detections() is None
    detector.hold.set()
    assert wait_until(lambda: not perception.running)

    assert perception.latest_detections() is None
    assert perception.wait_for_detections(timeout_s=0.01) is None
    camera.shutdown()


def test_a_waiting_consumer_is_released_when_perception_stops():
    detector = FakePersonDetector()
    detector.hold = threading.Event()
    camera, perception, _ = running_pair(detector, join_timeout_s=0.02)
    assert detector.detect_started.wait(1.0)
    results = []
    waiter = threading.Thread(target=lambda: results.append(perception.wait_for_detections(timeout_s=5.0)))
    waiter.start()

    perception.stop()
    waiter.join(1.0)

    assert results == [None]
    detector.hold.set()
    stop_pair(camera, perception)
