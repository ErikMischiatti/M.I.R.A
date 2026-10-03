"""The listening session's segmentation, on synthetic audio with known windows.

Each sample's value encodes its position, so the exact audio an utterance
holds can be checked. Windows are given directly (a scripted VAD), and the
energy detector is tested on its own.
"""

from __future__ import annotations

import math
from array import array

import pytest

from mira.domain.audio import AudioFrame
from mira.domain.listening import ActivationSource
from mira.domain.voice import (
    MAX_WINDOW_SAMPLES,
    EnergyVoiceActivityDetector,
    ListeningSession,
    ListeningSessionState,
    SegmentationPolicy,
    SessionEnd,
    SessionEnded,
    SpeechStarted,
    SpeechWindow,
    WakeWordConfig,
)

RATE = 16000
WINDOW = 512
POLICY = SegmentationPolicy()


def pcm_from(start: int, count: int) -> bytes:
    """Samples whose values are their positions (mod 30000), to check slicing."""
    return array("h", [(start + i) % 30000 for i in range(count)]).tobytes()


class Script:
    """Feeds a session one VAD window per frame, from a speech pattern."""

    def __init__(self, session):
        self.session = session
        self.position = 0
        self.events = []

    def feed(self, pattern: str):
        for flag in pattern:
            frame = AudioFrame(pcm_from(self.position, WINDOW), RATE, 0.0)
            self.position += WINDOW
            window = SpeechWindow(flag == "S", self.position, WINDOW)
            self.events += self.session.feed(frame, [window])
        return self

    def silence(self, seconds: float):
        return self.feed("." * math.ceil(seconds * RATE / WINDOW))

    def ended(self):
        return [e for e in self.events if isinstance(e, SessionEnded)]


def session(policy=POLICY):
    return Script(ListeningSession(ActivationSource.KEYBOARD, utterance_id=1, policy=policy))


# --- Configuration --------------------------------------------------------------------------


def test_the_wake_phrase_is_text_and_validated():
    assert WakeWordConfig("  Nero ").phrase == "nero"
    assert WakeWordConfig("hey mira", 0.8).sensitivity == 0.8
    for bad in ("", "m1ra", "x" * 40):
        with pytest.raises(ValueError):
            WakeWordConfig(bad)
    with pytest.raises(ValueError):
        WakeWordConfig("mira", 1.5)


def test_impossible_policies_are_rejected():
    for bad in (dict(onset_windows=6), dict(end_silence_s=0.05), dict(wait_timeout_s=100), dict(max_utterance_s=1)):
        with pytest.raises(ValueError):
            SegmentationPolicy(**bad)


# --- Onset ----------------------------------------------------------------------------------


def test_a_single_noisy_window_does_not_start_speech():
    script = session().feed("...S...S....S..")

    assert script.session.state is ListeningSessionState.WAITING_FOR_SPEECH
    assert not any(isinstance(e, SpeechStarted) for e in script.events)


def test_sustained_speech_starts_at_its_first_window():
    script = session().feed("......SSS")

    started = [e for e in script.events if isinstance(e, SpeechStarted)]
    assert len(started) == 1 and started[0].at_s == pytest.approx(6 * WINDOW / RATE)
    assert script.session.state is ListeningSessionState.SPEECH_ACTIVE


# --- End ------------------------------------------------------------------------------------


def test_a_natural_pause_does_not_end_the_utterance():
    script = session().feed("SSSSSSSSSS").silence(0.5).feed("SSSSSS")

    assert script.ended() == [] and script.session.state is ListeningSessionState.SPEECH_ACTIVE


def test_sustained_silence_ends_with_exactly_one_utterance():
    script = session().feed("..SSSSSSSSSSSS").silence(POLICY.end_silence_s + 0.1).silence(2)

    ended = script.ended()
    assert len(ended) == 1 and ended[0].reason is SessionEnd.UTTERANCE_READY
    utterance = ended[0].utterance
    assert utterance.activation is ActivationSource.KEYBOARD and utterance.sample_rate == RATE
    assert utterance.speech_started_s == pytest.approx(2 * WINDOW / RATE)
    assert utterance.speech_ended_s == pytest.approx(14 * WINDOW / RATE)
    assert script.session.state is ListeningSessionState.IDLE


def test_the_utterance_holds_pre_roll_speech_and_a_short_tail():
    script = session().silence(1.0)
    speech_start = script.position
    script.feed("S" * 30)
    speech_end = script.position
    script.silence(1.2)

    utterance = script.ended()[0].utterance
    samples = array("h")
    samples.frombytes(utterance.pcm)
    pre_roll = round(POLICY.pre_roll_s * RATE)
    tail = round(POLICY.tail_s * RATE)
    assert samples[0] == (speech_start - pre_roll) % 30000  # starts exactly one pre-roll early
    assert len(samples) == (speech_end + tail) - (speech_start - pre_roll)
    assert all(b == (a + 1) % 30000 for a, b in zip(samples, samples[1:]))  # contiguous, nothing lost


