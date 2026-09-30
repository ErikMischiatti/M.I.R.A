"""Standalone notch demo: ``python -m mira.ui.notch``.

Developer tooling only; M.I.R.A.'s normal startup never shows the notch. The
demo cycles compact, expanded, compact and hidden so placement, stacking and
show/hide can be watched. Clicking the notch toggles compact and expanded for
the rest of the current step, which makes the input mask observable: clicks
beside the notch reach whatever window lies underneath. Ctrl+C quits.
"""

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchPresence


CYCLE = (
    (NotchPresence.COMPACT, 3000),
    (NotchPresence.EXPANDED, 3000),
    (NotchPresence.COMPACT, 2000),
    (NotchPresence.HIDDEN, 2000),
)


class DemoNotchWindow(NotchWindow):
    def mousePressEvent(self, event):
        toggled = (
            NotchPresence.COMPACT
            if self.presence is NotchPresence.EXPANDED
            else NotchPresence.EXPANDED
        )
        print(f"[notch] click -> {toggled}")
        self.set_presence(toggled)
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
        presence, duration = CYCLE[step % len(CYCLE)]
        step += 1
        window.set_presence(presence)
        print(f"[notch] {presence} (window visible: {window.isVisible()})")
        QTimer.singleShot(duration, advance)

    advance()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
