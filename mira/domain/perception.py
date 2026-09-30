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
from enum import StrEnum
from typing import Protocol

from mira.domain.camera import CameraFrame


class PerceptionState(StrEnum):
    """Where person perception is in its life, for status reporting."""

    DISABLED = "disabled"        # no detector configured
    STOPPED = "stopped"          # configured, not running
    STARTING = "starting"        # loading the model or waiting for frames
    ACTIVE = "active"            # analysing frames
    UNAVAILABLE = "unavailable"  # cannot run: no camera, no model, no library
    FAILED = "failed"            # the detector kept failing


@dataclass(frozen=True, slots=True)
class PerceptionStatus:
    """Perception's state and why it is in it."""

    state: PerceptionState
    detail: str = ""


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


@dataclass(frozen=True, slots=True)
class PersonDetections:
    """Everyone found in one analysed frame, possibly no one.

    ``version`` counts published results from one and never restarts, even
    when perception or the camera does, so a consumer can tell results apart
    and order them. ``frame_sequence`` and ``observed_at`` identify the frame.
    """

    version: int
    frame_sequence: int
    observed_at: float
    persons: tuple[PersonObservation, ...] = ()

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("detections version starts at 1")
        if self.frame_sequence < 0:
            raise ValueError("frame sequence must not be negative")
        if not math.isfinite(self.observed_at):
            raise ValueError("observation time must be finite")
        object.__setattr__(self, "persons", tuple(self.persons))

    @property
    def count(self) -> int:
        return len(self.persons)


@dataclass(frozen=True, slots=True)
class HumanPresence:
    """Whether someone is reliably there, with the latest sighting while they are.

    ``persons`` is the most recent non-empty detection while present, possibly
    a little stale during the disappearance grace period (``last_seen_at``
    says when it was made), and empty while nobody is present.
    ``changed_at`` is when ``present`` last changed, None before the first
    change.
    """

    present: bool = False
    persons: tuple[PersonObservation, ...] = ()
    last_seen_at: float | None = None
    changed_at: float | None = None


class PresenceStabilizer:
    """Turn flickering per-frame detections into stable presence.

    Someone becomes present once detected continuously for ``confirm_after_s``
    and absent once no one has been detected for ``release_after_s``. Time is
    explicit: each detection carries its frame's capture time, and `expire`
    lets presence lapse when no frames arrive at all. This is not tracking;
    it does not follow individuals or tell them apart.
    """

    def __init__(self, *, confirm_after_s: float = 0.3, release_after_s: float = 1.5):
        for name, value in (("confirm_after_s", confirm_after_s), ("release_after_s", release_after_s)):
            if not (math.isfinite(value) and value >= 0.0):
                raise ValueError(f"{name} must be finite and non-negative")
        self._confirm_after_s = confirm_after_s
        self._release_after_s = release_after_s
        self._presence = HumanPresence()
        self._candidate_since: float | None = None

    @property
    def presence(self) -> HumanPresence:
        return self._presence

    def observe(self, detections: PersonDetections) -> HumanPresence:
        """Account for one analysed frame."""

        now = detections.observed_at
        if detections.persons:
            if self._presence.present:
                self._presence = HumanPresence(
                    True, detections.persons, now, self._presence.changed_at
                )
            else:
                if self._candidate_since is None:
                    self._candidate_since = now
                if now - self._candidate_since >= self._confirm_after_s:
                    self._candidate_since = None
                    self._presence = HumanPresence(True, detections.persons, now, now)
            return self._presence

        self._candidate_since = None
        return self.expire(now)

    def expire(self, now: float) -> HumanPresence:
        """Let presence lapse once nobody has been seen for the grace period."""

        presence = self._presence
        if presence.present and now - presence.last_seen_at >= self._release_after_s:
            self._presence = HumanPresence(False, (), presence.last_seen_at, now)
        return self._presence


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
