"""Notch interaction: user intents leave the notch as actions the shell honours.

The notch never holds the main window. A double click, or a click on one of
the notch's integrated controls (opened by a right click; no native menu),
emits a `NotchAction`; `DesktopShell` (in NOTCH mode, as `MIRA-NOTCH`
runs it) builds the one window it owns on first request and afterwards shows,
raises and activates that same window. Offscreen, so stacking and real activation are left to
the manual smoke test; what is checked is that the right calls happen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mira.actions.action_models import ActionResult
from mira.application.composition import build_application
from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui import main_window as main_window_module
from mira.core.notice_producers import task_notice
from mira.ui.desktop_shell import NOTCH_ACTIONS, DesktopShell, Surface
from mira.ui.notch.config_store import ConfigSession
from mira.ui.notch.notch_window import ACTION_LABELS, NotchWindow
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import CONTROLS_LINGER_MS, ExpansionReason, NotchAction, NotchPresenter
from mira.ui.notch.startup import bind_presenter


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def desktop(qt_application, tmp_path):
    application = build_application(scheduler=ManualScheduler())
    quits = []
    shell = DesktopShell(
        application, initial_surface=Surface.NOTCH, notch_session=ConfigSession(tmp_path / "notch.json"),
        quit=lambda: quits.append(True),
    )
    shell.quits = quits  # for the tests' convenience only
    shell.start()
    shell.notch.transition.setCurrentTime(shell.notch.transition.duration())
    shell.application = application  # for the tests' convenience only
    yield shell
    shell.shutdown()
    application.shutdown()
    for obj in (shell.notch, shell.window, shell.animator, shell.settings_window):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def double_click(widget) -> None:
    QTest.mouseDClick(widget, Qt.LeftButton, Qt.NoModifier, QPoint(widget.width() // 2, 6))


def finish(notch: NotchWindow) -> None:
    transition = notch.transition
    if transition.state() is not transition.State.Stopped:
        transition.setCurrentTime(transition.duration())


def right_click(notch: NotchWindow) -> None:
    point = QPoint(notch.width() // 2, 6)
    QCoreApplication.sendEvent(notch, QContextMenuEvent(QContextMenuEvent.Mouse, point, notch.mapToGlobal(point)))
    finish(notch)


def open_controls(notch: NotchWindow) -> dict:
    """Right-click the notch and wait for its controls to be drawn."""
    right_click(notch)
    return notch.control_rects


def use_control(notch: NotchWindow, action: NotchAction) -> None:
    """Open the integrated controls and click ``action`` where it is drawn."""
    rects = open_controls(notch)
    centre = rects[action].center()
    QTest.mouseClick(notch, Qt.LeftButton, Qt.NoModifier, QPoint(round(centre.x()), round(centre.y())))


def native_menus() -> list:
    from PySide6.QtWidgets import QMenu

    return [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QMenu) and widget.isVisible()]


class Calls:
    def __init__(self, window, names):
        self.log = []
        for name in names:
            original = getattr(window, name)
            setattr(window, name, lambda *a, o=original, n=name: (self.log.append(n), o(*a))[1])


# --- Double click ------------------------------------------------------------------


def test_the_first_double_click_builds_and_shows_the_main_window(desktop):
    assert desktop.window is None

    double_click(desktop.notch)

    assert isinstance(desktop.window, main_window_module.MainWindow)
    assert desktop.window.isVisible()


def test_double_click_shows_raises_and_activates_the_main_window(desktop):
    desktop.open_main_window().hide()
    calls = Calls(desktop.window, ("show", "raise_", "activateWindow"))

    double_click(desktop.notch)

    assert calls.log == ["show", "raise_", "activateWindow"]
    assert desktop.window.isVisible()


def test_double_click_restores_a_minimised_main_window(desktop):
    desktop.open_main_window().setWindowState(Qt.WindowMinimized)

    double_click(desktop.notch)

    assert not desktop.window.windowState() & Qt.WindowMinimized


def test_double_click_never_creates_another_main_window(desktop, monkeypatch):
    built = []
    original = main_window_module.MainWindow.__init__
    monkeypatch.setattr(main_window_module.MainWindow, "__init__",
                        lambda self, *a, **k: (built.append(self), original(self, *a, **k))[1])
    for _ in range(3):
        double_click(desktop.notch)
    window = desktop.window
    window.close()
    double_click(desktop.notch)

    assert built == [window]
    assert desktop.window is window and window.isVisible()


def test_the_notch_holds_no_main_window(desktop):
    assert not any(isinstance(value, main_window_module.MainWindow) for value in vars(desktop.notch).values())


def test_a_notch_offering_no_actions_ignores_double_clicks(qt_application):
    notch = NotchWindow()
    presenter = NotchPresenter(ManualScheduler())
    bind_presenter(notch, presenter)
    presenter.show()
    requested = []
    notch.action_requested.connect(requested.append)
    try:
        double_click(notch)
        right_click(notch)
        assert requested == [] and notch.presentation.controls == ()
    finally:
        notch.hide()
        notch.deleteLater()


# --- Single click ------------------------------------------------------------------


def test_a_single_click_changes_nothing(desktop):
    requested = []
    desktop.notch.action_requested.connect(requested.append)
    before = (desktop.notch.presence, desktop.controller.intent, desktop.application.activity.current_intent())

    QTest.mouseClick(desktop.notch, Qt.LeftButton, Qt.NoModifier, QPoint(desktop.notch.width() // 2, 6))

    assert requested == []
    assert (desktop.notch.presence, desktop.controller.intent, desktop.application.activity.current_intent()) == before


# --- Integrated controls and settings --------------------------------------------------


def test_right_click_opens_the_notchs_own_controls_not_a_native_menu(desktop):
    rects = open_controls(desktop.notch)

    assert native_menus() == []
    assert list(rects) == [NotchAction.OPEN_SETTINGS, NotchAction.QUIT]
    assert [ACTION_LABELS[action] for action in rects] == ["Settings", "Quit M.I.R.A."]
    # Opening the main window is the double click, not a control.
    assert NotchAction.OPEN_MAIN_WINDOW not in rects
    assert desktop.notch.presence is NotchPresence.EXPANDED
    assert ExpansionReason.CONTROLS in desktop.presenter.presentation.reasons
    # Drawn inside the notch, in its content band.
    for rect in rects.values():
        assert desktop.notch.rect().contains(rect.toRect())


def test_the_double_click_still_opens_the_main_window_and_controls_do_not(desktop):
    open_controls(desktop.notch)
    assert desktop.window is None

    double_click(desktop.notch)

    assert desktop.window is not None and desktop.window.isVisible()


def test_a_control_can_quit_the_notch_runtime(desktop):
    use_control(desktop.notch, NotchAction.QUIT)

    assert desktop.quits == [True]
    assert desktop.presenter.presentation.controls == ()


def test_settings_open_once_for_the_running_notch(desktop):
    use_control(desktop.notch, NotchAction.OPEN_SETTINGS)
    settings = desktop.settings_window
    assert desktop.presenter.presentation.controls == ()  # a used control closes them
    use_control(desktop.notch, NotchAction.OPEN_SETTINGS)

    assert desktop.settings_window is settings
    assert settings.isVisible()
    assert settings.notch is desktop.notch
    assert settings.presenter is desktop.presenter
    assert settings.session is desktop.notch_session
    # No controller: settings can never override the application's expression.
    assert settings.controller is None
    assert not settings.expression.isEnabled()
    # Nor synthetic notices: the running application shows only real ones.
    assert settings.preview_buttons == {}


def test_a_second_right_click_closes_the_controls(desktop):
    open_controls(desktop.notch)
    right_click(desktop.notch)

    assert desktop.presenter.presentation.controls == ()
    assert desktop.notch.presence is NotchPresence.COMPACT


def test_controls_linger_after_the_pointer_leaves_and_survive_coming_back(desktop):
    scheduler = desktop.application.scheduler
    desktop.notch.hover_changed.emit(True)
    open_controls(desktop.notch)

    desktop.notch.hover_changed.emit(False)
    scheduler.advance(CONTROLS_LINGER_MS - 100)
    assert desktop.presenter.controls_open  # still there to be reached
    desktop.notch.hover_changed.emit(True)
    scheduler.advance(CONTROLS_LINGER_MS * 2)
    assert desktop.presenter.controls_open  # coming back kept them

    desktop.notch.hover_changed.emit(False)
    scheduler.advance(CONTROLS_LINGER_MS)
    assert not desktop.presenter.controls_open
    assert desktop.notch.presence is NotchPresence.COMPACT


def test_hover_keeps_the_notch_open_after_the_controls_close(desktop):
    desktop.notch.hover_changed.emit(True)
    open_controls(desktop.notch)
    right_click(desktop.notch)

    assert desktop.presenter.presentation.reasons == {ExpansionReason.HOVER}
    assert desktop.notch.presence is NotchPresence.EXPANDED


def test_the_notch_accepts_presses_so_real_clicks_reach_their_release(desktop):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    rects = open_controls(desktop.notch)
    centre = rects[NotchAction.QUIT].center()
    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(centre), desktop.notch.mapToGlobal(QPointF(centre)),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    press.ignore()

    QCoreApplication.sendEvent(desktop.notch, press)

    # An ignored press would never get its release from the window system.
    assert press.isAccepted()


def test_a_click_beside_the_controls_does_nothing(desktop):
    requested = []
    desktop.notch.action_requested.connect(requested.append)
    open_controls(desktop.notch)

    QTest.mouseClick(desktop.notch, Qt.LeftButton, Qt.NoModifier, QPoint(desktop.notch.width() // 2, 4))

    assert requested == [] and desktop.presenter.controls_open


def test_shutdown_closes_the_settings(desktop):
    desktop.open_settings()

    desktop.shutdown()

    assert not desktop.settings_window.isVisible()


# --- Task notices -------------------------------------------------------------------------


def test_completed_and_failed_actions_become_task_notices(desktop):
    bus = desktop.application.event_bus

    bus.emit("action_completed", ActionResult(True, "open_browser", "ok"))
    assert desktop.presenter.presentation.notice == Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)
    # Task notices do not expand by default.
    assert desktop.notch.presence is NotchPresence.COMPACT

    bus.emit("action_failed", ActionResult(False, "open_browser", "no"))
    assert desktop.presenter.presentation.notice.tone is NoticeTone.ERROR


def test_an_action_awaiting_confirmation_is_a_warning():
    result = ActionResult(False, "delete", "confirm", data={"requires_confirmation": True})

    assert task_notice(False, result) == Notice(NoticeCategory.TASK, NoticeTone.WARNING, "Needs confirmation")
    assert task_notice(False, object()).tone is NoticeTone.ERROR


def test_task_notices_never_touch_application_state(desktop):
    before = desktop.application.activity.current_intent()

    desktop.application.event_bus.emit("action_completed", ActionResult(True, "x", "ok"))

    assert desktop.application.activity.current_intent() == before
    assert desktop.controller.intent.activity is ActivityState.IDLE
