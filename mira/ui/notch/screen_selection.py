"""Qt-free choice of the screen the notch appears on.

The notch goes on the geometrically central screen of the desktop: the screen
whose centre is nearest the centre of the bounding rectangle of all screens.
Distance is squared Euclidean between centres, so no square root is taken and
ties compare exactly for the whole-pixel geometries window systems report.

Screens are identified only by their geometry — never by name, index or size
— so the same arrangement selects the same screen however it is listed. Only
screens exactly as close to the centre tie; those are ranked here by geometry
alone, and a window-system adapter may prefer one of them (such as its primary
screen) before falling back to that ranking.
"""

from __future__ import annotations

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
