"""Characterize the pixels `FaceWidget` paints for a given embodiment frame.

The oracle below is a frozen transcription of the widget's eye painting as it
stood before the painter was extracted from it. The widget's render must equal
the oracle's pixel for pixel. Both paint through the same Qt raster engine in
the same process, so the comparison is exact without depending on stored images
that would change with the Qt version, platform or font setup.

The semantic probes at the end are independent of the oracle: they pin what the
pixels mean (background, open eye, closed eye, each eyelid), so a mistake copied
into both the oracle and the widget cannot pass unnoticed.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment_frame import EmbodimentFrame, EyeFrame
from mira.domain.micro_motion import MicroMotions
from mira.domain.state import FaceState
from mira.ui.face.face_controller import FaceController
from mira.ui.face.face_widget import FaceWidget


TICK = 0.030
BACKGROUND = QColor(10, 10, 10)
EYE = QColor(245, 245, 245)

# Letterboxed horizontally, the exact design size, and letterboxed vertically.
SIZES = ((1000, 450), (700, 450), (500, 600))


# --- Frozen oracle -----------------------------------------------------------

DESIGN_WIDTH = 700.0
DESIGN_HEIGHT = 450.0
EYES = {
    "left": (0.27, 0.38, 0.18, 0.22),
    "right": (0.55, 0.38, 0.18, 0.22),
}


def oracle_canvas(width: int, height: int) -> tuple[QRectF, float]:
    scale = min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT)
    canvas = QRectF(
        (width - DESIGN_WIDTH * scale) / 2.0,
        (height - DESIGN_HEIGHT * scale) / 2.0,
        DESIGN_WIDTH * scale,
        DESIGN_HEIGHT * scale,
    )
    return canvas, scale


def oracle_eye_rect(canvas: QRectF, scale: float, side: str, eye: EyeFrame) -> QRectF:
    x_ratio, y_ratio, width_ratio, height_ratio = EYES[side]
    return QRectF(
        canvas.x() + (DESIGN_WIDTH * x_ratio + eye.offset_x * 700.0) * scale,
        canvas.y() + (DESIGN_HEIGHT * y_ratio + eye.offset_y * 450.0) * scale,
        DESIGN_WIDTH * width_ratio * eye.width_scale * scale,
        DESIGN_HEIGHT * height_ratio * eye.height_scale * scale,
    )


def oracle_paint(frame: EmbodimentFrame, width: int, height: int) -> QImage:
    image = blank(width, height)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillRect(QRectF(0, 0, width, height).toRect(), BACKGROUND)
    painter.setPen(Qt.NoPen)

    canvas, scale = oracle_canvas(width, height)
    eyes = {"left": frame.left_eye, "right": frame.right_eye}
    rects = {side: oracle_eye_rect(canvas, scale, side, eye) for side, eye in eyes.items()}

    painter.setBrush(QBrush(EYE))
    for side in ("left", "right"):
        rect, eye = rects[side], eyes[side]
        radius = eye.corner_radius * 700.0 * scale
        painter.setBrush(QBrush(EYE))
        if eye.closed:
            closed_height = max(6 * scale, rect.height() * 0.08)
            closed_y = rect.y() + (rect.height() - closed_height) / 2
            painter.drawRoundedRect(rect.x(), closed_y, rect.width(), closed_height, radius, radius)
        else:
            painter.drawRoundedRect(rect, radius, radius)

    for side in ("left", "right"):
        rect, eye = rects[side], eyes[side]
        if eye.closed:
            continue
        # Tired: the lid slopes down towards the outer corner.
        if eye.tired_lid > 0.01:
            painter.setBrush(QBrush(BACKGROUND))
            cut = rect.height() * eye.tired_lid
            low = rect.left() if side == "left" else rect.right()
            painter.drawPolygon([
                QPointF(rect.left(), rect.top() - 1),
                QPointF(rect.right(), rect.top() - 1),
                QPointF(low, rect.top() + cut),
            ])
        # Angry: the lid slopes down towards the nose.
        if eye.angry_lid > 0.01:
            painter.setBrush(QBrush(BACKGROUND))
            cut = rect.height() * eye.angry_lid
            low = rect.right() if side == "left" else rect.left()
            painter.drawPolygon([
                QPointF(rect.left(), rect.top() - 1),
                QPointF(rect.right(), rect.top() - 1),
                QPointF(low, rect.top() + cut),
            ])
        if eye.happy_lid > 0.01:
            painter.setBrush(QBrush(BACKGROUND))
            cover = rect.height() * eye.happy_lid
            radius = eye.corner_radius * 700.0 * scale
            painter.drawRoundedRect(
                rect.left() - 1,
                rect.bottom() - cover + 1,
                rect.width() + 2,
                rect.height(),
                radius,
                radius,
            )

    painter.end()
    return image


# --- Scenarios ---------------------------------------------------------------


def blank(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    return image


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(application):
    face = FaceWidget(FaceController(MicroMotions(random.Random(7))))
    face.frame_timer.stop()
    yield face
    face.close()
    application.processEvents()


def settle(controller: FaceController, steps: int = 40) -> None:
    for _ in range(steps):
        controller.update(TICK)


def step_until_closed(controller: FaceController, limit: int = 600) -> None:
    for _ in range(limit):
        controller.update(TICK)
        if controller.get_frame().left_eye.closed:
            return
    raise AssertionError("no blink within the step limit")


def scenario_state(state: FaceState):
    def arrange(controller: FaceController) -> None:
        controller.set_state(state)
        settle(controller)

    return arrange


def arrange_attention(controller: FaceController) -> None:
    controller.set_attention_target(AttentionTarget(0.9, -0.6))
    settle(controller, 60)


def arrange_blink(controller: FaceController) -> None:
    step_until_closed(controller)


def arrange_asymmetric(controller: FaceController) -> None:
    profile = controller.get_profile()
    profile.asymmetry_offset_y_left = -14.0
    profile.asymmetry_height_right = 0.7
    controller.refresh_profile_targets()
    settle(controller)


SCENARIOS = {
    **{f"state-{state.name.lower()}": scenario_state(state) for state in FaceState},
    "attention": arrange_attention,
    "blink": arrange_blink,
    "asymmetric": arrange_asymmetric,
}


def render(widget: FaceWidget, width: int, height: int) -> QImage:
    widget.resize(width, height)
    image = blank(width, height)
    widget.render(image)
    return image


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("name", SCENARIOS)
def test_widget_render_matches_the_frozen_oracle(widget, name, size):
    SCENARIOS[name](widget.controller)
    frame = widget.controller.get_frame()

    actual = render(widget, *size)

    assert actual == oracle_paint(frame, *size)


def test_the_scenarios_exercise_every_drawing_branch(widget):
    frames = []
    for arrange in SCENARIOS.values():
        controller = FaceController(MicroMotions(random.Random(7)))
        arrange(controller)
        frames.append(controller.get_frame())
    eyes = [eye for frame in frames for eye in (frame.left_eye, frame.right_eye)]

    assert any(eye.closed for eye in eyes)
    assert any(eye.tired_lid > 0.01 for eye in eyes)
    assert any(eye.angry_lid > 0.01 for eye in eyes)
    assert any(eye.happy_lid > 0.01 for eye in eyes)
    assert any(abs(eye.offset_x) > 0.02 and abs(eye.offset_y) > 0.02 for eye in eyes)
    assert any(frame.left_eye.offset_y != frame.right_eye.offset_y for frame in frames)


# --- Semantic probes (independent of the oracle) -----------------------------


def pixel(image: QImage, x: float, y: float) -> QColor:
    return image.pixelColor(round(x), round(y))


def eye_box(width: int, height: int, side: str, frame: EmbodimentFrame) -> QRectF:
    canvas, scale = oracle_canvas(width, height)
    eye = frame.left_eye if side == "left" else frame.right_eye
    return oracle_eye_rect(canvas, scale, side, eye)


def test_the_background_fills_the_widget_outside_the_eyes(widget):
    image = render(widget, 1000, 450)

    for x, y in ((1, 1), (998, 1), (1, 448), (998, 448), (500, 20)):
        assert pixel(image, x, y) == BACKGROUND


def test_open_eyes_are_solid_eye_colour_at_their_centre(widget):
    SCENARIOS["state-idle"](widget.controller)
    frame = widget.controller.get_frame()
    image = render(widget, 1000, 450)

    for side in ("left", "right"):
        box = eye_box(1000, 450, side, frame)
        assert pixel(image, box.center().x(), box.center().y()) == EYE


def test_a_closed_eye_is_a_thin_bar_through_its_centre(widget):
    arrange_blink(widget.controller)
    frame = widget.controller.get_frame()
    image = render(widget, 1000, 450)

    box = eye_box(1000, 450, "left", frame)
    centre = box.center()
    assert pixel(image, centre.x(), centre.y()) == EYE
    assert pixel(image, centre.x(), box.top() + box.height() * 0.2) == BACKGROUND
    assert pixel(image, centre.x(), box.bottom() - box.height() * 0.2) == BACKGROUND


@pytest.mark.parametrize(
    "state,probe",
    [
        # Tired lids cover the outer upper corner; angry lids the inner one.
        (FaceState.TIRED, (0.3, 0.1)),
        (FaceState.ANGRY, (0.7, 0.1)),
        # Happy lids cover the lower band of the eye.
        (FaceState.HAPPY, (0.5, 0.9)),
    ],
)
def test_eyelids_cover_their_part_of_the_left_eye(widget, state, probe):
    scenario_state(state)(widget.controller)
    frame = widget.controller.get_frame()
    if frame.left_eye.closed:
        pytest.skip("the seeded run is mid-blink at this step")
    image = render(widget, 1000, 450)

    box = eye_box(1000, 450, "left", frame)
    covered = pixel(image, box.left() + box.width() * probe[0], box.top() + box.height() * probe[1])
    centre = pixel(image, box.center().x(), box.top() + box.height() * 0.45)

    assert covered == BACKGROUND
    assert centre == EYE
