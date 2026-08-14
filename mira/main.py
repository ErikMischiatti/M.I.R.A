import sys
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application
from mira.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    # Composition runs on the Qt main thread, before any widget exists.
    # `QtScheduler` binds whichever thread builds it as the serialized context,
    # and this is the thread that runs the event loop below.
    application = build_application()

    window = MainWindow(application)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()