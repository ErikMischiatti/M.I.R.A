"""Voice settings, engine selection and the sherpa-onnx adapters.

Engine selection is checked with a fake loader. The adapters' file
discovery is checked on a fake layout; the real models are exercised only
when installed (``~/.local/share/mira/models``), and never open a microphone:
they are fed synthetic audio.
"""

from __future__ import annotations

import json
import math
from array import array

import pytest

pytest.importorskip("PySide6")

from mira.application.composition import build_application
from mira.application.voice import configure_listening
from mira.domain.audio import AudioFrame
from mira.domain.listening import ListeningActivationMode
from mira.domain.scheduler import ManualScheduler
from mira.domain.voice import EnergyVoiceActivityDetector, SegmentationPolicy, WakeWordConfig
from mira.ui.listening_settings import ListeningConfigError, ListeningSession, ListeningSettings


# --- Settings v2 ----------------------------------------------------------------------------


def test_version_2_round_trips_the_voice_settings(tmp_path):
    path = tmp_path / "listening.json"
    settings = ListeningSettings(ListeningActivationMode.WAKE_WORD, wake_word=WakeWordConfig("nero", 0.7),
                                 speech_end_s=1.2, wait_timeout_s=6.0)
    ListeningSession(path).save(settings)

    data = json.loads(path.read_text())
    assert data["version"] == 2 and data["wake_word"] == {"phrase": "nero", "sensitivity": 0.7}
    assert ListeningSession(path).settings == settings
    assert settings.policy == SegmentationPolicy(end_silence_s=1.2, wait_timeout_s=6.0)


def test_a_version_1_file_loads_with_defaults_and_is_not_rewritten(tmp_path):
    path = tmp_path / "listening.json"
    path.write_text(json.dumps({"version": 1, "activation": "keyboard", "in_window_key": "L"}))
    before = path.read_bytes()

    session = ListeningSession(path)

    assert session.settings.in_window_key == "L" and session.migrated_from == 1
    assert session.settings.wake_word == WakeWordConfig() and session.settings.speech_end_s == 0.8
    assert path.read_bytes() == before


@pytest.mark.parametrize("broken", [
    {"version": 2, "wake_word": {"phrase": "m1ra"}},
    {"version": 2, "speech_end_s": 9.0},
    {"version": 2, "wait_timeout_s": "long"},
    {"version": 2, "wake_word": {"phrase": "mira", "volume": 3}},
    {"version": 1, "speech_end_s": 1.0},  # a version 2 key in a version 1 file
])
def test_invalid_voice_settings_fall_back_with_a_diagnostic(tmp_path, broken):
    path = tmp_path / "listening.json"
    path.write_text(json.dumps(broken))

    session = ListeningSession(path)

    assert session.settings == ListeningSettings() and session.diagnostic


# --- Engine selection --------------------------------------------------------------------------


class Engines:
    def __init__(self, detector=None, vad=None, detector_problem=None, vad_problem=None):
        self.detector, self.vad = detector, vad
        self.detector_problem, self.vad_problem = detector_problem, vad_problem


class Detector:
    name = "fake spotter"

    def process(self, frame):
        return []

    def reset(self):
        pass


def loader_recording(calls, engines):
    def load(config, *, want_detector):
        calls.append((config, want_detector))
        return engines
    return load


@pytest.fixture
def app():
    application = build_application(scheduler=ManualScheduler())
    yield application
    application.shutdown()


def test_keyboard_mode_never_loads_the_wake_word_detector(app):
    calls = []
    setup = configure_listening(app.listening, ListeningActivationMode.KEYBOARD, WakeWordConfig(), SegmentationPolicy(),
                                loader=loader_recording(calls, Engines(vad_problem="no model")))

    assert calls[0][1] is False
    assert setup.detector is None and setup.vad == "energy (built-in)"
    assert isinstance(app.listening.vad, EnergyVoiceActivityDetector)


def test_wake_mode_without_an_engine_reports_why_and_keeps_the_keyboard(app):
    setup = configure_listening(app.listening, ListeningActivationMode.WAKE_WORD, WakeWordConfig(), SegmentationPolicy(),
                                loader=loader_recording([], Engines(detector_problem="the voice extra is not installed")))

    assert setup.detector_problem == "the voice extra is not installed"
    assert app.listening.mode is ListeningActivationMode.WAKE_WORD and not app.listening.monitoring


def test_wake_mode_with_an_engine_uses_it(app):
    detector = Detector()
    setup = configure_listening(app.listening, ListeningActivationMode.WAKE_WORD, WakeWordConfig("nero"),
                                SegmentationPolicy(end_silence_s=1.0), loader=loader_recording([], Engines(detector)))

    assert app.listening.detector is detector and setup.detector == "fake spotter"
    assert app.listening.policy.end_silence_s == 1.0


