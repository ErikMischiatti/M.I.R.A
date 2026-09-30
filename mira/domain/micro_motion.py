"""Renderer-independent micro-motions layered on an expression's pose."""

from __future__ import annotations

from dataclasses import dataclass, replace

from mira.domain.embodiment_playback import PlaybackPose


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
