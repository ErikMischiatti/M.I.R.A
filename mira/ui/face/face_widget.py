from PySide6.QtCore import Qt, QRectF, QTimer, QPointF, QSize
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QColor, QPainter

from mira.ui.face.eye import Eye
from mira.ui.face.eye_painter import EyePainter
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.domain.attention import AttentionTarget
from mira.domain.state import FaceState


def cursor_attention_target(position: QPointF, canvas: QRectF) -> AttentionTarget | None:
    """Convert a cursor position into attention within the face canvas.

    The canvas maps onto the normalized [-1, 1] view space; positions beyond
    it attend to the nearest edge. Returns None for an empty canvas.
    """

    if canvas.width() <= 0 or canvas.height() <= 0:
        return None
    return AttentionTarget.clamped(
        (position.x() - canvas.x()) / canvas.width() * 2.0 - 1.0,
        (position.y() - canvas.y()) / canvas.height() * 2.0 - 1.0,
    )


class FaceWidget(QWidget):
    """Render one embodiment's frames and turn the cursor into attention.

    Standalone, the widget creates and starts its own `FaceAnimator`, parented
    to itself. Given a shared `animator`, it shows that animator's controller,
    repaints on its ticks and leaves starting and stopping it to the owner.
    Either way the widget never steps the controller itself.
    """

    DESIGN_WIDTH = 700.0
    DESIGN_HEIGHT = 450.0

    def __init__(
        self,
        controller: FaceController | None = None,
        *,
        animator: FaceAnimator | None = None,
    ):
        super().__init__()

        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        if animator is None:
            self.controller = FaceController() if controller is None else controller
            self.animator = FaceAnimator(self.controller, parent=self)
            self.animator.start()
        else:
            if controller is not None and controller is not animator.controller:
                raise ValueError("controller must be the one the animator steps")
            self.controller = animator.controller
            self.animator = animator
        self.animator.frame_advanced.connect(self.update)

        self.eye_painter = EyePainter(
            left=Eye(
                x_ratio=0.27,
                y_ratio=0.38,
                width_ratio=0.18,
                height_ratio=0.22,
                corner_radius=28.0,
            ),
            right=Eye(
                x_ratio=0.55,
                y_ratio=0.38,
                width_ratio=0.18,
                height_ratio=0.22,
                corner_radius=28.0,
            ),
            eye_color=QColor(245, 245, 245),
            lid_color=QColor(10, 10, 10),
        )

    def sizeHint(self) -> QSize:
        return QSize(620, 360)

    @property
    def frame_timer(self) -> QTimer:
        """The animator's timer; shared with every view on that animator."""
        return self.animator.timer

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
        return self.eye_painter.canvas_scale(self.get_face_canvas_rect())

    # The eyelids cut into the eyes in the colour painted beneath them, so the
    # widget background and the painter's lid colour are one setting.
    @property
    def background_color(self) -> QColor:
        return self.eye_painter.lid_color

    @background_color.setter
    def background_color(self, color: QColor):
        self.eye_painter.lid_color = color

    @property
    def eye_color(self) -> QColor:
        return self.eye_painter.eye_color

    @eye_color.setter
    def eye_color(self, color: QColor):
        self.eye_painter.eye_color = color

    @property
    def left_eye(self) -> Eye:
        return self.eye_painter.left

    @property
    def right_eye(self) -> Eye:
        return self.eye_painter.right

    def get_eye_rect(self, eye: Eye, side: str) -> QRectF:
        frame = self.controller.get_frame()
        eye_frame = frame.left_eye if side == "left" else frame.right_eye
        return self.eye_painter.eye_rect(self.get_face_canvas_rect(), eye, eye_frame)

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.controller.clear_attention_target()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        target = cursor_attention_target(event.position(), self.get_face_canvas_rect())
        if target is not None:
            self.controller.set_attention_target(target)
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), self.background_color)
        self.eye_painter.paint(
            painter, self.get_face_canvas_rect(), self.controller.get_frame()
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
