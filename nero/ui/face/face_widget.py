from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QBrush
from PySide6.QtWidgets import QWidget

from nero.ui.face.eye import Eye
from nero.ui.face.face_controller import FaceController
from nero.ui.face.face_state import FaceState


class FaceWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.setMinimumSize(700, 450)
        self.setFocusPolicy(Qt.StrongFocus)

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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), self.background_color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self.eye_color))

        self.draw_eye(painter, self.left_eye, self.controller.left_eye_closed, "left")
        self.draw_eye(painter, self.right_eye, self.controller.right_eye_closed, "right")

    def draw_eye(self, painter: QPainter, eye: Eye, is_closed: bool, side: str):
        rect = self.get_eye_rect(eye, side)
        radius = self.controller.current_corner_radius

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
            painter.drawRoundedRect(
                rect,
                radius,
                radius,
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