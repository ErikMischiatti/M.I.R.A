"""Tests for the scheduler port and its deterministic implementation.

The point of the port is that the turn lifecycle becomes testable through its
public surface: no threads, no wall-clock sleeps, and no reaching into private
methods to simulate what the scheduler would have done.
"""

from __future__ import annotations

import inspect

import pytest

from doubles import RecordingActionExecutor, RecordingActivityAuthority, StaticIntentEngine

from mira.core.brain import Brain
from mira.core.embodied_behavior import EmbodiedBehavior
from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent
from mira.domain.embodiment_compatibility import resolve_face_state
from mira.domain.models import BrainResponse, IntentResult, UserInput
from mira.domain.scheduler import (
    ManualScheduler,
    ManualTimer,
    Scheduler,
    TimerHandle,
)
from mira.domain.state import FaceState
from mira.messaging.events import EventBus


class ExplodingIntentEngine:
    def infer(self, user_input: UserInput) -> IntentResult:
        raise RuntimeError("inference exploded")


class EchoResponseBuilder:
    def build(self, intent, user_input, action_result=None) -> BrainResponse:
        return BrainResponse(
            text=f"answer:{user_input.text}",
            embodiment=EmbodimentIntent(ActivityState.SPEAKING),
            metadata={"intent": intent.intent},
        )


def make_brain(scheduler: ManualScheduler, engine=None) -> Brain:
    """Local on purpose: this one takes the scheduler and echoes the input.

    `doubles.make_recording_brain` is the shared factory, and it is a different
    contract — a fixed `ManualScheduler` and a recording builder. The tests here
    drive the clock themselves and assert on echoed text.
    """
    brain = Brain(
        event_bus=EventBus(),
        activity=RecordingActivityAuthority(),
        intent_engine=engine or StaticIntentEngine(IntentResult(intent="time_query")),
        response_builder=EchoResponseBuilder(),
        scheduler=scheduler,
    )
    brain.action_executor = RecordingActionExecutor()
    return brain


# --- port conformance ---------------------------------------------------


def signature_of(obj, name: str) -> list[str]:
    """Parameter names of a bound member, excluding self."""
    params = list(inspect.signature(getattr(obj, name)).parameters)
    return [p for p in params if p != "self"]


def test_manual_scheduler_conforms_to_the_port():
    """No type checker is configured, so conformance is asserted here.

    `runtime_checkable` only compares member names, so parameter names are
    compared too — that is what catches an arity or rename mismatch.
    """
    scheduler = ManualScheduler()
    assert isinstance(scheduler, Scheduler)
    assert isinstance(scheduler.call_later(1, lambda: None), TimerHandle)

    for name in ("call_later", "submit"):
        assert signature_of(scheduler, name) == signature_of(Scheduler, name), name


def test_qt_scheduler_conforms_to_the_port():
    """Structural conformance only; behaviour lives in the adapter tests."""
    pytest.importorskip("PySide6")
    from mira.adapters.qt_scheduler import QtScheduler, _QtTimerHandle

    assert isinstance(QtScheduler.__new__(QtScheduler), Scheduler)
    assert isinstance(_QtTimerHandle.__new__(_QtTimerHandle), TimerHandle)

    for name in ("call_later", "submit"):
        assert signature_of(QtScheduler, name) == signature_of(Scheduler, name), name
    for name in ("cancel", "is_pending"):
        assert signature_of(_QtTimerHandle, name) == signature_of(TimerHandle, name), name


def test_manual_timer_conforms_to_the_handle_port():
    timer = ManualTimer(0, lambda: None)
    assert isinstance(timer, TimerHandle)
    for name in ("cancel", "is_pending"):
        assert signature_of(timer, name) == signature_of(TimerHandle, name), name


# --- ManualScheduler itself ---------------------------------------------


def test_submitted_work_does_not_run_until_asked():
    scheduler = ManualScheduler()
    ran: list[str] = []

    scheduler.submit(lambda: ran.append("work") or "result", ran.append)

    assert scheduler.pending_work() == 1
    assert ran == []

    assert scheduler.run_next() is True
    assert ran == ["work", "result"]
    assert scheduler.pending_work() == 0
    assert scheduler.run_next() is False


def test_run_all_drains_work_queued_while_running():
    scheduler = ManualScheduler()
    order: list[str] = []

    def outer() -> str:
        order.append("outer")
        scheduler.submit(lambda: order.append("inner") or "inner", lambda _: None)
        return "outer"

    scheduler.submit(outer, lambda _: None)

    assert scheduler.run_all() == 2
    assert order == ["outer", "inner"]


def test_timers_fire_only_when_logical_time_reaches_them():
    scheduler = ManualScheduler()
    fired: list[str] = []

    scheduler.call_later(500, lambda: fired.append("first"))
    scheduler.call_later(1500, lambda: fired.append("second"))

    assert scheduler.pending_timers() == 2
    assert scheduler.advance(499) == 0
    assert fired == []

    assert scheduler.advance(1) == 1
    assert fired == ["first"]

    assert scheduler.advance(1000) == 1
    assert fired == ["first", "second"]
    assert scheduler.pending_timers() == 0


def test_timers_fire_in_due_order_regardless_of_scheduling_order():
    scheduler = ManualScheduler()
    fired: list[int] = []

    scheduler.call_later(900, lambda: fired.append(900))
    scheduler.call_later(100, lambda: fired.append(100))
    scheduler.call_later(500, lambda: fired.append(500))

    scheduler.advance(1000)

    assert fired == [100, 500, 900]


def test_cancelled_timer_does_not_fire():
    scheduler = ManualScheduler()
    fired: list[str] = []

    handle = scheduler.call_later(100, lambda: fired.append("nope"))
    assert handle.is_pending() is True

    handle.cancel()
    assert handle.is_pending() is False

    assert scheduler.advance(1000) == 0
    assert fired == []
    assert scheduler.pending_timers() == 0


