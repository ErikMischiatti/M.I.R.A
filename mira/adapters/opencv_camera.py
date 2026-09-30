"""OpenCV implementation of the `CameraSource` port.

The only module that knows frames come from `cv2.VideoCapture`. OpenCV is an
optional dependency (the ``camera`` extra) and is imported only when a camera
is actually opened, so MIRA runs without it and reports the camera as
unavailable instead.

Each `VideoCapture.read()` allocates a new image, so a frame can wrap that
image's buffer without copying it: nothing else holds or reuses the buffer.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from mira.domain.camera import (
    CameraError,
    CameraFrame,
    CameraInfo,
    CameraSettings,
    PixelFormat,
)

# `cv2.CAP_PROP_*` identifiers, fixed by OpenCV's API.
_CAP_PROP_FRAME_WIDTH = 3
_CAP_PROP_FRAME_HEIGHT = 4
_CAP_PROP_FPS = 5


def open_video_capture(device: int | str) -> Any:
    """Create a `cv2.VideoCapture`; the default capture factory."""

    try:
        import cv2
    except ImportError as error:
        raise CameraError(
            "OpenCV is not installed; install MIRA with the 'camera' extra"
        ) from error
    return cv2.VideoCapture(device)


class OpenCVCameraSource:
    """Acquire BGR frames from a webcam through OpenCV.

    `open` and `read` must be called from one thread. Requested capture
    properties are best effort; `CameraInfo` reports what the device agreed to.
    """

    def __init__(
        self,
        settings: CameraSettings = CameraSettings(),
        *,
        capture_factory: Callable[[int | str], Any] = open_video_capture,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._settings = settings
        self._capture_factory = capture_factory
        self._clock = clock
        self._capture: Any = None
        self._info: CameraInfo | None = None
        self._sequence = 0

    @property
    def is_open(self) -> bool:
        return self._capture is not None

    def open(self) -> CameraInfo:
        if self._capture is not None:
            return self._info

        device = self._settings.device
        capture = self._capture_factory(int(device) if device.isdigit() else device)
        if not capture.isOpened():
            _release(capture)
            raise CameraError(
                f"camera {device!r} could not be opened (missing, busy or not permitted)"
            )

        self._request_properties(capture)
        try:
            info = CameraInfo(
                device=device,
                width=int(capture.get(_CAP_PROP_FRAME_WIDTH)),
                height=int(capture.get(_CAP_PROP_FRAME_HEIGHT)),
                fps=_reported_fps(capture.get(_CAP_PROP_FPS)),
            )
        except ValueError as error:
            _release(capture)
            raise CameraError(f"camera {device!r} reported unusable properties: {error}") from error

        self._capture = capture
        self._info = info
        self._sequence = 0
        return info

    def read(self) -> CameraFrame | None:
        if self._capture is None:
            return None
        try:
            ok, image = self._capture.read()
        except Exception:  # noqa: BLE001 - a backend error is a failed read
            return None
        timestamp_s = self._clock()
        if not ok or image is None:
            return None

        frame = _frame_from_image(image, self._sequence, timestamp_s)
        if frame is not None:
            self._sequence += 1
        return frame

    def close(self) -> None:
        capture, self._capture = self._capture, None
        self._info = None
        if capture is not None:
            _release(capture)

    def _request_properties(self, capture: Any) -> None:
        settings = self._settings
        for prop, value in (
            (_CAP_PROP_FRAME_WIDTH, settings.width),
            (_CAP_PROP_FRAME_HEIGHT, settings.height),
            (_CAP_PROP_FPS, settings.fps),
        ):
            if value is not None:
                capture.set(prop, float(value))


def _frame_from_image(image: Any, sequence: int, timestamp_s: float) -> CameraFrame | None:
    """Wrap an 8-bit, three-channel, row-major image; None for anything else."""

    try:
        view = memoryview(image)
    except TypeError:
        return None
    if view.ndim != 3 or view.shape[2] != 3 or view.format != "B":
        return None
    height, width, _channels = view.shape
    try:
        return CameraFrame(
            sequence=sequence,
            timestamp_s=timestamp_s,
            width=width,
            height=height,
            pixel_format=PixelFormat.BGR8,
            pixels=view,
        )
    except ValueError:
        return None


def _reported_fps(value: float) -> float | None:
    return value if math.isfinite(value) and value > 0 else None


def _release(capture: Any) -> None:
    try:
        capture.release()
    except Exception:  # noqa: BLE001 - releasing must never raise to the caller
        pass
