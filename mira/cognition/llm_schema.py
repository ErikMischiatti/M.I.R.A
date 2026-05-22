from __future__ import annotations


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