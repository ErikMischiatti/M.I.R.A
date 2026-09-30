"""Renderer-independent micro-motions layered on an expression's pose.

Micro-motions advance in fixed steps: one `step()` per rendered frame at the
playback reference rate. Only `EmbodimentPlayback` interpolates by elapsed
time. Randomness comes from an injected source; the default is the `random`
module so production keeps drawing from the process-wide generator.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Protocol

from mira.domain.embodiment_playback import PlaybackPose


class RandomSource(Protocol):
    """The draws micro-motions need; `random` and `random.Random` both fit."""

    def randint(self, a: int, b: int) -> int: ...


@dataclass(frozen=True, slots=True)
class MicroMotionState:
    """One step's micro-motion contribution relative to the expression pose.

    Offsets are in the `PlaybackPose` face units and scale deltas are additive,
    so the neutral state leaves an expression's pose unchanged.
    """

    offset_x: float = 0.0
    offset_y: float = 0.0
    width_delta: float = 0.0
    height_delta: float = 0.0
    eyes_closed: bool = False

    def apply_to(self, pose: PlaybackPose) -> PlaybackPose:
        """Return the playback target for ``pose`` with this motion added."""

        return replace(
            pose,
            offset_x=pose.offset_x + self.offset_x,
            offset_y=pose.offset_y + self.offset_y,
            width_scale=pose.width_scale + self.width_delta,
            height_scale=pose.height_scale + self.height_delta,
        )


@dataclass(frozen=True, slots=True)
class BlinkSettings:
    """An expression's blink cadence, in micro-motion steps."""

    enabled: bool = True
    min_interval_steps: int = 70
    max_interval_steps: int = 170
    duration_steps: int = 5


class BlinkMotion:
    """Wait a drawn interval, close both eyes, reopen, then draw the next.

    A blink armed with ``duration_steps`` keeps the eyes closed for
    ``duration_steps - 1`` steps: the step that counts the duration down to
    zero is already open.
    """

    def __init__(self, rng: RandomSource = random):
        self._rng = rng
        self._settings = BlinkSettings()
        self._interval: int | None = None
        self._elapsed = 0
        self._remaining = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def configure(self, settings: BlinkSettings) -> None:
        """Adopt new cadence and redraw the interval; the elapsed count is kept."""

        self._settings = settings
        self._interval = self._draw_interval()

    def step(self) -> bool:
        """Advance one step and return whether the eyes are closed."""

        if not self._settings.enabled:
            self._closed = False
            return self._closed

        if self._remaining > 0:
            self._remaining -= 1
            self._closed = self._remaining > 0
            if self._remaining == 0:
                self._elapsed = 0
                self._interval = self._draw_interval()
            return self._closed

        if self._interval is None:
            self._interval = self._draw_interval()
        self._elapsed += 1
        if self._elapsed >= self._interval:
            self._remaining = self._settings.duration_steps
        return self._closed

    def _draw_interval(self) -> int:
        low = min(self._settings.min_interval_steps, self._settings.max_interval_steps)
        high = max(self._settings.min_interval_steps, self._settings.max_interval_steps)
        return self._rng.randint(low, high)
