"""The voice pipeline: activation → session → utterance, on one shared capture.

Fake engines read meaning from the samples themselves (a frame of 1000s is
speech, a frame starting with 7777 holds the wake word); a ManualScheduler
runs the worker, so every step is deterministic and no microphone is used.
"""

from __future__ import annotations

from array import array

import pytest

from mira.application.composition import build_application
from mira.core.listening import LISTENING_EVENT, TOGGLE_EVENT, UTTERANCE_EVENT, WAKE_COOLDOWN_S
from mira.domain.audio import AudioFrame, AudioInputInfo, CapturePurpose, CaptureStatus
from mira.domain.embodiment import ActivityState
from mira.domain.listening import ActivationSource, ListeningActivationMode
from mira.domain.scheduler import ManualScheduler
from mira.domain.voice import SegmentationPolicy, SessionEnd, SpeechWindow, WakeWordDetection

RATE = 16000
FRAME = 512
WAKE = 7777


class Backend:
    name = "fake"

    def __init__(self):
        self.starts = 0
        self.on_frame = self.on_failure = None
        self.clock = 0.0

    @property
    def info(self):
        return AudioInputInfo("fake", "Fake Mic", RATE, 1)

    def start(self, on_frame, on_failure):
        self.starts += 1
        self.on_frame, self.on_failure = on_frame, on_failure
        return True

    def stop(self):
        self.on_frame = self.on_failure = None

    @property
    def running(self):
        return self.on_frame is not None

    def send(self, value: int, first: int | None = None):
        samples = array("h", [value] * FRAME)
        if first is not None:
            samples[0] = first
        self.clock += FRAME / RATE
        self.on_frame(AudioFrame(samples.tobytes(), RATE, self.clock))


class ScriptedVad:
    name = "scripted"

    def __init__(self):
        self.position = 0
        self.resets = 0

    def reset(self):
        self.position = 0
        self.resets += 1

    def process(self, frame):
        samples = array("h")
        samples.frombytes(frame.pcm)
        self.position += len(samples)
        return [SpeechWindow(samples[-1] == 1000, self.position, len(samples))]


class FakeDetector:
    name = "fake spotter"

    def __init__(self, fails=False):
        self.fails = fails
        self.frames = 0

    def reset(self):
        pass

    def process(self, frame):
        self.frames += 1
        if self.fails:
            raise RuntimeError("model exploded")
        samples = array("h")
        samples.frombytes(frame.pcm[:2])
        return [WakeWordDetection("mira")] if samples[0] == WAKE else []


@pytest.fixture
def rig():
    backend = Backend()
    scheduler = ManualScheduler()
    app = build_application(scheduler=scheduler, audio_backend=backend)
    app.listening.configure(ListeningActivationMode.KEYBOARD, vad=ScriptedVad())
    statuses, utterances = [], []
    app.event_bus.subscribe(LISTENING_EVENT, statuses.append)
    app.event_bus.subscribe(UTTERANCE_EVENT, utterances.append)

    class Rig:
        pass

    rig = Rig()
    rig.app, rig.backend, rig.scheduler = app, backend, scheduler
    rig.statuses, rig.utterances = statuses, utterances

    def send(*values, first=None):
        for value in values:
            backend.send(value, first)
        scheduler.run_all()

    def speak(seconds):
        send(*[1000] * int(seconds * RATE / FRAME))

    def quiet(seconds):
        send(*[0] * int(seconds * RATE / FRAME))

    rig.send, rig.speak, rig.quiet = send, speak, quiet
    yield rig
    app.shutdown()


def wake_mode(rig, detector=None):
    detector = detector or FakeDetector()
    rig.app.listening.configure(ListeningActivationMode.WAKE_WORD, detector=detector)
    return detector


# --- Keyboard ------------------------------------------------------------------------------


def test_a_keyboard_session_waits_hears_and_delivers_one_utterance(rig):
    rig.app.event_bus.emit(TOGGLE_EVENT, ActivationSource.KEYBOARD)
    assert rig.app.activity.current_intent().activity is ActivityState.LISTENING
    assert rig.statuses[-1].listening and not rig.statuses[-1].speech_active

    rig.quiet(0.5)
    rig.speak(1.0)
    assert rig.statuses[-1].speech_active
    assert rig.app.activity.current_intent().activity is ActivityState.LISTENING  # still the one activity

    rig.quiet(1.5)
    assert len(rig.utterances) == 1
    utterance = rig.utterances[0]
    assert utterance.activation is ActivationSource.KEYBOARD
    assert 1.0 <= utterance.duration_s <= 1.0 + 0.3 + 0.25 + 0.1
    assert not rig.statuses[-1].listening
    assert rig.app.activity.current_intent().activity is ActivityState.IDLE
    assert rig.app.audio.lease_purposes == [] and not rig.backend.running


