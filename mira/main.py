import sys
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application
from mira.ui.desktop_shell import DesktopShell
from mira.ui.notch.config_store import load_settings


def main():
    app = QApplication(sys.argv)

    # Composition runs on the Qt main thread, before any widget exists.
    # `QtScheduler` binds whichever thread builds it as the serialized context,
    # and this is the thread that runs the event loop below.
    application = build_application()

    # The same file `MIRA-NOTCH` and `MIRA-TUNE` read and save: missing or
    # invalid contents fall back to the production defaults, never fail.
    notch = load_settings()
    print(f"[notch] configuration: {notch.path} ({notch.source.value})")
    if notch.diagnostic:
        print(f"[notch] saved configuration ignored, using production defaults: {notch.diagnostic}", file=sys.stderr)
    desktop = DesktopShell(application, notch_settings=notch.settings)

    # Qt calls these in connection order: the surfaces and their clock stop
    # first, then the services they present (camera, scheduler).
    app.aboutToQuit.connect(desktop.shutdown)
    app.aboutToQuit.connect(application.shutdown)
    application.start()
    desktop.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
