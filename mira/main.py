import sys
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application
from mira.ui.desktop_shell import DesktopShell


def main():
    app = QApplication(sys.argv)

    # Composition runs on the Qt main thread, before any widget exists.
    # `QtScheduler` binds whichever thread builds it as the serialized context,
    # and this is the thread that runs the event loop below.
    application = build_application()
    desktop = DesktopShell(application)

    # Qt calls these in connection order: the surfaces and their clock stop
    # first, then the services they present (camera, scheduler).
    app.aboutToQuit.connect(desktop.shutdown)
    app.aboutToQuit.connect(application.shutdown)
    application.start()
    desktop.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
