"""Gaze behaviour toward an attention target, independent of its source."""

from __future__ import annotations

import math

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment import ExpressionKey


# How far a full-strength gaze moves the eyes, in `PlaybackPose` face units.
GAZE_STRENGTH_X = 82.0
GAZE_STRENGTH_Y = 54.0

# How readily each expression lets its gaze follow attention.
_GAZE_WEIGHT = {
    ExpressionKey.IDLE: 1.00,
    ExpressionKey.LISTENING: 0.95,
    ExpressionKey.THINKING: 0.45,
    ExpressionKey.SPEAKING: 0.35,
    ExpressionKey.HAPPY: 0.80,
    ExpressionKey.CONFUSED: 0.75,
    ExpressionKey.TIRED: 0.50,
    ExpressionKey.ANGRY: 0.65,
}


def gaze_weight_for(expression: ExpressionKey) -> float:
    """Scale an expression applies to attention-driven gaze."""

    return _GAZE_WEIGHT[expression]


class GazeAttention:
    """Follow an attention target smoothly, hold it briefly, then return home.

    Advances in fixed micro-motion steps. Following starts with a quicker
    entry for ten steps. Released attention holds its last point for nine
    visible steps, then eases back to centre. While following a target it has
    settled on, the gaze adds a small orbit, so the eyes appear to examine it.
    """

    ENTRY_BOOST_STEPS = 10
    HOLD_STEPS = 10

    _FOLLOW_ALPHA = 0.18
    _ENTRY_ALPHA = 0.32
    _RETURN_ALPHA = 0.08
    _DEADZONE = 0.035
    _CURVE = 1.35

    _SETTLED_DISTANCE = 0.10
    _SCRUTINY_ALPHA = 0.08
    _SCRUTINY_CUTOFF = 0.01
    _SCRUTINY_FADE_ALPHA = 0.20
    _SCRUTINY_SPEED = 0.11
    _SCRUTINY_RADIUS_X = 0.12
    _SCRUTINY_RADIUS_Y = 0.07

    def __init__(self):
        self._target: AttentionTarget | None = None
        self._last_target = AttentionTarget()
        self._x = 0.0
        self._y = 0.0
        self._entry_boost = 0
        self._hold = 0
        self._scrutiny_phase = 0.0
        self._scrutiny_activation = 0.0
        self._scrutiny_x = 0.0
        self._scrutiny_y = 0.0

    @property
    def target(self) -> AttentionTarget | None:
        """The target being followed, or None once attention is released."""

        return self._target

    @property
    def point(self) -> AttentionTarget:
        """Where attention currently rests after smoothing."""

        return AttentionTarget.clamped(self._x, self._y)

    def look_at(self, target: AttentionTarget) -> None:
        if self._target is None:
            self._entry_boost = self.ENTRY_BOOST_STEPS
        self._target = target
        self._last_target = target

    def release(self) -> None:
        self._target = None
        self._hold = self.HOLD_STEPS

    def step(self, weight: float) -> tuple[float, float] | None:
        """Advance one step; return the gaze offset, or None when not gazing."""

        self._follow()
        self._scrutinize()
        if self._target is None and self._hold == 0:
            return None
        return self._offset(weight)

    def _follow(self) -> None:
        if self._target is not None:
            alpha = self._FOLLOW_ALPHA
            if self._entry_boost > 0:
                alpha = self._ENTRY_ALPHA
                self._entry_boost -= 1
            self._x = _lerp(self._x, self._target.x, alpha)
            self._y = _lerp(self._y, self._target.y, alpha)
            self._hold = 0
            return

        if self._hold > 0:
            self._hold -= 1
            return

        self._x = _lerp(self._x, 0.0, self._RETURN_ALPHA)
        self._y = _lerp(self._y, 0.0, self._RETURN_ALPHA)

    def _scrutinize(self) -> None:
        distance = abs(self._last_target.x - self._x) + abs(self._last_target.y - self._y)
        settled = distance < self._SETTLED_DISTANCE
        wanted = 1.0 if self._target is not None and settled else 0.0
        self._scrutiny_activation = _lerp(
            self._scrutiny_activation, wanted, self._SCRUTINY_ALPHA
        )

        if self._scrutiny_activation < self._SCRUTINY_CUTOFF:
            self._scrutiny_x = _lerp(self._scrutiny_x, 0.0, self._SCRUTINY_FADE_ALPHA)
            self._scrutiny_y = _lerp(self._scrutiny_y, 0.0, self._SCRUTINY_FADE_ALPHA)
            return

        self._scrutiny_phase += self._SCRUTINY_SPEED
        self._scrutiny_x = (
            math.cos(self._scrutiny_phase) * self._SCRUTINY_RADIUS_X * self._scrutiny_activation
        )
        self._scrutiny_y = (
            math.sin(self._scrutiny_phase) * self._SCRUTINY_RADIUS_Y * self._scrutiny_activation
        )

    def _offset(self, weight: float) -> tuple[float, float]:
        look_x = self._shape(self._x)
        look_y = self._shape(self._y)

        norm = look_x**2 + look_y**2
        if norm > 1.0:
            scale = 1.0 / math.sqrt(norm)
            look_x *= scale
            look_y *= scale

        look_x += self._scrutiny_x
        look_y += self._scrutiny_y
        return (look_x * GAZE_STRENGTH_X * weight, look_y * GAZE_STRENGTH_Y * weight)

    def _shape(self, value: float) -> float:
        if abs(value) < self._DEADZONE:
            value = 0.0
        return math.copysign(abs(value) ** self._CURVE, value)


def gaze_reach(weight: float) -> tuple[float, float]:
    """The largest offset magnitudes a gaze at ``weight`` can produce.

    The shaped look is limited to the unit disc, and the scrutiny orbit is
    added on top of that, so each axis reaches at most its strength times one
    plus its orbit radius.
    """

    return (
        GAZE_STRENGTH_X * (1.0 + GazeAttention._SCRUTINY_RADIUS_X) * weight,
        GAZE_STRENGTH_Y * (1.0 + GazeAttention._SCRUTINY_RADIUS_Y) * weight,
    )


def _lerp(current: float, target: float, alpha: float) -> float:
    return current + (target - current) * alpha
