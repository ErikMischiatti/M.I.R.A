from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF

from mira.domain.embodiment_cue import EyeShape
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

    ``offset_gain`` scales how far frame offsets move the eyes relative to the
    canvas: 1 (the face) moves them in proportion; a smaller view such as the
    notch may move them less without changing their size.
    """

    def __init__(
        self,
        left: Eye,
        right: Eye,
        *,
        eye_color: QColor,
        lid_color: QColor,
        offset_gain: float = 1.0,
    ):
        self.left = left
        self.right = right
        self.eye_color = eye_color
        self.lid_color = lid_color
        self.offset_gain = offset_gain

    @staticmethod
    def canvas_scale(canvas: QRectF) -> float:
        if canvas.isNull():
            return 1.0
        return canvas.width() / FACE_WIDTH_UNITS

    def eye_rect(self, canvas: QRectF, eye: Eye, eye_frame: EyeFrame) -> QRectF:
        scale = self.canvas_scale(canvas)

        gain = self.offset_gain
        x = canvas.x() + (
            FACE_WIDTH_UNITS * eye.x_ratio + eye_frame.offset_x * FACE_WIDTH_UNITS * gain
        ) * scale
        y = canvas.y() + (
            FACE_HEIGHT_UNITS * eye.y_ratio + eye_frame.offset_y * FACE_HEIGHT_UNITS * gain
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

        for rect, eye_frame in ((left_rect, frame.left_eye), (right_rect, frame.right_eye)):
            if self.is_cued(eye_frame):
                self.draw_cue_eye(painter, rect, eye_frame, scale)
            else:
                self.draw_eye(painter, rect, eye_frame, scale)

        if not frame.left_eye.closed and not self.is_cued(frame.left_eye):
            self.draw_tired_eyelid(painter, left_rect, "left", frame.left_eye)
            self.draw_angry_eyelid(painter, left_rect, "left", frame.left_eye)
            self.draw_happy_eyelid(painter, left_rect, frame.left_eye, scale)

        if not frame.right_eye.closed and not self.is_cued(frame.right_eye):
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

    @staticmethod
    def is_cued(eye_frame: EyeFrame) -> bool:
        return eye_frame.shape is not EyeShape.NATURAL and eye_frame.shape_amount > 0.0

    def draw_cue_eye(self, painter: QPainter, rect: QRectF, eye_frame: EyeFrame, scale: float):
        """A semantic cue's eye, always inside ``rect`` (see `EyeFrame`).

        Up to half way the natural eye closes towards its centre line; from
        there the shape grows from the centre to fill the rectangle. Blinks
        and eyelids do not apply while a cue shows. Strokes are inset by half
        their width, round caps included, so nothing is drawn outside the eye's
        rectangle and the eye-safety envelope still bounds everything.
        """

        amount = min(max(eye_frame.shape_amount, 0.0), 1.0)
        painter.save()
        if amount < 0.5:
            height = rect.height() * (1.0 - 2.0 * amount)
            closed = QRectF(rect.x(), rect.center().y() - height / 2.0, rect.width(), height)
            radius = min(eye_frame.corner_radius * FACE_WIDTH_UNITS * scale, height / 2.0, rect.width() / 2.0)
            painter.setBrush(QBrush(self.eye_color))
            painter.drawRoundedRect(closed, radius, radius)
            painter.restore()
            return

        grow = (amount - 0.5) * 2.0
        side = min(rect.width(), rect.height())
        box = QRectF(0.0, 0.0, rect.width() * grow, rect.height() * grow)
        box.moveCenter(rect.center())
        width = {EyeShape.CROSS: 0.24, EyeShape.CHEVRON: 0.22, EyeShape.TRIANGLE: 0.12}[eye_frame.shape] * side * grow
        inner = box.adjusted(width / 2.0, width / 2.0, -width / 2.0, -width / 2.0)
        pen = QPen(self.eye_color, max(width, 0.01))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        if eye_frame.shape is EyeShape.CROSS:
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(inner.topLeft(), inner.bottomRight())
            painter.drawLine(inner.topRight(), inner.bottomLeft())
        elif eye_frame.shape is EyeShape.CHEVRON:
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(QPolygonF([
                QPointF(inner.left(), inner.bottom() - inner.height() * 0.15),
                QPointF(inner.center().x(), inner.top() + inner.height() * 0.15),
                QPointF(inner.right(), inner.bottom() - inner.height() * 0.15),
            ]))
        else:
            painter.setBrush(QBrush(self.eye_color))
            painter.drawPolygon(QPolygonF([
                QPointF(inner.center().x(), inner.top()),
                inner.bottomRight(),
                inner.bottomLeft(),
            ]))
        painter.restore()

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
