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

Results use the same pull-based data plane as frames, not the event bus: the
newest `PersonDetections` is kept, `latest_detections` never blocks, and
`wait_for_detections(after=...)` sleeps until a newer result exists. Turning
changes such as "someone arrived" into semantic events is a later layer's job.

Raw results flicker, so `presence` offers a stabilized view: a
`PresenceStabilizer` fed every result, and told the time when no frames
arrive, so a stopped camera lets presence lapse too.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from mira.core.camera_capture import CameraCapture
from mira.domain.camera import CameraFrame
from mira.domain.perception import (
    HumanPresence,
    PerceptionDiagnostics,
    PersonDetections,
    PerceptionError,
    PerceptionState,
    PerceptionStatus,
    PersonDetector,
    PersonObservation,
    PresenceStabilizer,
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
    # Throughput diagnostics summarise this many recent results.
    METRICS_WINDOW = 30
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
        stabilizer_factory: Callable[[], PresenceStabilizer] = PresenceStabilizer,
        clock: Callable[[], float] = time.monotonic,
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
        self._results = threading.Condition()
        self._latest: PersonDetections | None = None
        self._version = 0
        self._stabilizer_factory = stabilizer_factory
        self._clock = clock
        self._stabilizer = stabilizer_factory()
        self._published_at: deque[float] = deque(maxlen=self.METRICS_WINDOW)
        self._inference_s: deque[float] = deque(maxlen=self.METRICS_WINDOW)

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

    @property
    def presence(self) -> HumanPresence:
        """Stabilized presence; nobody is present while perception is stopped."""

        with self._results:
            return self._stabilizer.presence

    def diagnostics(self) -> PerceptionDiagnostics:
        """Status, presence and recent throughput in one consistent snapshot."""

        status = self.status
        with self._results:
            published = list(self._published_at)
            durations = list(self._inference_s)
            presence = self._stabilizer.presence
        rate = None
        if len(published) >= 2 and published[-1] > published[0]:
            rate = (len(published) - 1) / (published[-1] - published[0])
        latency = 1000.0 * sum(durations) / len(durations) if durations else None
        return PerceptionDiagnostics(status, presence, rate, latency)

    def latest_detections(self) -> PersonDetections | None:
        """The newest result of the current run, or None; never blocks."""

        with self._results:
            return self._latest

    def wait_for_detections(
        self, *, after: PersonDetections | None = None, timeout_s: float
    ) -> PersonDetections | None:
        """Wait for the newest result other than ``after``.

        Returns None on timeout or once perception stops.
        """

        stop_requested = self._stop_requested

        def fresh() -> PersonDetections | None:
            latest = self._latest
            return latest if latest is not None and latest is not after else None

        with self._results:
            self._results.wait_for(
                lambda: stop_requested.is_set() or fresh() is not None, timeout=timeout_s
            )
            if stop_requested.is_set():
                return None
            return fresh()

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
        self._clear_results()
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

        try:
            self._analyse(detector, stop_requested)
        finally:
            self._clear_results()

    def _analyse(self, detector: PersonDetector, stop_requested: threading.Event) -> None:
        last_frame: CameraFrame | None = None
        failures = 0
        while not stop_requested.is_set():
            frame = self._camera.wait_for_frame(after=last_frame, timeout_s=self.FRAME_WAIT_S)
            if frame is None:
                with self._results:
                    if not stop_requested.is_set():
                        self._stabilizer.expire(self._clock())
                stop_requested.wait(self.IDLE_BACKOFF_S)
                continue
            last_frame = frame

            started = self._clock()
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
            self._publish(frame, persons, stop_requested, started)

    def _publish(
        self,
        frame: CameraFrame,
        persons: tuple[PersonObservation, ...],
        stop_requested: threading.Event,
        started: float,
    ) -> None:
        with self._results:
            # A result finishing after stop was requested is not published:
            # stop clears results under this same lock after setting the flag.
            if stop_requested.is_set():
                return
            self._version += 1
            self._latest = PersonDetections(
                version=self._version,
                frame_sequence=frame.sequence,
                observed_at=frame.timestamp_s,
                persons=persons,
            )
            self._stabilizer.observe(self._latest)
            finished = self._clock()
            self._inference_s.append(max(0.0, finished - started))
            self._published_at.append(finished)
            self._results.notify_all()

    def _clear_results(self) -> None:
        with self._results:
            self._latest = None
            self._stabilizer = self._stabilizer_factory()
            self._published_at.clear()
            self._inference_s.clear()
            self._results.notify_all()

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
