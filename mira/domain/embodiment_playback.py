"""Deterministic, renderer-independent interpolation of embodiment poses."""

from __future__ import annotations

import math
from dataclasses import dataclass

from mira.domain.embodiment_frame import (
    EmbodimentFrame,
    ExpressionDefinition,
    EyeFrame,
    FACE_HEIGHT_UNITS,
    FACE_WIDTH_UNITS,
)


REFERENCE_DT_SECONDS = 0.030

_OFFSET_ALPHA = 0.10
_SCALE_ALPHA = 0.07
_CORNER_RADIUS_ALPHA = 0.10
_EYELID_ALPHA = 0.10


@dataclass(frozen=True, slots=True)
class PlaybackPose:
    """Shared pre-asymmetry pose in the legacy face coordinate space."""

    offset_x: float = 0.0
    offset_y: float = 0.0
    width_scale: float = 1.0
    height_scale: float = 1.0
    corner_radius: float = 28.0
    eyelid_tired: float = 0.0
    eyelid_angry: float = 0.0
    eyelid_happy: float = 0.0

    @classmethod
    def from_definition(cls, definition: ExpressionDefinition) -> "PlaybackPose":
        return cls(
            offset_x=definition.offset_x,
            offset_y=definition.offset_y,
            width_scale=definition.width_scale,
            height_scale=definition.height_scale,
            corner_radius=definition.corner_radius,
            eyelid_tired=definition.eyelid_tired,
            eyelid_angry=definition.eyelid_angry,
            eyelid_happy=definition.eyelid_happy,
        )


@dataclass(frozen=True, slots=True)
class EyeAsymmetry:
    """Per-eye modifiers resolved after shared-pose interpolation."""

    offset_y_left: float = 0.0
    offset_y_right: float = 0.0
    height_left: float = 1.0
    height_right: float = 1.0

    @classmethod
    def from_definition(cls, definition: ExpressionDefinition) -> "EyeAsymmetry":
        return cls(
            offset_y_left=definition.asymmetry_offset_y_left,
            offset_y_right=definition.asymmetry_offset_y_right,
            height_left=definition.asymmetry_height_left,
            height_right=definition.asymmetry_height_right,
        )


class EmbodimentPlayback:
    """Advance one shared pose toward explicit targets using elapsed time."""

    def __init__(self, initial_pose: PlaybackPose | None = None):
        self._current_pose = initial_pose or PlaybackPose()

    @property
    def current_pose(self) -> PlaybackPose:
        return self._current_pose

    def advance(
        self,
        target_pose: PlaybackPose,
        dt_seconds: float,
        *,
        asymmetry: EyeAsymmetry = EyeAsymmetry(),
        left_eye_closed: bool = False,
        right_eye_closed: bool = False,
    ) -> EmbodimentFrame:
        """Advance by ``dt_seconds`` and return the newly resolved frame."""

        if not math.isfinite(dt_seconds) or dt_seconds < 0.0:
            raise ValueError("dt_seconds must be finite and non-negative")
        if dt_seconds == 0.0:
            return self.get_frame(
                asymmetry=asymmetry,
                left_eye_closed=left_eye_closed,
                right_eye_closed=right_eye_closed,
            )

        offset_alpha = _effective_alpha(_OFFSET_ALPHA, dt_seconds)
        scale_alpha = _effective_alpha(_SCALE_ALPHA, dt_seconds)
        corner_alpha = _effective_alpha(_CORNER_RADIUS_ALPHA, dt_seconds)
        eyelid_alpha = _effective_alpha(_EYELID_ALPHA, dt_seconds)
        current = self._current_pose
        self._current_pose = PlaybackPose(
            offset_x=_lerp(current.offset_x, target_pose.offset_x, offset_alpha),
            offset_y=_lerp(current.offset_y, target_pose.offset_y, offset_alpha),
            width_scale=_lerp(
                current.width_scale, target_pose.width_scale, scale_alpha
            ),
            height_scale=_lerp(
                current.height_scale, target_pose.height_scale, scale_alpha
            ),
            corner_radius=_lerp(
                current.corner_radius, target_pose.corner_radius, corner_alpha
            ),
            eyelid_tired=_lerp(
                current.eyelid_tired, target_pose.eyelid_tired, eyelid_alpha
            ),
            eyelid_angry=_lerp(
                current.eyelid_angry, target_pose.eyelid_angry, eyelid_alpha
            ),
            eyelid_happy=_lerp(
                current.eyelid_happy, target_pose.eyelid_happy, eyelid_alpha
            ),
        )
        return self.get_frame(
            asymmetry=asymmetry,
            left_eye_closed=left_eye_closed,
            right_eye_closed=right_eye_closed,
        )

    def get_frame(
        self,
        *,
        asymmetry: EyeAsymmetry = EyeAsymmetry(),
        left_eye_closed: bool = False,
        right_eye_closed: bool = False,
    ) -> EmbodimentFrame:
        """Resolve the current shared pose into immutable per-eye output."""

        pose = self._current_pose
        common = {
            "offset_x": pose.offset_x / FACE_WIDTH_UNITS,
            "width_scale": pose.width_scale,
            "corner_radius": pose.corner_radius / FACE_WIDTH_UNITS,
            "tired_lid": pose.eyelid_tired,
            "angry_lid": pose.eyelid_angry,
            "happy_lid": pose.eyelid_happy,
        }
        return EmbodimentFrame(
            left_eye=EyeFrame(
                **common,
                offset_y=(pose.offset_y + asymmetry.offset_y_left)
                / FACE_HEIGHT_UNITS,
                height_scale=pose.height_scale * asymmetry.height_left,
                closed=left_eye_closed,
            ),
            right_eye=EyeFrame(
                **common,
                offset_y=(pose.offset_y + asymmetry.offset_y_right)
                / FACE_HEIGHT_UNITS,
                height_scale=pose.height_scale * asymmetry.height_right,
                closed=right_eye_closed,
            ),
        )


def _effective_alpha(legacy_alpha: float, dt_seconds: float) -> float:
    if dt_seconds == REFERENCE_DT_SECONDS:
        return legacy_alpha
    return 1.0 - (1.0 - legacy_alpha) ** (dt_seconds / REFERENCE_DT_SECONDS)


def _lerp(current: float, target: float, alpha: float) -> float:
    return current + (target - current) * alpha
