"""Qt-free choice of the screen the notch appears on.

`select_screen` is the whole policy. A preferred monitor that is connected is
always used. Otherwise, or with no preference, the automatic policy depends on
how many screens there are:

- one screen: that screen;
- two screens: the primary screen — normally the laptop panel — falling back
  to a built-in panel by connector name, then to geometric order;
- three or more: the geometrically central screen.

A preference for a monitor that is not connected falls back to the automatic
choice without being forgotten, so the notch returns to that monitor as soon
as it is plugged in again.

The geometrically central screen is the screen whose centre is nearest the
centre of the bounding rectangle of all screens. Distance is squared Euclidean
between centres, so no square root is taken and ties compare exactly for the
whole-pixel geometries window systems report.

No choice ever depends on the order screens are listed in or on their
enumeration index: the automatic policy reads geometry and the primary flag,
breaking ties by geometric order and then connector name, and a preference
names a monitor by `MonitorIdentity`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from mira.ui.notch.presence import Rect


def desktop_bounds(screens: Sequence[Rect]) -> Rect:
    """The smallest rectangle containing every screen."""

    if not screens:
        raise ValueError("no screens")
    left = min(screen.x for screen in screens)
    top = min(screen.y for screen in screens)
    right = max(screen.right for screen in screens)
    bottom = max(screen.bottom for screen in screens)
    return Rect(x=left, y=top, width=right - left, height=bottom - top)


def distance_squared_to_desktop_centre(screen: Rect, desktop: Rect) -> float:
    dx = screen.center_x - desktop.center_x
    dy = (screen.y + screen.height / 2.0) - (desktop.y + desktop.height / 2.0)
    return dx * dx + dy * dy


def geometric_order(screen: Rect) -> tuple[float, float, float, float]:
    """A total order on geometries: leftmost, then topmost, then smaller."""

    return (screen.x, screen.y, screen.width, screen.height)


def central_screens(screens: Sequence[Rect]) -> list[int]:
    """Indices of the screens nearest the desktop centre, in geometric order.

    More than one index means an exact tie, such as two equal screens side by
    side. Screens with identical geometry cannot be told apart geometrically;
    among those the lower index comes first.
    """

    desktop = desktop_bounds(screens)
    distances = [distance_squared_to_desktop_centre(screen, desktop) for screen in screens]
    nearest = min(distances)
    tied = [index for index, distance in enumerate(distances) if distance == nearest]
    return sorted(tied, key=lambda index: (geometric_order(screens[index]), index))


def select_central_screen(screens: Sequence[Rect]) -> int:
    """The index of the central screen, breaking ties by geometric order."""

    return central_screens(screens)[0]


# --- Monitor identity and preference -------------------------------------------


@dataclass(frozen=True, slots=True)
class MonitorIdentity:
    """What a monitor reports about itself, as stable as the platform allows.

    ``name`` is the connector (``eDP-1``, ``HDMI-1``): stable for a port but not
    for a monitor moved to another port. ``manufacturer``, ``model`` and
    ``serial`` come from the monitor's EDID and may be empty. Enumeration
    indices are never part of an identity: they change with hot-plugging.
    """

    name: str
    manufacturer: str = ""
    model: str = ""
    serial: str = ""

    def matches(self, other: MonitorIdentity) -> bool:
        """Whether ``other`` is the same monitor.

        With a serial number on both sides the EDID triple decides, so a
        monitor moved to another connector is still recognised. Without one,
        the connector name and whatever EDID data exists must all agree.
        """

        if self.serial and other.serial:
            return (self.manufacturer, self.model, self.serial) == (
                other.manufacturer,
                other.model,
                other.serial,
            )
        return (self.name, self.manufacturer, self.model) == (
            other.name,
            other.manufacturer,
            other.model,
        )

    @property
    def label(self) -> str:
        """A short human-readable description: ``HDMI-1 (Philips 298P4)``."""

        detail = self.model or self.manufacturer
        return f"{self.name} ({detail})" if detail else self.name


@dataclass(frozen=True, slots=True)
class ScreenInfo:
    """One connected screen as the selection policy sees it."""

    identity: MonitorIdentity
    geometry: Rect
    primary: bool = False


@dataclass(frozen=True, slots=True)
class MonitorPreference:
    """Which monitor the notch should use; no monitor means automatic."""

    monitor: MonitorIdentity | None = None

    @property
    def is_automatic(self) -> bool:
        return self.monitor is None


class SelectionMode(StrEnum):
    """How `select_screen` arrived at its choice."""

    PREFERRED = "preferred"
    AUTOMATIC = "automatic"
    # A monitor was preferred but is not connected; the automatic choice applies.
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class ScreenSelection:
    """The chosen screen's index, or None for no screens, and why."""

    index: int | None
    mode: SelectionMode


# Connector-name prefixes of built-in laptop panels on Linux (DRM/RandR).
INTERNAL_PANEL_PREFIXES = ("eDP", "LVDS", "DSI")


def is_internal_panel(identity: MonitorIdentity) -> bool:
    return identity.name.startswith(INTERNAL_PANEL_PREFIXES)


def _identity_order(screens: Sequence[ScreenInfo], index: int):
    screen = screens[index]
    return (geometric_order(screen.geometry), screen.identity.name, index)


def automatic_screen(screens: Sequence[ScreenInfo]) -> int | None:
    """The automatic choice for ``screens``: see the module docstring.

    Ties are broken by primary, then geometric order, then connector name, so
    the result depends on the arrangement and never on the listing order.
    """

    if not screens:
        return None
    if len(screens) == 1:
        return 0
    if len(screens) == 2:
        for preferred in (lambda s: s.primary, lambda s: is_internal_panel(s.identity)):
            candidates = [index for index, screen in enumerate(screens) if preferred(screen)]
            if candidates:
                return min(candidates, key=lambda index: _identity_order(screens, index))
        return min(range(2), key=lambda index: _identity_order(screens, index))
    tied = central_screens([screen.geometry for screen in screens])
    primaries = [index for index in tied if screens[index].primary]
    return min(primaries or tied, key=lambda index: _identity_order(screens, index))


def select_screen(
    screens: Sequence[ScreenInfo], preference: MonitorPreference = MonitorPreference()
) -> ScreenSelection:
    """The screen the notch should use under ``preference``."""

    if preference.monitor is not None:
        matching = [
            index for index, screen in enumerate(screens) if preference.monitor.matches(screen.identity)
        ]
        if matching:
            # Several matches only for identical monitors without serials on
            # one connector name, which cannot happen; prefer the exact name.
            exact = [index for index in matching if screens[index].identity.name == preference.monitor.name]
            index = min(exact or matching, key=lambda index: _identity_order(screens, index))
            return ScreenSelection(index, SelectionMode.PREFERRED)
        return ScreenSelection(automatic_screen(screens), SelectionMode.FALLBACK)
    return ScreenSelection(automatic_screen(screens), SelectionMode.AUTOMATIC)
