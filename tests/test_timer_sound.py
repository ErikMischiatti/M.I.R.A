"""The timer's completion chime: one sound per finished countdown, never more.

Playback is asked of a `SoundOutput` port. These tests use a recording fake
in its place and a fake Qt sound effect for the adapter, so no audio device
is ever opened; the packaged WAV is checked against the script that renders it.
"""

from __future__ import annotations

import importlib.util
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.application.composition import build_application
from mira.core.countdown_timer import TIMER_FINISHED_EVENT
from mira.core.sound_cues import SoundCues
from mira.domain.scheduler import ManualScheduler
from mira.domain.sound import SilentSoundOutput, Sound, SoundOutput
from mira.domain.timer import TimerState
from mira.messaging.events import EventBus

REPOSITORY = Path(__file__).resolve().parents[1]
GENERATOR = REPOSITORY / "scripts" / "generate_sounds.py"
ASSET = REPOSITORY / "mira" / "sounds" / "timer_done.wav"


class RecordingOutput:
    def __init__(self) -> None:
        self.played: list[Sound] = []
        self.shutdowns = 0

    def play(self, sound: Sound) -> None:
        self.played.append(sound)

    def shutdown(self) -> None:
        self.shutdowns += 1


class BrokenOutput(RecordingOutput):
    def play(self, sound: Sound) -> None:
        super().play(sound)
        raise RuntimeError("no audio device")


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Rig:
    """The real application graph with a fake clock and a recording sound output."""

    def __init__(self, output=None) -> None:
        self.scheduler = ManualScheduler()
        self.clock = FakeClock()
        self.output = RecordingOutput() if output is None else output
        self.application = build_application(scheduler=self.scheduler, clock=self.clock, sound_output=self.output)
        self.timer = self.application.timer

    def elapse(self, seconds: float) -> None:
        self.clock.now += seconds
        self.scheduler.advance(round(seconds * 1000))


@pytest.fixture
def rig():
    rig = Rig()
    yield rig
    rig.application.shutdown()


# --- The port ---------------------------------------------------------------------------------


def test_the_silent_output_is_a_sound_output_that_does_nothing():
    output = SilentSoundOutput()

    assert isinstance(output, SoundOutput)
    output.prepare()
    output.play(Sound.TIMER_DONE)
    output.shutdown()


def test_without_a_sound_output_the_graph_is_silent():
    application = build_application(scheduler=ManualScheduler())
    try:
        assert isinstance(application.sound, SilentSoundOutput)
    finally:
        application.shutdown()


# --- When it plays ------------------------------------------------------------------------------


def test_a_finished_countdown_plays_the_chime_once(rig):
    rig.timer.start(60)

    rig.elapse(59)
    assert rig.output.played == []

    rig.elapse(1)
    assert rig.output.played == [Sound.TIMER_DONE]

    rig.elapse(3600)
    assert rig.output.played == [Sound.TIMER_DONE]


def test_pause_resume_reset_and_cancel_play_nothing(rig):
    rig.timer.start(60)
    rig.elapse(10)
    rig.timer.pause()
    rig.elapse(600)
    rig.timer.resume()
    rig.elapse(10)
    rig.timer.reset()
    rig.elapse(600)
    rig.timer.start(30)
    rig.timer.start(45)  # replaces the running countdown
    rig.timer.reset()

    assert rig.output.played == []


def test_acknowledging_plays_nothing_more(rig):
    rig.timer.start(5)
    rig.elapse(5)

    assert rig.timer.acknowledge() is True
    assert rig.timer.acknowledge() is False

    assert rig.output.played == [Sound.TIMER_DONE]


def test_each_new_countdown_that_finishes_plays_again(rig):
    for duration in (5, 8, 3):
        rig.timer.start(duration)
        rig.elapse(duration)
        rig.timer.acknowledge()

    assert rig.output.played == [Sound.TIMER_DONE] * 3


def test_a_countdown_finishing_while_paused_was_due_plays_once(rig):
    rig.timer.start(30)
    rig.clock.now += 31

    rig.timer.pause()  # past the deadline: finishes instead
    rig.scheduler.advance(60_000)

    assert rig.output.played == [Sound.TIMER_DONE]


# --- Failures and shutdown ----------------------------------------------------------------------


