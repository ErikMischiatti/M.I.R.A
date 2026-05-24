from __future__ import annotations

from typing import Any


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


ALLOWED_ACTIONS = [
    "get_time",
    "get_date",
    "echo_text",
    "get_session_summary",
    "get_last_intent",
    "clear_session_memory",
    "list_available_actions",
    "get_memory_size",
    "get_last_user_message",
    "open_url",
    "open_app",
    "show_notification",
    "open_directory",
    "get_system_info",
]


ALLOWED_ACTIONS_BY_INTENT = {
    "empty_input": set(),
    "greeting": set(),
    "status_query": set(),
    "identity_query": set(),
    "time_query": {"get_time"},
    "date_query": {"get_date"},
    "echo_request": {"echo_text"},
    "session_summary_request": {"get_session_summary"},
    "last_intent_query": {"get_last_intent"},
    "clear_session_memory": {"clear_session_memory"},
    "list_actions": {"list_available_actions"},
    "memory_size_query": {"get_memory_size"},
    "last_user_message_query": {"get_last_user_message"},
    "open_url_request": {"open_url"},
    "open_app_request": {"open_app"},
    "notification_request": {"show_notification"},
    "open_directory_request": {"open_directory"},
    "system_info_query": {"get_system_info"},
    "unknown": set(),
}


REQUIRED_ACTION_PARAMS = {
    "echo_text": {"text": str},
    "open_url": {"url": str},
    "open_app": {"app_name": str},
    "show_notification": {"text": str},
    "open_directory": {"directory": str},
}


def validate_llm_action_for_intent(
    intent: str,
    action_name: Any,
    parameters: Any,
) -> tuple[str | None, dict[str, Any], str | None]:
    """Validate the LLM action contract without executing anything."""
    if not isinstance(parameters, dict):
        return None, {}, "parameters_type"

    if action_name is None:
        return None, parameters, None

    if not isinstance(action_name, str) or not action_name.strip():
        return None, parameters, "action_name"

    action = action_name.strip()

    if action not in ALLOWED_ACTIONS:
        return None, parameters, "action_unknown"

    if action not in ALLOWED_ACTIONS_BY_INTENT.get(intent, set()):
        return None, parameters, "intent_action_mismatch"

    required_params = REQUIRED_ACTION_PARAMS.get(action, {})
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