"""Qt-free layout of the expanded notch's content row.

The eyes keep the placement they have in the compact notch
(`notch_eye_layout`): expanding never moves or rescales them. The space the
expansion reveals *below* the compact notch is the content row, where a symbol
and a short label sit:

    ╭──────────── expanded ────────────╮
    │        ( eyes, compact band )    │  <- compact height
    │        symbol   short text       │  <- content row
    ╰──────────────────────────────────╯

Because a valid configuration keeps every eye pose inside the compact notch
(`visual_config`), the row can never overlap the eyes. It is inset from the
sides by the expanded corner radius so nothing reaches into a rounded corner.

The row is fixed by the geometry, not by the size drawn mid-transition: the
content does not slide while the notch grows, it is revealed by the outline
(the painter clips to it) and faded in by `reveal_fraction`.
"""

from __future__ import annotations

from dataclasses import dataclass

from mira.ui.notch.presence import NotchGeometry, NotchSize, Rect


# The symbol's side as a share of the row height, capped in pixels so a tall
# notch does not grow a large icon; the label's pixel size follows the symbol.
SYMBOL_SHARE = 0.34
MAX_SYMBOL_PX = 20.0
MIN_SYMBOL_PX = 8.0
LABEL_SHARE_OF_SYMBOL = 0.72
GAP_SHARE_OF_SYMBOL = 0.5


@dataclass(frozen=True, slots=True)
class ContentRow:
    """Where the expanded content goes, in window-local pixels."""

    area: Rect
    symbol_px: float
    label_px: float
    gap_px: float

    @property
    def center_y(self) -> float:
        return self.area.y + self.area.height / 2.0


def content_row(window: Rect, geometry: NotchGeometry) -> ContentRow | None:
    """The content row for ``geometry`` in a window of ``window``'s size.

    None when the expanded notch reveals too little height for a symbol.
    """

    compact, expanded = geometry.compact, geometry.expanded
    height = expanded.height - compact.height
    symbol = min(height * SYMBOL_SHARE, MAX_SYMBOL_PX)
    if symbol < MIN_SYMBOL_PX:
        return None
    inset = expanded.corner_radius
    width = max(expanded.width - 2.0 * inset, 0.0)
    if width < symbol:
        return None
    return ContentRow(
        area=Rect(x=window.width / 2.0 - width / 2.0, y=compact.height, width=width, height=height),
        symbol_px=symbol,
        label_px=symbol * LABEL_SHARE_OF_SYMBOL,
        gap_px=symbol * GAP_SHARE_OF_SYMBOL,
    )


def reveal_fraction(size: NotchSize, geometry: NotchGeometry) -> float:
    """How far ``size`` has grown from compact to expanded height, in [0, 1].

    Measured by height, since height is what reveals the content row. Hidden
    and compact sizes give 0.
    """

    span = geometry.expanded.height - geometry.compact.height
    if span <= 0:
        return 0.0
    return min(max((size.height - geometry.compact.height) / span, 0.0), 1.0)
