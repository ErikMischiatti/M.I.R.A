"""The desktop shell: one embodiment, owned once, shown by every surface.

The surfaces are `MainWindow` and `NotchWindow`; the notch's presence is the
shell's own policy and stays apart from the activity it shows. `MIRA` builds
the shell in MAIN_WINDOW mode (no notch), `MIRA-NOTCH` in NOTCH mode (the
notch, and the main window only once asked for); tests that need both open
the window from a NOTCH shell, as a double click does.

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
from mira.ui.desktop_shell import DesktopShell, Surface
from mira.ui.main_window import MainWindow
from mira.ui.notch.notch_window import NotchWindow
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

    def compose(mode=Surface.NOTCH, **shell_options):
        application = build_application(scheduler=ManualScheduler())
        desktop = DesktopShell(application, initial_surface=mode, **shell_options)
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
            if obj is not None:
                obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- One owner ---------------------------------------------------------------


def test_the_window_and_the_notch_show_the_shells_one_embodiment(composed):
    desktop, _ = composed()
    desktop.open_main_window()

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
    desktop.open_main_window()
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
    desktop.open_main_window()
    desktop.notch.set_presence(NotchPresence.COMPACT, animated=False)

    for _ in range(20):
        desktop.animator.advance()
        QCoreApplication.processEvents()

    assert controller.updates == 20


def test_both_surfaces_paint_the_same_frame(composed):
    desktop, _ = composed(controller=CountingController())
    desktop.open_main_window()
    for _ in range(15):
        desktop.animator.advance()

    assert desktop.window.face_widget.controller.get_frame() == desktop.notch.animator.controller.get_frame()


# --- One state authority -------------------------------------------------------


def test_committed_state_changes_reach_both_surfaces(composed):
    desktop, application = composed()
    desktop.open_main_window()

    application.event_bus.emit("input_focused")
    assert desktop.window.face_widget.controller.intent.activity is ActivityState.LISTENING
    assert desktop.notch.animator.controller.intent.activity is ActivityState.LISTENING

    application.event_bus.emit("input_unfocused")
    assert desktop.controller.intent.activity is ActivityState.IDLE


@pytest.mark.parametrize("mode", list(Surface))
def test_the_window_no_longer_presents_state_itself(composed, mode):
    desktop, application = composed(mode)
    desktop.open_main_window()

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
    assert not desktop.notch.isVisible()

    desktop.start()

    assert desktop.notch.isVisible()
    assert desktop.animator.is_running()


def test_shutdown_stops_the_clock_and_closes_every_surface_idempotently(composed):
    desktop, _ = composed()
    desktop.start()
    desktop.open_main_window()

    desktop.shutdown()
    desktop.shutdown()

    assert not desktop.animator.is_running()
    assert not desktop.window.isVisible()
    assert not desktop.notch.isVisible()
    assert desktop.notch.presence is NotchPresence.HIDDEN


def test_a_main_window_shell_shuts_down_without_a_notch(composed):
    desktop, _ = composed(Surface.MAIN_WINDOW)
    desktop.start()

    desktop.shutdown()
    desktop.shutdown()

    assert not desktop.animator.is_running()
    assert not desktop.window.isVisible()


def test_shutdown_mid_transition_leaves_no_notch_behind(composed):
    desktop, _ = composed()
    desktop.start()
    assert desktop.notch.transition.state() is desktop.notch.transition.State.Running

    desktop.shutdown()

    assert desktop.notch.transition.state() is desktop.notch.transition.State.Stopped
    assert not desktop.notch.isVisible()


# --- Surfaces per launcher ---------------------------------------------------------


def visible_top_levels(kind) -> list:
    from PySide6.QtWidgets import QApplication

    return [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, kind) and widget.isVisible()]


def test_mira_shows_the_main_window_and_builds_no_notch(composed):
    desktop, application = composed(Surface.MAIN_WINDOW)
    desktop.start()

    assert desktop.window.isVisible()
    assert desktop.notch is None and desktop.presenter is None
    assert visible_top_levels(NotchWindow) == []
    assert visible_top_levels(MainWindow) == [desktop.window]


def test_mira_still_presents_state_without_a_notch(composed):
    desktop, application = composed(Surface.MAIN_WINDOW)
    desktop.start()

    application.event_bus.emit("input_focused")

    assert desktop.window.face_widget.controller.intent.activity is ActivityState.LISTENING


def test_mira_notch_shows_only_the_notch_and_builds_no_main_window(composed, monkeypatch):
    built = []
    original = MainWindow.__init__
    monkeypatch.setattr(MainWindow, "__init__", lambda self, *a, **k: (built.append(self), original(self, *a, **k))[1])

    desktop, _ = composed(Surface.NOTCH)
    desktop.start()

    assert desktop.notch.isVisible()
    assert desktop.window is None and built == []
    assert visible_top_levels(MainWindow) == []


def test_the_main_window_opened_from_the_notch_belongs_to_the_same_runtime(composed, monkeypatch):
    built_controllers = []
    original_controller = FaceController.__init__
    monkeypatch.setattr(
        FaceController, "__init__",
        lambda self, *a, **k: (built_controllers.append(self), original_controller(self, *a, **k))[1],
    )
    desktop, application = composed(Surface.NOTCH)
    desktop.start()

    window = desktop.open_main_window()

    assert window is desktop.window and window.isVisible()
    assert window.event_bus is application.event_bus
    assert window.brain is application.brain
    assert window.face_widget.animator is desktop.animator
    assert window.face_widget.controller is desktop.controller
    assert built_controllers == [desktop.controller]


def test_opening_the_main_window_again_never_builds_another(composed, monkeypatch):
    built = []
    original = MainWindow.__init__
    monkeypatch.setattr(MainWindow, "__init__", lambda self, *a, **k: (built.append(self), original(self, *a, **k))[1])
    desktop, _ = composed(Surface.NOTCH)
    desktop.start()

    first = desktop.open_main_window()
    first.close()
    again = [desktop.open_main_window() for _ in range(3)]

    assert built == [first]
    assert all(window is first for window in again) and first.isVisible()
    assert visible_top_levels(MainWindow) == [first]


def test_a_closed_main_window_leaves_the_notch_running(composed):
    desktop, _ = composed(Surface.NOTCH)
    desktop.start()
    desktop.open_main_window().close()

    assert desktop.notch.isVisible()
    assert desktop.animator.is_running()


# --- Surface requests (one runtime) -----------------------------------------------------


def test_a_main_window_runtime_builds_the_notch_only_when_asked(composed, tmp_path):
    from mira.ui.notch.config_store import ConfigSession

    loaded = []

    def load(path):
        loaded.append(path)
        return ConfigSession(path or tmp_path / "default.json")

    desktop, _ = composed(Surface.MAIN_WINDOW, load_notch_session=load)
    desktop.start()
    assert desktop.notch is None and loaded == []  # MIRA alone reads no notch configuration

    requested = tmp_path / "requested.json"
    notch = desktop.show_surface(Surface.NOTCH, notch_config=requested)

    assert loaded == [requested] and desktop.notch_session.path == requested
    assert notch is desktop.notch and notch.isVisible()
    assert desktop.show_surface(Surface.NOTCH) is notch and loaded == [requested]
    assert desktop.surfaces == {Surface.MAIN_WINDOW, Surface.NOTCH}


def test_a_notch_built_later_shares_the_embodiment_and_the_current_activity(composed):
    desktop, application = composed(Surface.MAIN_WINDOW)
    desktop.start()
    application.event_bus.emit("input_focused")

    desktop.show_surface(Surface.NOTCH)

    assert desktop.notch.animator is desktop.animator is desktop.window.face_widget.animator
    assert desktop.presenter.presentation.activity is ActivityState.LISTENING


def test_closing_the_main_window_ends_the_runtime_only_while_no_notch_exists(composed, qt_application):
    qt_application.setQuitOnLastWindowClosed(True)
    desktop, _ = composed(Surface.MAIN_WINDOW)
    assert qt_application.quitOnLastWindowClosed()

    desktop.show_surface(Surface.NOTCH)

    assert not qt_application.quitOnLastWindowClosed()
    qt_application.setQuitOnLastWindowClosed(True)


def test_surface_requests_never_create_another_producer(qt_application):
    class Source:
        name = "fake"
        starts = 0

        def start(self, listener):
            Source.starts += 1
            return True

        def shutdown(self):
            pass

    application = build_application(scheduler=ManualScheduler(), system_event_sources=[Source()])
    desktop = DesktopShell(application, initial_surface=Surface.MAIN_WINDOW)
    try:
        application.start()
        desktop.start()
        for surface in (Surface.MAIN_WINDOW, Surface.NOTCH, Surface.MAIN_WINDOW, Surface.NOTCH):
            desktop.show_surface(surface)

        assert Source.starts == 1
        assert len(application.system_notices.active_sources) == 1
        assert len(application.event_bus._subscribers["action_completed"]) == 2  # producer + the one window
    finally:
        desktop.shutdown()
        application.shutdown()
        for obj in (desktop.notch, desktop.window, desktop.animator):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_request_during_shutdown_builds_nothing(composed):
    desktop, _ = composed(Surface.MAIN_WINDOW)
    desktop.shutdown()

    assert desktop.show_surface(Surface.NOTCH) is None
    assert desktop.notch is None


# --- Notices from the bus ---------------------------------------------------------------


def test_a_notice_on_the_bus_reaches_the_notch_presenter(composed):
    desktop, application = composed(Surface.NOTCH)
    desktop.start()
    notice = Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Keyboard connected")

    application.event_bus.emit("notice", notice)

    assert desktop.presenter.presentation.notice == notice
    assert desktop.notch.presence is NotchPresence.EXPANDED  # device changes expand by default


def test_a_main_window_shell_ignores_notices(composed):
    desktop, application = composed(Surface.MAIN_WINDOW)
    desktop.start()

    application.event_bus.emit("notice", Notice(NoticeCategory.ALERT, NoticeTone.ERROR))

    assert desktop.notch is None


# --- Standalone face -----------------------------------------------------------


def test_a_standalone_face_still_owns_and_runs_its_own_clock(qt_application):
    face = FaceWidget()
    try:
        assert face.animator.parent() is face
        assert face.animator.is_running()
    finally:
        face.animator.stop()
        face.close()
