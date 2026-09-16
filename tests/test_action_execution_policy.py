from __future__ import annotations

import pytest

from doubles import RecordingActivityAuthority, RecordingEventBus

from mira.actions.action_contracts import ACTION_CONTRACTS
from mira.actions.action_executor import ActionExecutor
from mira.actions.action_models import (
    ActionContract,
    ActionEffect,
    ActionRequest,
    ActionResult,
)
from mira.actions.action_registry import ActionRegistry
from mira.actions.execution_policy import (
    ActionExecutionPolicy,
    ExecutionPolicyOutcome,
)
from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.core.brain import Brain
from mira.domain.models import IntentResult
from mira.domain.scheduler import ManualScheduler
from mira.messaging.events import EventBus


def register_recording_action(
    *,
    effect: ActionEffect,
    requires_confirmation: bool = False,
) -> tuple[ActionExecutor, list[dict]]:
    registry = ActionRegistry()
    calls: list[dict] = []

    def handler(parameters: dict) -> ActionResult:
        calls.append(parameters)
        return ActionResult(True, "example", "executed")

    registry.register(
        "example",
        handler,
        contract=ActionContract(
            name="example",
            effect=effect,
            requires_confirmation=requires_confirmation,
        ),
    )
    return ActionExecutor(registry), calls


def test_safe_action_executes():
    executor, calls = register_recording_action(effect=ActionEffect.READ_ONLY)

    result = executor.execute(ActionRequest("example", {"value": 1}))

    assert result.success is True
    assert calls == [{"value": 1}]


def test_explicitly_allowed_side_effect_executes():
    executor, calls = register_recording_action(effect=ActionEffect.SIDE_EFFECT)

    result = executor.execute(ActionRequest("example"))

    assert result.success is True
    assert calls == [{}]


@pytest.mark.parametrize(
    "request_requires_confirmation,contract_requires_confirmation",
    [(False, True), (True, False)],
)
def test_confirmation_required_action_does_not_execute(
    request_requires_confirmation: bool,
    contract_requires_confirmation: bool,
):
    executor, calls = register_recording_action(
        effect=ActionEffect.SIDE_EFFECT,
        requires_confirmation=contract_requires_confirmation,
    )

    result = executor.execute(
        ActionRequest(
            "example",
            requires_confirmation=request_requires_confirmation,
        )
    )

    assert result.success is False
    assert result.data == {
        "execution_policy": "confirmation_required",
        "reason": "confirmation_required",
        "requires_confirmation": True,
    }
    assert calls == []


def test_denied_action_does_not_call_handler():
    executor, calls = register_recording_action(effect=ActionEffect.DENIED)

    result = executor.execute(ActionRequest("example"))

    assert result.success is False
    assert result.data == {
        "execution_policy": "denied",
        "reason": "action_denied",
    }
    assert calls == []


def test_blocked_action_emits_failure_without_started_event():
    registry = ActionRegistry()
    event_bus = RecordingEventBus()
    registry.register(
        "blocked",
        lambda parameters: ActionResult(True, "blocked", "unexpected"),
        contract=ActionContract(
            name="blocked",
            effect=ActionEffect.SIDE_EFFECT,
            requires_confirmation=True,
        ),
    )

    result = ActionExecutor(registry, event_bus).execute(ActionRequest("blocked"))

    assert result.success is False
    assert [name for name, _payload in event_bus.emitted] == ["action_failed"]


def test_unknown_action_is_denied_without_execution():
    result = ActionExecutor(ActionRegistry()).execute(ActionRequest("missing"))

    assert result.success is False
    assert result.data == {
        "execution_policy": "denied",
        "reason": "action_unknown",
    }


def test_registered_handler_without_contract_is_denied_by_default():
    registry = ActionRegistry()
    calls: list[dict] = []
    registry.register(
        "unclassified",
        lambda parameters: calls.append(parameters)
        or ActionResult(True, "unclassified", "executed"),
    )

    result = ActionExecutor(registry).execute(ActionRequest("unclassified"))

    assert result.data == {
        "execution_policy": "denied",
        "reason": "policy_contract_missing",
    }
    assert calls == []


