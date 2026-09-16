"""Source-independent authorization for action execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mira.actions.action_models import ActionEffect, ActionRequest
from mira.actions.action_registry import ActionRegistry


class ExecutionPolicyOutcome(str, Enum):
    SAFE = "safe"
    ALLOWED_SIDE_EFFECT = "allowed_side_effect"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DENIED = "denied"


@dataclass(frozen=True)
class ExecutionPolicyDecision:
    outcome: ExecutionPolicyOutcome
    reason: str

    @property
    def allows_execution(self) -> bool:
        return self.outcome in {
            ExecutionPolicyOutcome.SAFE,
            ExecutionPolicyOutcome.ALLOWED_SIDE_EFFECT,
        }

    def metadata(self) -> dict[str, object]:
        return {
            "execution_policy": self.outcome.value,
            "reason": self.reason,
        }


class ActionExecutionPolicy:
    """Authorize a request from its registered contract, never its source."""

    def __init__(self, registry: ActionRegistry) -> None:
        self._registry = registry

    def evaluate(self, request: ActionRequest) -> ExecutionPolicyDecision:
        contract = self._registry.get_contract(request.action_name)
        if contract is None:
            return ExecutionPolicyDecision(
                ExecutionPolicyOutcome.DENIED,
                "policy_contract_missing",
            )

        if contract.effect is ActionEffect.DENIED:
            return ExecutionPolicyDecision(
                ExecutionPolicyOutcome.DENIED,
                "action_denied",
            )

        if contract.requires_confirmation or request.requires_confirmation:
            return ExecutionPolicyDecision(
                ExecutionPolicyOutcome.CONFIRMATION_REQUIRED,
                "confirmation_required",
            )

        if contract.effect is ActionEffect.SIDE_EFFECT:
            return ExecutionPolicyDecision(
                ExecutionPolicyOutcome.ALLOWED_SIDE_EFFECT,
                "policy_allows_side_effect",
            )

        return ExecutionPolicyDecision(
            ExecutionPolicyOutcome.SAFE,
            "policy_allows_safe_action",
        )
