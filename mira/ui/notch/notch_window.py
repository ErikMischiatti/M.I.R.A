from PySide6.QtCore import QEasingCurve, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
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

from mira.domain.embodiment_frame import FACE_HEIGHT_UNITS, FACE_WIDTH_UNITS
from mira.ui.face.eye import Eye
from mira.ui.face.eye_painter import EyePainter
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.notch.eye_layout import NotchEyeLayout, NotchEyeStyle, notch_eye_layout
from mira.ui.notch.presence import (
    NotchGeometry,
    NotchPresence,
    NotchShape,
    NotchSize,
    Rect,
    remaining_fraction,
    window_bounds,
    window_shape_for_size,
)
from mira.ui.notch.screen_selection import MonitorPreference, ScreenSelection
from mira.ui.notch.screens import (
    ScreenSelector,
    application_screens,
    rect_from_qrect,
    select_screen_of,
)


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

# Below this height the notch counts as retracted: nothing is painted and the
# mask shrinks to `RETRACTED_MASK`, so no invisible strip catches clicks.
MIN_VISIBLE_HEIGHT_PX = 1.0

# A shown window needs some mask: Qt treats an empty one as no mask, which would
# let the whole window catch input. One pixel in the top-left corner stands in.
RETRACTED_MASK = QRect(0, 0, 1, 1)


def is_retracted(shape: NotchShape) -> bool:
    return shape.bounds.width <= 0 or shape.bounds.height < MIN_VISIBLE_HEIGHT_PX


def notch_mask(shape: NotchShape) -> QRegion:
    """The notch's input and bounding region; empty for an empty shape."""

    path = notch_path(shape)
    if path.isEmpty():
        return QRegion()
    stroker = QPainterPathStroker()
    stroker.setWidth(MASK_MARGIN_PX * 2.0)
    outline = path.united(stroker.createStroke(path))
    return QRegion(outline.toFillPolygon().toPolygon())


EYE_COLOR = QColor(245, 245, 245)


def eye_canvas(layout: NotchEyeLayout) -> QRectF:
    """The face design canvas at the layout's fixed scale, eye tops at its top."""

    return QRectF(
        layout.center_x - FACE_WIDTH_UNITS / 2.0 * layout.scale,
        layout.top,
        FACE_WIDTH_UNITS * layout.scale,
        FACE_HEIGHT_UNITS * layout.scale,
    )


def layout_eyes(layout: NotchEyeLayout) -> tuple[Eye, Eye]:
    """The layout's resting eyes as `EyePainter` placements on `eye_canvas`."""

    def eye(side: str) -> Eye:
        return Eye(
            x_ratio=(FACE_WIDTH_UNITS / 2.0 + layout.eye_left_units(side)) / FACE_WIDTH_UNITS,
            y_ratio=0.0,
            width_ratio=layout.eye_width / FACE_WIDTH_UNITS,
            height_ratio=layout.eye_height / FACE_HEIGHT_UNITS,
        )

    return eye("left"), eye("right")


