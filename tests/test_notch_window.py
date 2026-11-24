"""`NotchWindow`: a frameless overlay that draws the notch, verified offscreen.

These tests pin configuration, placement, settled presence and the input mask. What
only a real window manager decides — stacking, taskbar or dash exclusion,
whether focus is actually withheld — is left to manual verification; the
offscreen platform activates any shown window and does not apply masks.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QRect, Qt, Signal, SIGNAL
from PySide6.QtGui import QColor, QGuiApplication, QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from mira.ui.notch.notch_window import (
    MASK_MARGIN_PX,
    NotchWindow,
    notch_mask,
    notch_path,
    rect_from_qrect,
)
from mira.ui.notch.presence import (
    NotchGeometry,
    NotchPresence,
    NotchShape,
    NotchSize,
    Rect,
    window_bounds,
    window_shape,
)


GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)
NOTCH = QColor(0, 0, 0)


class FakeScreen(QObject):
    """Stands in for a `QScreen`: its geometry and its desktop work area.

    By default the work area lies 32 px below the screen top, as under a
    GNOME top bar, so any use of it shows up as a gap above the notch.
    """

    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, geometry: QRect, available: QRect | None = None):
        super().__init__()
        self._geometry = geometry
        self._available = geometry.adjusted(0, 32, 0, 0) if available is None else available

    def geometry(self) -> QRect:
        return self._geometry

    def availableGeometry(self) -> QRect:
        return self._available

    def move_to(self, geometry: QRect) -> None:
        self._geometry = geometry
        self.geometryChanged.emit(geometry)

    def move_panel(self, available: QRect) -> None:
        self._available = available
        self.availableGeometryChanged.emit(available)

    def connections(self) -> int:
        return self.receivers(SIGNAL("geometryChanged(QRect)"))

    def work_area_connections(self) -> int:
        return self.receivers(SIGNAL("availableGeometryChanged(QRect)"))


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window():
    notch = NotchWindow(GEOMETRY)
    # These tests check settled states, not timing: make transitions instant.
    # Animated transitions are covered in test_notch_transitions.py.
    notch.transition_duration_ms = 0
    yield notch
    notch.hide()
    notch.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def expected_bounds(screen_geometry: QRect) -> Rect:
    return window_bounds(rect_from_qrect(screen_geometry), GEOMETRY)


def geometry_of(window: NotchWindow) -> Rect:
    geometry = window.geometry()
    return Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())


def render(window: NotchWindow) -> QImage:
    image = QImage(window.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    # render() paints only the part of the window inside its mask and places
    # that part's top-left at the target offset; offsetting by the same point
    # keeps every pixel at its window coordinates.
    painted = QRegion(window.rect())
    if not window.mask().isEmpty():
        painted &= window.mask()
    window.render(image, painted.boundingRect().topLeft(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


# --- Window configuration ----------------------------------------------------


def test_the_window_is_a_parentless_top_level(window):
    assert window.parent() is None
    assert window.isWindow()


@pytest.mark.parametrize(
    "flag",
    [
        Qt.FramelessWindowHint,
        Qt.WindowStaysOnTopHint,
        Qt.WindowDoesNotAcceptFocus,
    ],
)
def test_window_hints_are_set(window, flag):
    assert window.windowFlags() & flag


def test_the_window_type_is_a_tool_window(window):
    assert window.windowType() == Qt.Tool


@pytest.mark.parametrize("attribute", [Qt.WA_TranslucentBackground, Qt.WA_ShowWithoutActivating])
def test_window_attributes_are_set(window, attribute):
    assert window.testAttribute(attribute)


def test_the_window_takes_no_focus(window):
    assert window.focusPolicy() == Qt.NoFocus


def test_no_platform_specific_or_click_through_hints_are_set(window):
    assert not window.windowFlags() & Qt.X11BypassWindowManagerHint
    assert not window.windowFlags() & Qt.WindowTransparentForInput


# --- Geometry ----------------------------------------------------------------


def test_the_window_tracks_the_selected_screen(window):
    # Offscreen has one screen, which the central-screen policy selects.
    assert window.tracked_screen is QGuiApplication.primaryScreen()


def test_the_window_is_the_expanded_notch_top_centre_on_its_screen(window):
    screen_geometry = QGuiApplication.primaryScreen().geometry()

    assert geometry_of(window) == expected_bounds(screen_geometry)
    assert window.width() == 360 and window.height() == 96


def test_the_window_size_is_fixed(window):
    assert window.minimumSize() == window.maximumSize() == window.size()


@pytest.mark.parametrize("presence", list(NotchPresence))
def test_presence_changes_the_shape_but_never_the_window(window, presence):
    before = geometry_of(window)

    window.set_presence(presence)

    assert geometry_of(window) == before
    assert window.shape == window_shape(before, GEOMETRY, presence)


def test_a_screen_with_a_non_zero_origin_places_the_window_on_it(window):
    screen = FakeScreen(QRect(-1280, -200, 1281, 1024))

    window.track_screen(screen)

    # (1281 - 360) / 2 = 460.5 floors to 460: -1280 + 460.
    assert geometry_of(window) == Rect(-820, -200, 360, 96)


def test_the_top_margin_lowers_the_window_from_the_screen_top_exactly_once():
    notch = NotchWindow(NotchGeometry(top_margin=6.0))
    try:
        # The work area (top 32) must not add to the margin.
        notch.track_screen(FakeScreen(QRect(0, 0, 1920, 1080), available=QRect(0, 32, 1920, 1048)))
        assert notch.y() == 6
        notch.track_screen(FakeScreen(QRect(0, 313, 1920, 1080)))
        assert notch.y() == 319
    finally:
        notch.deleteLater()


def test_a_zero_margin_puts_the_notch_top_on_the_screen_top_not_the_work_area(window):
    # The reported layout: the central screen starts at y=313 while the
    # desktop work area, pushed down by the top bar, starts at 345.
    screen = FakeScreen(QRect(1920, 313, 2560, 1080), available=QRect(1920, 345, 2560, 1048))

    window.track_screen(screen)

    assert window.y() == 313
    assert window.shape.bounds.y == 0.0
    # Centred on the selected screen: 1920 + (2560 - 360) / 2.
    assert window.x() == 3020


def test_a_negative_origin_screen_anchors_to_its_own_top_and_centre(window):
    window.track_screen(FakeScreen(QRect(-2560, -1440, 2560, 1440)))

    assert (window.x(), window.y()) == (-2560 + (2560 - 360) // 2, -1440)


def test_fractional_geometry_rounds_to_the_documented_whole_pixels():
    fractional = NotchGeometry(
        compact=NotchSize(200.0, 32.0, 14.0), expanded=NotchSize(360.4, 96.2, 24.0)
    )
    notch = NotchWindow(fractional)
    try:
        notch.track_screen(FakeScreen(QRect(0, 0, 1001, 800)))
        # Size rounds up to 361x97; origin floors: (1001 - 361) / 2 = 320.
        assert geometry_of(notch) == Rect(320, 0, 361, 97)
    finally:
        notch.deleteLater()


# --- Screen changes ----------------------------------------------------------


def test_a_changed_screen_geometry_moves_the_window(window):
    screen = FakeScreen(QRect(0, 0, 1920, 1080))
    window.track_screen(screen)

    screen.move_to(QRect(0, 48, 2560, 1392))

    assert geometry_of(window) == expected_bounds(QRect(0, 48, 2560, 1392))


def test_a_changed_work_area_does_not_move_the_window(window):
    screen = FakeScreen(QRect(0, 0, 1920, 1080))
    window.track_screen(screen)
    before = geometry_of(window)

    screen.move_panel(QRect(0, 64, 1920, 1016))

    assert geometry_of(window) == before
    assert screen.work_area_connections() == 0


def test_tracking_a_new_screen_drops_the_old_connection(window):
    first = FakeScreen(QRect(0, 0, 1920, 1080))
    second = FakeScreen(QRect(1920, 0, 1280, 1024))

    window.track_screen(first)
    window.track_screen(second)

    assert first.connections() == 0
    assert second.connections() == 1
    assert window.tracked_screen is second

    first.move_to(QRect(0, 0, 800, 600))
    assert geometry_of(window) == expected_bounds(QRect(1920, 0, 1280, 1024))


def test_repeated_and_alternating_screen_changes_never_accumulate_connections(window):
    first = FakeScreen(QRect(0, 0, 1920, 1080))
    second = FakeScreen(QRect(1920, 0, 1280, 1024))

    for screen in (first, first, second, first, second, second, first):
        window.track_screen(screen)

    assert first.connections() == 1
    assert second.connections() == 0


def test_a_destroyed_tracked_screen_does_not_break_tracking_the_next(window):
    gone = FakeScreen(QRect(0, 0, 1920, 1080))
    window.track_screen(gone)
    gone.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    replacement = FakeScreen(QRect(0, 0, 1280, 800))
    window.track_screen(replacement)

    assert geometry_of(window) == expected_bounds(QRect(0, 0, 1280, 800))


def test_a_primary_screen_change_reruns_the_screen_selection():
    chosen = FakeScreen(QRect(100, 0, 1920, 1080))
    picks = [QGuiApplication.primaryScreen()]
    notch = NotchWindow(GEOMETRY, screen_selector=lambda: picks[-1])
    assert notch.tracked_screen is QGuiApplication.primaryScreen()

    picks.append(chosen)
    QGuiApplication.instance().primaryScreenChanged.emit(QGuiApplication.primaryScreen())

    assert notch.tracked_screen is chosen
    assert geometry_of(notch) == expected_bounds(QRect(100, 0, 1920, 1080))
    notch.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- Presence ----------------------------------------------------------------


def test_the_notch_starts_hidden_and_the_window_unshown(window):
    assert window.presence is NotchPresence.HIDDEN
    assert not window.isVisible()


def test_show_makes_the_notch_compact_and_the_window_visible(window):
    window.show_notch()

    assert window.presence is NotchPresence.COMPACT
    assert window.isVisible()


def test_show_keeps_an_already_shown_presence(window):
    window.set_presence(NotchPresence.EXPANDED)

    window.show_notch()

    assert window.presence is NotchPresence.EXPANDED


@pytest.mark.parametrize("shown", [NotchPresence.COMPACT, NotchPresence.EXPANDED])
def test_hide_retracts_the_notch_and_hides_the_window(window, shown):
    window.set_presence(shown)

    window.hide_notch()

    assert window.presence is NotchPresence.HIDDEN
    assert window.shape.bounds.height == 0.0
    assert not window.isVisible()


def test_hide_then_show_restores_the_compact_notch(window):
    window.show_notch()
    window.hide_notch()
    window.show_notch()

    assert window.presence is NotchPresence.COMPACT
    assert window.isVisible()


# --- Input mask ----------------------------------------------------------------


@pytest.mark.parametrize("presence", [NotchPresence.COMPACT, NotchPresence.EXPANDED])
def test_the_mask_follows_the_visible_shape_within_its_margin(window, presence):
    window.set_presence(presence)
    bounds = window.shape.bounds
    mask = window.mask()
    reach = MASK_MARGIN_PX + 1  # the margin plus polygon rounding

    box = mask.boundingRect()
    assert abs(box.x() - bounds.x) <= reach
    assert abs(box.right() + 1 - bounds.right) <= reach
    assert box.y() <= 0 and abs(box.bottom() + 1 - bounds.bottom) <= reach
    assert mask.contains(QPoint(int(bounds.center_x), int(bounds.height / 2)))


def test_the_mask_covers_the_antialiased_edge_of_the_notch(window):
    window.show_notch()
    bounds = window.shape.bounds

    # Just outside the straight side and bottom edges.
    assert window.mask().contains(QPoint(int(bounds.x) - 1, int(bounds.height / 2)))
    assert window.mask().contains(QPoint(int(bounds.center_x), int(bounds.bottom)))


def test_an_empty_shape_has_an_empty_mask():
    assert notch_mask(NotchShape(bounds=Rect(0.0, 0.0, 200.0, 0.0), corner_radius=0.0)).isEmpty()


def test_the_compact_mask_leaves_the_rest_of_the_window_to_pass_through(window):
    window.show_notch()
    bounds = window.shape.bounds
    mask = window.mask()

    # Beside the compact notch, below it, and in its rounded lower corner.
    assert not mask.contains(QPoint(5, 5))
    assert not mask.contains(QPoint(int(bounds.center_x), int(bounds.bottom) + 10))
    assert not mask.contains(QPoint(int(bounds.x) + 1, int(bounds.bottom) - 1))


def test_expanding_widens_the_mask_and_collapsing_narrows_it(window):
    window.set_presence(NotchPresence.EXPANDED)
    expanded = window.mask().boundingRect()
    window.set_presence(NotchPresence.COMPACT)
    compact = window.mask().boundingRect()

    assert expanded.contains(compact)
    assert expanded != compact


def test_hidden_leaves_no_window_that_could_intercept_input(window):
    window.show_notch()
    window.hide_notch()

    # Qt cannot express an empty mask, so hidden means no shown window at all.
    assert not window.isVisible()


# --- Rendering (structural, a few stable pixels) -----------------------------


def test_the_notch_path_has_a_flat_top_and_rounded_lower_corners():
    shape = NotchShape(bounds=Rect(10.0, 0.0, 200.0, 32.0), corner_radius=14.0)
    path = notch_path(shape)

    assert path.boundingRect() == QRect(10, 0, 200, 32)
    assert path.contains(QPoint(11, 1).toPointF())  # square top corner
    assert not path.contains(QPoint(11, 31).toPointF())  # rounded lower corner


def test_an_empty_shape_has_an_empty_path():
    assert notch_path(NotchShape(bounds=Rect(0.0, 0.0, 200.0, 0.0), corner_radius=0.0)).isEmpty()


def test_the_notch_is_opaque_inside_and_transparent_outside(window):
    window.show_notch()
    bounds = window.shape.bounds
    image = render(window)

    inside = image.pixelColor(int(bounds.center_x), int(bounds.height / 2))
    assert inside == NOTCH and inside.alpha() == 255
    assert image.pixelColor(5, 5).alpha() == 0
    assert image.pixelColor(int(bounds.center_x), int(bounds.bottom) + 10).alpha() == 0
    assert image.pixelColor(int(bounds.x), int(bounds.bottom) - 1).alpha() == 0


def test_a_hidden_notch_paints_nothing(window):
    image = render(window)

    assert all(
        image.pixelColor(x, y).alpha() == 0
        for x in range(0, image.width(), 7)
        for y in range(0, image.height(), 7)
    )
