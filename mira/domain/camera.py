"""Technology-independent boundary for camera input.

A `CameraSource` delivers `CameraFrame` values. Nothing here knows which
library or device produced them: a frame is an immutable, row-major block of
8-bit pixels plus the metadata needed to interpret it, so a consumer can view
the pixels without copying them and without depending on the capture library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PixelFormat(StrEnum):
    """How a frame's bytes are laid out per pixel."""

    BGR8 = "bgr8"  # three 8-bit channels, blue first

    @property
    def channels(self) -> int:
        return 3


class CameraError(Exception):
    """A camera could not be opened; the message says why, for diagnostics."""


@dataclass(frozen=True, slots=True)
class CameraInfo:
    """What an opened camera actually delivers, as negotiated with the device.

    ``device`` is an opaque identifier for diagnostics only; callers must not
    parse it. ``fps`` is None when the device does not report a rate.
    """

    device: str
    width: int
    height: int
    fps: float | None = None

    def __post_init__(self) -> None:
        _require_dimensions(self.width, self.height)
        if self.fps is not None and not (math.isfinite(self.fps) and self.fps > 0):
            raise ValueError("camera fps must be positive and finite when reported")


@dataclass(frozen=True, slots=True)
class CameraSettings:
    """What to ask a camera for. Devices may deliver something else.

    ``device`` is an opaque identifier interpreted by the chosen source (for
    OpenCV: a device index such as ``"0"`` or a device path). Unset capture
    properties leave the device's own defaults in place.
    """

    device: str = "0"
    width: int | None = None
    height: int | None = None
    fps: float | None = None

    def __post_init__(self) -> None:
        if not self.device.strip():
            raise ValueError("camera device must not be empty")
        for name, value in (("width", self.width), ("height", self.height)):
            if value is not None and value <= 0:
                raise ValueError(f"requested camera {name} must be positive")
        if self.fps is not None and not (math.isfinite(self.fps) and self.fps > 0):
            raise ValueError("requested camera fps must be positive and finite")


@dataclass(frozen=True, slots=True, eq=False)
class CameraFrame:
    """One captured image.

    ``pixels`` is a read-only, one-dimensional byte view of exactly
    ``height * width * channels`` bytes, row-major with no padding. The frame
    keeps its buffer alive and nothing can write through it, so a frame can be
    handed to any number of consumers and threads. A source must give every
    frame its own buffer rather than reusing one between reads.

    ``timestamp_s`` is monotonic capture time in seconds, comparable only with
    other timestamps from the same process. ``sequence`` counts delivered
    frames from zero for one opening of a source.

    Frames compare by identity: comparing pixel content is rarely what a
    consumer wants and costs a full scan of the image.
    """

    sequence: int
    timestamp_s: float
    width: int
    height: int
    pixel_format: PixelFormat
    pixels: memoryview

    def __post_init__(self) -> None:
        _require_dimensions(self.width, self.height)
        if self.sequence < 0:
            raise ValueError("frame sequence must not be negative")
        if not math.isfinite(self.timestamp_s):
            raise ValueError("frame timestamp must be finite")

        view = memoryview(self.pixels)
        if not view.c_contiguous:
            raise ValueError("frame pixels must be contiguous")
        view = view.cast("B") if view.format != "B" or view.ndim != 1 else view
        expected = self.width * self.height * self.pixel_format.channels
        if view.nbytes != expected:
            raise ValueError(
                f"frame pixels hold {view.nbytes} bytes; "
                f"{self.width}x{self.height} {self.pixel_format} needs {expected}"
            )
        object.__setattr__(self, "pixels", view.toreadonly())

    @property
    def row_stride(self) -> int:
        """Bytes per image row."""

        return self.width * self.pixel_format.channels


class CameraSource(Protocol):
    """A camera that can be opened, read frame by frame and released.

    One source is used from one thread at a time. ``read`` may block until the
    next frame is available.
    """

    def open(self) -> CameraInfo:
        """Acquire the device and report what it delivers; raise `CameraError`."""

    def read(self) -> CameraFrame | None:
        """Return the next frame, or None when this read failed."""

    def close(self) -> None:
        """Release the device. Safe to call more than once, and when unopened."""


def _require_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("camera dimensions must be positive")
