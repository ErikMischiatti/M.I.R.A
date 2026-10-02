"""The M.I.R.A. desktop runtime: ``python -m mira.main [--notch] [--config PATH]``.

One process, one application graph, one embodiment, whichever surface opens
first (`DesktopShell` and `ShellMode`):

- no flag (``bin/mira``, `MIRA`): the main window; no notch is built and the
  notch configuration is not read.
- ``--notch`` (``bin/mira-notch``, `MIRA-NOTCH`): the ambient notch alone.
  Double-clicking it opens the main window of this same runtime. Closing that
  window hides it and leaves the notch running; the notch's context menu
  (Quit) or Ctrl+C ends the runtime.

``--config PATH`` (notch mode) reads and saves another notch configuration
than ``$MIRA_NOTCH_CONFIG`` or ``$XDG_CONFIG_HOME/mira/notch.json``. Any
other argument is left to Qt.
"""

import argparse
import signal
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QSocketNotifier, QTimer
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application, desktop_system_event_sources
from mira.ui.desktop_shell import DesktopShell, ShellMode
from mira.ui.notch.config_store import ConfigSession


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


def parse_arguments(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """This runtime's options, and the arguments left for Qt."""

    parser = argparse.ArgumentParser(
        prog="python -m mira.main",
        description="Run M.I.R.A.: the main window, or with --notch the ambient notch.",
    )
    parser.add_argument("--notch", action="store_true",
                        help="start with the notch only; double-click it to open the main window")
    parser.add_argument("--config", type=Path, metavar="PATH",
                        help="notch configuration file (default: $MIRA_NOTCH_CONFIG or "
                             "$XDG_CONFIG_HOME/mira/notch.json)")
    return parser.parse_known_args(argv)


def load_notch_session(path: Path | None) -> ConfigSession:
    """The notch configuration, reported as `MIRA-TUNE` reports it."""

    # Missing or invalid contents fall back to the production defaults, never
    # fail. The session also lets the notch settings window save and reload.
    session = ConfigSession(path)
    print(f"[notch] configuration: {session.path} ({session.source.value})")
    if session.last_load.migrated_from is not None:
        print(f"[notch] configuration is schema version {session.last_load.migrated_from}; Save in the notch settings upgrades it")
    if session.diagnostic:
        print(f"[notch] saved configuration ignored, using production defaults: {session.diagnostic}", file=sys.stderr)
    return session


def main(argv: list[str] | None = None):
    arguments, qt_arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1] + qt_arguments)
    mode = ShellMode.NOTCH if arguments.notch else ShellMode.MAIN_WINDOW

    # Composition runs on the Qt main thread, before any widget exists.
    # `QtScheduler` binds whichever thread builds it as the serialized context,
    # and this is the thread that runs the event loop below.
    # The notch is the ambient surface, so only it watches the system: the
    # main window alone has nowhere to show a device or network change.
    if mode is ShellMode.NOTCH:
        application = build_application(system_event_sources=desktop_system_event_sources())
    else:
        application = build_application()

    if mode is ShellMode.NOTCH:
        # The notch is a tool window, which Qt never counts, and the main
        # window may be closed and reopened: neither ends the runtime.
        app.setQuitOnLastWindowClosed(False)
        session = load_notch_session(arguments.config)
        desktop = DesktopShell(
            application, mode=mode, notch_settings=session.startup_settings, notch_session=session
        )
    else:
        desktop = DesktopShell(application, mode=mode)
    print(f"[mira] surface: {mode.value}")

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
