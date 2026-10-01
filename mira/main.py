import signal
import socket
import sys
from PySide6.QtCore import QSocketNotifier, QTimer
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application
from mira.ui.desktop_shell import DesktopShell
from mira.ui.notch.config_store import load_settings


def quit_on_interrupt(app: QApplication) -> tuple[socket.socket, socket.socket] | None:
    """Make Ctrl+C quit the event loop, so the normal shutdown path runs.

    Python runs signal handlers only between bytecodes on the main thread,
    and the Qt event loop is C++. Left alone, SIGINT raises KeyboardInterrupt
    inside whichever Python slot runs next — in practice the face animator's
    tick — where PySide prints and swallows it, and the application keeps
    running. Instead the handler asks Qt to quit, and the signal's wakeup fd
    is watched by the event loop, so the handler runs at once even when no
    Python code would otherwise run.

    After the first Ctrl+C the default action is restored, so a second one
    still ends a shutdown that hangs. An interrupt that was ignored when the
    process started (a background job) stays ignored. Returns the wakeup
    sockets, which must stay open for the life of the event loop.
    """

    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        return None

    receiver, sender = socket.socketpair()
    receiver.setblocking(False)
    sender.setblocking(False)

    def drain():
        # Running this slot is what lets Python run the pending handler.
        try:
            while receiver.recv(64):
                pass
        except BlockingIOError:
            pass

    notifier = QSocketNotifier(receiver.fileno(), QSocketNotifier.Type.Read, app)
    notifier.activated.connect(drain)
    # Process-wide state changes only once the event loop side exists.
    signal.set_wakeup_fd(sender.fileno(), warn_on_full_buffer=False)

    def interrupt(_signum, _frame):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        # Posted rather than called: it also quits a loop that has not
        # started yet when the signal arrives.
        QTimer.singleShot(0, app.quit)

    signal.signal(signal.SIGINT, interrupt)
    return receiver, sender


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

    interrupt_wakeup = quit_on_interrupt(app)  # noqa: F841 - held for the loop's lifetime
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