class NotchWindow(QWidget):
    """A frameless top-level overlay that draws the notch and nothing else.

    The window keeps one fixed size, the expanded notch rounded to whole pixels,
    hung top-centre from the selected screen's full geometry: with no top margin
    the notch's top edge is the screen's top edge. The desktop work area
    (`QScreen.availableGeometry`) is deliberately not used — on X11 it follows
    the window manager's panel struts, which change asynchronously with
    monitor hot-plugging and can leave a gap above the notch. Presence only
    changes the shape drawn inside the window; it is never resized to follow
    the notch.

    Three things are kept apart:

    - `presence`, the semantic target: hidden, compact or expanded;
    - `visual_size`, what is drawn now, which mid-transition lies between sizes;
    - one `transition` animation that blends the visual size to the target's.

    A new target restarts the blend from the current visual size, so reversing
    mid-transition never jumps, and the duration shrinks with the share of the
    path still left. Showing shows the window before the notch grows out of the
    top edge; hiding keeps the window shown until the notch has retracted and
    only then hides it. The mask follows the drawn shape on every frame.

    Given a `FaceAnimator`, the notch is also a view of that embodiment: it
    repaints on each tick and draws the controller's current frame with the
    same `EyePainter` as the face, clipped to the drawn shape, at a layout fixed
    by the compact notch (`notch_eye_layout`) — so expanding reveals space
    around the eyes rather than rescaling them. It never steps the controller
    and never starts or stops the animator; that clock belongs to its owner.
    Without an animator the notch is a plain overlay.

    Which screen the notch appears on is `screen_selector`'s choice — by
    default `select_screen` under the window's `monitor_preference`: the
    preferred monitor when connected, otherwise the automatic 1 / 2 / 3+
    screen policy. The window re-runs it whenever screens are added or
    removed, change geometry, or the primary screen changes, and follows
    whatever it picks, emitting `screen_selection_changed` each time. A
    preferred monitor that is unplugged is therefore left for the automatic
    choice and returned to when it is plugged in again.

    The visual parameters — `notch_geometry`, `eye_style` and
    `transition_duration_ms` — can be replaced at run time through their
    setters; the window then re-derives bounds, shape, mask and eye layout
    through the same pipeline as at construction.

    The window never takes keyboard focus and shows without activating.
    """

    TRANSITION_DURATION_MS = 200

    # Emitted after every screen selection, whether or not the screen changed.
    screen_selection_changed = Signal()

    def __init__(
        self,
        geometry: NotchGeometry | None = None,
        *,
        color: QColor | None = None,
        animator: FaceAnimator | None = None,
        screen_selector: ScreenSelector | None = None,
        eye_style: NotchEyeStyle | None = None,
        monitor_preference: MonitorPreference | None = None,
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

        self._geometry = NotchGeometry() if geometry is None else geometry
        self._eye_style = NotchEyeStyle() if eye_style is None else eye_style
        self.transition_duration_ms = self.TRANSITION_DURATION_MS
        self.animator = animator
        self._eye_layout = notch_eye_layout(self._geometry, Rect(0.0, 0.0, 0.0, 0.0), self._eye_style)
        left, right = layout_eyes(self._eye_layout)
        # The eyelids cut into the eyes in the notch colour; see `color`.
        self.eye_painter = EyePainter(left, right, eye_color=EYE_COLOR, lid_color=QColor(0, 0, 0))
        self.color = QColor(0, 0, 0) if color is None else color

        self._presence = NotchPresence.HIDDEN
        self._visual_size = self._geometry.size_for(NotchPresence.HIDDEN)
        self._from_size = self._visual_size
        self._to_size = self._visual_size
        self._window_bounds = Rect(0.0, 0.0, 0.0, 0.0)
        self._screen: QScreen | None = None

        self._transition = QVariantAnimation(self)
        self._transition.setStartValue(0.0)
        self._transition.setEndValue(1.0)
        self._transition.setEasingCurve(QEasingCurve.OutCubic)
        self._transition.valueChanged.connect(self._on_transition_value)
        self._transition.finished.connect(self._on_transition_finished)

        self._monitor_preference = MonitorPreference() if monitor_preference is None else monitor_preference
        self._selection: ScreenSelection | None = None
        self._select_screen = self._select_preferred_screen if screen_selector is None else screen_selector
        self._watched_screens: list[QScreen] = []
        application = QGuiApplication.instance()
        application.screenAdded.connect(self._on_screen_added)
        application.screenRemoved.connect(self._on_screen_removed)
        application.primaryScreenChanged.connect(self._on_primary_screen_changed)
        for screen in QGuiApplication.screens():
            self._watch_screen(screen)
        self.retarget_screen()

        if animator is not None:
            animator.frame_advanced.connect(self.update)

    @property
    def color(self) -> QColor:
        """The notch fill, which is also the eyelid colour.

        Eyelids are drawn by covering part of an eye in the colour beneath it,
        so both must stay one setting.
        """
        return self._color

    @color.setter
    def color(self, color: QColor):
        self._color = color
        self.eye_painter.lid_color = color

    @property
    def notch_geometry(self) -> NotchGeometry:
        return self._geometry

    def set_notch_geometry(self, geometry: NotchGeometry):
        """Replace the notch geometry; the current presence keeps its target.

        At rest the notch takes the new size at once. Mid-transition the one
        transition is restarted from the shape drawn now towards the new
        target size, for the time it had left, so nothing jumps.
        """

        self._geometry = geometry
        target = geometry.size_for(self._presence)
        if self._transition.state() is QVariantAnimation.State.Stopped:
            self._visual_size = target
        else:
            remaining = self._transition.duration() - self._transition.currentTime()
            self._transition.stop()
            self._run_transition(target, remaining)
        self.reposition()

    @property
    def eye_style(self) -> NotchEyeStyle:
        return self._eye_style

    def set_eye_style(self, style: NotchEyeStyle):
        """Replace the eye layout rules and re-derive the layout."""
        self._eye_style = style
        self._update_eye_layout()
        self.update()

    @property
    def eye_layout(self) -> NotchEyeLayout:
        return self._eye_layout

    @property
    def eye_canvas(self) -> QRectF:
        return eye_canvas(self._eye_layout)

    # --- Presence ------------------------------------------------------------

    @property
    def presence(self) -> NotchPresence:
        """The semantic target; mid-transition the drawn notch lags behind it."""
        return self._presence

    @property
    def visual_size(self) -> NotchSize:
        return self._visual_size

    @property
    def shape(self) -> NotchShape:
        """The notch as drawn now, in window-local coordinates."""
        return window_shape_for_size(self._window_bounds, self._geometry, self._visual_size)

    @property
    def transition(self) -> QVariantAnimation:
        return self._transition

    def show_notch(self):
        """Show the compact notch if hidden; a shown notch keeps its presence."""
        if self._presence is NotchPresence.HIDDEN:
            self.set_presence(NotchPresence.COMPACT)

    def hide_notch(self):
        self.set_presence(NotchPresence.HIDDEN)

    def expand_notch(self):
        self.set_presence(NotchPresence.EXPANDED)

    def collapse_notch(self):
        self.set_presence(NotchPresence.COMPACT)

    def set_presence(self, presence: NotchPresence, *, animated: bool = True):
        """Move towards ``presence`` from wherever the notch is drawn now.

        Requesting the current target again changes nothing, even mid-way.
        """

        if presence is self._presence:
            return

        origin = self._geometry.size_for(self._presence)
        target = self._geometry.size_for(presence)
        self._presence = presence

        # stop() does not emit `finished`, so an interrupted transition never
        # completes — or hides the window — on behalf of the new target.
        self._transition.stop()
        if presence.is_visible:
            self.show()

        duration = round(
            self.transition_duration_ms * remaining_fraction(self._visual_size, origin, target)
        )
        self._run_transition(target, duration if animated else 0)

    def _run_transition(self, target: NotchSize, duration: int):
        """Blend from the drawn size to ``target``; settle at once if no time."""

        self._from_size = self._visual_size
        self._to_size = target
        if duration <= 0:
            self._set_visual_size(target)
            self._settle()
            return
        self._transition.setDuration(duration)
        self._transition.start()

    def _on_transition_value(self, progress: float):
        # A stopped animation still emits: setDuration() re-evaluates the value
        # at the time the previous run ended, against the new endpoints. That is
        # no frame of any transition, and applying it would flash a wrong shape
        # and mask for an instant.
        if self._transition.state() is QVariantAnimation.State.Stopped:
            return
        self._set_visual_size(self._from_size.lerp(self._to_size, progress))

    def _on_transition_finished(self):
        self._set_visual_size(self._to_size)
        self._settle()

    def _settle(self):
        # Reads the current target, never the one a transition started for.
        if self._presence is NotchPresence.HIDDEN:
            self.hide()

    def _set_visual_size(self, size: NotchSize):
        self._visual_size = size
        self._apply_shape()

    # --- Screen placement ----------------------------------------------------

    @property
    def tracked_screen(self) -> QScreen | None:
        return self._screen

    @property
    def watched_screens(self) -> list[QScreen]:
        """Screens whose geometry changes trigger a new screen selection."""
        return list(self._watched_screens)

    @property
    def monitor_preference(self) -> MonitorPreference:
        return self._monitor_preference

    def set_monitor_preference(self, preference: MonitorPreference):
        """Prefer a monitor (or none: automatic) and select again at once."""
        self._monitor_preference = preference
        self.retarget_screen()

    @property
    def selection(self) -> ScreenSelection | None:
        """How the default selector chose; None under a substituted selector."""
        return self._selection

    def _select_preferred_screen(self) -> QScreen | None:
        screens, primary = application_screens()
        screen, self._selection = select_screen_of(screens, primary, self._monitor_preference)
        return screen

    def retarget_screen(self):
        """Re-run screen selection and follow the screen it picks."""
        self.track_screen(self._select_screen())
        self.screen_selection_changed.emit()

    def _watch_screen(self, screen: QScreen):
        if any(watched is screen for watched in self._watched_screens):
            return
        screen.geometryChanged.connect(self._on_screen_geometry)
        self._watched_screens.append(screen)

    def _unwatch_screen(self, screen: QScreen):
        if not any(watched is screen for watched in self._watched_screens):
            return
        self._watched_screens = [watched for watched in self._watched_screens if watched is not screen]
        try:
            screen.geometryChanged.disconnect(self._on_screen_geometry)
        except (RuntimeError, TypeError):
            pass  # The screen is already gone, and its connections with it.

    def _on_screen_added(self, screen: QScreen):
        self._watch_screen(screen)
        self.retarget_screen()

    def _on_screen_removed(self, screen: QScreen):
        self._unwatch_screen(screen)
        if screen is self._screen:
            self._release_tracked_screen()
        # The screen may still be listed while this signal is delivered, so
        # choose again once the event loop has finished removing it.
        QTimer.singleShot(0, self, self.retarget_screen)

    def _on_primary_screen_changed(self, _screen: QScreen):
        # The primary screen decides between two screens and breaks ties.
        self.retarget_screen()

    def _on_screen_geometry(self, _geometry: QRect):
        # Any screen moving or resizing can change the automatic choice.
        self.retarget_screen()

    def _release_tracked_screen(self):
        try:
            self._screen.geometryChanged.disconnect(self._on_tracked_geometry)
        except (RuntimeError, TypeError):
            pass  # The screen is already gone, and its connections with it.
        self._screen = None

    def track_screen(self, screen: QScreen | None):
        """Follow ``screen``'s geometry, dropping the previous screen.

        Normally called by `retarget_screen` with the selector's choice. The
        previous screen's connection is removed first, so repeated or
        alternating changes never accumulate connections.
        """

        if screen is self._screen:
            self.reposition()
            return
        if self._screen is not None:
            self._release_tracked_screen()
        self._screen = screen
        if screen is not None:
            screen.geometryChanged.connect(self._on_tracked_geometry)
        self.reposition()

    def reposition(self):
        """Recompute the window's bounds from the tracked screen."""

        if self._screen is None:
            self._apply_shape()
            return
        self._window_bounds = window_bounds(rect_from_qrect(self._screen.geometry()), self._geometry)
        bounds = self._window_bounds
        self._update_eye_layout()
        self.setFixedSize(int(bounds.width), int(bounds.height))
        self.move(int(bounds.x), int(bounds.y))
        self._apply_shape()

    def _on_tracked_geometry(self, _geometry: QRect):
        self.reposition()

    def _update_eye_layout(self):
        self._eye_layout = notch_eye_layout(self._geometry, self._window_bounds, self._eye_style)
        self.eye_painter.left, self.eye_painter.right = layout_eyes(self._eye_layout)

    # --- Shape ---------------------------------------------------------------

    def _apply_shape(self):
        # Known issue (X11, not reproduced): one capture during a fast reversal
        # showed a stale and a current frame at once. setMask() reaches the X
        # server at once while the repaint lands with the next flush, so the
        # two are not atomic. No deterministic failure exists yet; do not
        # reorder mask and paint until one does.
        shape = self.shape
        if is_retracted(shape):
            self.setMask(QRegion(RETRACTED_MASK))
        else:
            self.setMask(notch_mask(shape))
        self.update()

    def paintEvent(self, event):
        # The surface outside the notch stays transparent (WA_TranslucentBackground).
        shape = self.shape
        if is_retracted(shape):
            return
        path = notch_path(shape)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, self.color)

        if self.animator is not None:
            painter.setClipPath(path)
            self.eye_painter.paint(painter, self.eye_canvas, self.animator.controller.get_frame())
