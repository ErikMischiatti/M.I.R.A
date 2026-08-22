"""Test doubles shared by more than one test module.

Imported explicitly (`from doubles import RecordingStateManager`) rather than
injected as fixtures, so a reader can see at the call site exactly what a test is
standing in for.

Each double here replaced two or more near-identical declarations. Where the
variants differed, the superset was adopted, and the assertion that depends on
the difference is named in the class docstring.

Deliberately not here. `FakeFallbackEngine` (`test_llm_intent_engine.py`) and
`EchoResponseBuilder` and `ExplodingIntentEngine` (`test_scheduler.py`) are each
declared once and belong where they are. `FakeClient` is declared twice on
purpose — configurable in `test_llm_intent_engine.py`, a fixed canned reply in
`test_action_contract_consistency.py` — because those are two contracts, and a
merged double would serve neither.

Every double here is covered by `tests/test_shared_fixtures.py`. The variant
counts quoted below are the declarations present at commit 476293a, compared
ignoring annotations and docstrings.
"""

from __future__ import annotations

from mira.actions.action_models import ActionRequest, ActionResult
from mira.core.activity_authority import ActivityAuthority
from mira.core.brain import Brain
from mira.domain.embodiment import ActivityState, EmbodimentIntent
from mira.domain.models import BrainResponse, IntentResult, UserInput
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState
from mira.messaging.events import EventBus


class RecordingEventBus(EventBus):
    """Real bus that also records what was emitted.

    Subclasses the production bus and delegates, so dispatch behaviour is the
    real one. Replaced three variants: two identical but for annotations, and a
    bare non-delegating spy in `test_action_executor.py` exposing `events`.
    Delegation is inert for that use — `ActionExecutor` only calls `emit`, and a
    bus with no subscribers does nothing (`EventBus.emit`,
    `mira/messaging/events.py:12-14`) — so
    the merged double records to `emitted` and matches
    `ActionExecutor.__init__`'s `EventBus | None` parameter more closely.
    """

    def __init__(self) -> None:
        super().__init__()
        self.emitted: list[tuple[str, object]] = []

    def emit(self, event_name: str, payload: object = None) -> None:
        self.emitted.append((event_name, payload))
        super().emit(event_name, payload)


class RecordingStateManager:
    """Records every state written, and answers `get_state`.

    Replaced four variants: two identical, two omitting `states` and/or
    `get_state`. Nothing asserted those omissions, so this superset satisfies all
    four. `get_state` is required by `EmbodiedBehavior._decay_to_neutral`
    (`mira/core/embodied_behavior.py:94`).
    """

    def __init__(self) -> None:
        self.states: list[FaceState] = []
        self.current_state = FaceState.IDLE

    def set_state(self, state: FaceState) -> None:
        self.states.append(state)
        self.current_state = state

    def get_state(self) -> FaceState:
        return self.current_state


class RecordingActivityAuthority(ActivityAuthority):
    """The real authority, committing into a `RecordingStateManager`.

    A real `ActivityAuthority` rather than a stand-in, because the thing under
    test is usually what a component *asked* for, and a fake authority would let
    a wrong request look right. The recording happens one level down, where the
    commit lands.

    `states` and `current_state` are forwarded so assertions read
    `brain.activity.states` instead of reaching through to the manager. Before
    the authority existed these tests said `brain.state_manager.states`; the
    values compared are unchanged.
    """

    def __init__(self) -> None:
        super().__init__(RecordingStateManager())

    @property
    def states(self) -> list[FaceState]:
        return self.state_manager.states

    @property
    def current_state(self) -> FaceState:
        return self.state_manager.current_state

    @current_state.setter
    def current_state(self, state: FaceState) -> None:
        self.state_manager.current_state = state


class StaticIntentEngine:
    """Returns one prepared intent and records the inputs it saw.

    Replaced three variants that differed only in type annotations.
    """

    def __init__(self, intent: IntentResult) -> None:
        self.intent = intent
        self.calls: list[UserInput] = []

    def infer(self, user_input: UserInput) -> IntentResult:
        self.calls.append(user_input)
        return self.intent


class RecordingActionExecutor:
    """Records requests and reports success without executing anything.

    Replaced three variants. Two produced the message `"ok"`, which nothing
    asserted; this keeps the informative `f"executed {name}"`, which
    `test_brain_async_contract.py::test_finalize_executes_action_and_mutates_memory_in_main_flow`
    asserts through the response text.
    """

    def __init__(self) -> None:
        self.requests: list[ActionRequest] = []

    def execute(self, request: ActionRequest) -> ActionResult:
        self.requests.append(request)
        return ActionResult(
            success=True,
            action_name=request.action_name,
            message=f"executed {request.action_name}",
        )


class RecordingResponseBuilder:
    """Records build calls and derives the reply from the action result.

    Replaced two variants. The other returned a constant and recorded nothing;
    this action-aware form is a superset, and `test_brain_async_contract.py`
    asserts both of its outputs (`"executed get_time"` and `"no action"`).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[IntentResult, UserInput, ActionResult | None]] = []

    def build(
        self,
        intent: IntentResult,
        user_input: UserInput,
        action_result: ActionResult | None = None,
    ) -> BrainResponse:
        self.calls.append((intent, user_input, action_result))
        text = action_result.message if action_result is not None else "no action"
        return BrainResponse(
            text=text,
            embodiment=EmbodimentIntent(ActivityState.SPEAKING),
            metadata={"intent": intent.intent},
        )


def make_recording_brain(intent: IntentResult) -> Brain:
    """A `Brain` whose every collaborator records, on a manual scheduler.

    `test_brain_async_contract.py` and `test_scheduler_contracts.py` wrote the
    same construction, so it is shared rather than duplicated — but the merge was
    not a no-op for the second: its local `RecordingResponseBuilder` returned a
    constant `"response"` and recorded nothing, and its `RecordingActionExecutor`
    said `"ok"`. Nothing in that module asserted either, which is what makes
    adopting the recording versions safe; `test_request_ids_are_monotonic_and_
    allocation_claims_latest` is its only test using a brain, and it reads only
    request ids.

    `test_scheduler.py` keeps its own factory: that one takes the scheduler as an
    argument and uses a real `EventBus` and an echoing builder, and its tests
    assert on the echoed text, so it is a different contract.

    `action_executor` is assigned after construction because `Brain.__init__`
    builds its own (`mira/core/brain.py:73`) and takes no parameter for it.
    """
    brain = Brain(
        event_bus=RecordingEventBus(),
        activity=RecordingActivityAuthority(),
        intent_engine=StaticIntentEngine(intent),
        response_builder=RecordingResponseBuilder(),
        scheduler=ManualScheduler(),
    )
    brain.action_executor = RecordingActionExecutor()
    return brain
