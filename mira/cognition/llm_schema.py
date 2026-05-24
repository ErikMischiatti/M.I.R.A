from __future__ import annotations

from typing import Any

from mira.actions.action_contracts import build_action_contract_registry
from mira.actions.action_registry import ActionRegistry


ALLOWED_INTENTS = [
    "empty_input",
    "greeting",
    "status_query",
    "identity_query",
    "time_query",
    "date_query",
    "echo_request",
    "session_summary_request",
    "last_intent_query",
    "clear_session_memory",
    "list_actions",
    "memory_size_query",
    "last_user_message_query",
    "open_url_request",
    "open_app_request",
    "notification_request",
    "open_directory_request",
    "system_info_query",
    "unknown",
]


_ACTION_CONTRACT_REGISTRY = build_action_contract_registry()

ALLOWED_ACTIONS = _ACTION_CONTRACT_REGISTRY.list_contract_names()


def describe_action_intent_compatibility(registry: ActionRegistry | None = None) -> list[str]:
    action_registry = registry or _ACTION_CONTRACT_REGISTRY
    descriptions = []

    for contract in action_registry.list_contracts():
        for intent in sorted(contract.compatible_intents):
            descriptions.append(f"- {intent}: {contract.name}")

    return descriptions


def describe_required_action_params(registry: ActionRegistry | None = None) -> list[str]:
    action_registry = registry or _ACTION_CONTRACT_REGISTRY
    descriptions = []

    for contract in action_registry.list_contracts():
        if not contract.required_params:
            continue

        param_names = ", ".join(
            f"\"{param_name}\"" for param_name in sorted(contract.required_params.keys())
        )
        descriptions.append(f"- For {contract.name}, parameters must contain: {{{param_names}}}")

    return descriptions


def validate_llm_action_for_intent(
    intent: str,
    action_name: Any,
    parameters: Any,
    registry: ActionRegistry | None = None,
) -> tuple[str | None, dict[str, Any], str | None]:
    """Validate the LLM action contract without executing anything."""
    action_registry = registry or _ACTION_CONTRACT_REGISTRY

    if not isinstance(parameters, dict):
        return None, {}, "parameters_type"

    if action_name is None:
        return None, parameters, None

    if not isinstance(action_name, str) or not action_name.strip():
        return None, parameters, "action_name"

    action = action_name.strip()

    if not action_registry.has_contract(action):
        return None, parameters, "action_unknown"

    if not action_registry.is_action_compatible_with_intent(action, intent):
        return None, parameters, "intent_action_mismatch"

    required_params = action_registry.required_params_for(action)
    for param_name, param_type in required_params.items():
        value = parameters.get(param_name)
        if not isinstance(value, param_type):
            return None, parameters, f"missing_or_invalid_param:{param_name}"
        if param_type is str and not value.strip():
            return None, parameters, f"missing_or_invalid_param:{param_name}"

    return action, parameters, None


LLM_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ALLOWED_INTENTS,
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "emotion": {
            "type": "string",
            "enum": [
                "neutral",
                "happy",
                "confused",
                "thinking",
                "speaking",
            ],
        },
        "action_name": {
            "type": ["string", "null"],
        },
        "parameters": {
            "type": "object",
            "additionalProperties": True,
        },
        "response_text": {
            "type": "string",
        },
    },
    "required": [
        "intent",
        "confidence",
        "emotion",
        "action_name",
        "parameters",
        "response_text",
    ],
}