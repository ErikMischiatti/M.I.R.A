from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QBrush

from mira.ui.face.eye import Eye
from mira.domain.embodiment_frame import (
    EmbodimentFrame,
    EyeFrame,
    FACE_HEIGHT_UNITS,
    FACE_WIDTH_UNITS,
)


class EyePainter:
    """Paint an `EmbodimentFrame`'s two eyes onto a face design canvas.

    The canvas is the face's design space (`FACE_WIDTH_UNITS` by
    `FACE_HEIGHT_UNITS`) scaled uniformly into a view; where it sits in that
    view, and what surrounds it, is the view's concern. `left` and `right`
    place each eye's resting rectangle on the canvas and frame offsets move it
    from there.

    Eyelids are drawn by covering part of the eye in `lid_color`, so the view
    must paint that same colour under the eyes for the lids to read as cut-outs.
    """

    def __init__(self, left: Eye, right: Eye, *, eye_color: QColor, lid_color: QColor):
        self.left = left
        self.right = right
        self.eye_color = eye_color
        self.lid_color = lid_color

    @staticmethod
    def canvas_scale(canvas: QRectF) -> float:
        if canvas.isNull():
            return 1.0
        return canvas.width() / FACE_WIDTH_UNITS

    def eye_rect(self, canvas: QRectF, eye: Eye, eye_frame: EyeFrame) -> QRectF:
        scale = self.canvas_scale(canvas)

        x = canvas.x() + (
            FACE_WIDTH_UNITS * eye.x_ratio + eye_frame.offset_x * FACE_WIDTH_UNITS
        ) * scale
        y = canvas.y() + (
            FACE_HEIGHT_UNITS * eye.y_ratio + eye_frame.offset_y * FACE_HEIGHT_UNITS
        ) * scale
        w = FACE_WIDTH_UNITS * eye.width_ratio * eye_frame.width_scale * scale
        h = FACE_HEIGHT_UNITS * eye.height_ratio * eye_frame.height_scale * scale

        return QRectF(x, y, w, h)

    def paint(self, painter: QPainter, canvas: QRectF, frame: EmbodimentFrame):
        scale = self.canvas_scale(canvas)
        left_rect = self.eye_rect(canvas, self.left, frame.left_eye)
        right_rect = self.eye_rect(canvas, self.right, frame.right_eye)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        self.draw_eye(painter, left_rect, frame.left_eye, scale)
        self.draw_eye(painter, right_rect, frame.right_eye, scale)

        if not frame.left_eye.closed:
            self.draw_tired_eyelid(painter, left_rect, "left", frame.left_eye)
            self.draw_angry_eyelid(painter, left_rect, "left", frame.left_eye)
            self.draw_happy_eyelid(painter, left_rect, frame.left_eye, scale)

        if not frame.right_eye.closed:
            self.draw_tired_eyelid(painter, right_rect, "right", frame.right_eye)
            self.draw_angry_eyelid(painter, right_rect, "right", frame.right_eye)
            self.draw_happy_eyelid(painter, right_rect, frame.right_eye, scale)

        painter.restore()

    def draw_eye(self, painter: QPainter, rect: QRectF, eye_frame: EyeFrame, scale: float):
        radius = eye_frame.corner_radius * FACE_WIDTH_UNITS * scale
        painter.setBrush(QBrush(self.eye_color))

        if eye_frame.closed:
            closed_height = max(6 * scale, rect.height() * 0.08)
            closed_y = rect.y() + (rect.height() - closed_height) / 2
            painter.drawRoundedRect(
                rect.x(),
                closed_y,
                rect.width(),
                closed_height,
                radius,
                radius,
            )
        else:
            painter.drawRoundedRect(rect, radius, radius)

    def draw_tired_eyelid(
        self, painter: QPainter, rect: QRectF, side: str, eye_frame: EyeFrame
    ):
        amount = eye_frame.tired_lid
        if amount <= 0.01:
            return

        painter.setBrush(QBrush(self.lid_color))
        top_cut = rect.height() * amount

        if side == "left":
            points = [
                QPointF(rect.left(), rect.top() - 1),
                QPointF(rect.right(), rect.top() - 1),
                QPointF(rect.left(), rect.top() + top_cut),
            ]
        else:
            points = [
                QPointF(rect.left(), rect.top() - 1),
                QPointF(rect.right(), rect.top() - 1),
                QPointF(rect.right(), rect.top() + top_cut),
            ]

        painter.drawPolygon(points)

    def draw_angry_eyelid(
        self, painter: QPainter, rect: QRectF, side: str, eye_frame: EyeFrame
    ):
        amount = eye_frame.angry_lid
        if amount <= 0.01:
            return

        painter.setBrush(QBrush(self.lid_color))
        top_cut = rect.height() * amount

        if side == "left":
            points = [
                QPointF(rect.left(), rect.top() - 1),
                QPointF(rect.right(), rect.top() - 1),
                QPointF(rect.right(), rect.top() + top_cut),
            ]
        else:
            points = [
                QPointF(rect.left(), rect.top() - 1),
                QPointF(rect.right(), rect.top() - 1),
                QPointF(rect.left(), rect.top() + top_cut),
            ]

        painter.drawPolygon(points)

    def draw_happy_eyelid(
        self, painter: QPainter, rect: QRectF, eye_frame: EyeFrame, scale: float
    ):
        amount = eye_frame.happy_lid
        if amount <= 0.01:
            return

        painter.setBrush(QBrush(self.lid_color))

        bottom_cover = rect.height() * amount
        painter.drawRoundedRect(
            rect.left() - 1,
            rect.bottom() - bottom_cover + 1,
            rect.width() + 2,
            rect.height(),
            eye_frame.corner_radius * FACE_WIDTH_UNITS * scale,
            eye_frame.corner_radius * FACE_WIDTH_UNITS * scale,
        )
