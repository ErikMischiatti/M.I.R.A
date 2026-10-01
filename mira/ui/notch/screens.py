from typing import Callable, Protocol, Sequence

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QScreen

from mira.ui.notch.presence import Rect
from mira.ui.notch.screen_selection import (
    MonitorIdentity,
    MonitorPreference,
    ScreenInfo,
    ScreenSelection,
    central_screens,
    select_screen,
)


class ScreenLike(Protocol):
    """What screen selection reads from a `QScreen`."""

    def geometry(self) -> QRect: ...

    def name(self) -> str: ...


# Chooses the screen the notch should appear on, or None when there is none.
# `NotchWindow`'s default is `select_screen` under its monitor preference;
# tests and tools may substitute any function of this shape.
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


def monitor_identity(screen: ScreenLike) -> MonitorIdentity:
    """The identity a `QScreen` reports: connector name plus EDID data.

    Screens that report no EDID data (or stand-ins without those queries)
    are identified by connector name alone.
    """

    def text(query: str) -> str:
        method = getattr(screen, query, None)
        return (method() or "").strip() if method is not None else ""

    return MonitorIdentity(
        name=screen.name(),
        manufacturer=text("manufacturer"),
        model=text("model"),
        serial=text("serialNumber"),
    )


def screen_infos(screens: Sequence[ScreenLike], primary: ScreenLike | None) -> list[ScreenInfo]:
    return [
        ScreenInfo(
            identity=monitor_identity(screen),
            geometry=rect_from_qrect(screen.geometry()),
            primary=screen is primary,
        )
        for screen in screens
    ]


def select_screen_of(
    screens: Sequence[ScreenLike], primary: ScreenLike | None, preference: MonitorPreference
) -> tuple[ScreenLike | None, ScreenSelection]:
    """Run the selection policy over Qt screens; the screen and the decision."""

    selection = select_screen(screen_infos(screens, primary), preference)
    chosen = None if selection.index is None else screens[selection.index]
    return chosen, selection


def application_screens() -> tuple[list[QScreen], QScreen | None]:
    return QGuiApplication.screens(), QGuiApplication.primaryScreen()
