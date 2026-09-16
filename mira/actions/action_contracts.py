from __future__ import annotations

from mira.actions.action_models import ActionContract, ActionEffect
from mira.actions.action_registry import ActionRegistry


ACTION_CONTRACTS = {
    "get_time": ActionContract(
        name="get_time",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"time_query"}),
    ),
    "get_date": ActionContract(
        name="get_date",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"date_query"}),
    ),
    "echo_text": ActionContract(
        name="echo_text",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"echo_request"}),
        required_params={"text": str},
    ),
    "get_session_summary": ActionContract(
        name="get_session_summary",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"session_summary_request"}),
    ),
    "get_last_intent": ActionContract(
        name="get_last_intent",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"last_intent_query"}),
    ),
    "clear_session_memory": ActionContract(
        name="clear_session_memory",
        compatible_intents=frozenset({"clear_session_memory"}),
        effect=ActionEffect.SIDE_EFFECT,
        requires_confirmation=True,
    ),
    "list_available_actions": ActionContract(
        name="list_available_actions",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"list_actions"}),
    ),
    "get_memory_size": ActionContract(
        name="get_memory_size",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"memory_size_query"}),
    ),
    "get_last_user_message": ActionContract(
        name="get_last_user_message",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"last_user_message_query"}),
    ),
    "open_url": ActionContract(
        name="open_url",
        compatible_intents=frozenset({"open_url_request"}),
        required_params={"url": str},
        effect=ActionEffect.SIDE_EFFECT,
    ),
    "open_app": ActionContract(
        name="open_app",
        compatible_intents=frozenset({"open_app_request"}),
        required_params={"app_name": str},
        effect=ActionEffect.SIDE_EFFECT,
    ),
    "show_notification": ActionContract(
        name="show_notification",
        compatible_intents=frozenset({"notification_request"}),
        required_params={"text": str},
        effect=ActionEffect.SIDE_EFFECT,
    ),
    "open_directory": ActionContract(
        name="open_directory",
        compatible_intents=frozenset({"open_directory_request"}),
        required_params={"directory": str},
        effect=ActionEffect.SIDE_EFFECT,
    ),
    "get_system_info": ActionContract(
        name="get_system_info",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"system_info_query"}),
    ),
    "get_project_path": ActionContract(
        name="get_project_path",
        effect=ActionEffect.READ_ONLY,
        compatible_intents=frozenset({"project_path_query"}),
    ),
}


def get_builtin_action_contract(name: str) -> ActionContract | None:
    return ACTION_CONTRACTS.get(name)


def build_action_contract_registry() -> ActionRegistry:
    registry = ActionRegistry()
    for contract in ACTION_CONTRACTS.values():
        registry.register_contract(contract)
    return registry
