from __future__ import annotations

from mira.actions.action_models import ActionRequest, ActionResult
from mira.core.brain import Brain, BrainComputationResult
from mira.core.events import EventBus
from mira.domain.models import BrainResponse, IntentResult, UserInput
from mira.domain.state import FaceState


class StaticIntentEngine:
    def __init__(self, intent: IntentResult):
        self.intent = intent
        self.calls = []

    def infer(self, user_input: UserInput) -> IntentResult:
        self.calls.append(user_input)
        return self.intent


class RecordingResponseBuilder:
    def __init__(self):
        self.calls = []

    def build(self, intent, user_input, action_result=None):
        self.calls.append((intent, user_input, action_result))
        text = action_result.message if action_result is not None else "no action"
        return BrainResponse(
            text=text,
            face_state=FaceState.SPEAKING,
            metadata={"intent": intent.intent},
        )


class RecordingEventBus(EventBus):
    def __init__(self):
        super().__init__()
        self.emitted = []

    def emit(self, event_name, payload=None):
        self.emitted.append((event_name, payload))
        super().emit(event_name, payload)


class RecordingStateManager:
    def __init__(self):
        self.states = []
        self.current_state = FaceState.IDLE

    def set_state(self, new_state):
        self.states.append(new_state)
        self.current_state = new_state


class RecordingActionExecutor:
    def __init__(self):
        self.requests = []

    def execute(self, request: ActionRequest):
        self.requests.append(request)
        return ActionResult(
            success=True,
            action_name=request.action_name,
            message=f"executed {request.action_name}",
        )


def make_brain(intent: IntentResult):
    brain = Brain(
        event_bus=RecordingEventBus(),
        state_manager=RecordingStateManager(),
        intent_engine=StaticIntentEngine(intent),
        response_builder=RecordingResponseBuilder(),
    )
    brain.action_executor = RecordingActionExecutor()
    return brain


def test_worker_compute_only_infers_intent_and_builds_action_request():
    intent = IntentResult(
        intent="time_query",
        confidence=0.9,
        entities={"llm_action_name": "get_time"},
    )
    brain = make_brain(intent)
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
    assert brain.state_manager.states == []


def test_finalize_executes_action_and_mutates_memory_in_main_flow():
    intent = IntentResult(
        intent="time_query",
        confidence=0.9,
        entities={"llm_action_name": "get_time"},
    )
    brain = make_brain(intent)
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
    brain = make_brain(intent)
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
    assert brain.state_manager.states == []


def test_current_async_result_finalization_emits_response_and_invokes_callback():
    intent = IntentResult(intent="greeting", confidence=0.9)
    brain = make_brain(intent)
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
    assert brain.state_manager.states == [FaceState.SPEAKING]


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
    brain = make_brain(intent)

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
    brain = make_brain(intent)

    request = brain.build_action_request(intent)

    assert request is None


def test_rule_based_action_fallback_still_builds_request_without_llm_failure_marker():
    intent = IntentResult(
        intent="open_url_request",
        confidence=0.9,
        entities={"url": "example.com"},
    )
    brain = make_brain(intent)

    request = brain.build_action_request(intent)

    assert request == ActionRequest(
        action_name="open_url",
        parameters={"url": "example.com"},
    )


def test_rule_based_project_path_intent_builds_action_request():
    intent = IntentResult(intent="project_path_query", confidence=0.9)
    brain = make_brain(intent)

    request = brain.build_action_request(intent)

    assert request == ActionRequest(action_name="get_project_path")
