"""Standalone notch demo: ``python -m mira.ui.notch``.

Developer tooling only; M.I.R.A.'s normal startup never shows the notch. The
demo loops through animated transitions — show, expand, collapse, hide — and
two deliberate reversals: an expansion collapsed part-way and a close reopened
part-way. Clicking the notch toggles compact and expanded, which also makes the
input mask observable: clicks beside the notch reach the window underneath.
Ctrl+C quits.
"""

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchPresence


# (request, milliseconds until the next one). The short gaps interrupt the
# running transition to show reversals.
SCRIPT = (
    ("show_notch", 1500),
    ("expand_notch", 1500),
    ("collapse_notch", 1500),
    ("expand_notch", 80),
    ("collapse_notch", 1500),
    ("hide_notch", 90),
    ("show_notch", 1500),
    ("hide_notch", 1500),
)


class DemoNotchWindow(NotchWindow):
    def mousePressEvent(self, event):
        if self.presence is NotchPresence.EXPANDED:
            print("[notch] click -> collapse")
            self.collapse_notch()
        else:
            print("[notch] click -> expand")
            self.expand_notch()
        super().mousePressEvent(event)


def main():
    app = QApplication(sys.argv)
    # Let Ctrl+C in the terminal end the demo; the Qt loop otherwise hides it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = DemoNotchWindow()
    screen = window.tracked_screen
    print(f"[notch] platform: {app.platformName()}")
    if screen is not None:
        print(f"[notch] screen {screen.name()!r} available: {screen.availableGeometry()}")
    print(f"[notch] window bounds: {window.geometry()}")

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

    advance()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
