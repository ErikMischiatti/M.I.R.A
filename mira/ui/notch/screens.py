from typing import Callable, Protocol, Sequence

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QScreen

from mira.ui.notch.presence import Rect
from mira.ui.notch.screen_selection import central_screens


class ScreenLike(Protocol):
    """What screen selection reads from a `QScreen`."""

    def geometry(self) -> QRect: ...

    def name(self) -> str: ...


# Chooses the screen the notch should appear on, or None when there is none.
# The automatic central-screen policy is the only one today; a primary-screen
# or explicitly chosen screen policy would be another function of this shape.
ScreenSelector = Callable[[], QScreen | None]


def rect_from_qrect(rect: QRect) -> Rect:
    return Rect(x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height())


def central_screen_of(screens: Sequence[ScreenLike], primary: ScreenLike | None):
    """The geometrically central screen of ``screens``.

    Full screen geometry decides the arrangement. On an exact tie the primary
    screen wins if it is among the tied; otherwise geometric order decides,
    and screen names only separate screens with identical geometry, which
    geometry cannot. Returns None for no screens.
    """

    if not screens:
        return None
    tied = central_screens([rect_from_qrect(screen.geometry()) for screen in screens])
    for index in tied:
        if screens[index] is primary:
            return primary
    # `central_screens` already orders by geometry; the name only reorders
    # entries whose geometry is identical, so it never overrides geometry.
    tied.sort(key=lambda index: (rect_order(screens[index]), screens[index].name()))
    return screens[tied[0]]


def rect_order(screen: ScreenLike) -> tuple[int, int, int, int]:
    geometry = screen.geometry()
    return (geometry.x(), geometry.y(), geometry.width(), geometry.height())


def central_screen() -> QScreen | None:
    """The application's central screen: the default `ScreenSelector`."""

    return central_screen_of(QGuiApplication.screens(), QGuiApplication.primaryScreen())
