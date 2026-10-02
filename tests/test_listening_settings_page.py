"""The Listening settings page: honest choices, live keys, one audio service."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application
from mira.domain.audio import AudioInputInfo, CapturePurpose
from mira.domain.embodiment import ActivityState
from mira.domain.listening import ListeningActivationMode
from mira.domain.scheduler import ManualScheduler
from mira.ui.listening_settings import ListeningSession, ListeningSettings
from mira.ui.notch.listening_page import ListeningPage


@pytest.fixture(scope="module", autouse=True)
def application():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class Backend:
    name = "fake"

    def __init__(self):
        self.starts = 0
        self.running = False

    @property
    def info(self):
        return AudioInputInfo("fake backend", "Fake Mic", 16000 if self.running else None, 1 if self.running else None)

    def start(self, on_frame, on_failure):
        self.starts += 1
        self.running = True
        return True

    def stop(self):
        self.running = False


def test_wake_word_is_shown_as_coming_later_and_cannot_be_chosen(tmp_path):
    page = ListeningPage(session=ListeningSession(tmp_path / "listening.json"))
    model = page.activation.model()

    labels = [page.activation.itemText(i) for i in range(page.activation.count())]
    assert labels == ["Keyboard", "Wake word — coming later"]
    assert model.item(0).isEnabled() and not model.item(1).isEnabled()
    assert page.runtime_settings.activation is ListeningActivationMode.KEYBOARD


def test_a_new_shortcut_applies_at_once_and_is_written_only_by_save(tmp_path):
    path = tmp_path / "listening.json"
    applied = []
    page = ListeningPage(session=ListeningSession(path), apply=applied.append)

    page.global_shortcut.setKeySequence(QKeySequence("Ctrl+Alt+K"))

    assert applied[-1].global_shortcut == "Ctrl+Alt+K"
    assert page.has_unsaved_changes and not path.exists()
    page.save()
    assert json.loads(path.read_text())["global_shortcut"] == "Ctrl+Alt+K"
    assert not page.has_unsaved_changes


def test_a_shortcut_without_a_modifier_is_refused(tmp_path):
    applied = []
    page = ListeningPage(session=ListeningSession(tmp_path / "l.json"), apply=applied.append)

    page.global_shortcut.setKeySequence(QKeySequence("K"))

    assert applied == [] and "Ctrl, Alt or Super" in page.listening_error.text()
    assert page.runtime_settings == ListeningSettings()


def test_the_shortcut_can_be_turned_off_and_reset(tmp_path):
    applied = []
    page = ListeningPage(session=ListeningSession(tmp_path / "l.json"), apply=applied.append)

    page.global_enabled.setChecked(False)
    assert applied[-1].global_shortcut is None
    page.reset()
    assert page.runtime_settings == ListeningSettings() and page.global_enabled.isChecked()


def test_without_a_runtime_the_listening_settings_are_read_only():
    page = ListeningPage()

    assert not page.activation.isEnabled() and not page.global_shortcut.isEnabled()
    page.refresh()
    assert page.backend_label.text() == "-" and not page.test_microphone.isEnabled()


def test_the_microphone_test_uses_the_runtimes_one_service_without_listening(tmp_path):
    from mira.ui.desktop_shell import DesktopShell, Surface
    from mira.ui.notch.config_store import ConfigSession

    backend = Backend()
    app = build_application(scheduler=ManualScheduler(), audio_backend=backend)
    shell = DesktopShell(app, initial_surface=Surface.NOTCH, notch_session=ConfigSession(tmp_path / "notch.json"),
                         listening_session=ListeningSession(tmp_path / "listening.json"))
    try:
        shell.start()
        shell.open_settings()
        page = shell.settings_window.listening_page
        assert page.audio is app.audio

        page.test_microphone.setChecked(True)
        assert app.audio.lease_purposes == [CapturePurpose.DIAGNOSTICS] and backend.starts == 1
        assert not app.listening.listening
        assert app.activity.current_intent().activity is ActivityState.IDLE
        page.refresh()
        assert page.backend_label.text() == "fake backend" and page.device_label.text() == "Fake Mic"
        assert page.purposes_label.text() == "microphone test"
        assert "capturing" in page.status_label.text()

        shell.settings_window.hide()  # hiding ends the test
        assert app.audio.lease_purposes == [] and not backend.running
    finally:
        shell.shutdown()
        app.shutdown()
        for obj in (shell.notch, shell.window, shell.animator, shell.settings_window):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_applying_new_keys_re_registers_the_runtimes_keyboard_activation():
    from mira.ui.desktop_shell import DesktopShell, Surface

    app = build_application(scheduler=ManualScheduler(), audio_backend=Backend())
    shell = DesktopShell(app, initial_surface=Surface.MAIN_WINDOW, listening_settings=ListeningSettings())
    try:
        shell.start()
        first = shell.keyboard
        shell.apply_listening_settings(ListeningSettings(in_window_key="L", global_shortcut=None))

        assert shell.keyboard is not first and shell.keyboard.shortcut is None
        assert first._installed is False and shell.keyboard._installed
    finally:
        shell.shutdown()
        app.shutdown()
        for obj in (shell.window, shell.animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
