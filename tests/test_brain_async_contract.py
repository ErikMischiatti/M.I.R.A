"""Tests for the async turn's interpretation and commit phases.

Every collaborator on the brain built here is a recording double from
`tests/doubles.py`, so the assertions below read four recorders:
`brain.event_bus.emitted`, `brain.activity.states`,
`brain.action_executor.requests` and `brain.response_builder.calls`. The reply
text comes from a double too — `"executed <action>"` when an action ran,
`"no action"` when none did.
"""

from __future__ import annotations

from doubles import make_recording_brain

from mira.actions.action_models import ActionRequest
from mira.core.brain import BrainComputationResult
from mira.domain.models import IntentResult, UserInput
from mira.domain.state import FaceState


def test_worker_compute_only_infers_intent_and_builds_action_request():
    intent = IntentResult(
        intent="time_query",
        confidence=0.9,
        entities={"llm_action_name": "get_time"},
    )
    brain = make_recording_brain(intent)
    user_input = UserInput(text="che ore sono")

    result = brain._compute_response_for_worker(7, user_input)

    assert result.request_id == 7
    assert result.user_input is user_input
    assert result.intent is intent
    assert result.action_request == ActionRequest(
        action_name="get_time",
        parameters={},
        source_intent="time_query",
    )
    assert brain.action_executor.requests == []
    assert brain.memory.history == []
    assert brain.memory.last_intent is None
    assert brain.event_bus.emitted == []
    assert brain.activity.states == []


def test_finalize_executes_action_and_mutates_memory_in_main_flow():
    intent = IntentResult(
        intent="time_query",
        confidence=0.9,
        entities={"llm_action_name": "get_time"},
    )
    brain = make_recording_brain(intent)
    user_input = UserInput(text="che ore sono")
    request = ActionRequest("get_time", source_intent="time_query")
    result = BrainComputationResult(
        request_id=1,
        user_input=user_input,
        intent=intent,
        action_request=request,
    )

    response = brain._finalize_computation_result(result)

    assert response.text == "executed get_time"
    assert brain.memory.last_intent is intent
    assert [message.role for message in brain.memory.history] == ["assistant"]
    assert brain.action_executor.requests == [request]
    assert [event for event, _ in brain.event_bus.emitted] == ["intent_inferred"]


def test_stale_async_result_does_not_update_memory_events_actions_or_callback():
    intent = IntentResult(
        intent="time_query",
        confidence=0.9,
        entities={"llm_action_name": "get_time"},
    )
    brain = make_recording_brain(intent)
    brain._latest_request_id = 2
    callbacks = []
    result = BrainComputationResult(
        request_id=1,
        user_input=UserInput(text="old request"),
        intent=intent,
        action_request=ActionRequest("get_time"),
    )

    brain._finalize_async_response(result, callbacks.append)

    assert callbacks == []
    assert brain.memory.history == []
    assert brain.memory.last_intent is None
    assert brain.action_executor.requests == []
    assert brain.event_bus.emitted == []
    assert brain.activity.states == []


def test_current_async_result_finalization_emits_response_and_invokes_callback():
    intent = IntentResult(intent="greeting", confidence=0.9)
    brain = make_recording_brain(intent)
    brain._latest_request_id = 3
    callbacks = []
    result = BrainComputationResult(
        request_id=3,
        user_input=UserInput(text="ciao"),
        intent=intent,
        action_request=None,
    )

    brain._finalize_async_response(result, callbacks.append)

    assert len(callbacks) == 1
    assert callbacks[0].text == "no action"
    assert brain.memory.last_intent is intent
    assert [event for event, _ in brain.event_bus.emitted] == [
        "intent_inferred",
        "response_ready",
    ]
    assert brain.activity.states == [FaceState.SPEAKING]


def test_invalid_llm_action_marker_prevents_rule_based_action_fallback():
    intent = IntentResult(
        intent="open_url_request",
        confidence=0.8,
        entities={
            "url": "example.com",
            "llm_action_name": None,
            "llm_action_validation_failed": True,
            "llm_action_validation_reason": "intent_action_mismatch",
        },
    )
    brain = make_recording_brain(intent)

    request = brain.build_action_request(intent)

    assert request is None


def test_low_confidence_llm_action_suppression_prevents_rule_based_action_fallback():
    intent = IntentResult(
        intent="time_query",
        confidence=0.4,
        entities={
            "llm_action_name": None,
            "action_suppressed_reason": "low_confidence",
            "action_min_confidence": 0.65,
        },
    )
    brain = make_recording_brain(intent)

    request = brain.build_action_request(intent)

    assert request is None


def test_rule_based_action_fallback_still_builds_request_without_llm_failure_marker():
    intent = IntentResult(
        intent="open_url_request",
        confidence=0.9,
        entities={"url": "example.com"},
    )
    brain = make_recording_brain(intent)

    request = brain.build_action_request(intent)

    assert request == ActionRequest(
        action_name="open_url",
        parameters={"url": "example.com"},
    )


def test_rule_based_project_path_intent_builds_action_request():
    intent = IntentResult(intent="project_path_query", confidence=0.9)
    brain = make_recording_brain(intent)

    request = brain.build_action_request(intent)

    assert request == ActionRequest(action_name="get_project_path")
