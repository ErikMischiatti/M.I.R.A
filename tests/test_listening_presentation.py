"""The notch while MIRA listens: expanded by its own reason, real waveform only.

The presenter is checked Qt-free; the window and the shell on the offscreen
platform with a fake capture backend fed known samples.
"""

from __future__ import annotations

import math
from array import array

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from mira.application.composition import build_application
from mira.core.listening import TOGGLE_EVENT
from mira.domain.audio import AudioFrame, AudioInputInfo, AudioWaveform
from mira.domain.listening import ActivationSource
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.notch.expanded_layout import WAVEFORM_MIN_BAR_PX, ContentRow, waveform_bars
from mira.ui.notch.presence import NotchPresence, Rect
from mira.ui.notch.presentation import LISTENING_CONTENT, ExpansionReason, NotchAction, NotchPresenter

C, E = NotchPresence.COMPACT, NotchPresence.EXPANDED


@pytest.fixture(scope="module", autouse=True)
def application():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def presenter():
    p = NotchPresenter(ManualScheduler(), actions=frozenset(NotchAction))
    p.show()
    return p


# --- Presence and content -------------------------------------------------------------------


def test_listening_expands_for_its_own_reason_and_shows_the_waveform():
    p = presenter()
    p.set_listening(True, capturing=True)

    assert p.presentation.presence is E
    assert p.presentation.reasons == {ExpansionReason.LISTENING}
    assert p.presentation.shows_waveform


def test_listening_without_real_audio_shows_the_symbol_not_a_waveform():
    p = presenter()
    p.set_listening(True, capturing=False)

    assert p.presentation.presence is E
    assert not p.presentation.shows_waveform
    assert p.presentation.content == LISTENING_CONTENT


def test_when_listening_ends_the_remaining_reasons_decide():
    p = presenter()
    p.set_listening(True, True)
    p.set_listening(False)
    assert p.presentation.presence is C and ExpansionReason.LISTENING not in p.presentation.reasons

    p.set_hovered(True)
    p.set_listening(True, True)
    p.set_listening(False)
    assert p.presentation.presence is E and p.presentation.reasons == {ExpansionReason.HOVER}

    p.set_hovered(False)
    p.set_listening(True, True)
    p.open_controls()
    p.set_listening(False)
    assert p.presentation.presence is E and p.presentation.reasons == {ExpansionReason.CONTROLS}


def test_a_notice_and_the_controls_take_the_band_from_the_waveform():
    p = presenter()
    p.set_listening(True, True)
    p.present(Notice(NoticeCategory.TASK, NoticeTone.SUCCESS))
    assert not p.presentation.shows_waveform and p.presentation.content.label == "Done"

    p.dismiss_notice()
    p.open_controls()
    assert not p.presentation.shows_waveform and p.presentation.controls

    p.close_controls()
    assert p.presentation.shows_waveform


# --- Bars ------------------------------------------------------------------------------------


ROW = ContentRow(Rect(20.0, 50.0, 360.0, 28.0), 28.0, 20.0, 12.0)


def test_bars_are_one_per_bucket_inside_the_row_and_follow_the_values():
    bars = waveform_bars(ROW, (0.0, 0.5, 1.0))

    assert len(bars) == 3
    for bar in bars:
        assert ROW.area.x <= bar.x and bar.right <= ROW.area.right
        assert ROW.area.y - 1e-9 <= bar.y and bar.bottom <= ROW.area.bottom + 1e-9
    assert bars[0].height == WAVEFORM_MIN_BAR_PX  # silence is a quiet line, not nothing
    assert bars[1].height == pytest.approx(14.0) and bars[2].height == pytest.approx(28.0)
    assert waveform_bars(ROW, ()) == []


# --- The window and the shell -----------------------------------------------------------------


class Backend:
    name = "fake"

    def __init__(self):
        self.on_frame = None
        self.starts = 0

    @property
    def info(self):
        return AudioInputInfo("fake", "Fake Mic", 16000, 1)

    def start(self, on_frame, on_failure):
        self.starts += 1
        self.on_frame = on_frame
        return True

    def stop(self):
        self.on_frame = None

    def speak(self, amplitude, seconds=0.5, t=0.0):
        count = int(16000 * seconds)
        samples = array("h", [int(amplitude * math.sin(i / 7.0)) for i in range(count)])
        self.on_frame(AudioFrame(samples.tobytes(), 16000, t))


@pytest.fixture
def runtime():
    from mira.ui.desktop_shell import DesktopShell, Surface

    backend = Backend()
    app = build_application(scheduler=ManualScheduler(), audio_backend=backend)
    shell = DesktopShell(app, initial_surface=Surface.NOTCH)
    shell.start()
    finish(shell.notch)
    shell.backend, shell.app = backend, app
    yield shell
    shell.shutdown()
    app.shutdown()
    for obj in (shell.notch, shell.window, shell.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish(notch):
    if notch.transition.state() is not notch.transition.State.Stopped:
        notch.transition.setCurrentTime(notch.transition.duration())


def lit_in_band(notch) -> int:
    image = notch.grab().toImage()
    row = notch.content_row
    return sum(
        1 for y in range(int(row.area.y), int(row.area.bottom))
        for x in range(0, image.width(), 2)
        if image.pixelColor(x, y).lightness() > 128 and image.pixelColor(x, y).alpha() > 0
    )


def test_keyboard_listening_opens_the_notch_and_draws_the_real_samples(runtime):
    runtime.app.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    finish(runtime.notch)

    assert runtime.notch.presence is E and runtime.notch.presentation.shows_waveform
    assert runtime.notch.current_waveform() is None  # no samples yet: nothing drawn as audio

    runtime.backend.speak(20000)
    waveform = runtime.notch.current_waveform()
    assert isinstance(waveform, AudioWaveform) and max(waveform.buckets) > 0.8
    loud = lit_in_band(runtime.notch)

    runtime.backend.speak(300, t=0.5)
    quiet = lit_in_band(runtime.notch)
    assert loud > quiet > 0

    runtime.app.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    finish(runtime.notch)
    assert runtime.notch.presence is C and runtime.notch.current_waveform() is None


def test_no_samples_means_no_waveform_drawn(runtime):
    runtime.app.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    finish(runtime.notch)

    assert runtime.notch.current_waveform() is None
    assert runtime.notch.presentation.content == LISTENING_CONTENT


def test_one_audio_service_whatever_the_surfaces(runtime):
    from mira.ui.desktop_shell import Surface

    service = runtime.app.audio
    for surface in (Surface.MAIN_WINDOW, Surface.NOTCH, Surface.MAIN_WINDOW):
        runtime.show_surface(surface)
    runtime.app.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)

    assert runtime.app.audio is service and runtime.backend.starts == 1
    # The main window's face shows the same LISTENING activity.
    from mira.domain.embodiment import ActivityState

    assert runtime.window.face_widget.controller.intent.activity is ActivityState.LISTENING


def test_a_notch_built_while_listening_opens_listening(runtime):
    from mira.ui.desktop_shell import DesktopShell, Surface

    backend = Backend()
    app = build_application(scheduler=ManualScheduler(), audio_backend=backend)
    shell = DesktopShell(app, initial_surface=Surface.MAIN_WINDOW)
    try:
        shell.start()
        app.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
        shell.show_surface(Surface.NOTCH)

        assert shell.presenter.presentation.listening and shell.presenter.presentation.capturing
        assert ExpansionReason.LISTENING in shell.presenter.presentation.reasons
    finally:
        shell.shutdown()
        app.shutdown()
        for obj in (shell.notch, shell.window, shell.animator):
            if obj is not None:
                obj.deleteLater()
