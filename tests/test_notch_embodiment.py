"""The notch as a second view of the shared embodiment.

Frames are fixed or seeded, and assertions are geometric with a few targeted
pixels: eye rectangles come from the same `EyePainter` the notch paints with.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from mira.domain.embodiment_frame import EmbodimentFrame, EyeFrame
from mira.domain.embodiment_playback import REFERENCE_DT_SECONDS
from mira.domain.micro_motion import MicroMotions
from mira.domain.state import FaceState
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.face.face_widget import FaceWidget
from mira.ui.notch.eye_layout import BASE_EYE_HEIGHT_SHARE, notch_eye_layout, production_envelope
from mira.ui.notch.notch_window import EYE_COLOR, NotchWindow, notch_path
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize, Rect


GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)
NOTCH = QColor(0, 0, 0)
H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED

NEUTRAL = EmbodimentFrame(left_eye=EyeFrame(), right_eye=EyeFrame())


def both(**eye) -> EmbodimentFrame:
    return EmbodimentFrame(left_eye=EyeFrame(**eye), right_eye=EyeFrame(**eye))


class FixedFrameController:
    """A controller double that shows one frame and counts its steps."""

    def __init__(self, frame: EmbodimentFrame = NEUTRAL):
        self.frame = frame
        self.updates = 0

    def update(self, dt_seconds: float):
        self.updates += 1

    def get_frame(self) -> EmbodimentFrame:
        return self.frame


class CountingController(FaceController):
    def __init__(self):
        super().__init__(MicroMotions(random.Random(5)))
        self.updates = 0

    def update(self, dt_seconds: float):
        self.updates += 1
        super().update(dt_seconds)


class CountingNotch(NotchWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paints = 0

    def paintEvent(self, event):
        self.paints += 1
        super().paintEvent(event)


class CountingFace(FaceWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paints = 0

    def paintEvent(self, event):
        self.paints += 1
        super().paintEvent(event)


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


def dispose(*objects) -> None:
    for obj in objects:
        if isinstance(obj, QWidget):
            obj.hide()
        obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def notch_for(frame: EmbodimentFrame = NEUTRAL, presence: NotchPresence = C) -> NotchWindow:
    notch = NotchWindow(GEOMETRY, animator=FaceAnimator(FixedFrameController(frame)))
    notch.set_presence(presence, animated=False)
    return notch


def render(window: NotchWindow) -> QImage:
    image = QImage(window.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painted = QRegion(window.rect())
    if not window.mask().isEmpty():
        painted &= window.mask()
    window.render(image, painted.boundingRect().topLeft(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


def eye_rect(window: NotchWindow, side: str, frame: EmbodimentFrame):
    painter = window.eye_painter
    eye = painter.left if side == "left" else painter.right
    return painter.eye_rect(window.eye_canvas, eye, frame.left_eye if side == "left" else frame.right_eye)


def pixel(image: QImage, point: QPointF) -> QColor:
    return image.pixelColor(round(point.x()), round(point.y()))


def eye_pixels(image: QImage) -> list[tuple[int, int]]:
    return [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).red() > 128
    ]


# --- Shared animator -----------------------------------------------------------


def test_one_tick_steps_the_shared_controller_once_and_repaints_both_views():
    controller = CountingController()
    animator = FaceAnimator(controller)
    face = CountingFace(animator=animator)
    notch = CountingNotch(GEOMETRY, animator=animator)
    face.resize(400, 300)
    face.show()
    notch.set_presence(C, animated=False)
    QCoreApplication.processEvents()
    face.paints = notch.paints = 0

    animator.advance()
    QCoreApplication.processEvents()

    assert controller.updates == 1
    assert face.paints >= 1 and notch.paints >= 1
    dispose(face, notch, animator)


def test_many_ticks_with_two_views_advance_exactly_once_each():
    controller = CountingController()
    animator = FaceAnimator(controller)
    face = FaceWidget(animator=animator)
    notch = NotchWindow(GEOMETRY, animator=animator)
    notch.set_presence(E, animated=False)

    for _ in range(50):
        animator.advance()
        render(notch)
        face.resize(400, 300)
        face.render(QImage(400, 300, QImage.Format_ARGB32_Premultiplied))

    assert controller.updates == 50
    dispose(face, notch, animator)


def test_both_views_show_the_same_frame():
    animator = FaceAnimator(CountingController())
    face = FaceWidget(animator=animator)
    notch = NotchWindow(GEOMETRY, animator=animator)
    for _ in range(30):
        animator.advance()

    assert notch.animator.controller is face.controller
    assert notch.animator.controller.get_frame() == face.controller.get_frame()
    dispose(face, notch, animator)


def test_the_notch_neither_starts_nor_stops_the_animator():
    animator = FaceAnimator(CountingController())
    notch = NotchWindow(GEOMETRY, animator=animator)

    notch.show_notch()
    notch.transition.setCurrentTime(notch.transition.duration())
    assert not animator.is_running()

    animator.start()
    notch.hide_notch()
    notch.transition.setCurrentTime(notch.transition.duration())
    assert animator.is_running()

    animator.stop()
    dispose(notch, animator)


def test_painting_the_notch_never_steps_the_controller():
    controller = FixedFrameController()
    notch = NotchWindow(GEOMETRY, animator=FaceAnimator(controller))
    notch.set_presence(E, animated=False)

    for _ in range(5):
        render(notch)

    assert controller.updates == 0
    dispose(notch)


# --- Optional animator -----------------------------------------------------------


def test_without_an_animator_the_notch_is_a_plain_overlay():
    notch = NotchWindow(GEOMETRY)
    notch.set_presence(C, animated=False)
    image = render(notch)

    assert notch.animator is None
    assert eye_pixels(image) == []
    shape = notch.shape.bounds
    assert image.pixelColor(int(shape.center_x), int(shape.height / 2)) == NOTCH
    dispose(notch)


def test_without_an_animator_transitions_behave_as_before():
    notch = NotchWindow(GEOMETRY)
    notch.show_notch()
    assert notch.isVisible() and notch.transition.state() is QAbstractAnimation.State.Running
    notch.transition.setCurrentTime(notch.transition.duration())
    assert notch.visual_size == GEOMETRY.compact
    dispose(notch)


# --- Layout ----------------------------------------------------------------------


def test_the_layout_is_fixed_by_the_compact_notch():
    layout = notch_eye_layout(GEOMETRY, Rect(0, 0, 360, 96))
    base_height = 32.0 * BASE_EYE_HEIGHT_SHARE

    assert layout.scale == pytest.approx(base_height / 99.0)
    assert layout.center_x == 180.0
    left, right = layout.resting_eye("left"), layout.resting_eye("right")
    assert (left.width, left.height) == pytest.approx((126 * base_height / 99, base_height))
    assert right.center_x - left.center_x == pytest.approx(300 * base_height / 99)
    assert (left.center_x + right.center_x) / 2 == 180.0


def test_the_whole_motion_envelope_is_centred_in_the_compact_notch():
    layout = notch_eye_layout(GEOMETRY, Rect(0, 0, 360, 96))
    reach = layout.motion_bounds("left", production_envelope())

    assert reach.y == pytest.approx(GEOMETRY.compact.height - reach.bottom)
    assert reach.y > 0


def test_the_painted_resting_eyes_match_the_layout():
    notch = notch_for()

    for side in ("left", "right"):
        painted = eye_rect(notch, side, NEUTRAL)
        resting = notch.eye_layout.resting_eye(side)
        assert (painted.x(), painted.y(), painted.width(), painted.height()) == pytest.approx(
            (resting.x, resting.y, resting.width, resting.height)
        )
    dispose(notch)


@pytest.mark.parametrize("start,end", [(C, E), (E, C), (H, C)])
def test_the_eye_layout_does_not_change_during_a_transition(start, end):
    notch = notch_for(presence=start)
    before = (notch.eye_canvas, eye_rect(notch, "left", NEUTRAL), eye_rect(notch, "right", NEUTRAL))

    notch.set_presence(end)
    for ms in (0, 40, 100, 160, notch.transition.duration()):
        notch.transition.pause()
        notch.transition.setCurrentTime(ms)
        now = (notch.eye_canvas, eye_rect(notch, "left", NEUTRAL), eye_rect(notch, "right", NEUTRAL))
        assert now == before, f"eyes rescaled or moved at {ms} ms"
    dispose(notch)


# --- Rendering -------------------------------------------------------------------


@pytest.mark.parametrize("presence", [C, E])
def test_eyes_are_drawn_when_the_notch_is_shown(presence):
    notch = notch_for(presence=presence)
    image = render(notch)

    for side in ("left", "right"):
        assert pixel(image, eye_rect(notch, side, NEUTRAL).center()) == EYE_COLOR
    between = (eye_rect(notch, "left", NEUTRAL).center() + eye_rect(notch, "right", NEUTRAL).center()) / 2
    assert pixel(image, between) == NOTCH
    dispose(notch)


def test_no_eyes_are_drawn_when_hidden():
    notch = notch_for(presence=H)

    assert eye_pixels(render(notch)) == []
    dispose(notch)


@pytest.mark.parametrize(
    "frame",
    [
        both(offset_y=0.5, height_scale=2.0),  # far down: past the compact bottom
        both(offset_x=0.5),  # far right: the right eye leaves the compact notch
        both(offset_x=-0.5, offset_y=-0.2),  # up and left, into the flat top edge
    ],
    ids=["down", "right", "up-left"],
)
@pytest.mark.parametrize("presence", [C, E])
def test_eye_pixels_never_leave_the_drawn_shape(frame, presence):
    notch = notch_for(frame, presence)
    path = notch_path(notch.shape)
    image = render(notch)

    outside = [(x, y) for x, y in eye_pixels(image) if not path.contains(QPointF(x + 0.5, y + 0.5))]

    assert outside == []
    dispose(notch)


def test_the_shape_clips_the_eyes_mid_transition():
    notch = notch_for(both(height_scale=2.0), presence=H)
    notch.show_notch()
    notch.transition.pause()
    eye = eye_rect(notch, "left", notch.animator.controller.get_frame())
    # The first frame at which the growing notch has revealed part of the eye.
    ms = next(ms for ms in range(notch.transition.duration()) if (
        notch.transition.setCurrentTime(ms) or notch.shape.bounds.bottom > eye.top() + 3.0
    ))
    bottom = notch.shape.bounds.bottom
    assert eye.bottom() > bottom, f"the eye is no longer cut at {ms} ms"

    pixels = eye_pixels(render(notch))

    assert pixels, "the revealed part of the eyes is drawn"
    assert max(y for _, y in pixels) < bottom
    dispose(notch)


def test_the_notch_colour_is_the_eyelid_colour():
    notch = NotchWindow(GEOMETRY, color=QColor(20, 30, 40))

    assert notch.eye_painter.lid_color == QColor(20, 30, 40)
    notch.color = QColor(1, 2, 3)
    assert notch.eye_painter.lid_color == QColor(1, 2, 3)
    dispose(notch)


# --- Embodiment semantics ----------------------------------------------------------


def face_rect(frame: EmbodimentFrame, side: str):
    face = FaceWidget(animator=FaceAnimator(FixedFrameController(frame)))
    face.resize(700, 450)
    painter = face.eye_painter
    eye = painter.left if side == "left" else painter.right
    rect = painter.eye_rect(face.get_face_canvas_rect(), eye, frame.left_eye if side == "left" else frame.right_eye)
    dispose(face)
    return rect


@pytest.mark.parametrize(
    "frame,dx_sign,dy_sign",
    [
        (both(offset_x=-0.1), -1, 0),  # look left
        (both(offset_x=0.1), 1, 0),  # look right
        (both(offset_y=-0.08), 0, -1),  # look up
        (both(offset_y=0.08), 0, 1),  # look down
    ],
    ids=["left", "right", "up", "down"],
)
def test_gaze_moves_the_notch_eyes_the_same_way_as_the_face(frame, dx_sign, dy_sign):
    notch = notch_for(frame, E)
    # The face canvas is 1:1 at 700x450; the notch moves offsets at its offset scale.
    scale_ratio = notch.eye_layout.offset_scale / 1.0

    for side in ("left", "right"):
        notch_dx = eye_rect(notch, side, frame).x() - eye_rect(notch, side, NEUTRAL).x()
        notch_dy = eye_rect(notch, side, frame).y() - eye_rect(notch, side, NEUTRAL).y()
        face_dx = face_rect(frame, side).x() - face_rect(NEUTRAL, side).x()
        face_dy = face_rect(frame, side).y() - face_rect(NEUTRAL, side).y()

        assert (notch_dx > 0) - (notch_dx < 0) == dx_sign == (face_dx > 0) - (face_dx < 0)
        assert (notch_dy > 0) - (notch_dy < 0) == dy_sign == (face_dy > 0) - (face_dy < 0)
        assert notch_dx == pytest.approx(face_dx * scale_ratio)
        assert notch_dy == pytest.approx(face_dy * scale_ratio)
    dispose(notch)


def test_looking_left_is_visible_in_the_notch_pixels():
    notch = notch_for(both(offset_x=-0.1), E)
    image = render(notch)
    resting = eye_rect(notch, "left", NEUTRAL)
    moved = eye_rect(notch, "left", both(offset_x=-0.1))

    assert moved.right() < resting.right()
    assert pixel(image, QPointF(moved.left() + 1.5, moved.center().y())) == EYE_COLOR
    assert pixel(image, QPointF(resting.right() - 0.5, moved.center().y())) == NOTCH
    dispose(notch)


def test_a_neutral_frame_shows_two_open_symmetric_eyes():
    notch = notch_for(NEUTRAL, E)
    left, right = eye_rect(notch, "left", NEUTRAL), eye_rect(notch, "right", NEUTRAL)
    image = render(notch)

    assert left.center().x() + right.center().x() == pytest.approx(notch.width())
    assert pixel(image, left.center()) == EYE_COLOR == pixel(image, right.center())
    dispose(notch)


def test_closed_eyes_are_a_thin_bar_through_the_eye_centre():
    closed = both(closed=True, height_scale=2.0)
    notch = notch_for(closed, E)
    rect = eye_rect(notch, "left", closed)
    image = render(notch)

    centre_x, centre_y = round(rect.center().x()), int(rect.center().y())
    # The bar is under two pixels tall; it lands on a row next to the centre.
    assert max(image.pixelColor(centre_x, y).red() for y in range(centre_y - 1, centre_y + 2)) > 128
    assert pixel(image, QPointF(rect.center().x(), rect.top() + rect.height() * 0.2)) == NOTCH
    assert pixel(image, QPointF(rect.center().x(), rect.bottom() - rect.height() * 0.2)) == NOTCH
    dispose(notch)


def test_a_happy_eyelid_covers_the_lower_eye_in_the_notch_colour():
    happy = both(height_scale=2.0, happy_lid=0.45)
    notch = notch_for(happy, E)
    rect = eye_rect(notch, "left", happy)
    image = render(notch)

    assert pixel(image, QPointF(rect.center().x(), rect.top() + rect.height() * 0.25)) == EYE_COLOR
    assert pixel(image, QPointF(rect.center().x(), rect.bottom() - rect.height() * 0.15)) == NOTCH
    dispose(notch)


def test_a_real_expression_renders_through_the_shared_controller():
    controller = FaceController(MicroMotions(random.Random(2)))
    for profile in controller.expression_library.values():
        profile.blink_enabled = False
    controller.set_state(FaceState.HAPPY)
    animator = FaceAnimator(controller)
    for _ in range(60):
        animator.advance()
    notch = NotchWindow(GEOMETRY, animator=animator)
    notch.set_presence(E, animated=False)
    frame = controller.get_frame()
    rect = eye_rect(notch, "left", frame)
    image = render(notch)

    assert frame.left_eye.happy_lid > 0.3
    assert pixel(image, QPointF(rect.center().x(), rect.top() + rect.height() * 0.2)) == EYE_COLOR
    assert pixel(image, QPointF(rect.center().x(), rect.bottom() - 1.5)) == NOTCH
    dispose(notch, animator)
