from PySide6.QtCore import QObject, QTimer, Signal

from mira.domain.embodiment_playback import REFERENCE_DT_SECONDS
from mira.ui.face.face_controller import FaceController


class FaceAnimator(QObject):
    """The one clock that steps an embodiment; views repaint when it ticks.

    Each tick advances the controller exactly once — micro-motions step once
    and playback interpolates by one reference interval — then emits
    `frame_advanced`. Views connect to that signal and read
    `controller.get_frame()`; they never step the controller themselves, so any
    number of views can show one embodiment without speeding it up.

    The animator owns its timer. Parent it to whatever should own the clock: a
    standalone `FaceWidget` parents its own, and views sharing an embodiment
    share one animator owned by their common parent.
    """

    FRAME_INTERVAL_MS = round(REFERENCE_DT_SECONDS * 1000)

    frame_advanced = Signal()

    def __init__(self, controller: FaceController, parent: QObject | None = None):
        super().__init__(parent)
        self.controller = controller

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)

    def start(self):
        self.timer.start(self.FRAME_INTERVAL_MS)

    def stop(self):
        self.timer.stop()

    def is_running(self) -> bool:
        return self.timer.isActive()

    def advance(self):
        self.controller.update(REFERENCE_DT_SECONDS)
        self.frame_advanced.emit()