def test_a_failing_output_never_breaks_the_countdown(capsys):
    rig = Rig(BrokenOutput())
    changes = []
    rig.application.event_bus.subscribe("timer_changed", changes.append)
    try:
        rig.timer.start(5)
        rig.elapse(5)

        assert rig.timer.snapshot.state is TimerState.FINISHED
        assert changes[-1].state is TimerState.FINISHED
        assert rig.output.played == [Sound.TIMER_DONE]
        assert "[sound]" in capsys.readouterr().err

        # Still harmless the next time.
        rig.timer.acknowledge()
        rig.timer.start(5)
        rig.elapse(5)
        assert rig.output.played == [Sound.TIMER_DONE] * 2
    finally:
        rig.application.shutdown()


def test_shutdown_releases_the_output_once_and_silences_the_cues():
    rig = Rig()
    rig.timer.start(5)

    rig.application.shutdown()

    assert rig.output.shutdowns == 1
    rig.application.event_bus.emit(TIMER_FINISHED_EVENT, rig.timer.snapshot)
    assert rig.output.played == []


def test_the_cues_ignore_finishes_after_their_shutdown():
    bus, output = EventBus(), RecordingOutput()
    cues = SoundCues(bus, output)

    cues.shutdown()
    bus.emit(TIMER_FINISHED_EVENT, None)

    assert output.played == []


# --- In the desktop: no replay from the notch ----------------------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mira.ui.desktop_shell import DesktopShell, Surface  # noqa: E402
from mira.ui.notch.config_store import ConfigSession  # noqa: E402
from mira.ui.notch.presentation import TimerButton, TimerCommand  # noqa: E402


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    rig = Rig()
    shell = DesktopShell(
        rig.application, initial_surface=Surface.NOTCH, notch_session=ConfigSession(tmp_path / "notch.json"),
        quit=lambda: None,
    )
    shell.start()
    yield shell, rig
    shell.shutdown()
    rig.application.shutdown()
    for obj in (shell.notch, shell.window, shell.animator, shell.settings_window):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_hover_redraws_and_ok_never_replay_the_chime(desktop):
    shell, rig = desktop
    rig.timer.start(5)
    rig.elapse(5)
    assert rig.output.played == [Sound.TIMER_DONE]

    for hovered in (True, False, True, False):
        shell.presenter.set_hovered(hovered)
        rig.scheduler.advance(2000)
    shell.presenter.toggle_controls()
    shell.presenter.toggle_controls()
    for _ in range(30):
        shell.controller.update(0.03)
        shell.notch.repaint()
    shell.on_timer_requested(TimerButton(TimerCommand.ACKNOWLEDGE))
    shell.presenter.set_hovered(True)

    assert rig.output.played == [Sound.TIMER_DONE]
    assert rig.timer.snapshot.state is TimerState.IDLE


# --- The Qt adapter --------------------------------------------------------------------------------


class FakeEffect:
    """Stands in for `QSoundEffect`: records what the adapter asks of it."""

    def __init__(self, status="Ready", fail_on_play=False) -> None:
        self.source = None
        self.volume = None
        self.plays = 0
        self.stops = 0
        self.deleted = False
        self._status = status
        self._fail_on_play = fail_on_play

    def setSource(self, url) -> None:
        self.source = url

    def setVolume(self, volume) -> None:
        self.volume = volume

    def status(self):
        # Like `QSoundEffect.Status`, recognised by its name.
        return SimpleNamespace(name=self._status)

    def play(self) -> None:
        if self._fail_on_play:
            raise RuntimeError("backend gone")
        self.plays += 1

    def stop(self) -> None:
        self.stops += 1

    def deleteLater(self) -> None:
        self.deleted = True


def adapter(effects, sources=None):
    from mira.adapters.qt_sound import PACKAGED_SOUNDS, QtSoundOutput

    def factory():
        effect = effects.pop(0) if effects else FakeEffect()
        made.append(effect)
        return effect

    made: list[FakeEffect] = []
    output = QtSoundOutput(PACKAGED_SOUNDS if sources is None else sources, effect_factory=factory)
    return output, made


def test_the_adapter_plays_the_packaged_sound_through_one_reused_effect():
    output, made = adapter([])

    output.play(Sound.TIMER_DONE)
    output.play(Sound.TIMER_DONE)

    assert len(made) == 1
    assert made[0].plays == 2
    assert Path(made[0].source.toLocalFile()) == ASSET
    assert 0.0 < made[0].volume <= 1.0