def test_the_keyboard_cancels_a_running_session_without_an_utterance(rig):
    rig.app.listening.toggle(ActivationSource.KEYBOARD)
    rig.speak(0.6)

    rig.app.listening.toggle(ActivationSource.KEYBOARD)

    assert rig.utterances == [] and rig.app.listening.last_end is SessionEnd.CANCELLED
    assert not rig.backend.running  # nothing else needed the microphone


def test_no_speech_times_out_quietly(rig):
    rig.app.listening.configure(ListeningActivationMode.KEYBOARD, policy=SegmentationPolicy(wait_timeout_s=2.0))
    notices = []
    rig.app.event_bus.subscribe("notice", notices.append)
    rig.app.listening.toggle(ActivationSource.KEYBOARD)

    rig.quiet(2.5)

    assert rig.app.listening.last_end is SessionEnd.TIMED_OUT and rig.utterances == [] and notices == []
    assert not rig.app.listening.listening


def test_each_session_starts_with_a_fresh_vad(rig):
    vad = rig.app.listening.vad
    for _ in range(2):
        rig.app.listening.toggle(ActivationSource.KEYBOARD)
        rig.quiet(0.1)
        rig.app.listening.toggle(ActivationSource.KEYBOARD)

    assert vad.resets == 2


# --- Wake word -------------------------------------------------------------------------------


def test_wake_monitoring_captures_but_never_listens(rig):
    detector = wake_mode(rig)

    rig.quiet(3.0)

    assert rig.app.listening.monitoring and rig.app.audio.lease_purposes == [CapturePurpose.WAKE_MONITORING]
    assert detector.frames > 0
    assert not rig.app.listening.listening and rig.statuses == []
    assert rig.app.activity.current_intent().activity is ActivityState.IDLE


def test_the_wake_word_starts_the_same_session_as_the_keyboard(rig):
    wake_mode(rig)
    rig.quiet(0.5)

    rig.send(0, first=WAKE)
    assert rig.statuses[-1].listening and rig.statuses[-1].source is ActivationSource.WAKE_WORD
    assert rig.app.activity.current_intent().activity is ActivityState.LISTENING

    rig.speak(1.0)
    rig.quiet(1.5)
    assert [u.activation for u in rig.utterances] == [ActivationSource.WAKE_WORD]
    assert rig.app.listening.monitoring  # monitoring goes on after the session


def test_audio_captured_after_the_wake_word_reaches_the_session_but_the_phrase_does_not(rig):
    wake_mode(rig)
    rig.quiet(0.2)
    # The wake frame and the command's first frames arrive before the worker
    # runs: one batch. The command must not be lost, the phrase not included.
    rig.backend.send(0, first=WAKE)
    for _ in range(4):
        rig.backend.send(1000)
    rig.scheduler.run_all()
    rig.speak(0.5)
    rig.quiet(1.5)

    samples = array("h")
    samples.frombytes(rig.utterances[0].pcm)
    assert WAKE not in samples
    speech = int(0.5 * RATE / FRAME) * FRAME + 4 * FRAME
    assert samples.count(1000) == speech


def test_one_phrase_never_starts_two_sessions(rig):
    wake_mode(rig)
    rig.send(0, first=WAKE)
    rig.send(0, first=WAKE)  # heard again inside the session: ignored
    rig.speak(0.5)
    rig.quiet(1.5)
    rig.send(0, first=WAKE)  # still echoing, within the cooldown

    assert rig.app.listening.detections == 1 and len(rig.utterances) == 1
    assert not rig.app.listening.listening

    rig.quiet(WAKE_COOLDOWN_S + 0.1)
    rig.send(0, first=WAKE)
    assert rig.app.listening.listening  # later, a new phrase works


def test_the_keyboard_still_cancels_in_wake_mode_and_monitoring_continues(rig):
    wake_mode(rig)
    rig.send(0, first=WAKE)

    rig.app.listening.toggle(ActivationSource.KEYBOARD)

    assert not rig.app.listening.listening and rig.app.listening.monitoring
    assert rig.backend.running and rig.app.audio.lease_purposes == [CapturePurpose.WAKE_MONITORING]


def test_without_a_detector_wake_mode_says_so_and_the_keyboard_works(rig):
    rig.app.listening.configure(ListeningActivationMode.WAKE_WORD, replace_detector=True)

    assert not rig.app.listening.monitoring
    assert rig.app.listening.monitoring_problem == "no wake-word detector is installed"
    rig.app.listening.toggle(ActivationSource.KEYBOARD)
    assert rig.app.listening.listening