def test_pre_roll_never_reaches_before_the_session_began():
    script = session().feed("SSSS").silence(1.0)

    samples = array("h")
    samples.frombytes(script.ended()[0].utterance.pcm)
    assert samples[0] == 0


# --- Limits ---------------------------------------------------------------------------------


def test_no_speech_times_out_quietly_without_an_utterance():
    script = session().silence(POLICY.wait_timeout_s + 0.1)

    assert [(e.reason, e.utterance) for e in script.ended()] == [(SessionEnd.TIMED_OUT, None)]


def test_waiting_keeps_only_a_bounded_pre_roll():
    script = session(SegmentationPolicy(wait_timeout_s=30.0)).silence(25)

    bound = round(POLICY.pre_roll_s * RATE) + MAX_WINDOW_SAMPLES * (POLICY.onset_span + 1) + WINDOW
    assert script.session.held_samples <= bound


def test_endless_speech_stops_at_the_maximum_and_is_discarded():
    policy = SegmentationPolicy(max_utterance_s=3.0)
    script = session(policy)
    peak = 0
    for _ in range(200):
        script.feed("S")
        peak = max(peak, script.session.held_samples)
        if script.ended():
            break

    assert [(e.reason, e.utterance) for e in script.ended()] == [(SessionEnd.MAX_DURATION, None)]
    assert peak <= 3.0 * RATE + WINDOW


def test_cancel_during_speech_gives_no_utterance_and_stops_listening():
    script = session().feed("SSSSSSS")

    ended = script.session.end(SessionEnd.CANCELLED)

    assert ended == SessionEnded(SessionEnd.CANCELLED)
    assert script.session.end(SessionEnd.CANCELLED) is None
    assert script.ended() == [] and script.session.state is ListeningSessionState.IDLE


@pytest.mark.parametrize("reason", [SessionEnd.MICROPHONE_LOST, SessionEnd.SHUTDOWN])
def test_any_active_state_ends_cleanly_from_outside(reason):
    for prefix in ("..", "SSSS"):
        script = session().feed(prefix)
        assert script.session.end(reason).reason is reason
        assert script.session.held_samples == 0 and not script.session.active


# --- The built-in energy detector ------------------------------------------------------------


def tone(amplitude, count):
    return array("h", [int(amplitude * math.sin(i / 3.0)) for i in range(count)]).tobytes()


def test_the_energy_detector_finds_speech_above_the_noise_floor():
    vad = EnergyVoiceActivityDetector()
    quiet = vad.process(AudioFrame(tone(60, WINDOW * 20), RATE, 0.0))  # the room
    loud = vad.process(AudioFrame(tone(6000, WINDOW * 5), RATE, 0.0))
    after = vad.process(AudioFrame(tone(60, WINDOW * 5), RATE, 0.0))

    assert not any(w.speech for w in quiet)
    assert all(w.speech for w in loud)
    assert not any(w.speech for w in after)
    assert [w.end_sample for w in quiet[:2]] == [WINDOW, 2 * WINDOW]


def test_the_energy_detector_keeps_partial_windows_and_resets():
    vad = EnergyVoiceActivityDetector()
    assert vad.process(AudioFrame(tone(6000, 300), RATE, 0.0)) == []
    assert len(vad.process(AudioFrame(tone(6000, 300), RATE, 0.0))) == 1
    vad.reset()
    assert vad.process(AudioFrame(tone(6000, WINDOW), RATE, 0.0))[0].end_sample == WINDOW


def test_a_steady_noise_becomes_the_floor_within_two_seconds():
    vad = EnergyVoiceActivityDetector()
    vad.process(AudioFrame(tone(60, WINDOW * 20), RATE, 0.0))
    windows = vad.process(AudioFrame(tone(2000, WINDOW * 150), RATE, 0.0))  # a fan starts

    assert windows[0].speech  # at first it stands out
    assert not any(w.speech for w in windows[-80:])  # then it is the room


def test_speech_with_gaps_keeps_the_floor_low():
    vad = EnergyVoiceActivityDetector()
    vad.process(AudioFrame(tone(60, WINDOW * 20), RATE, 0.0))
    windows = []
    for _ in range(12):  # syllables with short gaps, for ~4 s
        windows += vad.process(AudioFrame(tone(6000, WINDOW * 6), RATE, 0.0))
        windows += vad.process(AudioFrame(tone(60, WINDOW * 2), RATE, 0.0))

    loud = [w for i, w in enumerate(windows) if i % 8 < 6]
    assert sum(w.speech for w in loud) / len(loud) > 0.95
