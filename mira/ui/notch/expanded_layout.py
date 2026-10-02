"""Qt-free layout of the expanded notch's content: a symbol and a short label.

The eyes keep the placement they have in the compact notch
(`notch_eye_layout`): expanding never moves or rescales them. The content
group sits below them, positioned *relative to the eyes*:

    ╭──────────────── expanded ────────────────╮
    │            ( eyes' motion envelope )      │
    │                    ↕ eye_gap_px           │
    │       [symbol] ↔ spacing_px ↔ short text  │  <- content group
    ╰───────────────────────────────────────────╯

`NotchContentStyle` holds the four tunable values: the symbol's side, the
label's pixel size, the symbol–text spacing and the gap between the bottom of
everything the eyes can reach (their whole motion envelope, not just the
resting eye) and the top of the group. Anchoring to the envelope rather than
to absolute coordinates means a valid gap can never put content on an eye,
whatever expression or gaze plays, and changing the eyes moves the content
with them. The group is centred horizontally.

Whether a style fits is `content_problems`: the group must end inside the
expanded notch's safe region, and the symbol must fit between the rounded
lower corners at the group's height. The label never makes a style invalid:
it is elided to the width left. A style that does not fit is rejected by
`visual_config`, like eyes that would clip, and the notch keeps its last
valid configuration.

The group is fixed by the geometry, not by the size drawn mid-transition: the
content does not slide while the notch grows, it is revealed by the outline
(the painter clips to it) and faded in by `reveal_fraction`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mira.ui.notch.presence import NotchGeometry, NotchSize, Rect


MIN_SYMBOL_PX, MAX_SYMBOL_PX = 8.0, 96.0
MIN_TEXT_PX, MAX_TEXT_PX = 6.0, 72.0
MAX_SPACING_PX = 80.0
MAX_EYE_GAP_PX = 200.0

# A label's box is taller than its pixel size: ascent plus descent.
LINE_HEIGHT_SHARE = 1.25

# Kept clear between the content and the notch outline at the sides.
SIDE_PADDING_PX = 8.0

# Content stays this far inside the outline, as the eyes do
# (`visual_config.SAFE_MARGIN_PX`; repeated here to keep the import one-way).
CONTENT_SAFE_MARGIN_PX = 1.0


@dataclass(frozen=True, slots=True)
class NotchContentStyle:
    """How large the expanded content is and where it sits below the eyes.

    All in pixels. The defaults are production for the default notch
    geometry; files written before this style existed are migrated to
    `legacy_content_style`, which reproduces their previous layout.
    """

    symbol_px: float = 26.0
    text_px: float = 18.0
    spacing_px: float = 10.0
    eye_gap_px: float = 19.0

    def __post_init__(self):
        for name, value, low, high in (
            ("symbol size", self.symbol_px, MIN_SYMBOL_PX, MAX_SYMBOL_PX),
            ("text size", self.text_px, MIN_TEXT_PX, MAX_TEXT_PX),
            ("symbol-text spacing", self.spacing_px, 0.0, MAX_SPACING_PX),
            ("gap below the eyes", self.eye_gap_px, 0.0, MAX_EYE_GAP_PX),
        ):
            if not (isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high):
                raise ValueError(f"content {name} must be within {low:g}..{high:g} px, got {value!r}")

    @property
    def group_height(self) -> float:
        return max(self.symbol_px, self.text_px * LINE_HEIGHT_SHARE)


@dataclass(frozen=True, slots=True)
class ContentRow:
    """Where the expanded content goes, in window-local pixels.

    ``area`` is the band the group is centred in horizontally; its height is
    the group's.
    """

    area: Rect
    symbol_px: float
    label_px: float
    gap_px: float

    @property
    def center_y(self) -> float:
        return self.area.y + self.area.height / 2.0


def corner_inset(size: NotchSize, top: float, bottom: float) -> float:
    """How far the rounded lower corners reach in over ``top``..``bottom``.

    The outline is flat-topped with lower corners of ``size.corner_radius``;
    the deepest point of the span is the narrowest.
    """

    radius = size.corner_radius
    corner_top = size.height - radius
    y = min(max(bottom, top), size.height)
    if radius <= 0 or y <= corner_top:
        return 0.0
    depth = y - corner_top
    return radius - math.sqrt(max(radius * radius - depth * depth, 0.0))


def content_row(window: Rect, geometry: NotchGeometry, style: NotchContentStyle, eye_bottom: float) -> ContentRow:
    """The content group for ``style`` below eyes that reach down to ``eye_bottom``.

    ``eye_bottom`` is the lowest point of the eyes' motion envelope in window
    pixels. Always returns a row; whether it fits is `content_problems`.
    """

    expanded = geometry.expanded
    top = eye_bottom + style.eye_gap_px
    height = style.group_height
    inset = corner_inset(expanded, top, top + height) + SIDE_PADDING_PX
    width = max(expanded.width - 2.0 * inset, 0.0)
    return ContentRow(
        area=Rect(x=window.width / 2.0 - width / 2.0, y=top, width=width, height=height),
        symbol_px=style.symbol_px,
        label_px=style.text_px,
        gap_px=style.spacing_px,
    )


# The integrated controls are pills laid out across the content group's band.
CONTROL_MAX_WIDTH_PX = 150.0
CONTROL_GAP_PX = 12.0


def control_cells(row: ContentRow, count: int) -> list[Rect]:
    """Where each of ``count`` controls goes: equal pills, centred in the row.

    Each is as tall as the row and at most `CONTROL_MAX_WIDTH_PX` wide, so
    the targets are large enough to click on a notch of any width.
    """

    if count <= 0 or row.area.width <= 0:
        return []
    gaps = CONTROL_GAP_PX * (count - 1)
    width = min(CONTROL_MAX_WIDTH_PX, max((row.area.width - gaps) / count, 0.0))
    total = width * count + gaps
    left = row.area.x + (row.area.width - total) / 2.0
    return [Rect(left + index * (width + CONTROL_GAP_PX), row.area.y, width, row.area.height) for index in range(count)]


def content_problems(row: ContentRow, geometry: NotchGeometry) -> list[str]:
    """Why ``row`` would not fit the expanded notch; empty when it does."""

    problems = []
    limit = geometry.expanded.height - CONTENT_SAFE_MARGIN_PX
    excess = row.area.bottom - limit
    if excess > 1e-9:
        problems.append(f"expanded content leaves the notch bottom by {excess:.2f} px")
    if row.area.width < row.symbol_px:
        problems.append(
            f"expanded content symbol ({row.symbol_px:.1f} px) is wider than the room between the corners "
            f"({row.area.width:.1f} px)"
        )
    return problems


# The layout before `NotchContentStyle`: the symbol a share of the band below
# the compact notch, capped, the label and spacing proportional to it, the
# group centred in that band. Only migration uses it.
_LEGACY_SYMBOL_SHARE = 0.34
_LEGACY_MAX_SYMBOL_PX = 20.0
_LEGACY_LABEL_SHARE = 0.72
_LEGACY_GAP_SHARE = 0.5


def legacy_content_style(geometry: NotchGeometry, eye_bottom: float) -> NotchContentStyle:
    """The style that draws what a pre-style configuration drew.

    Used when a file older than the content settings is loaded, so its
    content looks exactly as it did; the user can then enlarge it.
    """

    band = geometry.expanded.height - geometry.compact.height
    symbol = min(max(band * _LEGACY_SYMBOL_SHARE, MIN_SYMBOL_PX), _LEGACY_MAX_SYMBOL_PX)
    style = NotchContentStyle(
        symbol_px=symbol,
        text_px=max(symbol * _LEGACY_LABEL_SHARE, MIN_TEXT_PX),
        spacing_px=symbol * _LEGACY_GAP_SHARE,
        eye_gap_px=0.0,
    )
    centre = geometry.compact.height + band / 2.0
    gap = centre - style.group_height / 2.0 - eye_bottom
    return NotchContentStyle(
        symbol_px=style.symbol_px,
        text_px=style.text_px,
        spacing_px=style.spacing_px,
        eye_gap_px=min(max(gap, 0.0), MAX_EYE_GAP_PX),
    )


def reveal_fraction(size: NotchSize, geometry: NotchGeometry) -> float:
    """How far ``size`` has grown from compact to expanded height, in [0, 1].

    Measured by height, since height is what reveals the content. Hidden and
    compact sizes give 0.
    """

    span = geometry.expanded.height - geometry.compact.height
    if span <= 0:
        return 0.0
    return min(max((size.height - geometry.compact.height) / span, 0.0), 1.0)