def test_a_failing_detector_never_takes_the_runtime_down(rig, capsys):
    wake_mode(rig, FakeDetector(fails=True))

    rig.quiet(0.5)

    assert "model exploded" in capsys.readouterr().err
    assert rig.app.listening.monitoring and not rig.app.listening.listening


# --- Shared capture --------------------------------------------------------------------------


def test_monitoring_and_a_session_share_one_capture(rig):
    wake_mode(rig)
    rig.send(0, first=WAKE)

    assert rig.backend.starts == 1
    assert sorted(rig.app.audio.lease_purposes) == sorted([CapturePurpose.WAKE_MONITORING, CapturePurpose.CONVERSATION])

    rig.app.listening.configure(ListeningActivationMode.KEYBOARD)
    assert rig.app.listening.listening and rig.backend.running  # the session keeps it

    rig.app.listening.toggle(ActivationSource.KEYBOARD)
    assert not rig.backend.running and rig.backend.starts == 1


def test_losing_the_microphone_ends_the_session_and_monitoring(rig):
    wake_mode(rig)
    rig.send(0, first=WAKE)

    rig.backend.on_failure("the microphone was disconnected")

    assert not rig.app.listening.listening and not rig.app.listening.monitoring
    assert rig.app.listening.monitoring_problem == "the microphone was disconnected"
    assert rig.app.audio.status is CaptureStatus.FAILED


def test_resegmented_frames_keep_capture_order_with_one_reset():
    from mira.core.voice_processing import VoiceJob, VoiceProcessor

    seen = []

    class Vad:
        name = "order"

        def reset(self):
            seen.append("reset")

        def process(self, frame):
            seen.append(frame.timestamp_s)
            return []

    scheduler = ManualScheduler()
    processor = VoiceProcessor(scheduler, detector=None, vad=Vad(), on_results=lambda batch: None)
    frame = lambda t: AudioFrame(b"\0\0" * 4, RATE, t)
    processor.submit(VoiceJob(frame(0.0), segment=True))   # in flight
    processor.submit(VoiceJob(frame(3.0), detect=True))    # queued for detection only
    processor.resegment([frame(1.0), frame(2.0)])          # after the activating frame
    scheduler.run_all()

    assert seen == [0.0, "reset", 1.0, 2.0, 3.0]


def test_frames_queued_behind_the_wake_word_join_the_session(rig):
    wake_mode(rig)
    rig.backend.send(0, first=WAKE)  # dispatched alone: the worker was idle
    for _ in range(4):
        rig.backend.send(1000)       # queued, for detection only, before the result
    rig.scheduler.run_all()
    rig.quiet(1.5)

    samples = array("h")
    samples.frombytes(rig.utterances[0].pcm)
    assert samples.count(1000) == 4 * FRAME and WAKE not in samples


def test_a_worker_that_falls_behind_drops_the_oldest_frames(rig):
    processor = rig.app.listening.processor
    wake_mode(rig)
    for _ in range(200):  # the worker never runs
        rig.backend.send(0)

    assert processor.pending <= 64 and processor.dropped > 0
    rig.scheduler.run_all()
    assert processor.pending == 0


def test_shutdown_ends_everything_and_releases_the_microphone(rig):
    wake_mode(rig)
    rig.send(0, first=WAKE)
    rig.speak(0.3)

    rig.app.shutdown()

    assert rig.utterances == [] and rig.app.listening.last_end is SessionEnd.SHUTDOWN
    assert not rig.backend.running and rig.app.audio.lease_purposes == []


def test_frames_wait_for_a_full_batch_and_keep_their_order():
    from mira.core.voice_processing import VoiceJob, VoiceProcessor

    seen = []

    class Vad:
        name = "order"

        def reset(self):
            pass

        def process(self, frame):
            seen.append(frame.timestamp_s)
            return []

    scheduler = ManualScheduler()
    processor = VoiceProcessor(scheduler, detector=None, vad=Vad(), on_results=lambda batch: None)
    processor.min_batch = 4
    for t in range(3):
        processor.submit(VoiceJob(AudioFrame(b"\0\0", RATE, float(t)), segment=True))
    scheduler.run_all()
    assert seen == [] and processor.pending == 3  # not yet a batch

    processor.submit(VoiceJob(AudioFrame(b"\0\0", RATE, 3.0), segment=True))
    scheduler.run_all()
    assert seen == [0.0, 1.0, 2.0, 3.0]