def test_a_missing_sound_file_is_reported_and_never_raises(tmp_path, capsys):
    output, made = adapter([], {Sound.TIMER_DONE: tmp_path / "missing.wav"})

    output.play(Sound.TIMER_DONE)
    output.play(Sound.TIMER_DONE)

    assert made == []
    assert capsys.readouterr().err.count("[sound]") == 1


def test_an_effect_that_failed_to_load_is_not_played(capsys):
    output, made = adapter([FakeEffect(status="Error")])

    output.play(Sound.TIMER_DONE)

    assert made[0].plays == 0
    assert "[sound]" in capsys.readouterr().err


def test_a_backend_failure_is_reported_and_never_raises(capsys):
    output, made = adapter([FakeEffect(fail_on_play=True)])

    output.play(Sound.TIMER_DONE)

    assert "[sound]" in capsys.readouterr().err


def test_the_adapter_releases_its_effects_on_shutdown_and_then_plays_nothing():
    output, made = adapter([])
    output.play(Sound.TIMER_DONE)

    output.shutdown()
    output.shutdown()
    output.play(Sound.TIMER_DONE)

    assert made[0].stops == 1
    assert made[0].deleted
    assert made[0].plays == 1
    assert len(made) == 1


def test_prepare_loads_every_sound_once_ahead_of_playing():
    output, made = adapter([])

    output.prepare()
    assert len(made) == 1 and made[0].plays == 0

    output.prepare()
    output.play(Sound.TIMER_DONE)
    assert len(made) == 1 and made[0].plays == 1


def test_prepare_with_a_missing_file_or_after_shutdown_is_harmless(tmp_path, capsys):
    output, made = adapter([], {Sound.TIMER_DONE: tmp_path / "missing.wav"})
    output.prepare()
    assert made == [] and "[sound]" in capsys.readouterr().err

    output, made = adapter([])
    output.shutdown()
    output.prepare()
    assert made == []


def test_the_adapter_imports_no_multimedia_until_it_is_used():
    import sys

    from mira.adapters.qt_sound import PACKAGED_SOUNDS, QtSoundOutput

    if "PySide6.QtMultimedia" in sys.modules:
        pytest.skip("Qt Multimedia was already imported by another test")
    QtSoundOutput(PACKAGED_SOUNDS).shutdown()
    assert "PySide6.QtMultimedia" not in sys.modules


# --- The sound itself ------------------------------------------------------------------------------


def load_generator():
    import sys

    name = "_generate_sounds"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, GENERATOR)
        module = importlib.util.module_from_spec(spec)
        # Registered first: its dataclasses look their module up while executing.
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def read_asset() -> tuple[wave._wave_params, list[int]]:
    with wave.open(str(ASSET), "rb") as wav:
        params = wav.getparams()
        frames = wav.readframes(params.nframes)
    samples = [int.from_bytes(frames[i:i + 2], "little", signed=True) for i in range(0, len(frames), 2)]
    return params, samples


def test_the_chime_is_a_short_quiet_mono_wav():
    params, samples = read_asset()

    assert (params.nchannels, params.sampwidth) == (1, 2)
    assert 0.5 <= params.nframes / params.framerate <= 1.0
    peak = max(abs(sample) for sample in samples) / 32768
    # Never loud (at most -6 dBFS), still clearly audible.
    assert 0.25 <= peak <= 0.5
    # Starts and ends in silence, so it never clicks.
    assert abs(samples[0]) <= 1 and max(abs(sample) for sample in samples[-20:]) <= 1


def test_the_chime_is_two_or_three_rising_notes():
    generator = load_generator()

    pitches = [note.frequency_hz for note in generator.TIMER_DONE_NOTES]
    assert 2 <= len(pitches) <= 3
    assert pitches == sorted(pitches)


def test_the_packaged_chime_is_what_the_generator_renders():
    generator = load_generator()
    params, samples = read_asset()

    rendered = generator.to_pcm16(generator.render_timer_done())

    assert params.framerate == generator.SAMPLE_RATE
    assert len(rendered) == len(samples)
    # Bit-exact on the machine that rendered it; a last-bit rounding
    # difference of another platform's math library is not a different sound.
    assert max(abs(a - b) for a, b in zip(rendered, samples)) <= 1
