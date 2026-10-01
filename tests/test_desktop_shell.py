"""The desktop shell: one embodiment, owned once, shown by every surface.

The surfaces are `MainWindow` and `NotchWindow`; the notch's presence is the
shell's own policy and stays apart from the activity it shows.

Built in-process on the offscreen platform with `ManualScheduler` and no
camera, so nothing here needs a display, a timer loop or a webcam. State
changes are driven through the real bus and the real activity authority, never
by calling the controller, so the path under test is the production one.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject

from mira.application.composition import build_application
from mira.domain.embodiment import ActivityState
from mira.domain.micro_motion import MicroMotions
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.desktop_shell import DesktopShell
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.face.face_widget import FaceWidget
from mira.ui.notch.config_store import NotchSettings
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.screen_selection import MonitorIdentity, MonitorPreference
from mira.ui.notch.visual_config import NotchVisualConfig


TUNED = NotchSettings(
    visual=dataclasses.replace(NotchVisualConfig(), transition_duration_ms=350),
    monitor=MonitorPreference(MonitorIdentity(name="HDMI-9", model="Tuned")),
)


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
        # Closed is not destroyed: a live notch stays connected to the
        # application's screen signals and would answer later tests' screen
        # changes. Production builds one shell per process; tests build many.
        for obj in (desktop.notch, desktop.window, desktop.animator):
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- One owner ---------------------------------------------------------------


def test_the_window_and_the_notch_show_the_shells_one_embodiment(composed):
    desktop, _ = composed()

    face = desktop.window.face_widget
    assert face.animator is desktop.animator
    assert face.controller is desktop.controller
    assert desktop.notch.animator is desktop.animator
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


def test_one_tick_advances_the_embodiment_once_and_repaints_both_surfaces(composed):
    controller = CountingController()
    desktop, _ = composed(controller=controller)
    desktop.window.show()
    desktop.notch.set_presence(NotchPresence.COMPACT, animated=False)
    QCoreApplication.processEvents()
    face_paints = PaintCounter(desktop.window.face_widget)
    notch_paints = PaintCounter(desktop.notch)

    desktop.animator.advance()
    QCoreApplication.processEvents()

    assert controller.updates == 1
    assert face_paints.paints >= 1
    assert notch_paints.paints >= 1


def test_ticks_advance_once_each_however_many_surfaces_show_them(composed):
    controller = CountingController()
    desktop, _ = composed(controller=controller)
    desktop.window.show()
    desktop.notch.set_presence(NotchPresence.COMPACT, animated=False)

    for _ in range(20):
        desktop.animator.advance()
        QCoreApplication.processEvents()

    assert controller.updates == 20


def test_both_surfaces_paint_the_same_frame(composed):
    desktop, _ = composed(controller=CountingController())
    for _ in range(15):
        desktop.animator.advance()

    assert desktop.window.face_widget.controller.get_frame() == desktop.notch.animator.controller.get_frame()


# --- One state authority -------------------------------------------------------


def test_committed_state_changes_reach_both_surfaces(composed):
    desktop, application = composed()

    application.event_bus.emit("input_focused")
    assert desktop.window.face_widget.controller.intent.activity is ActivityState.LISTENING
    assert desktop.notch.animator.controller.intent.activity is ActivityState.LISTENING

    application.event_bus.emit("input_unfocused")
    assert desktop.controller.intent.activity is ActivityState.IDLE


def test_the_window_no_longer_presents_state_itself(composed):
    _, application = composed()

    subscribers = application.event_bus._subscribers["state_changed"]
    assert [type(callback.__self__).__name__ for callback in subscribers] == ["DesktopShell"]


# --- Presence policy -------------------------------------------------------------


def test_the_notch_starts_compact(composed):
    desktop, _ = composed()
    assert desktop.notch.presence is NotchPresence.HIDDEN

    desktop.start()

    assert desktop.notch.presence is NotchPresence.COMPACT
    assert desktop.notch.isVisible()


def test_activity_never_changes_the_notch_presence(composed):
    desktop, application = composed()
    desktop.start()

    activities = []
    for event in ("input_focused", "input_unfocused"):
        application.event_bus.emit(event)
        activities.append(desktop.controller.intent.activity)
        assert desktop.notch.presence is NotchPresence.COMPACT, event

    # The activity did change underneath; only presence stayed put.
    assert activities == [ActivityState.LISTENING, ActivityState.IDLE]


def test_the_notch_presenter_follows_the_committed_activity(composed):
    desktop, application = composed()
    desktop.start()

    application.event_bus.emit("input_focused")
    assert desktop.presenter.presentation.activity is ActivityState.LISTENING
    assert desktop.notch.presentation.activity is ActivityState.LISTENING

    application.event_bus.emit("input_unfocused")
    assert desktop.notch.presentation.activity is ActivityState.IDLE


def test_hover_expands_the_running_notch_and_leaving_collapses_it(composed):
    desktop, _ = composed()
    desktop.start()

    desktop.notch.hover_changed.emit(True)
    assert desktop.notch.presence is NotchPresence.EXPANDED
    desktop.notch.hover_changed.emit(False)
    assert desktop.notch.presence is NotchPresence.COMPACT


def test_notices_expire_on_the_applications_scheduler(composed):
    desktop, application = composed()
    desktop.start()

    desktop.presenter.present(Notice(NoticeCategory.ALERT, NoticeTone.WARNING))
    assert desktop.notch.presence is NotchPresence.EXPANDED
    assert application.scheduler.pending_timers() == 1

    application.scheduler.advance(desktop.presenter.behaviour.notice_duration_ms)
    assert desktop.notch.presence is NotchPresence.COMPACT


def test_shutdown_cancels_a_showing_notice_before_the_scheduler_stops(composed):
    desktop, application = composed()
    desktop.start()
    desktop.presenter.present(Notice(NoticeCategory.ALERT, NoticeTone.WARNING))

    desktop.shutdown()

    assert application.scheduler.pending_timers() == 0
    assert desktop.notch.presence is NotchPresence.HIDDEN


# --- Notch configuration ----------------------------------------------------------


def test_the_notch_uses_the_given_settings(composed):
    desktop, _ = composed(notch_settings=TUNED)

    assert desktop.notch.visual_config == TUNED.visual
    assert desktop.notch.monitor_preference == TUNED.monitor


def test_without_settings_the_notch_uses_the_production_defaults(composed):
    desktop, _ = composed()

    assert desktop.notch.visual_config == NotchSettings().visual
    assert desktop.notch.monitor_preference == NotchSettings().monitor


def test_settings_the_embodiment_rejects_fall_back_to_the_defaults(composed, capsys):
    oversized_eyes = NotchSettings(
        visual=dataclasses.replace(NotchVisualConfig(), eye_style=NotchEyeStyle(height_share=3.0)),
        monitor=TUNED.monitor,
    )

    desktop, _ = composed(notch_settings=oversized_eyes)

    assert desktop.notch.visual_config == NotchSettings().visual
    # Only the rejected visual part falls back; the monitor choice is kept.
    assert desktop.notch.monitor_preference == TUNED.monitor
    assert "saved visual configuration not applied" in capsys.readouterr().err


# --- Lifetime ------------------------------------------------------------------


def test_building_starts_nothing_and_start_shows_and_runs_the_clock(composed):
    desktop, _ = composed()
    assert not desktop.animator.is_running()
    assert not desktop.window.isVisible()
    assert not desktop.notch.isVisible()

    desktop.start()

    assert desktop.window.isVisible()
    assert desktop.notch.isVisible()
    assert desktop.animator.is_running()


def test_shutdown_stops_the_clock_and_closes_every_surface_idempotently(composed):
    desktop, _ = composed()
    desktop.start()

    desktop.shutdown()
    desktop.shutdown()

    assert not desktop.animator.is_running()
    assert not desktop.window.isVisible()
    assert not desktop.notch.isVisible()
    assert desktop.notch.presence is NotchPresence.HIDDEN


def test_shutdown_mid_transition_leaves_no_notch_behind(composed):
    desktop, _ = composed()
    desktop.start()
    assert desktop.notch.transition.state() is desktop.notch.transition.State.Running

    desktop.shutdown()

    assert desktop.notch.transition.state() is desktop.notch.transition.State.Stopped
    assert not desktop.notch.isVisible()


# --- Standalone face -----------------------------------------------------------


def test_a_standalone_face_still_owns_and_runs_its_own_clock(qt_application):
    face = FaceWidget()
    try:
        assert face.animator.parent() is face
        assert face.animator.is_running()
    finally:
        face.animator.stop()
        face.close()
