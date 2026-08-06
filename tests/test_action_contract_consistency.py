from __future__ import annotations

from mira.actions.action_contracts import ACTION_CONTRACTS
from mira.actions.action_models import ActionContract
from mira.actions.action_registry import ActionRegistry
from mira.cognition.llm_intent_engine import LLMIntentEngine
from mira.cognition.llm_schema import (
    ALLOWED_ACTIONS,
    describe_required_action_params,
    validate_llm_action_for_intent,
)
from mira.core.brain import Brain
from mira.core.events import EventBus
from mira.domain.models import UserInput
from mira.domain.scheduler import ManualScheduler
from mira.core.session_memory import MemoryMessage, SessionMemory
from mira.domain.state import FaceState


class RecordingStateManager:
    def __init__(self):
        self.current_state = FaceState.IDLE

    def set_state(self, new_state):
        self.current_state = new_state


class FakeClient:
    def generate_structured(self, **kwargs):
        return {
            "intent": "unknown",
            "confidence": 0.5,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        }


def build_brain_registry() -> ActionRegistry:
    brain = Brain(
        event_bus=EventBus(),
        state_manager=RecordingStateManager(),
        intent_engine=object(),
        scheduler=ManualScheduler(),
    )
    return brain.action_registry


def test_builtin_contracts_match_actions_registered_by_brain():
    registry = build_brain_registry()

    registered_actions = set(registry.list_actions())
    registered_contracts = set(registry.list_contract_names())
    builtin_contracts = set(ACTION_CONTRACTS)

    assert builtin_contracts == registered_actions
    assert registered_contracts == registered_actions

    for action_name, contract in ACTION_CONTRACTS.items():
        assert registry.get(action_name) is not None
        assert registry.get_contract(action_name) == contract


def test_every_llm_exposed_brain_action_has_contract():
    registry = build_brain_registry()

    exposed_actions = {
        contract.name
        for contract in registry.list_contracts()
        if contract.compatible_intents
    }

    assert exposed_actions == set(registry.list_actions())
    assert set(ALLOWED_ACTIONS) == set(ACTION_CONTRACTS)


def test_no_builtin_contract_points_to_missing_brain_action():
    registry = build_brain_registry()

    missing_actions = [
        contract.name
        for contract in ACTION_CONTRACTS.values()
        if not registry.has(contract.name)
    ]

    assert missing_actions == []


def test_required_contract_params_are_present_in_llm_prompt_metadata():
    registry = build_brain_registry()
    engine = LLMIntentEngine(
        client=FakeClient(),
        action_registry=registry,
    )

    prompt = engine._build_prompt(UserInput(text="ripeti ciao"))
    required_param_descriptions = describe_required_action_params(registry)

    assert required_param_descriptions
    for description in required_param_descriptions:
        assert description in prompt

    for contract in registry.list_contracts():
        for param_name in contract.required_params:
            assert f'"{param_name}"' in prompt


def test_llm_validation_uses_supplied_registry_contracts():
    registry = ActionRegistry()
    registry.register_contract(
        ActionContract(
            name="custom_action",
            compatible_intents=frozenset({"custom_intent"}),
            required_params={"value": str},
        )
    )

    accepted_action, accepted_params, accepted_reason = validate_llm_action_for_intent(
        "custom_intent",
        "custom_action",
        {"value": "ok"},
        registry,
    )
    rejected_action, rejected_params, rejected_reason = validate_llm_action_for_intent(
        "time_query",
        "get_time",
        {},
        registry,
    )

    assert accepted_action == "custom_action"
    assert accepted_params == {"value": "ok"}
    assert accepted_reason is None
    assert rejected_action is None
    assert rejected_params == {}
    assert rejected_reason == "action_unknown"


def test_llm_prompt_includes_session_context_when_memory_is_supplied():
    memory = SessionMemory()
    memory.history.append(MemoryMessage(role="user", text="mi chiamo Erik"))
    engine = LLMIntentEngine(
        client=FakeClient(),
        action_registry=build_brain_registry(),
        session_memory=memory,
    )

    prompt = engine._build_prompt(UserInput(text="come mi chiamo?"))

    assert "Recent conversation context:\nUser: mi chiamo Erik" in prompt
    assert "Current user input:\ncome mi chiamo?" in prompt
