"""Voice policy: opt-in, serialization, cooldown, failure handling and wiring.

Driven by `ManualScheduler` and a recording `AudioOutput`, so nothing renders
on a thread and nothing needs speakers. Synthesis is replaced by a stub that
returns a clip of known length, which makes playback timing exact.
"""

from __future__ import annotations

import pytest

from mira.application.composition import build_application
from mira.application.voice_config import parse_voice_configuration
from mira.core.voice import VoiceController, vocal_intent_for_response
from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent
from mira.domain.models import BrainResponse
from mira.domain.scheduler import ManualScheduler
from mira.domain.vocal_synthesis import synthesize
from mira.domain.vocalization import (
    AudioOutputError,
    PcmClip,
    VocalIntent,
    VocalizationRequest,
    VoiceEngine,
    VoiceIdentity,
)
from mira.messaging.events import EventBus

CLIP_MS = 300


class RecordingOutput:
    """An `AudioOutput` that records what it was asked to play."""

    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail
        self.played: list[PcmClip] = []
        self.closed = 0

    def play(self, clip: PcmClip) -> None:
        if self.fail:
            raise AudioOutputError("speaker on fire")
        self.played.append(clip)

    def close(self) -> None:
        self.closed += 1


class StubSynth:
    """Records requests; returns silence lasting `CLIP_MS`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[VocalizationRequest] = []
        self.fail = fail

    def __call__(self, request: VocalizationRequest, identity: VoiceIdentity) -> PcmClip:
        if self.fail:
            raise RuntimeError("synth broke")
        self.requests.append(request)
        return PcmClip(sample_rate=1000, samples=bytes(2 * CLIP_MS))


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def scheduler() -> ManualScheduler:
    return ManualScheduler()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make(scheduler, output=None, *, enabled=True, synth=None, clock=None, **kwargs):
    synth = synth or StubSynth()
    voice = VoiceController(
        scheduler,
        RecordingOutput() if output is None else output,
        enabled=enabled,
        synthesizer=synth,
        clock=clock or Clock(),
        gap_ms=0,
        **kwargs,
    )
    return voice, synth


def intents(synth: StubSynth) -> list[VocalIntent]:
    return [r.intent for r in synth.requests]


# --- opt-in -------------------------------------------------------------


def test_a_disabled_voice_accepts_nothing_and_renders_nothing(scheduler):
    output = RecordingOutput()
    voice, synth = make(scheduler, output, enabled=False)
    assert voice.enabled is False
    assert voice.vocalize(VocalIntent.HAPPY) is False
    voice.start()
    scheduler.run_all()
    assert scheduler.pending_work() == 0
    assert output.played == [] and synth.requests == []


def test_enabled_without_an_output_is_disabled(scheduler):
    voice = VoiceController(scheduler, None, enabled=True)
    assert voice.enabled is False
    assert voice.vocalize(VocalIntent.HAPPY) is False


# --- serialization and queueing ----------------------------------------


def test_a_request_renders_off_the_serialized_context_then_plays(scheduler):
    output = RecordingOutput()
    voice, synth = make(scheduler, output)
    assert voice.vocalize(VocalIntent.GREETING, 0.9, seed=5) is True
    assert voice.busy and output.played == []  # rendering is submitted work
    scheduler.run_all()
    assert len(output.played) == 1
    assert synth.requests == [VocalizationRequest(VocalIntent.GREETING, 0.9, 5)]
    scheduler.advance(CLIP_MS)
    assert not voice.busy


def test_clips_never_overlap_and_the_queue_is_bounded(scheduler, clock):
    output = RecordingOutput()
    voice, synth = make(scheduler, output, clock=clock, cooldown_ms=0, max_queue=1)

    assert voice.vocalize(VocalIntent.GREETING)
    assert voice.vocalize(VocalIntent.HAPPY)          # waits
    assert not voice.vocalize(VocalIntent.CURIOUS)    # queue full
    assert voice.dropped == 1

    scheduler.run_all()
    assert len(output.played) == 1                    # second waits for the first
    scheduler.advance(CLIP_MS - 1)
    scheduler.run_all()
    assert len(output.played) == 1
    scheduler.advance(1)
    scheduler.run_all()
    assert len(output.played) == 2
    assert intents(synth) == [VocalIntent.GREETING, VocalIntent.HAPPY]


def test_an_intent_already_waiting_is_not_queued_twice(scheduler):
    voice, _ = make(scheduler, cooldown_ms=0, max_queue=3)
    voice.vocalize(VocalIntent.GREETING)
    assert voice.vocalize(VocalIntent.HAPPY)
    assert not voice.vocalize(VocalIntent.HAPPY)
    assert voice.queued == 1


def test_the_cooldown_drops_bursts(scheduler, clock):
    output = RecordingOutput()
    voice, _ = make(scheduler, output, clock=clock, cooldown_ms=400)
    assert voice.vocalize(VocalIntent.ACKNOWLEDGEMENT)
    for _ in range(10):
        clock.now += 0.03
        assert not voice.vocalize(VocalIntent.ACKNOWLEDGEMENT)
    scheduler.run_all()
    scheduler.advance(CLIP_MS)
    clock.now = 1.0
    assert voice.vocalize(VocalIntent.ACKNOWLEDGEMENT)
    scheduler.run_all()
    assert len(output.played) == 2
    assert voice.dropped == 10


# --- failure ------------------------------------------------------------


def test_a_synthesis_failure_is_skipped_and_the_voice_recovers(scheduler, clock, capsys):
    synth = StubSynth(fail=True)
    output = RecordingOutput()
    voice, _ = make(scheduler, output, synth=synth, clock=clock, cooldown_ms=0)
    voice.vocalize(VocalIntent.HAPPY)
    scheduler.run_all()
    assert output.played == [] and not voice.busy
    assert "synthesis failed" in capsys.readouterr().out

    synth.fail = False
    assert voice.vocalize(VocalIntent.HAPPY)
    scheduler.run_all()
    assert len(output.played) == 1


def test_a_playback_failure_is_skipped_and_the_queue_continues(scheduler, capsys):
    output = RecordingOutput(fail=True)
    voice, synth = make(scheduler, output, cooldown_ms=0, max_queue=1)
    voice.vocalize(VocalIntent.HAPPY)
    voice.vocalize(VocalIntent.ERROR)
    scheduler.run_all()
    assert intents(synth) == [VocalIntent.HAPPY, VocalIntent.ERROR]
    assert not voice.busy and voice.played == 0
    assert "playback failed" in capsys.readouterr().out


def test_no_device_is_reported_once_and_drops_the_queue(scheduler, clock, capsys):
    output = RecordingOutput(available=False)
    voice, _ = make(scheduler, output, clock=clock, cooldown_ms=0, max_queue=2)
    voice.vocalize(VocalIntent.HAPPY)
    voice.vocalize(VocalIntent.ERROR)
    scheduler.run_all()
    voice.vocalize(VocalIntent.CURIOUS)
    scheduler.run_all()
    assert output.played == [] and not voice.busy and voice.queued == 0
    assert capsys.readouterr().out.count("no audio output available") == 1


# --- lifecycle ----------------------------------------------------------


def test_shutdown_cancels_playback_drops_results_and_closes_once(scheduler):
    output = RecordingOutput()
    voice, _ = make(scheduler, output, cooldown_ms=0)
    voice.vocalize(VocalIntent.GREETING)
    scheduler.run_all()
    voice.vocalize(VocalIntent.HAPPY)
    assert scheduler.pending_timers() == 1

    voice.shutdown()
    voice.shutdown()
    assert output.closed == 1
    assert scheduler.pending_timers() == 0
    assert voice.vocalize(VocalIntent.HAPPY) is False
    scheduler.advance(10_000)
    scheduler.run_all()
    assert len(output.played) == 1


def test_a_render_finishing_after_shutdown_is_discarded(scheduler):
    output = RecordingOutput()
    voice, _ = make(scheduler, output)
    voice.vocalize(VocalIntent.GREETING)
    voice.shutdown()
    # ManualScheduler drops queued work on its own shutdown; here it has not
    # shut down yet, so the late completion really is delivered.
    scheduler.run_all()
    assert output.played == []


def test_start_greets(scheduler):
    voice, synth = make(scheduler)
    voice.start()
    scheduler.run_all()
    assert intents(synth) == [VocalIntent.GREETING]


# --- mapping and event wiring -------------------------------------------


def response(affect=AffectState.NEUTRAL, intent="time_query") -> BrainResponse:
    return BrainResponse(
        text="...",
        embodiment=EmbodimentIntent(ActivityState.SPEAKING, affect),
        metadata={"intent": intent},
    )


@pytest.mark.parametrize(
    ("brain_response", "failed", "expected"),
    [
        (response(intent="greeting", affect=AffectState.HAPPY), False, VocalIntent.GREETING),
        (response(AffectState.CONFUSED, "unknown"), False, VocalIntent.CONFUSED),
        (response(AffectState.HAPPY), False, VocalIntent.HAPPY),
        (response(), False, VocalIntent.ACKNOWLEDGEMENT),
        (response(AffectState.CONFUSED), True, VocalIntent.ERROR),
        (response(intent="greeting"), True, VocalIntent.ERROR),
    ],
)
def test_responses_map_onto_an_allowlisted_intent(brain_response, failed, expected):
    assert vocal_intent_for_response(brain_response, action_failed=failed) is expected


def test_attached_voice_reacts_to_turns_and_resets_the_failure_flag(scheduler, clock):
    bus = EventBus()
    voice, synth = make(scheduler, clock=clock, cooldown_ms=0, max_queue=3)
    voice.attach(bus)

    bus.emit("action_failed", object())
    bus.emit("response_ready", response(AffectState.CONFUSED))
    bus.emit("response_ready", response())
    bus.emit("response_ready", "not a response")
    scheduler.run_all()
    scheduler.advance(CLIP_MS)
    scheduler.run_all()
    assert intents(synth) == [VocalIntent.ERROR, VocalIntent.ACKNOWLEDGEMENT]


# --- composition ----------------------------------------------------------


def test_the_application_builds_and_runs_a_turn_with_the_voice_disabled(scheduler):
    application = build_application(scheduler=scheduler)
    assert application.voice.enabled is False
    subscribers = application.event_bus._subscribers
    assert all(
        type(getattr(cb, "__self__", None)).__name__ != "VoiceController"
        for callbacks in subscribers.values()
        for cb in callbacks
    )
    application.start()
    application.brain.process_text("ciao")
    scheduler.run_all()
    assert scheduler.pending_work() == 0
    application.shutdown()


def test_opting_in_greets_at_start_and_vocalizes_a_turn(monkeypatch, scheduler):
    monkeypatch.setenv("MIRA_VOICE", "1")
    monkeypatch.setenv("MIRA_VOICE_PITCH", "400")
    output = RecordingOutput()
    application = build_application(scheduler=scheduler, voice_output=output)

    assert application.voice.enabled
    assert application.voice.identity.base_pitch_hz == 400.0
    assert application.event_bus._subscribers["response_ready"][-1].__self__ is application.voice

    application.start()
    scheduler.run_all()
    assert len(output.played) == 1  # the greeting

    application.shutdown()
    assert output.closed == 1


def test_voice_configuration_parsing():
    assert parse_voice_configuration().enabled is False
    for raw in ("1", "TRUE", " yes ", "on"):
        assert parse_voice_configuration(enabled=raw).enabled is True
    for raw in ("0", "", "no", "maybe"):
        assert parse_voice_configuration(enabled=raw).enabled is False

    config = parse_voice_configuration(volume="0.9", pitch="abc", rate="5", brightness="nan")
    assert config.identity.volume == 0.9
    assert config.identity.base_pitch_hz == VoiceIdentity().base_pitch_hz
    assert config.identity.speaking_rate == VoiceIdentity.RATE_RANGE[1]
    assert config.identity.brightness == VoiceIdentity().brightness


def test_voice_engine_parsing_defaults_to_v1():
    assert parse_voice_configuration().engine is VoiceEngine.V1
    for raw in ("v2", "V2", " v2 "):
        assert parse_voice_configuration(engine=raw).engine is VoiceEngine.V2
    for raw in ("v1", "", "v3", "fancy", None):
        assert parse_voice_configuration(engine=raw).engine is VoiceEngine.V1


def test_the_engine_variable_alone_does_not_enable_the_voice(monkeypatch, scheduler):
    """`MIRA_VOICE_ENGINE=v2` without `MIRA_VOICE`: still disabled, still no subscriber."""
    monkeypatch.setenv("MIRA_VOICE_ENGINE", "v2")
    for enabled in (None, "0"):
        if enabled is not None:
            monkeypatch.setenv("MIRA_VOICE", enabled)
        application = build_application(scheduler=scheduler, voice_output=RecordingOutput())
        assert application.voice.enabled is False
        assert all(
            type(getattr(cb, "__self__", None)).__name__ != "VoiceController"
            for callbacks in application.event_bus._subscribers.values()
            for cb in callbacks
        )
        application.shutdown()


@pytest.mark.parametrize(("raw", "engine"), [(None, VoiceEngine.V1), ("v2", VoiceEngine.V2)])
def test_composition_plays_through_the_configured_engine(monkeypatch, scheduler, raw, engine):
    monkeypatch.setenv("MIRA_VOICE", "1")
    if raw is not None:
        monkeypatch.setenv("MIRA_VOICE_ENGINE", raw)
    output = RecordingOutput()
    application = build_application(scheduler=scheduler, voice_output=output)

    assert application.voice.vocalize(VocalIntent.CURIOUS, 0.6, seed=77)
    scheduler.run_all()
    request = VocalizationRequest(VocalIntent.CURIOUS, 0.6, 77)
    assert output.played == [synthesize(request, application.voice.identity, engine)]
    application.shutdown()
