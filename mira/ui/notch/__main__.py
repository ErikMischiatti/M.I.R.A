"""Standalone notch demo: ``python -m mira.ui.notch``.

Developer tooling only; M.I.R.A.'s normal startup never shows the notch. The
demo builds its own embodiment — one `FaceController` stepped by one running
`FaceAnimator` — and shows it in the notch: blink, idle motion and expressions
come from the same controller the face uses. The eyes follow the mouse pointer
anywhere on screen (read by polling, so on X11), and every few seconds the
expression changes.

Presence loops through animated transitions and two deliberate reversals.
Clicking the notch toggles compact and expanded; clicks beside it reach the
window underneath. Ctrl+C quits.
"""

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from mira.domain.attention import AttentionTarget
from mira.domain.state import FaceState
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchPresence


# (request, milliseconds until the next one). The short gaps interrupt the
# running transition to show reversals.
SCRIPT = (
    ("show_notch", 3000),
    ("expand_notch", 3000),
    ("collapse_notch", 3000),
    ("expand_notch", 80),
    ("collapse_notch", 3000),
    ("hide_notch", 90),
    ("show_notch", 3000),
    ("hide_notch", 1500),
)

EXPRESSIONS = (
    FaceState.IDLE,
    FaceState.LISTENING,
    FaceState.THINKING,
    FaceState.SPEAKING,
    FaceState.HAPPY,
    FaceState.CONFUSED,
    FaceState.TIRED,
    FaceState.ANGRY,
)
EXPRESSION_MS = 2500


class DemoNotchWindow(NotchWindow):
    def mousePressEvent(self, event):
        if self.presence is NotchPresence.EXPANDED:
            print("[notch] click -> collapse")
            self.collapse_notch()
        else:
            print("[notch] click -> expand")
            self.expand_notch()
        super().mousePressEvent(event)


def pointer_attention(window: NotchWindow) -> AttentionTarget | None:
    """Attention towards the pointer, relative to the notch on its screen."""

    screen = window.tracked_screen
    if screen is None:
        return None
    area = screen.geometry()
    pointer = QCursor.pos()
    centre = window.geometry().center()
    return AttentionTarget.clamped(
        (pointer.x() - centre.x()) / (area.width() / 2.0),
        (pointer.y() - centre.y()) / (area.height() / 2.0),
    )


def main():
    app = QApplication(sys.argv)
    # Let Ctrl+C in the terminal end the demo; the Qt loop otherwise hides it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    controller = FaceController()
    animator = FaceAnimator(controller)
    window = DemoNotchWindow(animator=animator)

    def follow_pointer():
        target = pointer_attention(window)
        if target is not None:
            controller.set_attention_target(target)

    animator.frame_advanced.connect(follow_pointer)
    animator.start()

    screen = window.tracked_screen
    print(f"[notch] platform: {app.platformName()}")
    if screen is not None:
        print(f"[notch] screen {screen.name()!r} available: {screen.availableGeometry()}")
    print(f"[notch] window bounds: {window.geometry()}")
    print(f"[notch] eye layout: {window.eye_layout}")

    step = 0

    def advance():
        nonlocal step
        request, delay = SCRIPT[step % len(SCRIPT)]
        step += 1
        getattr(window, request)()
        print(
            f"[notch] {request} -> {window.presence} "
            f"({window.transition.duration()} ms, window visible: {window.isVisible()})"
        )
        QTimer.singleShot(delay, advance)

    expression = 0

    def next_expression():
        nonlocal expression
        state = EXPRESSIONS[expression % len(EXPRESSIONS)]
        expression += 1
        controller.set_state(state)
        print(f"[notch] expression {state.name.lower()}")

    expressions = QTimer(window)
    expressions.timeout.connect(next_expression)
    expressions.start(EXPRESSION_MS)

    advance()
    next_expression()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
