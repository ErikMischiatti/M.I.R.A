from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QRegion,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from mira.ui.notch.presence import (
    NotchGeometry,
    NotchPresence,
    NotchShape,
    Rect,
    window_bounds,
    window_shape,
)


def rect_from_qrect(rect: QRect) -> Rect:
    return Rect(x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height())


def notch_path(shape: NotchShape) -> QPainterPath:
    """The notch outline: a flat top edge and rounded lower corners."""

    bounds = shape.bounds
    left, top, right, bottom = bounds.x, bounds.y, bounds.right, bounds.bottom
    diameter = shape.corner_radius * 2.0

    path = QPainterPath()
    if bounds.width <= 0 or bounds.height <= 0:
        return path

    path.moveTo(left, top)
    path.lineTo(right, top)
    path.lineTo(right, bottom - shape.corner_radius)
    path.arcTo(QRectF(right - diameter, bottom - diameter, diameter, diameter), 0.0, -90.0)
    path.lineTo(left + shape.corner_radius, bottom)
    path.arcTo(QRectF(left, bottom - diameter, diameter, diameter), 270.0, -90.0)
    path.closeSubpath()
    return path


# How far the input mask reaches beyond the drawn outline. On X11 a top-level
# mask is the window's bounding shape and clips painting too, so a mask exactly
# on the outline would cut off the antialiased edge pixels and leave the curves
# stepped. One pixel keeps the whole antialiased edge inside the mask.
MASK_MARGIN_PX = 1.0


def notch_mask(shape: NotchShape) -> QRegion:
    """The notch's input and bounding region; empty for an empty shape."""

    path = notch_path(shape)
    if path.isEmpty():
        return QRegion()
    stroker = QPainterPathStroker()
    stroker.setWidth(MASK_MARGIN_PX * 2.0)
    outline = path.united(stroker.createStroke(path))
    return QRegion(outline.toFillPolygon().toPolygon())


class NotchWindow(QWidget):
    """A frameless top-level overlay that draws the notch and nothing else.

    The window keeps one fixed size, the expanded notch rounded to whole pixels,
    hung top-centre from the primary screen's available geometry — so below a
    desktop panel such as GNOME's top bar. Presence changes only the shape drawn
    inside it, which later lets the shape animate without resizing the window.

    Presence and Qt visibility are distinct. `NotchPresence.HIDDEN` is a logical
    geometry (the compact notch retracted to zero height); on reaching it the
    window is also hidden, because an empty input mask cannot be expressed —
    Qt treats an empty mask as no mask at all, which would make the whole
    window catch clicks. Visible presences show the window and mask it to the
    drawn shape plus `MASK_MARGIN_PX`, so clicks beside the notch reach the
    windows underneath. Transitions are currently instant.

    The window never takes keyboard focus and shows without activating.
    """

    def __init__(
        self,
        geometry: NotchGeometry | None = None,
        *,
        color: QColor | None = None,
    ):
        super().__init__(None)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("M.I.R.A. notch")

        self.notch_geometry = NotchGeometry() if geometry is None else geometry
        self.color = QColor(0, 0, 0) if color is None else color

        self._presence = NotchPresence.HIDDEN
        self._window_bounds = Rect(0.0, 0.0, 0.0, 0.0)
        self._screen: QScreen | None = None

        QGuiApplication.instance().primaryScreenChanged.connect(self.track_screen)
        self.track_screen(QGuiApplication.primaryScreen())

    # --- Presence ------------------------------------------------------------

    @property
    def presence(self) -> NotchPresence:
        return self._presence

    @property
    def shape(self) -> NotchShape:
        """The notch as drawn, in window-local coordinates."""
        return window_shape(self._window_bounds, self.notch_geometry, self._presence)

    def show_notch(self):
        """Show the compact notch if hidden; a shown notch keeps its presence."""
        if self._presence is NotchPresence.HIDDEN:
            self.set_presence(NotchPresence.COMPACT)

    def hide_notch(self):
        self.set_presence(NotchPresence.HIDDEN)

    def set_presence(self, presence: NotchPresence):
        """Apply ``presence`` immediately: shape, input mask and visibility."""

        self._presence = presence
        self._apply_shape()
        if presence.is_visible and self.shape.bounds.height > 0:
            self.show()
        else:
            self.hide()

    # --- Screen placement ----------------------------------------------------

    @property
    def tracked_screen(self) -> QScreen | None:
        return self._screen

    def track_screen(self, screen: QScreen | None):
        """Follow ``screen``'s available geometry, dropping the previous screen.

        Connected to the application's primary-screen changes. The previous
        screen's connection is removed first, so repeated or alternating
        changes never accumulate connections.
        """

        if screen is self._screen:
            self.reposition()
            return
        if self._screen is not None:
            try:
                self._screen.availableGeometryChanged.disconnect(self._on_available_geometry)
            except (RuntimeError, TypeError):
                pass  # The screen is already gone, and its connections with it.
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._on_available_geometry)
        self.reposition()

    def reposition(self):
        """Recompute the window's bounds from the tracked screen."""

        if self._screen is None:
            return
        self._window_bounds = window_bounds(
            rect_from_qrect(self._screen.availableGeometry()), self.notch_geometry
        )
        bounds = self._window_bounds
        self.setFixedSize(int(bounds.width), int(bounds.height))
        self.move(int(bounds.x), int(bounds.y))
        self._apply_shape()

    def _on_available_geometry(self, _geometry: QRect):
        self.reposition()

    # --- Shape ---------------------------------------------------------------

    def _apply_shape(self):
        mask = notch_mask(self.shape)
        if mask.isEmpty():
            self.clearMask()
        else:
            self.setMask(mask)
        self.update()

    def paintEvent(self, event):
        path = notch_path(self.shape)
        if path.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, self.color)
