"""Deterministic person-detector doubles shared by the perception tests."""

from __future__ import annotations

import threading

from mira.domain.camera import CameraFrame
from mira.domain.perception import BoundingBox, PersonObservation


def person(
    frame: CameraFrame | None = None,
    *,
    box: tuple[float, float, float, float] = (0.4, 0.3, 0.6, 0.7),
    confidence: float = 0.9,
    sequence: int = 0,
    observed_at: float = 0.0,
) -> PersonObservation:
    return PersonObservation(
        confidence=confidence,
        bounds=BoundingBox(*box),
        frame_sequence=frame.sequence if frame is not None else sequence,
        observed_at=frame.timestamp_s if frame is not None else observed_at,
    )


class FakePersonDetector:
    """A scripted `PersonDetector` that records what it was shown.

    ``script`` maps frame sequence numbers to what `detect` should do for that
    frame: a list of boxes (possibly empty) or an exception instance to raise.
    Frames not in the script use ``default`` boxes. ``hold`` makes each
    `detect` wait, to keep an inference in flight.
    """

    def __init__(self, *, default=(), script=None, load_error: BaseException | None = None):
        self.default = list(default)
        self.script = dict(script or {})
        self.load_error = load_error
        self.hold: threading.Event | None = None
        self.detect_started = threading.Event()
        self.loads = 0
        self.seen: list[int] = []
        self.threads: set[str] = set()

    def load(self) -> None:
        self.loads += 1
        self.threads.add(threading.current_thread().name)
        if self.load_error is not None:
            raise self.load_error

    def detect(self, frame: CameraFrame):
        self.threads.add(threading.current_thread().name)
        self.seen.append(frame.sequence)
        self.detect_started.set()
        if self.hold is not None:
            self.hold.wait(5.0)
        outcome = self.script.get(frame.sequence, self.default)
        if isinstance(outcome, BaseException):
            raise outcome
        return tuple(person(frame, box=box) for box in outcome)
