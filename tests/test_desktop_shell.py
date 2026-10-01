"""The desktop shell: one embodiment, owned once, shown by every surface.

Built in-process on the offscreen platform with `ManualScheduler` and no
camera, so nothing here needs a display, a timer loop or a webcam. State
changes are driven through the real bus and the real activity authority, never
by calling the controller, so the path under test is the production one.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject

from mira.application.composition import build_application
from mira.domain.embodiment import ActivityState
from mira.domain.micro_motion import MicroMotions
from mira.domain.scheduler import ManualScheduler
from mira.ui.desktop_shell import DesktopShell
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.face.face_widget import FaceWidget


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class CountingController(FaceController):
    def __init__(self):
        super().__init__(MicroMotions(random.Random(5)))
        self.updates = 0

    def update(self, dt_seconds: float):
        self.updates += 1
        super().update(dt_seconds)


class PaintCounter(QObject):
    """Counts paint events delivered to the widget it filters."""

    def __init__(self, widget):
        super().__init__()
        self.paints = 0
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Paint:
            self.paints += 1
        return False


@pytest.fixture
def composed(qt_application):
    """A production-shaped application and shell, torn down in owner order."""
    built: list[tuple] = []

    def compose(**shell_options):
        application = build_application(scheduler=ManualScheduler())
        desktop = DesktopShell(application, **shell_options)
        built.append((desktop, application))
        return desktop, application

    yield compose
    for desktop, application in built:
        desktop.shutdown()
        application.shutdown()


# --- One owner ---------------------------------------------------------------


def test_the_window_shows_the_shells_embodiment(composed):
    desktop, _ = composed()

    face = desktop.window.face_widget
    assert face.animator is desktop.animator
    assert face.controller is desktop.controller
    assert desktop.animator.controller is desktop.controller


def test_composition_creates_exactly_one_controller_and_one_clock(composed, monkeypatch):
    created: list[str] = []
    original_controller = FaceController.__init__
    original_animator = FaceAnimator.__init__

    def counting_controller(self, *args, **kwargs):
        created.append("controller")
        original_controller(self, *args, **kwargs)

    def counting_animator(self, *args, **kwargs):
        created.append("animator")
        original_animator(self, *args, **kwargs)

    monkeypatch.setattr(FaceController, "__init__", counting_controller)
    monkeypatch.setattr(FaceAnimator, "__init__", counting_animator)

    composed()

    assert created == ["controller", "animator"]


def test_one_tick_advances_the_embodiment_once_and_repaints_the_window(composed):
    controller = CountingController()
    desktop, _ = composed(controller=controller)
    face = desktop.window.face_widget
    desktop.window.show()
    QCoreApplication.processEvents()
    counter = PaintCounter(face)

    desktop.animator.advance()
    QCoreApplication.processEvents()

    assert controller.updates == 1
    assert counter.paints >= 1


# --- One state authority -------------------------------------------------------


def test_committed_state_changes_reach_the_shared_embodiment(composed):
    desktop, application = composed()

    application.event_bus.emit("input_focused")
    assert desktop.controller.intent.activity is ActivityState.LISTENING
    assert desktop.window.face_widget.controller.intent.activity is ActivityState.LISTENING

    application.event_bus.emit("input_unfocused")
    assert desktop.controller.intent.activity is ActivityState.IDLE


def test_the_window_no_longer_presents_state_itself(composed):
    _, application = composed()

    subscribers = application.event_bus._subscribers["state_changed"]
    assert [type(callback.__self__).__name__ for callback in subscribers] == ["DesktopShell"]


# --- Lifetime ------------------------------------------------------------------


def test_building_starts_nothing_and_start_shows_and_runs_the_clock(composed):
    desktop, _ = composed()
    assert not desktop.animator.is_running()
    assert not desktop.window.isVisible()

    desktop.start()

    assert desktop.window.isVisible()
    assert desktop.animator.is_running()


def test_shutdown_stops_the_clock_and_closes_the_window_idempotently(composed):
    desktop, _ = composed()
    desktop.start()

    desktop.shutdown()
    desktop.shutdown()

    assert not desktop.animator.is_running()
    assert not desktop.window.isVisible()


# --- Standalone face -----------------------------------------------------------


def test_a_standalone_face_still_owns_and_runs_its_own_clock(qt_application):
    face = FaceWidget()
    try:
        assert face.animator.parent() is face
        assert face.animator.is_running()
    finally:
        face.animator.stop()
        face.close()
