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

Plain on purpose: no registry, no singleton. Constructing a shell has no side
effects beyond subscribing to the bus; `start` shows and starts, `shutdown`
stops the clock and closes the surfaces.
"""

from __future__ import annotations

from mira.application.composition import Application
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.main_window import MainWindow


class DesktopShell:
    """Owns the shared embodiment and the top-level surfaces presenting it.

    `controller` is an injection seam for tests (a seeded `MicroMotions`, say);
    production passes nothing and gets the packaged expression library.
    """

    def __init__(self, application: Application, *, controller: FaceController | None = None):
        self.controller = FaceController() if controller is None else controller
        # Unparented and held here: the clock lives exactly as long as the
        # shell, never as long as whichever surface happens to be built first.
        self.animator = FaceAnimator(self.controller)

        # Subscribed before any surface exists, so the embodiment follows the
        # application state whichever surfaces are built below.
        application.event_bus.subscribe("state_changed", self.on_state_changed)

        self.window = MainWindow(application, self.animator)

    def start(self) -> None:
        """Show the surfaces, then start the one clock that steps them."""
        self.window.show()
        self.animator.start()

    def shutdown(self) -> None:
        """Stop the clock first, so no frame reaches a closing surface.

        Idempotent. Closing an already closed window does nothing.
        """
        self.animator.stop()
        self.window.close()

    def on_state_changed(self, payload) -> None:
        """Present a committed state change on the shared embodiment."""
        new_state = payload["new_state"]
        embodiment = payload.get("embodiment")

        print(f"[STATE] {new_state}")

        if embodiment is None:
            self.controller.set_state(new_state)
        else:
            self.controller.set_intent(embodiment)