def test_policy_is_independent_of_request_source():
    registry = ActionRegistry()
    registry.register_contract(
        ActionContract(name="example", effect=ActionEffect.SIDE_EFFECT)
    )
    policy = ActionExecutionPolicy(registry)

    rule_decision = policy.evaluate(
        ActionRequest("example", source_intent="rule_intent")
    )
    llm_decision = policy.evaluate(
        ActionRequest("example", source_intent="llm_intent")
    )

    assert rule_decision == llm_decision
    assert rule_decision.outcome is ExecutionPolicyOutcome.ALLOWED_SIDE_EFFECT
    assert rule_decision.metadata() == {
        "execution_policy": "allowed_side_effect",
        "reason": "policy_allows_side_effect",
    }


class StaticIntentEngine:
    def __init__(self, intent: IntentResult) -> None:
        self._intent = intent

    def infer(self, user_input):
        return self._intent


@pytest.mark.parametrize(
    "intent_engine,text",
    [
        (RuleIntentEngine(), "cancella memoria sessione"),
        (
            StaticIntentEngine(
                IntentResult(
                    intent="clear_session_memory",
                    confidence=1.0,
                    entities={"llm_action_name": "clear_session_memory"},
                )
            ),
            "clear memory",
        ),
    ],
    ids=["rules", "llm-shaped"],
)
def test_brain_paths_cannot_bypass_confirmation_policy(intent_engine, text):
    brain = Brain(
        event_bus=EventBus(),
        activity=RecordingActivityAuthority(),
        intent_engine=intent_engine,
        scheduler=ManualScheduler(),
    )

    response = brain.process_text(text)

    assert response.metadata["action_name"] == "clear_session_memory"
    assert response.metadata["execution_policy"] == "confirmation_required"
    assert response.metadata["reason"] == "confirmation_required"
    assert response.metadata["requires_confirmation"] is True
    assert [message.role for message in brain.memory.history] == ["user", "assistant"]


def test_async_brain_finalize_cannot_bypass_confirmation_policy():
    scheduler = ManualScheduler()
    brain = Brain(
        event_bus=EventBus(),
        activity=RecordingActivityAuthority(),
        intent_engine=RuleIntentEngine(),
        scheduler=scheduler,
    )
    responses = []

    brain.process_text_async("cancella memoria sessione", responses.append)
    scheduler.advance(brain.listening_delay_ms)
    scheduler.run_all()

    assert len(responses) == 1
    assert responses[0].metadata["execution_policy"] == "confirmation_required"
    assert responses[0].metadata["reason"] == "confirmation_required"
    assert [message.role for message in brain.memory.history] == ["user", "assistant"]


def test_every_builtin_action_has_an_explicit_effect_classification():
    expected_read_only = {
        "get_time",
        "get_date",
        "echo_text",
        "get_session_summary",
        "get_last_intent",
        "list_available_actions",
        "get_memory_size",
        "get_last_user_message",
        "get_system_info",
        "get_project_path",
    }
    expected_side_effect = {
        "clear_session_memory",
        "open_url",
        "open_app",
        "show_notification",
        "open_directory",
    }

    assert set(ACTION_CONTRACTS) == expected_read_only | expected_side_effect
    assert {
        name
        for name, contract in ACTION_CONTRACTS.items()
        if contract.effect is ActionEffect.READ_ONLY
    } == expected_read_only
    assert {
        name
        for name, contract in ACTION_CONTRACTS.items()
        if contract.effect is ActionEffect.SIDE_EFFECT
    } == expected_side_effect
    assert ACTION_CONTRACTS["clear_session_memory"].requires_confirmation is True
    assert all(
        not contract.requires_confirmation
        for name, contract in ACTION_CONTRACTS.items()
        if name != "clear_session_memory"
    )
