"""Listening: activation asks, the controller decides, the authority shows it.

Capture is always a fake backend, so no test opens a microphone; keyboard
tests run on the offscreen platform, where the global shortcut cannot grab
and the in-window key is exercised through real Qt key events.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

from mira.application.composition import build_application
from mira.core.listening import LISTENING_EVENT, TOGGLE_EVENT, ListeningController
from mira.core.audio_input import AudioInputService
from mira.domain.audio import AudioInputInfo, CapturePurpose, CaptureStatus
from mira.domain.embodiment import ActivityState
from mira.domain.listening import (
    ActivationSource,
    ListeningActivationMode,
    ListeningState,
    toggled,
)
from mira.domain.notice import NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.messaging.events import EventBus
from mira.ui.desktop.global_shortcut import GlobalShortcut, ShortcutError, parse_shortcut
from mira.ui.listening_keys import InWindowKey, is_text_entry
from mira.ui.listening_settings import ListeningConfigError, ListeningSession, ListeningSettings


@pytest.fixture(scope="module", autouse=True)
def application():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class Backend:
    """A capture backend that only records what it was asked to do."""

    name = "fake"

    def __init__(self, opens=True):
        self.opens = opens
        self.calls = []
        self.on_failure = None

    @property
    def info(self):
        return AudioInputInfo("fake", "Fake Mic")

    def start(self, on_frame, on_failure):
        # Note what it is told: frames and failures, never who asked or why.
        self.calls.append("start")
        self.on_failure = on_failure
        return self.opens

    def stop(self):
        self.calls.append("stop")


def controller(opens=True):
    bus = EventBus()
    backend = Backend(opens)
    audio = AudioInputService(bus, backend)
    listening = ListeningController(bus, audio)
    seen, notices = [], []
    bus.subscribe(LISTENING_EVENT, seen.append)
    bus.subscribe("notice", notices.append)
    return bus, backend, audio, listening, seen, notices


# --- The model --------------------------------------------------------------------------


def test_the_keyboard_toggle_alternates():
    assert toggled(ListeningState.OFF) is ListeningState.LISTENING
    assert toggled(ListeningState.LISTENING) is ListeningState.OFF


def test_toggling_starts_listening_with_a_conversation_lease_and_stops_it():
    bus, backend, audio, listening, seen, _ = controller()

    bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    assert listening.listening and audio.lease_purposes == [CapturePurpose.CONVERSATION]
    assert seen[-1].listening and seen[-1].capturing and seen[-1].source is ActivationSource.KEYBOARD

    bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    assert not listening.listening and audio.lease_purposes == []
    assert audio.status is CaptureStatus.IDLE
    assert backend.calls == ["start", "stop"]


def test_repeated_toggles_are_deterministic():
    bus, backend, audio, listening, seen, _ = controller()

    for _ in range(7):
        listening.toggle(ActivationSource.KEYBOARD)

    assert listening.listening  # odd number of presses
    assert [status.state for status in seen] == [ListeningState.LISTENING, ListeningState.OFF] * 3 + [ListeningState.LISTENING]
    assert backend.calls.count("start") == 4 and backend.calls.count("stop") == 3


def test_the_backend_never_learns_what_activated_listening():
    import inspect

    assert list(inspect.signature(Backend.start).parameters) == ["self", "on_frame", "on_failure"]
    assert "source" not in inspect.signature(AudioInputService.acquire).parameters


def test_a_microphone_that_cannot_open_refuses_listening_with_one_warning():
    bus, backend, audio, listening, seen, notices = controller(opens=False)

    listening.toggle(ActivationSource.KEYBOARD)

    assert not listening.listening and seen == []
    assert [(n.tone, n.text) for n in notices] == [(NoticeTone.WARNING, "Microphone unavailable")]


def test_losing_the_microphone_ends_listening_and_warns_once():
    bus, backend, audio, listening, seen, notices = controller()
    listening.toggle(ActivationSource.KEYBOARD)

    backend.on_failure("the microphone was disconnected")

    assert not listening.listening
    assert seen[-1].state is ListeningState.OFF and seen[-1].reason == "the microphone was disconnected"
    assert [n.text for n in notices] == ["Microphone disconnected"]


def test_shutdown_releases_the_lease():
    bus, backend, audio, listening, seen, _ = controller()
    listening.toggle(ActivationSource.KEYBOARD)

    listening.shutdown()
    listening.toggle(ActivationSource.KEYBOARD)

    assert audio.lease_purposes == [] and backend.calls[-1] == "stop"


# --- Through the real graph: LISTENING is the authority's ---------------------------------


@pytest.fixture
def graph():
    app = build_application(scheduler=ManualScheduler(), audio_backend=Backend())
    yield app
    app.shutdown()


def test_voice_listening_is_the_one_listening_activity(graph):
    graph.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    assert graph.activity.current_intent().activity is ActivityState.LISTENING

    graph.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    assert graph.activity.current_intent().activity is ActivityState.IDLE


def test_a_focused_input_keeps_listening_shown_after_voice_stops(graph):
    graph.event_bus.emit("input_focused")
    graph.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    graph.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)

    assert graph.activity.current_intent().activity is ActivityState.LISTENING


def test_listening_does_not_interrupt_a_turn_and_returns_after_it(graph):
    graph.event_bus.emit("processing_started")
    graph.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    assert graph.activity.current_intent().activity is ActivityState.THINKING

    graph.interaction_manager.is_processing = False
    graph.interaction_manager.restore_post_response_state()
    assert graph.activity.current_intent().activity is ActivityState.LISTENING


def test_the_runtime_has_exactly_one_listening_controller_and_audio_service(graph):
    assert isinstance(graph.listening, ListeningController)
    assert graph.event_bus._subscribers[TOGGLE_EVENT] == [graph.listening.on_toggle_requested]


# --- The in-window key ----------------------------------------------------------------------


@pytest.fixture
def keys():
    presses = []
    key = InWindowKey("M", lambda: presses.append(True))
    QApplication.instance().installEventFilter(key)
    yield presses
    QApplication.instance().removeEventFilter(key)


def make_window():
    window = QWidget()
    layout = QVBoxLayout(window)
    field, button = QLineEdit(), QPushButton("Send")
    layout.addWidget(field)
    layout.addWidget(button)
    window.show()
    return window, field, button


def test_typing_m_in_a_text_field_types_it(keys):
    window, field, _ = make_window()
    try:
        field.setFocus()
        QTest.keyClicks(field, "MIRA mm")

        assert field.text() == "MIRA mm" and keys == []
    finally:
        window.close()
        window.deleteLater()


def test_m_toggles_where_no_text_is_being_typed(keys):
    window, _, button = make_window()
    try:
        button.setFocus()
        QTest.keyClick(button, Qt.Key_M)
        QTest.keyClick(button, Qt.Key_M)

        assert keys == [True, True]
    finally:
        window.close()
        window.deleteLater()


def test_a_read_only_view_is_not_a_text_entry():
    from PySide6.QtWidgets import QTextEdit

    history = QTextEdit()
    history.setReadOnly(True)
    editable = QTextEdit()

    assert not is_text_entry(history) and is_text_entry(editable)
    for widget in (history, editable):
        widget.deleteLater()


def test_autorepeat_and_modified_presses_do_not_toggle(keys):
    window, _, button = make_window()
    try:
        button.setFocus()
        QTest.keyClick(button, Qt.Key_M, Qt.ControlModifier)
        QTest.keyClick(button, Qt.Key_M, Qt.ShiftModifier)
        from PySide6.QtGui import QKeyEvent

        QCoreApplication.sendEvent(button, QKeyEvent(QEvent.KeyPress, Qt.Key_M, Qt.NoModifier, "m", True))

        assert keys == []
    finally:
        window.close()
        window.deleteLater()


def test_the_main_windows_chat_input_never_toggles(keys):
    from mira.ui.desktop_shell import DesktopShell, Surface

    app = build_application(scheduler=ManualScheduler(), audio_backend=Backend())
    shell = DesktopShell(app, initial_surface=Surface.MAIN_WINDOW)
    try:
        shell.start()
        entries = [w for w in shell.window.findChildren(QWidget) if is_text_entry(w) and w.isVisible()]
        assert entries, "the main window has a text entry"
        for entry in entries:
            entry.setFocus()
            QTest.keyClicks(entry, "Mm MIRA")
        assert keys == []
        assert not app.listening.listening
    finally:
        shell.shutdown()
        app.shutdown()
        for obj in (shell.window, shell.animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_shells_keyboard_activation_asks_the_controller():
    from mira.ui.desktop_shell import DesktopShell, Surface

    app = build_application(scheduler=ManualScheduler(), audio_backend=Backend())
    shell = DesktopShell(app, initial_surface=Surface.MAIN_WINDOW, listening_settings=ListeningSettings())
    try:
        shell.start()
        assert shell.keyboard is not None and not shell.keyboard.shortcut.active  # offscreen: no grab
        shell.window.debug_toggle.setFocus()
        QTest.keyClick(shell.window.debug_toggle, Qt.Key_M)
        assert app.listening.listening
        QTest.keyClick(shell.window.debug_toggle, Qt.Key_M)
        assert not app.listening.listening
    finally:
        shell.shutdown()
        app.shutdown()
        for obj in (shell.window, shell.animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- The global shortcut ------------------------------------------------------------------------


def test_a_global_shortcut_must_carry_a_modifier():
    assert parse_shortcut("Ctrl+Alt+M") == ("m", 4 | 8)
    assert parse_shortcut("Meta+F9")[0] == "F9"
    assert parse_shortcut("Super+L") == parse_shortcut("Meta+L")
    for unusable in ("M", "Shift+M", "Ctrl+Alt+M, Ctrl+K", "", "Ctrl+Space"):
        with pytest.raises(ShortcutError):
            parse_shortcut(unusable)


def test_holding_the_shortcut_toggles_once_per_press():
    presses = []
    shortcut = GlobalShortcut("Ctrl+Alt+M", lambda: presses.append(True))
    shortcut._keycode = 58

    for event in (2, 2, 2, 3, 2, 3):  # press, repeats, release, press, release
        shortcut.handle(event, 58)
    shortcut.handle(2, 59)  # another key: never ours

    assert presses == [True, True]


def test_no_global_shortcut_off_x11():
    shortcut = GlobalShortcut("Ctrl+Alt+M", lambda: None, platform="offscreen")

    assert shortcut.start() is False and "not available" in shortcut.error


# --- Settings ---------------------------------------------------------------------------------


def test_the_listening_settings_default_to_keyboard_activation(tmp_path):
    session = ListeningSession(tmp_path / "listening.json")

    assert session.settings == ListeningSettings()
    assert session.settings.activation is ListeningActivationMode.KEYBOARD
    assert not (tmp_path / "listening.json").exists()  # loading never writes


def test_wake_word_is_modelled_but_never_silently_selected(tmp_path):
    path = tmp_path / "listening.json"
    path.write_text(json.dumps({"version": 1, "activation": "wake word"}))

    session = ListeningSession(path)

    assert session.settings.activation is ListeningActivationMode.KEYBOARD
    assert "not available yet" in session.diagnostic
    with pytest.raises(ListeningConfigError):
        ListeningSettings(activation=ListeningActivationMode.WAKE_WORD)


def test_listening_settings_round_trip_and_bad_files_fall_back(tmp_path):
    path = tmp_path / "listening.json"
    session = ListeningSession(path)
    session.save(ListeningSettings(in_window_key="L", global_shortcut="Ctrl+Alt+L"))

    assert ListeningSession(path).settings == ListeningSettings(in_window_key="L", global_shortcut="Ctrl+Alt+L")
    for broken in ("{", json.dumps({"version": 1, "global_shortcut": "M"}), json.dumps({"version": 9})):
        path.write_text(broken)
        reloaded = ListeningSession(path)
        assert reloaded.settings == ListeningSettings() and reloaded.diagnostic
    path.write_text(json.dumps({"version": 1, "global_shortcut": None}))
    assert ListeningSession(path).settings.global_shortcut is None
