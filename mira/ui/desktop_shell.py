"""The running application's desktop surfaces and the one embodiment they show.

`mira.application` composes the application graph and must not import
`mira.ui`; the face's runtime — one `FaceController` stepped by one
`FaceAnimator` — is a UI object. This module is therefore the UI's counterpart
to `build_application`: it receives the built `Application`, creates the
embodiment once and hands it to every surface that presents MIRA. `mira.main`
creates both and ties their lifetimes to the Qt application.

Before it, `MainWindow` created the embodiment implicitly through a standalone
`FaceWidget` and mapped `state_changed` onto it, so nothing outside the window
could show the same face. Now:

  - one controller and one animator exist per shell, created here and owned
    here; surfaces receive the animator and only repaint on its ticks;
  - `state_changed` reaches the controller once, through `on_state_changed`,
    whichever surfaces are showing it. The activity authority stays the only
    source of state; this is presentation of its result, not a second state
    machine.

The surfaces are `MainWindow` and the `NotchWindow`. The notch opens with the
same `NotchSettings` model `MIRA-NOTCH` and `MIRA-TUNE` use, built by the same
`build_notch`.

Two questions stay apart. `EmbodimentIntent` — what MIRA is doing and
expressing — comes from the activity authority through `state_changed`.
What the notch presents — how much of it is visible, and what it shows when
open — is decided by one `NotchPresenter`, which the shell owns and feeds:
the committed activity, hover reported by the notch, and transient notices.
Activity alone never expands the notch; the presenter's expansion reasons do
(see `mira.ui.notch.presentation`). The presenter's notice timer runs on the
application's own `Scheduler`, so the notch adds no clock of its own.

Plain on purpose: no registry, no singleton. Constructing a shell has no side
effects beyond subscribing to the bus and reading no files; `start` shows and
starts, `shutdown` stops the clock and closes the surfaces.
"""

from __future__ import annotations

from mira.application.composition import Application
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.main_window import MainWindow
from mira.ui.notch.config_store import NotchSettings
from PySide6.QtCore import Qt

from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.ui.notch.config_store import ConfigSession
from mira.ui.notch.debug_window import NotchDebugWindow
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import NotchAction, NotchPresenter
from mira.ui.notch.startup import bind_presenter, build_notch

# The intents the integrated notch offers; the shell honours each of them.
NOTCH_ACTIONS = frozenset({NotchAction.OPEN_MAIN_WINDOW, NotchAction.OPEN_SETTINGS})


def task_notice(succeeded: bool, payload) -> Notice:
    """The notice for an action lifecycle event.

    Read by attribute, as `MainWindow` does, so the UI needs no import from
    `mira.actions`. An action held back for confirmation is a warning, not
    an error: nothing went wrong yet.
    """
    if succeeded:
        return Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)
    data = getattr(payload, "data", None) or {}
    if data.get("requires_confirmation"):
        return Notice(NoticeCategory.TASK, NoticeTone.WARNING, "Needs confirmation")
    return Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed")


class DesktopShell:
    """Owns the shared embodiment and the top-level surfaces presenting it.

    `notch_settings` are the loaded notch settings (`config_store.load_settings`
    in production); without them the notch uses the production defaults, so
    building a shell never reads a file. `notch_session` is the file behind
    them, which the notch settings window saves to and reloads from; without
    it that window cannot persist. `controller` is an injection seam for
    tests (a seeded `MicroMotions`, say); production passes nothing and gets the
    packaged expression library.
    """

    def __init__(
        self,
        application: Application,
        *,
        notch_settings: NotchSettings | None = None,
        notch_session: ConfigSession | None = None,
        controller: FaceController | None = None,
    ):
        self.controller = FaceController() if controller is None else controller
        # Unparented and held here: the clock lives exactly as long as the
        # shell, never as long as whichever surface happens to be built first.
        self.animator = FaceAnimator(self.controller)

        # Subscribed before any surface exists, so the embodiment follows the
        # application state whichever surfaces are built below.
        application.event_bus.subscribe("state_changed", self.on_state_changed)

        self.window = MainWindow(application, self.animator)
        settings = NotchSettings() if notch_settings is None else notch_settings
        self.notch = build_notch(settings, animator=self.animator)
        self.presenter = NotchPresenter(application.scheduler, settings.behaviour, actions=NOTCH_ACTIONS)
        bind_presenter(self.notch, self.presenter)
        self.notch.action_requested.connect(self.on_notch_action)
        self.notch_session = notch_session
        # Built on first request; one window however often it is asked for.
        self.settings_window: NotchDebugWindow | None = None

        # After MainWindow's own action handlers, which subscribed while it
        # was built above.
        application.event_bus.subscribe("action_completed", self.on_action_completed)
        application.event_bus.subscribe("action_failed", self.on_action_failed)

    def start(self) -> None:
        """Show the surfaces, then start the one clock that steps them."""
        self.window.show()
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
        self.presenter.shutdown()
        self.notch.set_presence(NotchPresence.HIDDEN, animated=False)
        self.notch.close()
        if self.settings_window is not None:
            self.settings_window.close()
        self.window.close()

    def on_action_completed(self, payload) -> None:
        self.presenter.present(task_notice(True, payload))

    def on_action_failed(self, payload) -> None:
        self.presenter.present(task_notice(False, payload))

    # --- Notch actions ----------------------------------------------------------

    def on_notch_action(self, action: str) -> None:
        if action == NotchAction.OPEN_MAIN_WINDOW:
            self.open_main_window()
        elif action == NotchAction.OPEN_SETTINGS:
            self.open_settings()

    def open_main_window(self) -> None:
        """Bring the one main window forward: restored, shown, raised, active."""
        _bring_forward(self.window)

    def open_settings(self) -> None:
        """Open the notch settings for the running notch, or bring them forward.

        The settings tune this notch and its presenter live. They hold no face
        controller and offer no previews, so they can never override the
        application's expression or show synthetic notices.
        """
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
            self.presenter.set_activity(ActivityState.IDLE)
        else:
            self.controller.set_intent(embodiment)
            self.presenter.set_activity(embodiment.activity)


def _bring_forward(window) -> None:
    if window.windowState() & Qt.WindowMinimized:
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
