"""Where the embodiment should look, independent of what noticed it."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AttentionTarget:
    """A point of attention in the face's normalized view space.

    ``x`` and ``y`` lie in [-1, 1]. (0, 0) is straight ahead, +x is toward the
    viewer's right and +y is downward, the same axes as `EmbodimentFrame`
    offsets. Each input source converts its own coordinates into this space,
    so gaze behaviour never sees pixels, widgets or devices.
    """

    x: float = 0.0
    y: float = 0.0

    def __post_init__(self) -> None:
        for axis, value in (("x", self.x), ("y", self.y)):
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"attention {axis} must be finite and within [-1, 1]")

    @classmethod
    def clamped(cls, x: float, y: float) -> "AttentionTarget":
        """Build a target from a point that may lie outside the view space."""

        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("attention coordinates must be finite")
        return cls(x=max(-1.0, min(1.0, x)), y=max(-1.0, min(1.0, y)))
