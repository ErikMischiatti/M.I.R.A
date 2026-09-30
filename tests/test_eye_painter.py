"""`EyePainter` paints a frame onto any canvas, independent of `FaceWidget`."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from mira.domain.embodiment_frame import EmbodimentFrame, EyeFrame
from mira.ui.face.eye import Eye
from mira.ui.face.eye_painter import EyePainter


EYE = QColor(245, 245, 245)
LID = QColor(10, 10, 10)
OPEN = EmbodimentFrame(left_eye=EyeFrame(), right_eye=EyeFrame())


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


def eye_painter() -> EyePainter:
    return EyePainter(
        left=Eye(x_ratio=0.27, y_ratio=0.38, width_ratio=0.18, height_ratio=0.22),
        right=Eye(x_ratio=0.55, y_ratio=0.38, width_ratio=0.18, height_ratio=0.22),
        eye_color=EYE,
        lid_color=LID,
    )


def paint(canvas: QRectF, frame: EmbodimentFrame, size=(400, 300)) -> QImage:
    image = QImage(*size, QImage.Format_ARGB32_Premultiplied)
    image.fill(LID)
    painter = QPainter(image)
    eye_painter().paint(painter, canvas, frame)
    painter.end()
    return image


def test_the_canvas_scale_maps_design_units_onto_the_canvas_width():
    assert EyePainter.canvas_scale(QRectF(0, 0, 350, 225)) == 0.5
    assert EyePainter.canvas_scale(QRectF()) == 1.0


def test_eye_rects_scale_and_move_with_the_canvas():
    painter = eye_painter()
    eye = painter.left

    at_design = painter.eye_rect(QRectF(0, 0, 700, 450), eye, EyeFrame())
    halved = painter.eye_rect(QRectF(20, 10, 350, 225), eye, EyeFrame())

    assert (halved.x(), halved.y()) == pytest.approx(
        (20 + at_design.x() / 2, 10 + at_design.y() / 2)
    )
    assert (halved.width(), halved.height()) == pytest.approx(
        (at_design.width() / 2, at_design.height() / 2)
    )


def test_a_translated_canvas_paints_the_same_pixels_translated():
    base = paint(QRectF(0, 0, 350, 225), OPEN)
    moved = paint(QRectF(40, 30, 350, 225), OPEN)

    assert moved.copy(40, 30, 350, 225) == base.copy(0, 0, 350, 225)


def test_painting_restores_the_callers_painter_state():
    image = QImage(100, 100, QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    pen_before = painter.pen()
    hints_before = painter.renderHints()

    eye_painter().paint(painter, QRectF(0, 0, 100, 64), OPEN)

    assert painter.pen() == pen_before
    assert painter.renderHints() == hints_before
    painter.end()


def test_eyelids_are_drawn_in_the_lid_colour():
    lidded = EyeFrame(happy_lid=0.5)
    image = paint(QRectF(0, 0, 350, 225), EmbodimentFrame(lidded, lidded))
    box = eye_painter().eye_rect(QRectF(0, 0, 350, 225), eye_painter().left, lidded)

    assert image.pixelColor(round(box.center().x()), round(box.bottom() - 3)) == LID
    assert image.pixelColor(round(box.center().x()), round(box.top() + 5)) == EYE
