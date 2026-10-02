"""Notch interaction: user intents leave the notch as actions the shell honours.

The notch never holds the main window. A double click or a context-menu
entry emits a `NotchAction`; `DesktopShell` (in NOTCH mode, as `MIRA-NOTCH`
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
from mira.ui.desktop_shell import NOTCH_ACTIONS, DesktopShell, ShellMode
from mira.ui.notch.config_store import ConfigSession
from mira.ui.notch.notch_window import ACTION_LABELS, NotchWindow
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import NotchAction, NotchPresenter
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
        application, mode=ShellMode.NOTCH, notch_session=ConfigSession(tmp_path / "notch.json"),
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


def context_menu(notch: NotchWindow, choose: NotchAction | None, offered: list | None = None) -> None:
    """Open the notch's context menu and pick ``choose`` (None: dismiss it)."""

    def exec_menu(menu, _position):
        if offered is not None:
            offered.extend(entry.text() for entry in menu.actions())
        for entry in menu.actions():
            if choose is not None and entry.data() == choose.value:
                return entry
        return None

    notch.exec_menu = exec_menu
    point = QPoint(notch.width() // 2, 6)
    QCoreApplication.sendEvent(notch, QContextMenuEvent(QContextMenuEvent.Mouse, point, notch.mapToGlobal(point)))


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
        context_menu(notch, NotchAction.OPEN_SETTINGS)
        assert requested == []
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


# --- Context menu and settings --------------------------------------------------------


def test_the_context_menu_offers_exactly_the_shells_actions(desktop):
    offered = []
    context_menu(desktop.notch, None, offered)

    assert desktop.presenter.presentation.actions == NOTCH_ACTIONS
    assert offered == [ACTION_LABELS[action] for action in (
        NotchAction.OPEN_MAIN_WINDOW, NotchAction.OPEN_SETTINGS, NotchAction.QUIT,
    )]


def test_the_context_menu_can_quit_the_notch_runtime(desktop):
    context_menu(desktop.notch, NotchAction.QUIT)

    assert desktop.quits == [True]


def test_the_context_menu_can_open_the_main_window(desktop):
    context_menu(desktop.notch, NotchAction.OPEN_MAIN_WINDOW)

    assert desktop.window.isVisible()


def test_settings_open_once_for_the_running_notch(desktop):
    context_menu(desktop.notch, NotchAction.OPEN_SETTINGS)
    settings = desktop.settings_window
    context_menu(desktop.notch, NotchAction.OPEN_SETTINGS)

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
