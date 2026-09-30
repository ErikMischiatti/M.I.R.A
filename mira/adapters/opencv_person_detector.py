"""OpenCV cascade implementation of the `PersonDetector` port.

The default model is the frontal-face Haar cascade that ships inside the
OpenCV wheel (BSD-3-Clause), so detection needs no download and no asset in
this repository. A desk companion's user is typically facing it, which is
what a frontal-face detector finds; `bounds` is therefore the face region.
Any other cascade XML, such as OpenCV's bundled upper-body cascade, can be
configured as the model instead.

Only this module knows about `cv2`, numpy and cascade outputs. OpenCV and
numpy are the optional ``camera`` extra and are imported when the detector
loads, on the perception thread.

Cascades report how many neighbouring windows agreed on a detection, not a
probability. `confidence` maps that count onto [0, 1]; it orders detections
and gates weak ones but is not calibrated.
"""

from __future__ import annotations

import math
import os
from typing import Any

from mira.domain.camera import CameraFrame
from mira.domain.perception import BoundingBox, PerceptionError, PersonObservation


DEFAULT_MODEL_NAME = "haarcascade_frontalface_default.xml"
DEFAULT_MIN_CONFIDENCE = 0.4

# A detection agreed on by this many neighbouring windows scores 1.0.
_NEIGHBOURS_FOR_FULL_CONFIDENCE = 12
_MIN_NEIGHBOURS = 3
_SCALE_FACTOR = 1.1
# Ignore people smaller than this fraction of the frame's shorter side.
_MIN_SIZE_FRACTION = 0.08


class OpenCVCascadeBackend:
    """The OpenCV calls the detector needs; replaced by a fake in tests."""

    def load(self, model_path: str | None) -> Any:
        try:
            import cv2
            import numpy  # noqa: F401 - required by `prepare`
        except ImportError as error:
            raise PerceptionError(
                "OpenCV is not installed; install MIRA with the 'camera' extra"
            ) from error

        path = model_path or os.path.join(cv2.data.haarcascades, DEFAULT_MODEL_NAME)
        if not os.path.isfile(path):
            raise PerceptionError(f"person detection model not found: {path}")
        try:
            classifier = cv2.CascadeClassifier(path)
        except Exception as error:  # noqa: BLE001 - OpenCV raises on malformed XML
            raise PerceptionError(
                f"person detection model could not be loaded: {path}"
            ) from error
        if classifier.empty():
            raise PerceptionError(f"person detection model could not be loaded: {path}")
        return classifier

    def prepare(self, frame: CameraFrame) -> Any:
        """A contrast-equalized grayscale view of the frame, without copying it first."""

        import cv2
        import numpy

        image = numpy.frombuffer(frame.pixels, dtype=numpy.uint8).reshape(
            frame.height, frame.width, frame.pixel_format.channels
        )
        return cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))

    def run(self, classifier: Any, image: Any, min_size: int) -> tuple[Any, Any]:
        """Return the detected rectangles and each one's neighbour count."""

        return classifier.detectMultiScale2(
            image,
            scaleFactor=_SCALE_FACTOR,
            minNeighbors=_MIN_NEIGHBOURS,
            minSize=(min_size, min_size),
        )


class OpenCVCascadePersonDetector:
    """Detect people with an OpenCV cascade classifier."""

    def __init__(
        self,
        model_path: str | None = None,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        backend: Any = None,
    ):
        if not (math.isfinite(min_confidence) and 0.0 <= min_confidence <= 1.0):
            raise ValueError("min_confidence must be within [0, 1]")
        self._model_path = model_path
        self._min_confidence = min_confidence
        self._backend = OpenCVCascadeBackend() if backend is None else backend
        self._classifier: Any = None

    def load(self) -> None:
        if self._classifier is None:
            self._classifier = self._backend.load(self._model_path)

    def detect(self, frame: CameraFrame) -> tuple[PersonObservation, ...]:
        if self._classifier is None:
            raise PerceptionError("person detector used before load()")

        image = self._backend.prepare(frame)
        min_size = max(1, round(min(frame.width, frame.height) * _MIN_SIZE_FRACTION))
        rectangles, neighbours = self._backend.run(self._classifier, image, min_size)

        found = []
        for rectangle, agreeing in zip(rectangles, neighbours):
            observation = self._observation(rectangle, agreeing, frame)
            if observation is not None and observation.confidence >= self._min_confidence:
                found.append(observation)
        found.sort(key=lambda seen: (-seen.confidence, seen.bounds.left, seen.bounds.top))
        return tuple(found)

    @staticmethod
    def _observation(rectangle: Any, agreeing: Any, frame: CameraFrame) -> PersonObservation | None:
        """Normalize one pixel rectangle; None for anything malformed."""

        try:
            x, y, w, h = (float(value) for value in rectangle)
            confidence = min(1.0, float(agreeing) / _NEIGHBOURS_FOR_FULL_CONFIDENCE)
            if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
                return None
            return PersonObservation(
                confidence=max(0.0, confidence),
                bounds=BoundingBox(
                    left=_clamp(x / frame.width),
                    top=_clamp(y / frame.height),
                    right=_clamp((x + w) / frame.width),
                    bottom=_clamp((y + h) / frame.height),
                ),
                frame_sequence=frame.sequence,
                observed_at=frame.timestamp_s,
            )
        except (TypeError, ValueError):
            return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
