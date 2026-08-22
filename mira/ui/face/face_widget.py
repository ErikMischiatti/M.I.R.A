from PySide6.QtCore import Qt, QRectF, QTimer, QPointF, QSize
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QColor, QPainter, QBrush

from mira.ui.face.eye import Eye
from mira.ui.face.face_controller import FaceController
from mira.domain.embodiment_frame import (
    EyeFrame,
    FACE_HEIGHT_UNITS,
    FACE_WIDTH_UNITS,
)
from mira.domain.state import FaceState


class FaceWidget(QWidget):
    DESIGN_WIDTH = 700.0
    DESIGN_HEIGHT = 450.0

    def __init__(self):
        super().__init__()

        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.background_color = QColor(10, 10, 10)
        self.eye_color = QColor(245, 245, 245)

        self.controller = FaceController()

        self.left_eye = Eye(
            x_ratio=0.27,
            y_ratio=0.38,
            width_ratio=0.18,
            height_ratio=0.22,
            corner_radius=28.0,
        )

        self.right_eye = Eye(
            x_ratio=0.55,
            y_ratio=0.38,
            width_ratio=0.18,
            height_ratio=0.22,
            corner_radius=28.0,
        )

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.on_frame)
        self.frame_timer.start(30)

    def sizeHint(self) -> QSize:
        return QSize(620, 360)

    def on_frame(self):
        self.controller.update()
        self.update()

    def get_face_canvas_rect(self) -> QRectF:
        if self.width() <= 0 or self.height() <= 0:
            return QRectF()

        scale = min(
            self.width() / self.DESIGN_WIDTH,
            self.height() / self.DESIGN_HEIGHT,
        )
        canvas_width = self.DESIGN_WIDTH * scale
        canvas_height = self.DESIGN_HEIGHT * scale
        canvas_x = (self.width() - canvas_width) / 2.0
        canvas_y = (self.height() - canvas_height) / 2.0

        return QRectF(canvas_x, canvas_y, canvas_width, canvas_height)

    def get_canvas_scale(self) -> float:
        canvas = self.get_face_canvas_rect()
        if canvas.isNull():
            return 1.0
        return canvas.width() / self.DESIGN_WIDTH

    def get_eye_rect(self, eye: Eye, side: str) -> QRectF:
        frame = self.controller.get_frame()
        eye_frame = frame.left_eye if side == "left" else frame.right_eye
        canvas = self.get_face_canvas_rect()
        scale = self.get_canvas_scale()

        x = canvas.x() + (
            self.DESIGN_WIDTH * eye.x_ratio + eye_frame.offset_x * FACE_WIDTH_UNITS
        ) * scale
        y = canvas.y() + (
            self.DESIGN_HEIGHT * eye.y_ratio + eye_frame.offset_y * FACE_HEIGHT_UNITS
        ) * scale
        w = self.DESIGN_WIDTH * eye.width_ratio * eye_frame.width_scale * scale
        h = self.DESIGN_HEIGHT * eye.height_ratio * eye_frame.height_scale * scale

        return QRectF(x, y, w, h)

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.controller.clear_look_target()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        canvas = self.get_face_canvas_rect()
        if canvas.width() <= 0 or canvas.height() <= 0:
            return super().mouseMoveEvent(event)

        x_ratio = (event.position().x() - canvas.x()) / canvas.width()
        y_ratio = (event.position().y() - canvas.y()) / canvas.height()

        self.controller.set_look_target(x_ratio, y_ratio)
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), self.background_color)
        painter.setPen(Qt.NoPen)

        left_rect = self.get_eye_rect(self.left_eye, "left")
        right_rect = self.get_eye_rect(self.right_eye, "right")

        painter.setBrush(QBrush(self.eye_color))
        frame = self.controller.get_frame()
        self.draw_eye(painter, left_rect, frame.left_eye)
        self.draw_eye(painter, right_rect, frame.right_eye)

        if not frame.left_eye.closed:
            self.draw_tired_eyelid(painter, left_rect, "left", frame.left_eye)
            self.draw_angry_eyelid(painter, left_rect, "left", frame.left_eye)
            self.draw_happy_eyelid(painter, left_rect, frame.left_eye)

        if not frame.right_eye.closed:
            self.draw_tired_eyelid(painter, right_rect, "right", frame.right_eye)
            self.draw_angry_eyelid(painter, right_rect, "right", frame.right_eye)
            self.draw_happy_eyelid(painter, right_rect, frame.right_eye)

    def draw_eye(self, painter: QPainter, rect: QRectF, eye_frame: EyeFrame):
        scale = self.get_canvas_scale()
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

        painter.setBrush(QBrush(self.background_color))
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

        painter.setBrush(QBrush(self.background_color))
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

    def draw_happy_eyelid(self, painter: QPainter, rect: QRectF, eye_frame: EyeFrame):
        amount = eye_frame.happy_lid
        if amount <= 0.01:
            return

        painter.setBrush(QBrush(self.background_color))

        bottom_cover = rect.height() * amount
        painter.drawRoundedRect(
            rect.left() - 1,
            rect.bottom() - bottom_cover + 1,
            rect.width() + 2,
            rect.height(),
            eye_frame.corner_radius * FACE_WIDTH_UNITS * self.get_canvas_scale(),
            eye_frame.corner_radius * FACE_WIDTH_UNITS * self.get_canvas_scale(),
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_1:
            self.controller.set_state(FaceState.IDLE)
        elif event.key() == Qt.Key_2:
            self.controller.set_state(FaceState.LISTENING)
        elif event.key() == Qt.Key_3:
            self.controller.set_state(FaceState.THINKING)
        elif event.key() == Qt.Key_4:
            self.controller.set_state(FaceState.SPEAKING)
        elif event.key() == Qt.Key_5:
            self.controller.set_state(FaceState.HAPPY)
        elif event.key() == Qt.Key_6:
            self.controller.set_state(FaceState.TIRED)
        elif event.key() == Qt.Key_7:
            self.controller.set_state(FaceState.ANGRY)
        elif event.key() == Qt.Key_8:
            self.controller.set_state(FaceState.CONFUSED)

        self.update()
        super().keyPressEvent(event)
