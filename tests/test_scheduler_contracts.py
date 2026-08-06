"""Characterization tests for timing contracts held nowhere else.

Written and run green against the pre-port Qt implementation, then re-run
unchanged afterwards; only the construction helpers gained a `scheduler=`
argument, so the current file no longer runs on the pre-port tree.

It covers only the two contracts no other file pins:

- request-id monotonicity, and that allocating a new id invalidates in-flight ones;
- the expression decay delay table and its three re-entry guards.

The staleness gates, interpretation-phase purity and commit ordering are pinned
directly by `test_brain_async_contract.py` — which does run on both trees — and
again, more strongly, through the production path by `test_scheduler.py`. They
were duplicated here and removed: a third copy carried no independent signal,
since one mutation defeating a gate fails every copy at once.
"""

from __future__ import annotations

from mira.actions.action_models import ActionRequest
from mira.core.brain import Brain
from mira.core.embodied_behavior import EmbodiedBehavior
from mira.messaging.events import EventBus
from mira.domain.models import BrainResponse, IntentResult, UserInput
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState


class RecordingEventBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.emitted: list[tuple[str, object]] = []

    def emit(self, event_name: str, payload: object = None) -> None:
        self.emitted.append((event_name, payload))
        super().emit(event_name, payload)


class RecordingStateManager:
    def __init__(self) -> None:
        self.states: list[FaceState] = []
        self.current_state = FaceState.IDLE

    def set_state(self, state: FaceState) -> None:
        self.states.append(state)
        self.current_state = state

    def get_state(self) -> FaceState:
        return self.current_state


class StaticIntentEngine:
    def __init__(self, intent: IntentResult) -> None:
        self.intent = intent
        self.calls: list[UserInput] = []

    def infer(self, user_input: UserInput) -> IntentResult:
        self.calls.append(user_input)
        return self.intent


class RecordingResponseBuilder:
    def build(self, intent, user_input, action_result=None) -> BrainResponse:
        return BrainResponse(
            text="response",
            face_state=FaceState.SPEAKING,
            metadata={"intent": intent.intent},
        )


class RecordingActionExecutor:
    def __init__(self) -> None:
        self.requests: list[ActionRequest] = []

    def execute(self, request: ActionRequest):
        self.requests.append(request)
        from mira.actions.action_models import ActionResult

        return ActionResult(success=True, action_name=request.action_name, message="ok")


def make_brain(intent: IntentResult) -> Brain:
    """Construct a Brain with recording collaborators and a manual scheduler.

    The only place the scheduler port changed this file.
    """
    brain = Brain(
        event_bus=RecordingEventBus(),
        state_manager=RecordingStateManager(),
        intent_engine=StaticIntentEngine(intent),
        response_builder=RecordingResponseBuilder(),
        scheduler=ManualScheduler(),
    )
    brain.action_executor = RecordingActionExecutor()
    return brain


def make_embodied_behavior() -> EmbodiedBehavior:
    return EmbodiedBehavior(
        RecordingEventBus(), RecordingStateManager(), scheduler=ManualScheduler()
    )


# --- request identity ---------------------------------------------------


def test_request_ids_are_monotonic_and_allocation_claims_latest():
    brain = make_brain(IntentResult(intent="time_query"))
    first = brain._new_request_id()
    second = brain._new_request_id()

    assert second > first
    assert brain._latest_request_id == second


# --- decay behaviour ----------------------------------------------------


def test_decay_delay_table_is_unchanged():
    behavior = make_embodied_behavior()
    assert behavior._get_decay_delay(FaceState.HAPPY) == 2200
    assert behavior._get_decay_delay(FaceState.CONFUSED) == 1600
    assert behavior._get_decay_delay(FaceState.SPEAKING) == 1800
    assert behavior._get_decay_delay(FaceState.THINKING) == 1200
    assert behavior._get_decay_delay(FaceState.IDLE) == 1500


def test_decay_only_fires_while_still_in_the_held_state():
    behavior = make_embodied_behavior()
    behavior.last_response_state = FaceState.HAPPY
    behavior.state_manager.current_state = FaceState.THINKING

    behavior._decay_to_neutral()

    assert behavior.state_manager.states == []
    assert behavior.decay_active is False


def test_decay_returns_to_listening_when_input_is_engaged():
    behavior = make_embodied_behavior()
    behavior.last_response_state = FaceState.SPEAKING
    behavior.state_manager.current_state = FaceState.SPEAKING
    behavior.input_has_focus = True

    behavior._decay_to_neutral()

    assert behavior.state_manager.states == [FaceState.LISTENING]


def test_decay_returns_to_idle_when_input_is_not_engaged():
    behavior = make_embodied_behavior()
    behavior.last_response_state = FaceState.SPEAKING
    behavior.state_manager.current_state = FaceState.SPEAKING
    behavior.input_has_focus = False
    behavior.input_has_text = False

    behavior._decay_to_neutral()

    assert behavior.state_manager.states == [FaceState.IDLE]
