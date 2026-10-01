"""Qt-free placement of the embodiment's eyes inside the notch.

The notch shows the same `EmbodimentFrame` as the face, through the same
painter, at one fixed scale. The layout depends only on the notch geometry and
the window, never on presence or transition progress: expanding reveals space
around the eyes instead of growing them.

Frame offsets are in face design units (`FACE_WIDTH_UNITS` by
`FACE_HEIGHT_UNITS`), so one scale in pixels per face unit maps every offset,
size and radius; directions are preserved and only the amplitude changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from mira.ui.notch.presence import NotchGeometry, Rect


# The face's resting eye, in face units: FaceWidget's 0.18 x 0.22 of 700 x 450.
BASE_EYE_WIDTH_UNITS = 126.0
BASE_EYE_HEIGHT_UNITS = 99.0

# The base eye is this share of the compact notch's height. The tallest
# standard profile (idle) doubles the base height, so it fills 3/4 of the
# compact notch and leaves an eighth above and below it.
BASE_EYE_HEIGHT_SHARE = 0.375
TALLEST_EYE_HEIGHT_SCALE = 2.0

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
    in face units. The defaults are the production layout.
    """

    height_share: float = BASE_EYE_HEIGHT_SHARE
    top_offset: float = 0.0
    center_offset: float = 0.0
    spacing: float = EYE_SPACING_UNITS

    def __post_init__(self):
        if self.height_share <= 0:
            raise ValueError(f"eye height share must be positive, got {self.height_share}")
        if self.spacing < 0:
            raise ValueError(f"eye spacing must be non-negative, got {self.spacing}")


@dataclass(frozen=True, slots=True)
class NotchEyeLayout:
    """Where the resting eyes sit, in window pixels, and the one face scale.

    ``scale`` converts face units to pixels. ``center_x`` is midway between
    the eyes and ``top`` is the resting eyes' top edge; eyes grow downward from
    it, as in the face. ``spacing``, ``eye_width`` and ``eye_height`` are in
    face units.
    """

    scale: float
    center_x: float
    top: float
    spacing: float = EYE_SPACING_UNITS
    eye_width: float = BASE_EYE_WIDTH_UNITS
    eye_height: float = BASE_EYE_HEIGHT_UNITS

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


def notch_eye_layout(
    geometry: NotchGeometry, window: Rect, style: NotchEyeStyle = NotchEyeStyle()
) -> NotchEyeLayout:
    """The eye layout for ``geometry`` in a window of ``window``'s size.

    Derived from the compact notch and ``style`` alone, so it is identical for
    every presence and every point of a transition.
    """

    base_height_px = geometry.compact.height * style.height_share
    tallest_px = base_height_px * TALLEST_EYE_HEIGHT_SCALE
    return NotchEyeLayout(
        scale=base_height_px / BASE_EYE_HEIGHT_UNITS,
        center_x=window.width / 2.0 + style.center_offset,
        top=(geometry.compact.height - tallest_px) / 2.0 + style.top_offset,
        spacing=style.spacing,
    )
