"""The running application's desktop surfaces and the one embodiment they show.

`mira.application` composes the application graph and must not import
`mira.ui`; the face's runtime — one `FaceController` stepped by one
`FaceAnimator` — is a UI object. This module is therefore the UI's counterpart
to `build_application`: it receives the built `Application`, creates the
embodiment once and hands it to every surface that presents MIRA. `mira.main`
creates both and ties their lifetimes to the Qt application.

One runtime, two entry points (`ShellMode`):

    MIRA         MAIN_WINDOW   the main window; no notch is built
    MIRA-NOTCH   NOTCH         the notch; the main window is built the first
                               time the user asks for it (double click, or
                               the notch's context menu) and kept afterwards

Either way there is exactly one application graph, one controller and one
animator per process, and at most one `MainWindow`: `open_main_window` builds
it once, through this shell, and only brings it forward after that. Closing
the window hides it; the notch runtime keeps running and the next request
shows the same window again. Nothing ever starts a second process.

  - one controller and one animator exist per shell, created here and owned
    here; surfaces receive the animator and only repaint on its ticks;
  - `state_changed` reaches the controller once, through `on_state_changed`,
    whichever surfaces are showing it. The activity authority stays the only
    source of state; this is presentation of its result, not a second state
    machine.

The notch opens with the same `NotchSettings` model `MIRA-TUNE` uses, built by
the same `build_notch`.

Two questions stay apart. `EmbodimentIntent` — what MIRA is doing and
expressing — comes from the activity authority through `state_changed`.
What the notch presents — how much of it is visible, and what it shows when
open — is decided by one `NotchPresenter`, which the shell owns and feeds:
the committed activity, hover reported by the notch, and transient notices
(the bus event ``"notice"``, emitted by the producers in `mira.core`).
Activity alone never expands the notch; the presenter's expansion reasons do
(see `mira.ui.notch.presentation`). The presenter's notice timer runs on the
application's own `Scheduler`, so the notch adds no clock of its own.

Plain on purpose: no registry, no singleton. Constructing a shell has no side
effects beyond subscribing to the bus and reading no files; `start` shows and
starts, `shutdown` stops the clock and closes the surfaces.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mira.application.composition import Application
from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.main_window import MainWindow
from mira.ui.notch.config_store import ConfigSession, NotchSettings
from mira.ui.notch.debug_window import NotchDebugWindow
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import NotchAction, NotchPresenter
from mira.ui.notch.startup import bind_presenter, build_notch


class ShellMode(StrEnum):
    """Which surface a launcher opens; the other one is never shown first."""

    MAIN_WINDOW = "main window"
    NOTCH = "notch"


# The intents the notch offers; the shell honours each of them.
NOTCH_ACTIONS = frozenset({NotchAction.OPEN_MAIN_WINDOW, NotchAction.OPEN_SETTINGS, NotchAction.QUIT})


class DesktopShell:
    """Owns the shared embodiment and the top-level surfaces presenting it.

    ``mode`` chooses the surface `start` shows (see `ShellMode`). In
    MAIN_WINDOW mode the window is built here and no notch exists:
    `notch`, `presenter` and the notch settings stay None. In NOTCH mode the
    notch and its presenter are built here and `window` stays None until
    `open_main_window`.

    `notch_settings` are the loaded notch settings (`config_store.load_settings`
    in production); without them the notch uses the production defaults, so
    building a shell never reads a file. `notch_session` is the file behind
    them, which the notch settings window saves to and reloads from; without
    it that window cannot persist. `controller` is an injection seam for
    tests (a seeded `MicroMotions`, say); production passes nothing and gets the
    packaged expression library. `quit` is what the notch's Quit entry calls,
    by default `QApplication.quit`: in NOTCH mode no window is required to
    stay open, so the notch is the one place left to end the runtime from.
    """

    def __init__(
        self,
        application: Application,
        *,
        mode: ShellMode = ShellMode.MAIN_WINDOW,
        notch_settings: NotchSettings | None = None,
        notch_session: ConfigSession | None = None,
        controller: FaceController | None = None,
        quit: Callable[[], None] | None = None,
    ):
        # Held only to build the main window on demand; nothing else here
        # reaches into the graph.
        self._application = application
        self.mode = mode
        self.controller = FaceController() if controller is None else controller
        # Unparented and held here: the clock lives exactly as long as the
        # shell, never as long as whichever surface happens to be built first.
        self.animator = FaceAnimator(self.controller)
        self._quit = QApplication.quit if quit is None else quit

        # Subscribed before any surface exists, so the embodiment follows the
        # application state whichever surfaces are built below.
        application.event_bus.subscribe("state_changed", self.on_state_changed)

        self.window: MainWindow | None = None
        self.notch: NotchWindow | None = None
        self.presenter: NotchPresenter | None = None
        self.notch_session = notch_session
        # Built on first request; one window however often it is asked for.
        self.settings_window: NotchDebugWindow | None = None

        if mode is ShellMode.MAIN_WINDOW:
            self._build_main_window()
        else:
            settings = NotchSettings() if notch_settings is None else notch_settings
            self.notch = build_notch(settings, animator=self.animator)
            self.presenter = NotchPresenter(application.scheduler, settings.behaviour, actions=NOTCH_ACTIONS)
            bind_presenter(self.notch, self.presenter)
            self.notch.action_requested.connect(self.on_notch_action)

        # Every notice, from whichever producer (`mira.core.notice_producers`).
        application.event_bus.subscribe("notice", self.on_notice)

    def _build_main_window(self) -> MainWindow:
        # The one construction site: the same graph and the same animator as
        # every other surface, never a second runtime.
        self.window = MainWindow(self._application, self.animator)
        return self.window

    def start(self) -> None:
        """Show the mode's surface, then start the one clock that steps them."""
        if self.mode is ShellMode.MAIN_WINDOW:
            self.window.show()
        else:
            self.presenter.show()
        self.animator.start()

    def shutdown(self) -> None:
        """Stop the clock first, so no frame reaches a closing surface.

        The notch is hidden without animating — nothing would drive the
        transition once the event loop ends — and closed with the window, so
        neither outlives the application. The presenter stops first: its
        notice timer is cancelled while the scheduler still accepts it, and it
        presents nothing more. Idempotent.
        """
        self.animator.stop()
        if self.presenter is not None:
            self.presenter.shutdown()
        if self.notch is not None:
            self.notch.set_presence(NotchPresence.HIDDEN, animated=False)
            self.notch.close()
        if self.settings_window is not None:
            self.settings_window.close()
        if self.window is not None:
            self.window.close()

    def on_notice(self, notice: Notice) -> None:
        if self.presenter is not None:
            self.presenter.present(notice)

    # --- Notch actions ----------------------------------------------------------

    def on_notch_action(self, action: str) -> None:
        if action == NotchAction.OPEN_MAIN_WINDOW:
            self.open_main_window()
        elif action == NotchAction.OPEN_SETTINGS:
            self.open_settings()
        elif action == NotchAction.QUIT:
            self._quit()

    def open_main_window(self) -> MainWindow:
        """Bring the one main window forward, building it on first use.

        Restored, shown, raised and activated. Built lazily in NOTCH mode,
        here and nowhere else, so it shares this shell's application graph and
        embodiment; asking again never builds another.
        """
        window = self.window if self.window is not None else self._build_main_window()
        _bring_forward(window)
        return window

    def open_settings(self) -> None:
        """Open the notch settings for the running notch, or bring them forward.

        The settings tune this notch and its presenter live. They hold no face
        controller and offer no previews, so they can never override the
        application's expression or show synthetic notices.
        """
        if self.notch is None:
            return
        if self.settings_window is None:
            self.settings_window = NotchDebugWindow(
                self.notch, None, self.notch_session, self.presenter, previews=False
            )
        _bring_forward(self.settings_window)

    def on_state_changed(self, payload) -> None:
        """Present a committed state change on the shared embodiment."""
        new_state = payload["new_state"]
        embodiment = payload.get("embodiment")

        print(f"[STATE] {new_state}")

        if embodiment is None:
            self.controller.set_state(new_state)
            activity = ActivityState.IDLE
        else:
            self.controller.set_intent(embodiment)
            activity = embodiment.activity
        if self.presenter is not None:
            self.presenter.set_activity(activity)


def _bring_forward(window) -> None:
    if window.windowState() & Qt.WindowMinimized:
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
