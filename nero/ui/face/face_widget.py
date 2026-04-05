from PySide6.QtCore import Qt, QRectF, QTimer, QPointF
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QColor, QPainter, QBrush

from nero.ui.face.eye import Eye
from nero.ui.face.face_controller import FaceController
from nero.ui.face.face_state import FaceState


class FaceWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.setMinimumSize(700, 450)
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

    def on_frame(self):
        self.controller.update()
        self.update()

    def get_eye_rect(self, eye: Eye, side: str) -> QRectF:
        profile = self.controller.profile

        asym_y = 0.0
        asym_h = 1.0

        if side == "left":
            asym_y = profile.asymmetry_offset_y_left
            asym_h = profile.asymmetry_height_left
        elif side == "right":
            asym_y = profile.asymmetry_offset_y_right
            asym_h = profile.asymmetry_height_right

        x = self.width() * eye.x_ratio + self.controller.current_offset_x
        y = self.height() * eye.y_ratio + self.controller.current_offset_y + asym_y
        w = self.width() * eye.width_ratio * self.controller.current_width_scale
        h = self.height() * eye.height_ratio * self.controller.current_height_scale * asym_h

        return QRectF(x, y, w, h)
    
    def enterEvent(self, event):
        self.setMouseTracking(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.controller.clear_look_target()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return super().mouseMoveEvent(event)

        x_ratio = event.position().x() / self.width()
        y_ratio = event.position().y() / self.height()

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
        self.draw_eye(painter, left_rect, self.controller.left_eye_closed)
        self.draw_eye(painter, right_rect, self.controller.right_eye_closed)

        if not self.controller.left_eye_closed:
            self.draw_tired_eyelid(painter, left_rect, "left")
            self.draw_angry_eyelid(painter, left_rect, "left")
            self.draw_happy_eyelid(painter, left_rect)

        if not self.controller.right_eye_closed:
            self.draw_tired_eyelid(painter, right_rect, "right")
            self.draw_angry_eyelid(painter, right_rect, "right")
            self.draw_happy_eyelid(painter, right_rect)

    def draw_eye(self, painter: QPainter, rect: QRectF, is_closed: bool):
        radius = self.controller.current_corner_radius
        painter.setBrush(QBrush(self.eye_color))

        if is_closed:
            closed_height = max(6, rect.height() * 0.08)
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

    def draw_tired_eyelid(self, painter: QPainter, rect: QRectF, side: str):
        amount = self.controller.current_eyelid_tired
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

    def draw_angry_eyelid(self, painter: QPainter, rect: QRectF, side: str):
        amount = self.controller.current_eyelid_angry
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

    def draw_happy_eyelid(self, painter: QPainter, rect: QRectF):
        amount = self.controller.current_eyelid_happy
        if amount <= 0.01:
            return

        painter.setBrush(QBrush(self.background_color))

        bottom_cover = rect.height() * amount
        painter.drawRoundedRect(
            rect.left() - 1,
            rect.bottom() - bottom_cover + 1,
            rect.width() + 2,
            rect.height(),
            self.controller.current_corner_radius,
            self.controller.current_corner_radius,
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