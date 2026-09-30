"""Technology-independent boundary for perceiving people in camera frames.

Observations live in *image space*: normalized coordinates in [0, 1] with
(0, 0) at the frame's top-left corner and (1, 1) at its bottom-right, as the
camera sees the scene. That is deliberately not the embodiment's gaze space
(`AttentionTarget`, [-1, 1] around the face's straight-ahead direction):
turning an observation into attention is a separate mapping, with its own
decisions about mirroring and which person to look at.

A person observation says only that someone is there and where. It carries no
identity, no name and no emotion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from mira.domain.camera import CameraFrame


class PerceptionError(Exception):
    """A detector cannot run, for example because its model is missing."""


@dataclass(frozen=True, slots=True)
class ImagePoint:
    """A point in normalized image space."""

    x: float
    y: float

    def __post_init__(self) -> None:
        for axis, value in (("x", self.x), ("y", self.y)):
            if not _is_unit(value):
                raise ValueError(f"image {axis} must be finite and within [0, 1]")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """An axis-aligned region of normalized image space with a positive area."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        for name, value in (
            ("left", self.left),
            ("top", self.top),
            ("right", self.right),
            ("bottom", self.bottom),
        ):
            if not _is_unit(value):
                raise ValueError(f"box {name} must be finite and within [0, 1]")
        if not (self.left < self.right and self.top < self.bottom):
            raise ValueError("box must have left < right and top < bottom")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> ImagePoint:
        return ImagePoint((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)


@dataclass(frozen=True, slots=True)
class PersonObservation:
    """One person seen in one frame.

    ``bounds`` is the region the detector found, which depends on the detector:
    a face detector reports the face, a body detector the body. ``confidence``
    is the detector's score in [0, 1]; it orders observations but is not
    necessarily a calibrated probability. ``frame_sequence`` and
    ``observed_at`` identify the frame: its sequence and its monotonic capture
    time in seconds.
    """

    confidence: float
    bounds: BoundingBox
    frame_sequence: int
    observed_at: float

    def __post_init__(self) -> None:
        if not _is_unit(self.confidence):
            raise ValueError("confidence must be finite and within [0, 1]")
        if self.frame_sequence < 0:
            raise ValueError("frame sequence must not be negative")
        if not math.isfinite(self.observed_at):
            raise ValueError("observation time must be finite")

    @property
    def center(self) -> ImagePoint:
        return self.bounds.center


class PersonDetector(Protocol):
    """Find the people visible in a camera frame.

    A detector is used from one thread. `load` prepares its model and may be
    slow; `detect` runs once per frame.
    """

    def load(self) -> None:
        """Prepare the model; raise `PerceptionError` when it cannot run."""

    def detect(self, frame: CameraFrame) -> tuple[PersonObservation, ...]:
        """Return every person found, most confident first; may be empty."""


def _is_unit(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= 1.0
