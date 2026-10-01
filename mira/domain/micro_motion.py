"""Renderer-independent micro-motions layered on an expression's pose.

Micro-motions advance in fixed steps: one `step()` per rendered frame at the
playback reference rate. Only `EmbodimentPlayback` interpolates by elapsed
time. Randomness comes from an injected source; the default is the `random`
module so production keeps drawing from the process-wide generator.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from typing import Protocol

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.gaze import GazeAttention


class RandomSource(Protocol):
    """The draws micro-motions need; `random` and `random.Random` both fit."""

    def randint(self, a: int, b: int) -> int: ...

    def uniform(self, a: float, b: float) -> float: ...


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


class IdleGaze:
    """Wander to a random offset: after 60 steps, then every 40–90 steps."""

    FIRST_INTERVAL_STEPS = 60
    INTERVAL_BOUNDS = (40, 90)

    def __init__(self, rng: RandomSource = random):
        self._rng = rng
        self._interval = self.FIRST_INTERVAL_STEPS
        self._elapsed = 0

    def step(self, amplitude_x: float, amplitude_y: float) -> tuple[float, float] | None:
        """Advance one step; return a new wander offset when one is due."""

        self._elapsed += 1
        if self._elapsed < self._interval:
            return None
        self._elapsed = 0
        self._interval = self._rng.randint(*self.INTERVAL_BOUNDS)
        return (
            self._rng.uniform(-amplitude_x, amplitude_x),
            self._rng.uniform(-amplitude_y, amplitude_y),
        )


# The drift and the pulse read one shared phase, so a thinking drift that
# follows speech continues where the pulse left it.
_PHASE_STEP = 0.05
_SPEAKING_PHASE_STEP = 0.22


# The thinking drift swings between these offsets; the speaking pulse adds at
# most this much height. Named so that motion bounds can be derived from them.
THINKING_DRIFT_RANGE = (-16.0, 0.0)
SPEAKING_PULSE_MAX = 0.16


def thinking_drift(phase: float) -> float:
    """Horizontal offset of the slow sideways glance while thinking."""

    low, high = THINKING_DRIFT_RANGE
    middle, amplitude = (low + high) / 2.0, (high - low) / 2.0
    return middle + amplitude * math.sin(phase * 0.35)


def speaking_pulse(phase: float) -> float:
    """Height added by the rhythmic widening while speaking."""

    return SPEAKING_PULSE_MAX * abs(math.sin(phase))


def gaze_deformation(offset_x: float, offset_y: float) -> tuple[float, float]:
    """Width and height deltas that make a displaced eye read as turned.

    Looking sideways widens and flattens the eye; looking up lifts it slightly
    and looking down squashes it.
    """

    horizontal = min(abs(offset_x) / 25.0, 1.0)
    vertical = min(abs(offset_y) / 20.0, 1.0)
    if offset_y < 0:
        vertical_adjust = 0.04 * vertical
    elif offset_y > 0:
        vertical_adjust = -0.06 * vertical
    else:
        vertical_adjust = 0.0
    return (0.10 * horizontal, vertical_adjust - 0.12 * horizontal)


@dataclass(frozen=True, slots=True)
class MotionSettings:
    """Which micro-motions an expression uses, and how strongly."""

    blink: BlinkSettings = BlinkSettings()
    idle_gaze: bool = False
    idle_amplitude_x: float = 18.0
    idle_amplitude_y: float = 10.0
    thinking_drift: bool = False
    speaking_pulse: bool = False
    gaze_weight: float = 1.0


class MicroMotions:
    """Compose an expression's micro-motions into one state per step.

    Each step layers, in order: blink; idle wander (or the neutral offset when
    idle gaze is off); thinking drift, which replaces the offset; speaking
    pulse; gaze toward attention, which replaces the offset again; and the
    deformation that the final offset implies. The offset persists between
    steps until a later contribution replaces it or `configure` resets it.

    Attention arrives as an `AttentionTarget` from any source; the gaze keeps
    following it across expression changes until it is released.
    """

    def __init__(self, rng: RandomSource = random):
        self._blink = BlinkMotion(rng)
        self._idle = IdleGaze(rng)
        self._gaze = GazeAttention()
        self._settings = MotionSettings()
        self._offset = (0.0, 0.0)
        self._phase = 0.0
        self._state = MicroMotionState()

    @property
    def state(self) -> MicroMotionState:
        return self._state

    def configure(self, settings: MotionSettings) -> None:
        """Switch to an expression's motions from its neutral pose.

        The wander offset is discarded; blink and idle schedules and the shared
        phase continue.
        """

        self._settings = settings
        self._blink.configure(settings.blink)
        self._offset = (0.0, 0.0)
        self._state = MicroMotionState(eyes_closed=self._blink.closed)

    @property
    def attention_target(self) -> AttentionTarget | None:
        return self._gaze.target

    def set_attention_target(self, target: AttentionTarget) -> None:
        """Start or keep following ``target``."""

        self._gaze.look_at(target)

    def clear_attention_target(self) -> None:
        """Release attention; the gaze holds briefly, then returns home."""

        self._gaze.release()

    def step(self) -> MicroMotionState:
        """Advance every micro-motion one step and return the combined state."""

        settings = self._settings
        eyes_closed = self._blink.step()

        if settings.idle_gaze:
            wander = self._idle.step(settings.idle_amplitude_x, settings.idle_amplitude_y)
            if wander is not None:
                self._offset = wander
        else:
            self._offset = (0.0, 0.0)

        gaze_offset = self._gaze.step(settings.gaze_weight)

        pulse = 0.0
        if settings.thinking_drift:
            self._offset = (thinking_drift(self._phase), 0.0)
        if settings.speaking_pulse:
            self._phase += _SPEAKING_PHASE_STEP
            pulse = speaking_pulse(self._phase)
        if gaze_offset is not None:
            self._offset = gaze_offset

        width_delta, height_delta = gaze_deformation(*self._offset)
        self._phase += _PHASE_STEP

        self._state = MicroMotionState(
            offset_x=self._offset[0],
            offset_y=self._offset[1],
            width_delta=width_delta,
            height_delta=pulse + height_delta,
            eyes_closed=eyes_closed,
        )
        return self._state
