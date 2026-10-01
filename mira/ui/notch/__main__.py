"""Standalone notch: ``python -m mira.ui.notch [--tune | --notch-only]``.

Developer tooling, independent of the application: `MIRA` shows the notch as
part of its own desktop (`mira.ui.desktop_shell`), while this opens the real
`NotchWindow` on its own, showing its own running embodiment (one
`FaceController` stepped by one `FaceAnimator`, so blink and micro-motions
play), with the saved notch configuration (see `config_store`), or the
production defaults when there is none or it is invalid. The notch starts
compact with the idle expression.

- ``--notch-only`` (``bin/mira-notch``): the notch alone; Ctrl+C or SIGTERM
  quits.
- ``--tune`` (the default; ``bin/mira-tune``): the notch next to a
  `NotchDebugWindow` that tunes it live and can save, reload and reset the
  configuration. Closing the tuning window, or Ctrl+C, quits.

``--config PATH`` reads and writes another file than the default
``$MIRA_NOTCH_CONFIG`` or ``$XDG_CONFIG_HOME/mira/notch.json``.
"""

from __future__ import annotations

import argparse
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mira.application.scheduling import build_scheduler
from mira.domain.embodiment import ActivityState, EmbodimentIntent
from mira.domain.scheduler import Scheduler
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.config_store import ConfigSession, NotchSettings
from mira.ui.notch.debug_window import NotchDebugWindow
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presentation import NotchPresenter
from mira.ui.notch.startup import bind_presenter, build_notch
from mira.ui.notch.screen_selection import desktop_bounds, distance_squared_to_desktop_centre
from mira.ui.notch.screens import monitor_identity, rect_from_qrect


@dataclass
class NotchEnvironment:
    controller: FaceController
    animator: FaceAnimator
    notch: NotchWindow
    session: ConfigSession | None
    presenter: NotchPresenter
    scheduler: Scheduler
    debug: NotchDebugWindow | None = None

    def shutdown(self):
        """Stop the clock and the notice timer; idempotent."""
        self.animator.stop()
        self.presenter.shutdown()
        self.scheduler.shutdown()


# The tuning environment is the notch environment with its tuning window.
TuningEnvironment = NotchEnvironment


def build_notch_environment(
    session: ConfigSession | None = None,
    *,
    tuning: bool = True,
    scheduler: Scheduler | None = None,
) -> NotchEnvironment:
    """The notch, opening to compact with the idle expression.

    The notch starts with ``session``'s startup settings, or the production
    defaults without a session. Settings the notch's own embodiment rejects
    are reported and replaced by the defaults; startup never fails on
    configuration. With ``tuning`` a tuning window is built too. The animator
    is not started and no tuning window is shown.

    The notch is presented by its own `NotchPresenter`, as in the
    application, so hover expands it here too. Its notice timer runs on
    ``scheduler``, by default the production one (`build_scheduler`).
    """

    controller = FaceController()
    controller.set_intent(EmbodimentIntent(ActivityState.IDLE))
    animator = FaceAnimator(controller)
    settings = NotchSettings() if session is None else session.startup_settings
    notch = build_notch(settings, animator=animator)
    scheduler = build_scheduler() if scheduler is None else scheduler
    presenter = NotchPresenter(scheduler, settings.behaviour)
    bind_presenter(notch, presenter)
    presenter.show()

    environment = NotchEnvironment(controller, animator, notch, session, presenter, scheduler)
    if tuning:
        environment.debug = NotchDebugWindow(notch, controller, session, presenter)
    return environment


def build_tuning_environment(
    session: ConfigSession | None = None, *, scheduler: Scheduler | None = None
) -> NotchEnvironment:
    """The notch and its tuner; without ``session`` nothing is persisted."""

    return build_notch_environment(session, tuning=True, scheduler=scheduler)


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
            f"[notch]   {monitor_identity(screen).label!r} geometry={screen.geometry().getRect()} "
            f"available={screen.availableGeometry().getRect()} "
            f"centre=({area.center_x}, {area.y + area.height / 2.0}) "
            f"d2={distance_squared_to_desktop_centre(area, desktop):.0f} {' '.join(marks)}"
        )
    selection = notch.selection
    if selection is not None:
        print(f"[notch] monitor selection: {selection.mode.value}")


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mira.ui.notch",
        description="Show the M.I.R.A. notch on its own, optionally with its tuning window.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tune", dest="tuning", action="store_true", default=True,
                      help="show the tuning window next to the notch (default)")
    mode.add_argument("--notch-only", dest="tuning", action="store_false",
                      help="show the notch alone, with the saved configuration")
    parser.add_argument("--config", type=Path, metavar="PATH",
                        help="configuration file (default: $MIRA_NOTCH_CONFIG or "
                             "$XDG_CONFIG_HOME/mira/notch.json)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    # Let Ctrl+C in the terminal end the tool; the Qt loop otherwise hides it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    session = ConfigSession(arguments.config)
    print(f"[notch] mode: {'tune' if arguments.tuning else 'notch only'}")
    print(f"[notch] configuration: {session.path} ({session.source.value})")
    if session.last_load.migrated_from is not None:
        print(f"[notch] configuration is schema version {session.last_load.migrated_from}; Save upgrades it")
    if session.diagnostic:
        print(f"[notch] saved configuration ignored, using production defaults: {session.diagnostic}", file=sys.stderr)

    environment = build_notch_environment(session, tuning=arguments.tuning)
    print(f"[notch] platform: {app.platformName()}")
    print_screens(app, environment.notch)

    app.aboutToQuit.connect(environment.shutdown)
    environment.animator.start()
    if environment.debug is not None:
        # The notch is a tool window, which Qt does not count as a last window.
        environment.debug.closed.connect(app.quit)
        environment.debug.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
