"""Notch tuning environment: ``python -m mira.ui.notch``.

Developer tooling only; M.I.R.A.'s normal startup never shows the notch. This
opens the real `NotchWindow`, showing its own running embodiment (one
`FaceController` stepped by one `FaceAnimator`, so blink and micro-motions
play), next to a `NotchDebugWindow` that tunes it live. The notch starts
compact with the idle expression and changes only when a control is used.
The screen is chosen by the production central-screen policy. Closing the
tuning window, or Ctrl+C, quits; nothing is saved.
"""

import signal
import sys
from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from mira.domain.embodiment import ActivityState, EmbodimentIntent
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.debug_window import NotchDebugWindow
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.screen_selection import desktop_bounds, distance_squared_to_desktop_centre
from mira.ui.notch.screens import rect_from_qrect


@dataclass
class TuningEnvironment:
    controller: FaceController
    animator: FaceAnimator
    notch: NotchWindow
    debug: NotchDebugWindow


def build_tuning_environment() -> TuningEnvironment:
    """The notch, opening to compact with the idle expression, and its tuner.

    The animator is not started and the tuning window not shown.
    """

    controller = FaceController()
    controller.set_intent(EmbodimentIntent(ActivityState.IDLE))
    animator = FaceAnimator(controller)
    notch = NotchWindow(animator=animator)
    notch.show_notch()
    debug = NotchDebugWindow(notch, controller)
    return TuningEnvironment(controller, animator, notch, debug)


def print_screens(app: QApplication, notch: NotchWindow):
    """Each screen's selection data, and which one the notch is on."""

    screens = app.screens()
    desktop = desktop_bounds([rect_from_qrect(screen.geometry()) for screen in screens])
    print(f"[notch] desktop bounds: {desktop}")
    for screen in screens:
        area = rect_from_qrect(screen.geometry())
        marks = []
        if screen is notch.tracked_screen:
            marks.append("notch")
        if screen is app.primaryScreen():
            marks.append("primary")
        print(
            f"[notch]   {screen.name()!r} geometry={screen.geometry().getRect()} "
            f"available={screen.availableGeometry().getRect()} "
            f"centre=({area.center_x}, {area.y + area.height / 2.0}) "
            f"d2={distance_squared_to_desktop_centre(area, desktop):.0f} {' '.join(marks)}"
        )


def main():
    app = QApplication(sys.argv)
    # Let Ctrl+C in the terminal end the tool; the Qt loop otherwise hides it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    environment = build_tuning_environment()
    # The notch is a tool window, which Qt does not count as a last window.
    environment.debug.closed.connect(app.quit)

    print(f"[notch] platform: {app.platformName()}")
    print_screens(app, environment.notch)

    environment.animator.start()
    environment.debug.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
