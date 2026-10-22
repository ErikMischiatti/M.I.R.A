"""The running application's desktop surfaces and the one embodiment they show.

`mira.application` composes the application graph and must not import
`mira.ui`; the face's runtime — one `FaceController` stepped by one
`FaceAnimator` — is a UI object. This module is therefore the UI's counterpart
to `build_application`: it receives the built `Application`, creates the
embodiment once and hands it to every surface that presents MIRA. `mira.main`
creates both and ties their lifetimes to the Qt application.

The shell is the one authority over surfaces (`Surface`): the main window
and the notch. Each is built the first time it is asked for, through
`show_surface`, and reused afterwards; building one never builds the other.
Every request goes through that one method — the initial surface of the
launcher that started the runtime, a second launcher's request forwarded by
`mira.main` (single instance), the notch's double click or context menu:

    show_surface(MAIN_WINDOW)   build once, then restore, show, raise, activate
    show_surface(NOTCH)         load the notch configuration, build once, show

Closing the main window hides it. While no notch exists that also ends the
runtime, as it always has for `MIRA`; once a notch exists the runtime
continues (the notch is a tool window, which Qt never counts as a last
window), and the notch's Quit ends it.

  - one controller and one animator exist per shell, created here and owned
    here; surfaces receive the animator and only repaint on its ticks;
  - `state_changed` reaches the controller once, through `on_state_changed`,
    whichever surfaces are showing it. The activity authority stays the only
    source of state; this is presentation of its result, not a second state
    machine.

The notch opens with the same `NotchSettings` model `MIRA-TUNE` uses, built by
the same `build_notch`. Its configuration is read only when the notch is
first requested.

Two questions stay apart. `EmbodimentIntent` — what MIRA is doing and
expressing — comes from the activity authority through `state_changed`.
What the notch presents — how much of it is visible, and what it shows when
open — is decided by one `NotchPresenter`, which the shell owns and feeds:
the committed activity, hover reported by the notch, and transient notices
(the bus event ``"notice"``, emitted by the producers in `mira.core`).
Activity alone never expands the notch; the presenter's expansion reasons do
(see `mira.ui.notch.presentation`). The presenter's notice timer runs on the
application's own `Scheduler`, so the notch adds no clock of its own.

Plain on purpose: no registry, no singleton. Constructing a shell builds its
initial surface and subscribes to the bus; `start` shows it and starts the
clock, `shutdown` stops the clock and closes every surface.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mira.application.composition import Application
from mira.application.voice import configure_listening, wake_word_available
from mira.domain.device_info import DeviceInfoUpdate, device_info_notice
from mira.domain.embodiment import ActivityState
from mira.domain.listening import ActivationSource
from mira.domain.notice import Notice
from mira.domain.timer import TimerSnapshot
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.listening_keys import KeyboardActivation
from mira.ui.listening_settings import ListeningSession, ListeningSettings
from mira.ui.face.face_controller import FaceController
from mira.ui.main_window import MainWindow
from mira.ui.notch.config_store import ConfigSession, NotchSettings
from mira.ui.notch.debug_window import NotchDebugWindow
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import NotchAction, NotchPresenter, TimerButton, TimerCommand
from mira.ui.notch.startup import bind_presenter, build_notch, deliver_notice, deliver_tasks, deliver_timer


class Surface(StrEnum):
    """A top-level surface presenting MIRA; launchers request them by name."""

    MAIN_WINDOW = "main window"
    NOTCH = "notch"


# The intents the notch offers; the shell honours each of them.
NOTCH_ACTIONS = frozenset(
    {NotchAction.OPEN_MAIN_WINDOW, NotchAction.OPEN_SETTINGS, NotchAction.QUIT, NotchAction.TIMER}
)


class DesktopShell:
    """Owns the shared embodiment and the top-level surfaces presenting it.

    ``initial_surface`` is built here and shown by `start`; any other surface
    is built by the first `show_surface` that asks for it. Until then
    `window`, or `notch` and `presenter`, stay None.

    The notch's settings come from ``notch_settings``/``notch_session`` when
    given, otherwise from ``load_notch_session`` — called once, when the notch
    is first built (`mira.main` reads the configuration file there); with
    none of them the notch uses the production defaults, so a shell never
    reads a file it was not told to. `notch_session` is the file the notch
    settings window saves to and reloads from. `controller` is an injection
    seam for tests (a seeded `MicroMotions`, say). `quit` is what the
    notch's Quit entry calls, by default `QApplication.quit`.

    ``listening_settings`` turns on keyboard activation of listening
    (`mira.ui.listening_keys`): the in-window key and the global shortcut
    only *ask* for a toggle on the bus. Without it no key is watched, which is
    what tests want.
    """

    def __init__(
        self,
        application: Application,
        *,
        initial_surface: Surface = Surface.MAIN_WINDOW,
        notch_settings: NotchSettings | None = None,
        notch_session: ConfigSession | None = None,
        load_notch_session: Callable[[Path | None], ConfigSession] | None = None,
        controller: FaceController | None = None,
        quit: Callable[[], None] | None = None,
        listening_settings: ListeningSettings | None = None,
        listening_session: ListeningSession | None = None,
    ):
        # Held only to build the main window on demand; nothing else here
        # reaches into the graph.
        self._application = application
        self.initial_surface = initial_surface
        self.controller = FaceController() if controller is None else controller
        # Unparented and held here: the clock lives exactly as long as the
        # shell, never as long as whichever surface happens to be built first.
        self.animator = FaceAnimator(self.controller)
        self._quit = QApplication.quit if quit is None else quit
        self._notch_settings = notch_settings
        self.notch_session = notch_session
        self._load_notch_session = load_notch_session

        # Subscribed before any surface exists, so the embodiment follows the
        # application state whichever surfaces are built below.
        application.event_bus.subscribe("state_changed", self.on_state_changed)
        # Every notice, from whichever producer (`mira.core.notice_producers`).
        application.event_bus.subscribe("notice", self.on_notice)
        # What providers learned about a device (`mira.core.device_info`).
        application.event_bus.subscribe("device_info", self.on_device_info)
        # Running work (`mira.core.task_activity`): persistent, not a notice.
        application.event_bus.subscribe("tasks_changed", self.on_tasks_changed)
        # Voice listening (`mira.core.listening`): the notch opens and shows
        # the real waveform; the eyes show LISTENING through the activity.
        application.event_bus.subscribe("listening_changed", self.on_listening_changed)
        # The countdown (`mira.core.countdown_timer`): its state changes; the
        # time left is pulled by the notch. FINISHED lasts until the user's OK.
        application.event_bus.subscribe("timer_changed", self.on_timer_changed)

        self.window: MainWindow | None = None
        self.notch: NotchWindow | None = None
        self.presenter: NotchPresenter | None = None
        # Built on first request; one window however often it is asked for.
        self.settings_window: NotchDebugWindow | None = None
        self._activity = ActivityState.IDLE
        self._tasks_running = False
        self._listening = (False, False)
        self._timer = application.timer.snapshot
        self._stopped = False
        self.listening_session = listening_session
        self.keyboard: KeyboardActivation | None = None
        if listening_settings is not None:
            self.keyboard = self._keyboard_for(listening_settings)

        self._ensure(initial_surface)

    # --- Surfaces -------------------------------------------------------------------

    def _ensure(self, surface: Surface, notch_config: Path | None = None):
        if surface is Surface.MAIN_WINDOW:
            return self.ensure_main_window()
        return self.ensure_notch(notch_config)

    def ensure_main_window(self) -> MainWindow:
        """The one main window, built on first use; never shown by this."""
        if self.window is None:
            # The one construction site: the same graph and the same animator
            # as every other surface, never a second runtime.
            self.window = MainWindow(self._application, self.animator)
            print("[mira] main window created")
        return self.window

    def ensure_notch(self, notch_config: Path | None = None) -> NotchWindow:
        """The one notch, built (and its configuration read) on first use.

        ``notch_config`` names the configuration file to read when the notch
        is built now; once a notch exists it is ignored (and reported).
        """
        if self.notch is not None:
            if notch_config is not None and (self.notch_session is None or self.notch_session.path != notch_config):
                print(f"[mira] notch already running; ignoring requested configuration {notch_config}")
            return self.notch
        if self._notch_settings is None and self.notch_session is None and self._load_notch_session is not None:
            self.notch_session = self._load_notch_session(notch_config)
        if self._notch_settings is None:
            self._notch_settings = (
                self.notch_session.startup_settings if self.notch_session is not None else NotchSettings()
            )
        settings = self._notch_settings
        self.notch = build_notch(settings, animator=self.animator)
        self.presenter = NotchPresenter(self._application.scheduler, settings.behaviour, actions=NOTCH_ACTIONS)
        self.presenter.set_activity(self._activity)
        self.presenter.set_tasks_running(self._tasks_running)
        self.presenter.set_listening(*self._listening)
        self.presenter.set_timer(self._timer)
        # Real captured audio, pulled by the notch on its own repaints.
        audio = self._application.audio
        self.notch.set_waveform_source(lambda: audio.waveform)
        # Likewise the countdown's time left.
        timer = self._application.timer
        self.notch.set_timer_source(lambda: timer.snapshot)
        bind_presenter(self.notch, self.presenter, self.controller)
        self.notch.action_requested.connect(self.on_notch_action)
        self.notch.timer_requested.connect(self.on_timer_requested)
        # From now on the notch keeps the runtime alive: closing the main
        # window only hides it.
        application = QApplication.instance()
        if application is not None:
            application.setQuitOnLastWindowClosed(False)
        print("[mira] notch created")
        return self.notch

    def show_surface(self, surface: Surface, *, notch_config: Path | None = None):
        """Build ``surface`` if needed and bring it forward; the one entry point.

        Ignored once the shell has shut down, so a request arriving during
        teardown builds nothing.
        """
        if self._stopped:
            return None
        built = self.notch is None if surface is Surface.NOTCH else self.window is None
        widget = self._ensure(surface, notch_config)
        if not built:
            print(f"[mira] {surface.value} reused")
        if surface is Surface.MAIN_WINDOW:
            _bring_forward(widget)
        else:
            self.presenter.show()
        return widget

    @property
    def surfaces(self) -> frozenset[Surface]:
        """The surfaces built so far."""
        built = set()
        if self.window is not None:
            built.add(Surface.MAIN_WINDOW)
        if self.notch is not None:
            built.add(Surface.NOTCH)
        return frozenset(built)

    def start(self) -> None:
        """Show the initial surface, then start the one clock that steps them."""
        if self.initial_surface is Surface.MAIN_WINDOW:
            self.window.show()
        else:
            self.presenter.show()
        if self.keyboard is not None:
            self.keyboard.start()
        self.animator.start()

    def shutdown(self) -> None:
        """Stop the clock first, so no frame reaches a closing surface.

        The notch is hidden without animating — nothing would drive the
        transition once the event loop ends — and closed with the window, so
        neither outlives the application. The presenter stops first: its
        notice timer is cancelled while the scheduler still accepts it, and it
        presents nothing more. Idempotent.
        """
        self._stopped = True
        if self.keyboard is not None:
            self.keyboard.stop()
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
        """The notch shows it; MIRA's eyes react, on every surface."""
        deliver_notice(notice, presenter=self.presenter, controller=self.controller)

    def on_device_info(self, update: DeviceInfoUpdate) -> None:
        """The notch's device notice grows; the eyes already reacted to the arrival."""
        if self.presenter is not None:
            self.presenter.refine(device_info_notice(update), announce=update.announce)

    # --- Notch actions ----------------------------------------------------------

    def on_listening_changed(self, status) -> None:
        self._listening = (bool(getattr(status, "listening", False)), bool(getattr(status, "capturing", False)))
        if self.presenter is not None:
            self.presenter.set_listening(*self._listening)

    def _keyboard_for(self, settings: ListeningSettings) -> KeyboardActivation:
        return KeyboardActivation(
            self.request_listening_toggle,
            in_window_key=settings.in_window_key,
            global_shortcut=settings.global_shortcut,
        )

    def apply_listening_settings(self, settings: ListeningSettings) -> None:
        """New settings take effect at once: keys re-registered (the old ones
        let go first), the activation mode, wake word and timing applied to
        the runtime's listening controller."""
        if self.keyboard is not None:
            self.keyboard.stop()
        self.keyboard = self._keyboard_for(settings)
        if not self._stopped:
            self.keyboard.start()
            configure_listening(self._application.listening, settings.activation, settings.wake_word, settings.policy)

    def request_listening_toggle(self) -> None:
        """A listening key was pressed: ask; the listening controller decides."""
        if not self._stopped:
            self._application.event_bus.emit("listening_toggle_requested", ActivationSource.KEYBOARD)

    def on_tasks_changed(self, running) -> None:
        """MIRA works while any task runs, on every surface showing it."""
        self._tasks_running = bool(getattr(running, "active", False))
        deliver_tasks(self._tasks_running, presenter=self.presenter, controller=self.controller)

    def on_timer_changed(self, timer: TimerSnapshot) -> None:
        """A finished countdown waits, on every surface, until the timer is acknowledged."""
        self._timer = timer
        deliver_timer(timer, presenter=self.presenter, controller=self.controller)

    def on_timer_requested(self, button: TimerButton) -> None:
        """A timer panel button: the countdown's commands go to the application's timer."""
        timer = self._application.timer
        command = button.command
        if command is TimerCommand.START:
            if self.presenter is not None:
                timer.start(self.presenter.timer_selection_s)
        elif command is TimerCommand.PAUSE:
            timer.pause()
        elif command is TimerCommand.RESUME:
            timer.resume()
        elif command is TimerCommand.RESET:
            timer.reset()
        elif command is TimerCommand.ACKNOWLEDGE:
            timer.acknowledge()
        elif self.presenter is not None:
            self.presenter.choose_timer(button)

    def on_notch_action(self, action: str) -> None:
        # A used control has done its job: the controls close.
        if self.presenter is not None and action in (NotchAction.OPEN_SETTINGS, NotchAction.QUIT):
            self.presenter.close_controls()
        if action == NotchAction.TIMER:
            if self.presenter is not None:
                self.presenter.open_timer_panel()
        elif action == NotchAction.OPEN_MAIN_WINDOW:
            self.open_main_window()
        elif action == NotchAction.OPEN_SETTINGS:
            self.open_settings()
        elif action == NotchAction.QUIT:
            self._quit()

    def open_main_window(self) -> MainWindow:
        """`show_surface(MAIN_WINDOW)`: what the notch's double click asks for."""
        return self.show_surface(Surface.MAIN_WINDOW)

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
                self.notch, None, self.notch_session, self.presenter, previews=False,
                audio=self._application.audio, listening_session=self.listening_session,
                apply_listening=self.apply_listening_settings,
                listening=self._application.listening, wake_available=wake_word_available()[0],
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
        # Kept for a notch built later, so it starts with the current activity.
        self._activity = activity
        if self.presenter is not None:
            self.presenter.set_activity(activity)


def _bring_forward(window) -> None:
    if window.windowState() & Qt.WindowMinimized:
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
