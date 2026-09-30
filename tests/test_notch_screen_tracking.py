"""`NotchWindow` follows its screen selector through screen configuration changes.

Offscreen Qt has a single screen, so configurations are simulated: fake screens
carry the two signals the window listens to, a selector runs the real
central-screen adapter over a mutable list of them, and the application's
screen signals are emitted by hand.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, Signal, SIGNAL
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchSize, window_bounds
from mira.ui.notch.screens import central_screen_of, rect_from_qrect


GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)


class FakeScreen(QObject):
    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, name: str, geometry: QRect, available: QRect | None = None):
        super().__init__()
        self._name = name
        self._geometry = geometry
        self._available = geometry if available is None else available

    def name(self) -> str:
        return self._name

    def geometry(self) -> QRect:
        return self._geometry

    def availableGeometry(self) -> QRect:
        return self._available

    def move_to(self, geometry: QRect) -> None:
        self._geometry = self._available = geometry
        self.geometryChanged.emit(geometry)
        self.availableGeometryChanged.emit(geometry)

    def geometry_listeners(self) -> int:
        return self.receivers(SIGNAL("geometryChanged(QRect)"))

    def placement_listeners(self) -> int:
        return self.receivers(SIGNAL("availableGeometryChanged(QRect)"))


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


class Desktop:
    """A mutable screen arrangement and a selector over it."""

    def __init__(self, *screens: FakeScreen):
        self.screens = list(screens)
        self.primary: FakeScreen | None = None

    def select(self):
        return central_screen_of(self.screens, self.primary)

    def plug(self, screen: FakeScreen) -> None:
        self.screens.append(screen)
        QGuiApplication.instance().screenAdded.emit(screen)

    def unplug(self, screen: FakeScreen) -> None:
        QGuiApplication.instance().screenRemoved.emit(screen)
        self.screens.remove(screen)
        QCoreApplication.processEvents()


def geometry_of(window: NotchWindow) -> tuple:
    return (window.x(), window.y(), window.width(), window.height())


def placed_on(screen: FakeScreen) -> tuple:
    bounds = window_bounds(rect_from_qrect(screen.availableGeometry()), GEOMETRY)
    return (bounds.x, bounds.y, bounds.width, bounds.height)


def notch_on(desktop: Desktop) -> NotchWindow:
    """A notch that has seen every screen of ``desktop`` arrive."""
    notch = NotchWindow(GEOMETRY, screen_selector=desktop.select)
    for screen in desktop.screens:
        QGuiApplication.instance().screenAdded.emit(screen)
    return notch


@pytest.fixture
def made():
    windows = []
    yield windows
    for window in windows:
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


LAPTOP = QRect(0, 273, 1920, 1200)
LANDSCAPE = QRect(1920, 273, 2560, 1080)
PORTRAIT = QRect(4480, 0, 1080, 1920)


def test_the_default_selector_is_the_central_screen_policy(made):
    notch = NotchWindow(GEOMETRY)
    made.append(notch)

    # Offscreen lists one screen; the policy must pick it.
    assert notch.tracked_screen is QGuiApplication.screens()[0]


def test_the_window_uses_the_policy_result_not_the_primary(made):
    laptop, landscape, portrait = FakeScreen("a", LAPTOP), FakeScreen("b", LANDSCAPE), FakeScreen("c", PORTRAIT)
    desktop = Desktop(laptop, landscape, portrait)
    desktop.primary = laptop

    notch = NotchWindow(GEOMETRY, screen_selector=desktop.select)
    made.append(notch)

    assert notch.tracked_screen is landscape
    assert geometry_of(notch) == placed_on(landscape)


def test_plugging_a_screen_reselects_and_repositions(made):
    laptop, landscape = FakeScreen("a", LAPTOP), FakeScreen("b", LANDSCAPE)
    desktop = Desktop(laptop, landscape)
    notch = notch_on(desktop)
    made.append(notch)
    assert notch.tracked_screen is landscape  # the larger screen is nearer the centre

    portrait = FakeScreen("c", PORTRAIT)
    left_of_laptop = FakeScreen("d", QRect(-2560, 273, 2560, 1440))
    desktop.plug(portrait)
    desktop.plug(left_of_laptop)

    assert notch.tracked_screen is laptop
    assert geometry_of(notch) == placed_on(laptop)
    assert portrait.geometry_listeners() == 1 and left_of_laptop.geometry_listeners() == 1


def test_unplugging_the_tracked_screen_moves_to_the_new_centre_and_drops_it(made):
    laptop, landscape, portrait = FakeScreen("a", LAPTOP), FakeScreen("b", LANDSCAPE), FakeScreen("c", PORTRAIT)
    desktop = Desktop(laptop, landscape, portrait)
    desktop.primary = laptop
    notch = notch_on(desktop)
    made.append(notch)
    assert notch.tracked_screen is landscape

    desktop.unplug(landscape)

    assert landscape.geometry_listeners() == 0
    assert landscape.placement_listeners() == 0
    assert all(screen is not landscape for screen in notch.watched_screens)
    # Laptop and portrait remain; the laptop's centre is nearer the new desktop centre.
    assert notch.tracked_screen is laptop
    assert geometry_of(notch) == placed_on(notch.tracked_screen)
    assert notch.tracked_screen.placement_listeners() == 1


def test_moving_a_screen_reselects(made):
    left, middle, right = (
        FakeScreen("a", QRect(0, 0, 1920, 1080)),
        FakeScreen("b", QRect(1920, 0, 1920, 1080)),
        FakeScreen("c", QRect(3840, 0, 1920, 1080)),
    )
    desktop = Desktop(left, middle, right)
    notch = notch_on(desktop)
    made.append(notch)
    assert notch.tracked_screen is middle

    # Drag the left screen to the far right: the old right one is now central.
    left.move_to(QRect(5760, 0, 1920, 1080))

    assert notch.tracked_screen is right
    assert geometry_of(notch) == placed_on(right)
    assert middle.placement_listeners() == 0
    assert right.placement_listeners() == 1


def test_reconfiguring_never_accumulates_connections(made):
    a, b, c = FakeScreen("a", LAPTOP), FakeScreen("b", LANDSCAPE), FakeScreen("c", PORTRAIT)
    desktop = Desktop(a, b)
    notch = notch_on(desktop)
    made.append(notch)

    for _ in range(3):
        desktop.plug(c)
        QGuiApplication.instance().screenAdded.emit(c)  # a duplicate notification
        desktop.unplug(c)
        b.move_to(LANDSCAPE)

    assert [screen.geometry_listeners() for screen in (a, b, c)] == [1, 1, 0]
    placement = [screen.placement_listeners() for screen in (a, b, c)]
    assert sum(placement) == 1 and placement[desktop.screens.index(notch.tracked_screen)] == 1


def test_the_primary_breaks_a_tie_and_its_change_reselects(made):
    left, right = FakeScreen("a", QRect(0, 0, 1920, 1080)), FakeScreen("b", QRect(1920, 0, 1920, 1080))
    desktop = Desktop(left, right)
    desktop.primary = right
    notch = notch_on(desktop)
    made.append(notch)
    assert notch.tracked_screen is right

    desktop.primary = left
    QGuiApplication.instance().primaryScreenChanged.emit(QGuiApplication.primaryScreen())

    assert notch.tracked_screen is left
