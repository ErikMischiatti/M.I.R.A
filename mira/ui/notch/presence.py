"""Qt-free description of how present the notch is and where it sits.

`NotchPresence` says how much of the notch is shown. It says nothing about what
the assistant is doing: activity and affect stay in `EmbodimentIntent`, which
the eyes inside the notch render. This module only turns a presence, or a
point between two presences, into bounds on a screen area; it owns no
lifecycle, timer or window and imports nothing from Qt or the rest of `mira`.

Coordinates are logical pixels in the same space as the screen area passed in,
normally a screen's available geometry, so the notch hangs from the bottom of
any desktop panel along the top edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotchPresence(StrEnum):
    """How much of the notch is shown."""

    HIDDEN = "hidden"
    COMPACT = "compact"
    EXPANDED = "expanded"

    @property
    def is_visible(self) -> bool:
        return self is not NotchPresence.HIDDEN


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle; +y points down, as on screen."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        if self.width < 0 or self.height < 0:
            raise ValueError(f"rect size must be non-negative, got {self.width}x{self.height}")

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0


@dataclass(frozen=True, slots=True)
class NotchSize:
    """The notch's extent and the radius of its rounded lower corners."""

    width: float
    height: float
    corner_radius: float = 0.0

    def __post_init__(self):
        if self.width < 0 or self.height < 0:
            raise ValueError(f"notch size must be non-negative, got {self.width}x{self.height}")
        if not 0 <= self.corner_radius <= min(self.width, self.height) / 2.0:
            raise ValueError(
                f"corner radius {self.corner_radius} does not fit a "
                f"{self.width}x{self.height} notch"
            )

    def lerp(self, other: NotchSize, progress: float) -> NotchSize:
        """Blend linearly towards ``other``; ``progress`` is clamped to [0, 1].

        Easing is the animation's choice: it maps time to ``progress``.
        """

        t = min(max(progress, 0.0), 1.0)
        return NotchSize(
            width=self.width + (other.width - self.width) * t,
            height=self.height + (other.height - self.height) * t,
            corner_radius=self.corner_radius + (other.corner_radius - self.corner_radius) * t,
        )


@dataclass(frozen=True, slots=True)
class NotchShape:
    """Where the notch is drawn: its bounds and lower-corner radius."""

    bounds: Rect
    corner_radius: float


@dataclass(frozen=True, slots=True)
class NotchGeometry:
    """The notch's size at each presence and how it hangs from the top edge.

    Hidden is the compact notch retracted to zero height into the top edge, so
    showing it grows it downward and every presence shares one top-centre
    anchor. The expanded size must contain the compact one: a window sized to
    the expanded bounds then contains the notch at every presence.
    """

    compact: NotchSize = NotchSize(width=200.0, height=32.0, corner_radius=14.0)
    expanded: NotchSize = NotchSize(width=360.0, height=96.0, corner_radius=24.0)
    top_margin: float = 0.0

    def __post_init__(self):
        if self.expanded.width < self.compact.width or self.expanded.height < self.compact.height:
            raise ValueError("the expanded notch must contain the compact notch")
        if self.top_margin < 0:
            raise ValueError(f"top margin must be non-negative, got {self.top_margin}")

    def size_for(self, presence: NotchPresence) -> NotchSize:
        if presence is NotchPresence.HIDDEN:
            return NotchSize(width=self.compact.width, height=0.0)
        if presence is NotchPresence.COMPACT:
            return self.compact
        return self.expanded

    def size_between(
        self, start: NotchPresence, end: NotchPresence, progress: float
    ) -> NotchSize:
        """The size ``progress`` of the way from ``start`` to ``end``."""

        return self.size_for(start).lerp(self.size_for(end), progress)

    def place(self, area: Rect, size: NotchSize) -> NotchShape:
        """Centre ``size`` along the top of ``area``, shrunk to fit if needed.

        A notch wider or taller than the area is clamped to it, and its radius
        with it; an empty area yields empty bounds at its top centre.
        """

        top = area.y + min(self.top_margin, area.height)
        width = min(size.width, area.width)
        height = min(size.height, area.bottom - top)
        radius = min(size.corner_radius, width / 2.0, height / 2.0)
        return NotchShape(
            bounds=Rect(x=area.center_x - width / 2.0, y=top, width=width, height=height),
            corner_radius=radius,
        )

    def shape_for(self, area: Rect, presence: NotchPresence) -> NotchShape:
        return self.place(area, self.size_for(presence))

    def shape_between(
        self, area: Rect, start: NotchPresence, end: NotchPresence, progress: float
    ) -> NotchShape:
        return self.place(area, self.size_between(start, end, progress))