# --- The settings page ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qt():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_page_edits_wake_word_and_timing(qt, tmp_path):
    from mira.ui.notch.listening_page import ListeningPage

    applied = []
    page = ListeningPage(session=ListeningSession(tmp_path / "l.json"), apply=applied.append, wake_available=True)
    assert not page.wake_phrase.isEnabled()  # keyboard mode: nothing to configure

    page.activation.setCurrentIndex(page.activation.findData("wake word"))
    assert page.wake_phrase.isEnabled() and page.wake_sensitivity.isEnabled()
    page.wake_phrase.setText("Nero")
    page.wake_phrase.editingFinished.emit()
    page.wake_sensitivity.setValue(0.65)
    page.speech_end.setValue(1.1)

    last = applied[-1]
    assert last.activation is ListeningActivationMode.WAKE_WORD
    assert last.wake_word == WakeWordConfig("nero", 0.65) and last.speech_end_s == 1.1


def test_the_page_shows_the_controllers_state_without_audio(qt, app):
    from mira.ui.notch.listening_page import ListeningPage

    page = ListeningPage(audio=app.audio, listening=app.listening)
    page.refresh()

    labels = {name: label.text() for name, label in page.voice_labels.items()}
    assert labels["Activation"] == "keyboard" and labels["Wake monitoring"] == "off"
    assert labels["Session"] == "idle" and labels["Last utterance"] == "none yet"
    assert labels["Speech detection"] == "energy (built-in)"


# --- The sherpa-onnx adapters ------------------------------------------------------------------------


def test_models_live_in_the_xdg_data_directory(tmp_path):
    from mira.adapters.sherpa_voice import models_directory

    assert models_directory({"XDG_DATA_HOME": str(tmp_path)}) == tmp_path / "mira" / "models"
    assert models_directory({}).parts[-4:] == (".local", "share", "mira", "models")


def test_a_keyword_spotter_is_found_by_its_files_preferring_int8(tmp_path):
    from mira.adapters.sherpa_voice import find_keyword_spotter

    directory = tmp_path / "sherpa-onnx-kws-zipformer-test"
    directory.mkdir()
    for name in ("tokens.txt", "bpe.model", "encoder-x.onnx", "encoder-x.int8.onnx", "decoder-x.onnx", "joiner-x.onnx"):
        (directory / name).write_text("x")

    model = find_keyword_spotter(tmp_path)
    assert model.encoder.name == "encoder-x.int8.onnx" and model.joiner.name == "joiner-x.onnx"
    (directory / "bpe.model").unlink()
    assert find_keyword_spotter(tmp_path) is None


def test_missing_models_are_reported_never_raised(tmp_path):
    from mira.adapters.sherpa_voice import load_voice_engines

    engines = load_voice_engines(WakeWordConfig(), models=tmp_path)

    assert engines.detector is None and engines.vad is None
    assert engines.detector_problem and engines.vad_problem


def installed_models():
    try:
        from mira.adapters.sherpa_voice import find_keyword_spotter, models_directory
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return None
    models = models_directory()
    return models if find_keyword_spotter(models) and (models / "silero_vad.onnx").is_file() else None


needs_models = pytest.mark.skipif(installed_models() is None, reason="voice models not installed")


def tone(seconds, amplitude):
    count = int(16000 * seconds)
    return AudioFrame(array("h", [int(amplitude * math.sin(i * 0.15)) for i in range(count)]).tobytes(), 16000, 0.0)


@needs_models
def test_the_real_spotter_spells_the_phrase_and_stays_quiet_on_silence_and_tones():
    from mira.adapters.sherpa_voice import load_voice_engines

    engines = load_voice_engines(WakeWordConfig("mira"), models=installed_models())
    assert engines.detector is not None, engines.detector_problem
    assert "@mira" in engines.detector.keywords
    for frame in (tone(2.0, 0), tone(2.0, 3000)):
        assert engines.detector.process(frame) == []


@needs_models
def test_the_real_silero_detector_hears_no_speech_in_silence_or_a_steady_tone():
    from mira.adapters.sherpa_voice import load_voice_engines

    vad = load_voice_engines(WakeWordConfig(), models=installed_models(), want_detector=False).vad
    windows = vad.process(tone(1.0, 0)) + vad.process(tone(1.0, 3000))

    assert len(windows) == 2 * 16000 // 512 and not any(w.speech for w in windows)