def test_advance_raises_instead_of_hanging_on_a_self_rescheduling_timer():
    """A pathological callback must fail diagnosably, not hang the suite."""
    scheduler = ManualScheduler()

    def tick() -> None:
        scheduler.call_later(0, tick)

    scheduler.call_later(0, tick)

    with pytest.raises(RuntimeError, match="rescheduling itself"):
        scheduler.advance(0)


def test_interpretation_never_raises_so_the_scheduler_contract_holds():
    """The port has no error channel, so Brain must absorb interpretation failures."""
    scheduler = ManualScheduler()
    brain = make_brain(scheduler, engine=ExplodingIntentEngine())

    result = brain._interpret_for_commit(1, UserInput(text="boom"))

    assert result.error_response is not None
    assert result.intent.intent == "processing_error"
    assert result.action_request is None


def test_cancel_is_idempotent_and_safe_after_firing():
    scheduler = ManualScheduler()
    handle = scheduler.call_later(10, lambda: None)
    scheduler.advance(10)

    assert handle.is_pending() is False
    handle.cancel()
    handle.cancel()
    assert handle.is_pending() is False


# --- the turn lifecycle through the port -------------------------------


def test_full_turn_runs_through_the_scheduler():
    scheduler = ManualScheduler()
    brain = make_brain(scheduler)
    responses: list[BrainResponse] = []

    brain.process_text_async("che ore sono", responses.append)

    # Nothing has been interpreted yet: the listening delay is still pending.
    assert responses == []
    assert brain.intent_engine.calls == []
    assert scheduler.pending_timers() == 1

    scheduler.advance(brain.listening_delay_ms)
    assert scheduler.pending_work() == 1
    assert responses == []

    scheduler.run_all()
    assert [r.text for r in responses] == ["answer:che ore sono"]
    assert brain.activity.states == [
        FaceState.LISTENING,
        FaceState.THINKING,
        FaceState.SPEAKING,
    ]


def test_superseded_turn_is_dropped_before_interpretation():
    """Second submission during the listening delay invalidates the first."""
    scheduler = ManualScheduler()
    brain = make_brain(scheduler)
    responses: list[BrainResponse] = []

    brain.process_text_async("first", responses.append)
    brain.process_text_async("second", responses.append)

    scheduler.advance(brain.listening_delay_ms)

    # Both listening timers fired, but only the surviving turn scheduled work.
    assert scheduler.pending_work() == 1
    assert brain.intent_engine.calls == []

    scheduler.run_all()

    # So only the surviving turn was ever interpreted.
    assert [call.text for call in brain.intent_engine.calls] == ["second"]
    assert [r.text for r in responses] == ["answer:second"]


def test_stale_result_is_dropped_at_commit_without_threads():
    """Interpret turn one, supersede it, then deliver it. It must not commit."""
    scheduler = ManualScheduler()
    brain = make_brain(scheduler)
    responses: list[BrainResponse] = []

    brain.process_text_async("first", responses.append)
    scheduler.advance(brain.listening_delay_ms)
    assert scheduler.pending_work() == 1

    # A newer turn arrives while the first is mid-flight.
    brain.process_text_async("second", responses.append)

    # Deliver the first turn's completion now that it is stale.
    scheduler.run_next()

    assert responses == []
    assert brain.action_executor.requests == []
    assert brain.memory.last_intent is None

    # The newer turn still completes normally.
    scheduler.advance(brain.listening_delay_ms)
    scheduler.run_all()
    assert [r.text for r in responses] == ["answer:second"]


def test_interpretation_failure_still_reports_to_the_user():
    scheduler = ManualScheduler()
    brain = make_brain(scheduler, engine=ExplodingIntentEngine())
    responses: list[BrainResponse] = []

    brain.process_text_async("boom", responses.append)
    scheduler.advance(brain.listening_delay_ms)
    scheduler.run_all()

    assert len(responses) == 1
    assert resolve_face_state(responses[0].embodiment) is FaceState.CONFUSED
    assert "errore" in responses[0].text.lower()
    assert brain.activity.states[-1] is FaceState.CONFUSED


# --- decay through the port --------------------------------------------


def test_decay_fires_after_its_delay():
    scheduler = ManualScheduler()
    activity = RecordingActivityAuthority()
    behavior = EmbodiedBehavior(EventBus(), activity, scheduler=scheduler)

    activity.current_state = FaceState.SPEAKING
    behavior.on_response_ready(
        BrainResponse(text="hi", embodiment=EmbodimentIntent(ActivityState.SPEAKING))
    )

    assert scheduler.pending_timers() == 1
    scheduler.advance(1799)
    assert activity.states == []

    scheduler.advance(1)
    assert activity.states == [FaceState.IDLE]


def test_new_response_cancels_the_previous_decay():
    scheduler = ManualScheduler()
    activity = RecordingActivityAuthority()
    behavior = EmbodiedBehavior(EventBus(), activity, scheduler=scheduler)

    behavior.on_response_ready(
        BrainResponse(
            text="a",
            embodiment=EmbodimentIntent(ActivityState.SPEAKING, AffectState.HAPPY),
        )
    )
    first_handle = behavior._decay_handle

    behavior.on_response_ready(
        BrainResponse(text="b", embodiment=EmbodimentIntent(ActivityState.SPEAKING))
    )

    assert first_handle is not None and first_handle.is_pending() is False
    assert scheduler.pending_timers() == 1

    # Only the second decay remains, on the second response's schedule.
    activity.current_state = FaceState.SPEAKING
    scheduler.advance(1800)
    assert activity.states == [FaceState.IDLE]
