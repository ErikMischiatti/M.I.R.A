import random

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QBrush
from PySide6.QtWidgets import QWidget

from nero.ui.face.eye import Eye
from nero.ui.face.face_state import FaceState


class FaceWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.setMinimumSize(700, 450)
        self.setFocusPolicy(Qt.StrongFocus)

        self.background_color = QColor(10, 10, 10)
        self.eye_color = QColor(245, 245, 245)

        self.state = FaceState.IDLE

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
        self.frame_timer.timeout.connect(self.update_animation)
        self.frame_timer.start(30)

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.trigger_blink)
        self.schedule_next_blink()

        self.speaking_phase = 0.0

    def schedule_next_blink(self):
        next_blink_ms = random.randint(2000, 5000)
        self.blink_timer.start(next_blink_ms)

    def trigger_blink(self):
        self.left_eye.is_closed = True
        self.right_eye.is_closed = True
        self.update()

        QTimer.singleShot(140, self.end_blink)

    def end_blink(self):
        self.left_eye.is_closed = False
        self.right_eye.is_closed = False
        self.update()
        self.schedule_next_blink()

    def set_state(self, new_state: FaceState):
        self.state = new_state
        self.update()

    def update_animation(self):
        if self.state == FaceState.SPEAKING:
            self.speaking_phase += 0.25
        else:
            self.speaking_phase = 0.0

        self.update()

    def get_eye_geometry_for_state(self, eye: Eye) -> QRectF:
        width = self.width()
        height = self.height()

        x = width * eye.x_ratio
        y = height * eye.y_ratio
        w = width * eye.width_ratio
        h = height * eye.height_ratio

        if self.state == FaceState.IDLE:
            pass

        elif self.state == FaceState.LISTENING:
            h *= 1.10
            y -= h * 0.03

        elif self.state == FaceState.THINKING:
            h *= 0.72
            y += height * 0.03

        elif self.state == FaceState.SPEAKING:
            pulse = 1.0 + 0.08 * abs(__import__("math").sin(self.speaking_phase))
            h *= pulse

        return QRectF(x, y, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), self.background_color)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self.eye_color))

        self.draw_eye(painter, self.left_eye)
        self.draw_eye(painter, self.right_eye)

    def draw_eye(self, painter: QPainter, eye: Eye):
        rect = self.get_eye_geometry_for_state(eye)

        if eye.is_closed:
            closed_height = max(6, rect.height() * 0.08)
            closed_y = rect.y() + (rect.height() - closed_height) / 2
            painter.drawRoundedRect(
                rect.x(),
                closed_y,
                rect.width(),
                closed_height,
                eye.corner_radius,
                eye.corner_radius,
            )
        else:
            painter.drawRoundedRect(
                rect,
                eye.corner_radius,
                eye.corner_radius,
            )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_1:
            self.set_state(FaceState.IDLE)
        elif event.key() == Qt.Key_2:
            self.set_state(FaceState.LISTENING)
        elif event.key() == Qt.Key_3:
            self.set_state(FaceState.THINKING)
        elif event.key() == Qt.Key_4:
            self.set_state(FaceState.SPEAKING)

        super().keyPressEvent(event)