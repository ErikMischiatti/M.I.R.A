"""The M.I.R.A. desktop runtime: ``python -m mira.main [--notch] [--config PATH]``.

One runtime per user (`mira.application.instance`). The first launcher
becomes it; a later one forwards its surface request to it and exits at once,
without starting Qt:

- no flag (``bin/mira``, `MIRA`): show the main window;
- ``--notch`` (``bin/mira-notch``, `MIRA-NOTCH`): show the ambient notch.

Whichever started the runtime, both surfaces then belong to the same
application graph and embodiment (`DesktopShell`), each built on first
request. Closing the main window ends the runtime only while no notch
exists; otherwise the notch's Quit, or Ctrl+C, ends it.

``--config PATH`` reads and saves another notch configuration than
``$MIRA_NOTCH_CONFIG`` or ``$XDG_CONFIG_HOME/mira/notch.json``; it applies
when the notch is first built, in whichever process that is. Any other
argument is left to Qt.
"""

import argparse
import os
import signal
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QSocketNotifier, QTimer
from PySide6.QtWidgets import QApplication

from mira.application.audio import desktop_audio_backend
from mira.application.composition import build_application, desktop_system_event_sources
from mira.application.instance import (
    Forwarded,
    InstanceError,
    Primary,
    SurfaceRequest,
    claim_or_forward,
    runtime_directory,
)
from mira.ui.desktop_shell import DesktopShell, Surface
from mira.ui.instance_server import InstanceServer
from mira.ui.listening_settings import ListeningSession
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


def claim_runtime(request: SurfaceRequest) -> Primary | Forwarded:
    """Become this user's runtime, or forward ``request`` to it."""
    return claim_or_forward(runtime_directory(), request)


def main(argv: list[str] | None = None, *, claim=claim_runtime):
    arguments, qt_arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    surface = Surface.NOTCH if arguments.notch else Surface.MAIN_WINDOW
    # Absolute: the runtime that reads it may run in another directory.
    config = None if arguments.config is None else arguments.config.expanduser().absolute()
    request = SurfaceRequest(surface.value, None if config is None else str(config))

    try:
        outcome = claim(request)
    except InstanceError as error:
        print(f"[mira] {error}", file=sys.stderr)
        sys.exit(1)
    if isinstance(outcome, Forwarded):
        owner = f" (pid {outcome.pid})" if outcome.pid is not None else ""
        print(f"[mira] connected to the running M.I.R.A.{owner}; {surface.value} requested")
        sys.exit(0)
    primary = outcome
    print(f"[mira] primary runtime (pid {os.getpid()}); requests on {primary.socket_path}")

    app = QApplication(sys.argv[:1] + qt_arguments)

    # Composition runs on the Qt main thread, before any widget exists.
    # `QtScheduler` binds whichever thread builds it as the serialized context,
    # and this is the thread that runs the event loop below. One runtime, so
    # one set of system watchers, whichever surfaces it shows.
    application = build_application(
        system_event_sources=desktop_system_event_sources(), audio_backend=desktop_audio_backend()
    )

    # The runtime's own preferences (not the notch's): how listening starts.
    listening = ListeningSession()
    if listening.diagnostic:
        print(f"[listening] {listening.diagnostic}", file=sys.stderr)

    desktop = DesktopShell(
        application,
        initial_surface=surface,
        listening_settings=listening.settings,
        listening_session=listening,
        # Read only when a notch is first requested, by this launcher or a
        # later one; a later one may name its own file.
        load_notch_session=lambda path=None: load_notch_session(path or config),
    )
    print(f"[mira] surface: {surface.value}")

    def serve(forwarded: SurfaceRequest) -> None:
        path = None if forwarded.notch_config is None else Path(forwarded.notch_config)
        desktop.show_surface(Surface(forwarded.show), notch_config=path)

    server = InstanceServer(primary.socket_path, serve)

    # Qt calls these in connection order: the surfaces and their clock stop
    # first, then the services they present (camera, scheduler), and only
    # then does the runtime stop answering and give up its lock.
    app.aboutToQuit.connect(desktop.shutdown)
    app.aboutToQuit.connect(application.shutdown)
    app.aboutToQuit.connect(server.close)
    app.aboutToQuit.connect(primary.release)
    interrupt_wakeup = quit_on_interrupt(app)  # noqa: F841 - held for the loop's lifetime
    application.start()
    desktop.start()
    server.listen()
    print("[mira] ready: listening for surface requests")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
