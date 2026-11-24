"""Qt-free placement of the embodiment's eyes inside the notch.

The notch shows the same `EmbodimentFrame` as the face, through the same
painter, at one fixed scale. The layout depends only on the notch geometry and
the window, never on presence or transition progress: expanding reveals space
around the eyes instead of growing them.

Frame offsets are in face design units (`FACE_WIDTH_UNITS` by
`FACE_HEIGHT_UNITS`). One scale in pixels per face unit maps every size and
radius; offsets map at that scale times `NotchEyeStyle.motion_gain`, so the
notch can show the face's motion with a smaller amplitude than the face's
proportions would give. Directions are always preserved.

The eyes are placed so that their whole motion envelope — every pose the
embodiment can reach, `mira.domain.motion_envelope` — is centred vertically
in the compact notch, not just the resting eye. Whether that envelope fits is
checked by `mira.ui.notch.visual_config`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Mapping

from mira.domain.embodiment import ExpressionKey
from mira.domain.gaze import gaze_weight_for
from mira.domain.motion_envelope import PoseEnvelope, embodiment_envelope
from mira.domain.state import FaceState
from mira.ui.face.expression_profile import ExpressionProfile
from mira.ui.face.expression_store import load_expression_library
from mira.ui.notch.presence import NotchGeometry, Rect


# The face's resting eye, in face units: FaceWidget's 0.18 x 0.22 of 700 x 450.
BASE_EYE_WIDTH_UNITS = 126.0
BASE_EYE_HEIGHT_UNITS = 99.0

# The base eye is this share of the compact notch's height, and offsets move
# at `MOTION_GAIN` of that scale. Together they are the largest balanced pair
# whose full motion envelope under the packaged expression profiles fits the
# default compact notch inside its safe margin (the share's limit at this gain
# is about 0.292); the tests pin that the defaults stay valid.
BASE_EYE_HEIGHT_SHARE = 0.28
MOTION_GAIN = 0.5

# Centre-to-centre distance between the eyes, in face units. Wider than the
# face's 196 so the pair reads as eyes across a wide, flat notch.
EYE_SPACING_UNITS = 300.0


@dataclass(frozen=True, slots=True)
class NotchEyeStyle:
    """The tunable rules `notch_eye_layout` derives the eye layout from.

    ``height_share`` is the base eye's height as a share of the compact
    notch's height, and so sets the face scale. ``top_offset`` moves the eyes
    down (positive) or up from where the tallest standard eye is centred in the
    compact notch, and ``center_offset`` moves the pair right (positive) or
    left of the window centre; both in pixels. ``spacing`` is centre-to-centre
    in face units. ``motion_gain`` scales how far frame offsets move the eyes
    relative to their size: 1 moves them as far as on the face, in proportion.
    The defaults are the production layout.
    """

    height_share: float = BASE_EYE_HEIGHT_SHARE
    top_offset: float = 0.0
    center_offset: float = 0.0
    spacing: float = EYE_SPACING_UNITS
    motion_gain: float = MOTION_GAIN

    def __post_init__(self):
        if self.height_share <= 0:
            raise ValueError(f"eye height share must be positive, got {self.height_share}")
        if self.spacing < 0:
            raise ValueError(f"eye spacing must be non-negative, got {self.spacing}")
        if not 0 < self.motion_gain <= 1:
            raise ValueError(f"motion gain must be in (0, 1], got {self.motion_gain}")


@dataclass(frozen=True, slots=True)
class NotchEyeLayout:
    """Where the resting eyes sit, in window pixels, and the one face scale.

    ``scale`` converts face-unit sizes to pixels and ``offset_scale`` converts
    face-unit offsets. ``center_x`` is midway between the eyes and ``top`` is
    the resting eyes' top edge; eyes grow downward from it, as in the face.
    ``spacing``, ``eye_width`` and ``eye_height`` are in face units.
    """

    scale: float
    center_x: float
    top: float
    spacing: float = EYE_SPACING_UNITS
    eye_width: float = BASE_EYE_WIDTH_UNITS
    eye_height: float = BASE_EYE_HEIGHT_UNITS
    offset_scale: float | None = None

    def __post_init__(self):
        if self.offset_scale is None:
            object.__setattr__(self, "offset_scale", self.scale)

    @property
    def motion_gain(self) -> float:
        return self.offset_scale / self.scale if self.scale else 1.0

    def eye_left_units(self, side: str) -> float:
        """The resting eye's left edge, in face units from the eye-pair centre."""
        half_spacing = self.spacing / 2.0
        centre = -half_spacing if side == "left" else half_spacing
        return centre - self.eye_width / 2.0

    def resting_eye(self, side: str) -> Rect:
        """A resting eye (no frame offsets or scaling) in window pixels."""
        return Rect(
            x=self.center_x + self.eye_left_units(side) * self.scale,
            y=self.top,
            width=self.eye_width * self.scale,
            height=self.eye_height * self.scale,
        )

    def motion_bounds(self, side: str, envelope: PoseEnvelope) -> Rect:
        """Everything the eye on ``side`` can cover, in window pixels.

        The eye's rectangle grows right and down from its displaced top-left
        corner, so its extremes are the extreme offsets plus the largest size.
        """

        resting = self.resting_eye(side)
        left = resting.x + envelope.offset_x_min * self.offset_scale
        top = resting.y + envelope.offset_y_min * self.offset_scale
        right = resting.x + envelope.offset_x_max * self.offset_scale + self.eye_width * envelope.width_scale_max * self.scale
        bottom = resting.y + envelope.offset_y_max * self.offset_scale + self.eye_height * envelope.height_scale_max * self.scale
        return Rect(x=left, y=top, width=right - left, height=bottom - top)


def library_envelope(library: Mapping[FaceState, ExpressionProfile]) -> PoseEnvelope:
    """The motion envelope of an expression library, as a controller plays it."""

    return embodiment_envelope(
        (
            profile.to_definition(),
            profile.to_motion_settings(gaze_weight=gaze_weight_for(ExpressionKey[state.name])),
        )
        for state, profile in library.items()
    )


@cache
def production_envelope() -> PoseEnvelope:
    """The envelope of the packaged expression profiles, read once."""

    return library_envelope(load_expression_library())


def notch_eye_layout(
    geometry: NotchGeometry,
    window: Rect,
    style: NotchEyeStyle = NotchEyeStyle(),
    envelope: PoseEnvelope | None = None,
) -> NotchEyeLayout:
    """The eye layout for ``geometry`` in a window of ``window``'s size.

    Derived from the compact notch, ``style`` and the motion ``envelope``
    (by default the packaged profiles'), so it is identical for every
    presence and every point of a transition. The envelope's vertical extent
    is centred in the compact notch, then moved by ``style.top_offset``.
    """

    envelope = production_envelope() if envelope is None else envelope
    scale = geometry.compact.height * style.height_share / BASE_EYE_HEIGHT_UNITS
    offset_scale = scale * style.motion_gain
    reach_up = envelope.offset_y_min * offset_scale
    reach_down = envelope.offset_y_max * offset_scale + BASE_EYE_HEIGHT_UNITS * envelope.height_scale_max * scale
    return NotchEyeLayout(
        scale=scale,
        center_x=window.width / 2.0 + style.center_offset,
        top=(geometry.compact.height - (reach_down - reach_up)) / 2.0 - reach_up + style.top_offset,
        spacing=style.spacing,
        offset_scale=offset_scale,
    )
