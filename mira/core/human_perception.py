"""Run a person detector over the newest camera frames.

Person perception gets its own thread, separate from both the UI thread and
the camera's capture thread. The capture thread only captures; this thread
pulls the newest frame from `CameraCapture`, runs the detector on it, and
repeats. Asking for a frame newer than the last one analysed means frames
that arrive while the detector is busy are simply never seen: a detector
slower than the camera analyses fewer frames, and nothing queues up.

The perception thread never reads the camera device, never touches Qt or the
event bus, and never calls into the UI. Detector failures stay on the thread
and become status.
"""

from __future__ import annotations

import threading

from mira.core.camera_capture import CameraCapture
from mira.domain.camera import CameraFrame
from mira.domain.perception import (
    PerceptionError,
    PerceptionState,
    PerceptionStatus,
    PersonDetector,
    PersonObservation,
)

# States a stop turns into STOPPED. A failure reason is kept instead.
_STOPPABLE = frozenset({PerceptionState.STOPPED, PerceptionState.STARTING, PerceptionState.ACTIVE})


class HumanPerception:
    """Own a `PersonDetector` and the thread that feeds it camera frames.

    ``detector`` None means person perception is disabled. Without an enabled
    camera it reports itself unavailable and starts no thread. `stop` waits up
    to ``join_timeout_s`` for an inference in progress; `shutdown` stops and
    refuses any later start.
    """

    MAX_CONSECUTIVE_DETECTION_FAILURES = 5
    THREAD_NAME = "mira-perception"
    FRAME_WAIT_S = 0.25
    # How long to back off when the camera has nothing to offer at all, so a
    # stopped camera is polled gently instead of spun on.
    IDLE_BACKOFF_S = 0.02

    def __init__(
        self,
        camera: CameraCapture,
        detector: PersonDetector | None,
        *,
        join_timeout_s: float = 2.0,
    ):
        self._camera = camera
        self._detector = detector
        self._join_timeout_s = join_timeout_s
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._shut_down = False
        self._status = PerceptionStatus(
            PerceptionState.STOPPED if detector is not None else PerceptionState.DISABLED
        )

    @property
    def enabled(self) -> bool:
        return self._detector is not None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def status(self) -> PerceptionStatus:
        with self._lock:
            return self._status

    def start(self) -> None:
        with self._lock:
            if self._detector is None or self._shut_down or self.running:
                return
            if not self._camera.enabled:
                self._set_status(
                    PerceptionStatus(PerceptionState.UNAVAILABLE, "the camera is disabled")
                )
                return
            self._stop_requested = threading.Event()
            self._set_status(PerceptionStatus(PerceptionState.STARTING, "loading detector"))
            self._thread = threading.Thread(
                target=self._run,
                args=(self._detector, self._stop_requested),
                name=self.THREAD_NAME,
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> bool:
        """Stop analysing frames; True once the perception thread has ended."""

        with self._lock:
            thread = self._thread
            self._stop_requested.set()
            if self._status.state in _STOPPABLE and self._detector is not None:
                self._set_status(PerceptionStatus(PerceptionState.STOPPED))
        if thread is None:
            return True
        thread.join(self._join_timeout_s)
        if thread.is_alive():
            print(f"[Perception] detector still finishing after {self._join_timeout_s}s")
            return False
        with self._lock:
            if self._thread is thread:
                self._thread = None
        return True

    def shutdown(self) -> bool:
        with self._lock:
            self._shut_down = True
        return self.stop()

    def _run(self, detector: PersonDetector, stop_requested: threading.Event) -> None:
        try:
            detector.load()
        except PerceptionError as error:
            self._report(stop_requested, PerceptionStatus(PerceptionState.UNAVAILABLE, str(error)))
            return
        except Exception as error:  # noqa: BLE001 - a broken detector must not escape the thread
            self._report(
                stop_requested,
                PerceptionStatus(PerceptionState.FAILED, f"detector failed to load: {error!r}"),
            )
            return
        self._report(stop_requested, PerceptionStatus(PerceptionState.STARTING, "waiting for camera"))

        last_frame: CameraFrame | None = None
        failures = 0
        while not stop_requested.is_set():
            frame = self._camera.wait_for_frame(after=last_frame, timeout_s=self.FRAME_WAIT_S)
            if frame is None:
                stop_requested.wait(self.IDLE_BACKOFF_S)
                continue
            last_frame = frame

            try:
                persons = _checked(detector.detect(frame))
            except Exception as error:  # noqa: BLE001 - counted, then reported
                failures += 1
                if failures >= self.MAX_CONSECUTIVE_DETECTION_FAILURES:
                    self._report(
                        stop_requested,
                        PerceptionStatus(
                            PerceptionState.FAILED,
                            f"{failures} consecutive detections failed: {error!r}",
                        ),
                    )
                    return
                continue
            failures = 0
            self._report(stop_requested, PerceptionStatus(PerceptionState.ACTIVE))
            self._publish(frame, persons, stop_requested)

    def _publish(
        self,
        frame: CameraFrame,
        persons: tuple[PersonObservation, ...],
        stop_requested: threading.Event,
    ) -> None:
        """Hand a detection result to consumers; results are dropped for now."""

    def _report(self, stop_requested: threading.Event, status: PerceptionStatus) -> None:
        with self._lock:
            if not stop_requested.is_set():
                self._set_status(status)

    def _set_status(self, status: PerceptionStatus) -> None:
        # Callers hold `_lock`.
        if status == self._status:
            return
        self._status = status
        detail = f": {status.detail}" if status.detail else ""
        print(f"[Perception] {status.state}{detail}")


def _checked(result: object) -> tuple[PersonObservation, ...]:
    """Accept only what the port promises; anything else is a failed detection."""

    persons = tuple(result)  # type: ignore[arg-type]
    if not all(isinstance(person, PersonObservation) for person in persons):
        raise TypeError("detector returned something other than PersonObservation values")
    return persons
