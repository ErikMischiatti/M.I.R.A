from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QTimer

from nero.actions.action_executor import ActionExecutor
from nero.actions.action_models import ActionRequest, ActionResult
from nero.actions.action_registry import ActionRegistry
from nero.actions.builtin_actions import (
    make_clear_session_memory_action,
    make_echo_text_action,
    make_get_date_action,
    make_get_last_intent_action,
    make_get_session_summary_action,
    make_get_time_action,
    make_list_available_actions_action,
    make_get_memory_size_action,
    make_get_last_user_message_action,
)
from nero.actions.desktop_actions import (
    make_open_url_action,
    make_open_app_action,
    make_show_notification_action,
    make_get_system_info_action,
)

from nero.cognition.response_builder import ResponseBuilder
from nero.cognition.rule_intent_engine import RuleIntentEngine
from nero.cognition.llm_intent_engine import LLMIntentEngine  # ✅ NEW

from nero.core.events import EventBus
from nero.core.models import BrainResponse, IntentResult, UserInput
from nero.core.session_memory import SessionMemory
from nero.core.state_manager import StateManager
from nero.ui.face.face_state import FaceState


class Brain:
    def __init__(
        self,
        event_bus: EventBus,
        state_manager: StateManager,
        intent_engine=None,
        response_builder=None,
    ):
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.memory = SessionMemory()

        # ✅ ENGINE SELECTION
        self.intent_engine = intent_engine or self._select_intent_engine()

        self.response_builder = response_builder or ResponseBuilder()

        self.action_registry = ActionRegistry()
        self.action_executor = ActionExecutor(self.action_registry, self.event_bus)
        self._register_builtin_actions()

        self.listening_delay_ms = 500
        self.thinking_delay_ms = 900
        self.speaking_reset_delay_ms = 1200

    # ============================================================
    # ENGINE SELECTION
    # ============================================================

    def _select_intent_engine(self):
        engine_type = os.getenv("NERO_INTENT_ENGINE", "rule").lower()

        if engine_type == "llm":
            print("[Brain] Using LLMIntentEngine")
            return LLMIntentEngine()

        print("[Brain] Using RuleIntentEngine")
        return RuleIntentEngine()

    # ============================================================
    # ACTION REGISTRATION
    # ============================================================

    def _register_builtin_actions(self) -> None:
        # Core
        self.action_registry.register("get_time", make_get_time_action())
        self.action_registry.register("get_date", make_get_date_action())
        self.action_registry.register("echo_text", make_echo_text_action())
        self.action_registry.register("get_last_intent", make_get_last_intent_action(self.memory))
        self.action_registry.register("get_session_summary", make_get_session_summary_action(self.memory))
        self.action_registry.register("clear_session_memory", make_clear_session_memory_action(self.memory))

        # Introspection
        self.action_registry.register("list_available_actions", make_list_available_actions_action(self.action_registry))
        self.action_registry.register("get_memory_size", make_get_memory_size_action(self.memory))
        self.action_registry.register("get_last_user_message", make_get_last_user_message_action(self.memory))

        # Desktop
        self.action_registry.register("open_url", make_open_url_action())
        self.action_registry.register("open_app", make_open_app_action())
        self.action_registry.register("show_notification", make_show_notification_action())
        self.action_registry.register("get_system_info", make_get_system_info_action())

    # ============================================================
    # PUBLIC API
    # ============================================================

    def process_text(self, text: str) -> BrainResponse:
        user_input = UserInput(text=text.strip())
        self.memory.add_user_input(user_input)

        self.event_bus.emit("user_input_received", user_input)
        self.state_manager.set_state(FaceState.LISTENING)

        self.event_bus.emit("processing_started", user_input)
        self.state_manager.set_state(FaceState.THINKING)

        response = self._build_response(user_input)

        self.event_bus.emit("response_ready", response)
        self.state_manager.set_state(response.face_state)

        return response

    def process_text_async(
        self,
        text: str,
        on_response: Callable[[BrainResponse], None] | None = None,
    ) -> None:
        user_input = UserInput(text=text.strip())
        self.memory.add_user_input(user_input)

        self.event_bus.emit("user_input_received", user_input)
        self.state_manager.set_state(FaceState.LISTENING)

        QTimer.singleShot(
            self.listening_delay_ms,
            lambda: self._continue_after_listening(user_input, on_response),
        )

    def _continue_after_listening(
        self,
        user_input: UserInput,
        on_response: Callable[[BrainResponse], None] | None,
    ) -> None:
        self.event_bus.emit("processing_started", user_input)
        self.state_manager.set_state(FaceState.THINKING)

        QTimer.singleShot(
            self.thinking_delay_ms,
            lambda: self._finalize_response(user_input, on_response),
        )

    def _finalize_response(
        self,
        user_input: UserInput,
        on_response: Callable[[BrainResponse], None] | None,
    ) -> None:
        response = self._build_response(user_input)

        self.event_bus.emit("response_ready", response)
        self.state_manager.set_state(response.face_state)

        if on_response is not None:
            on_response(response)

    # ============================================================
    # CORE LOGIC
    # ============================================================

    def infer_intent(self, user_input: UserInput) -> IntentResult:
        return self.intent_engine.infer(user_input)

    def build_response(
        self,
        intent: IntentResult,
        user_input: UserInput,
        action_result: ActionResult | None = None,
    ) -> BrainResponse:
        return self.response_builder.build(intent, user_input, action_result)

    def build_action_request(self, intent: IntentResult) -> ActionRequest | None:
        # --- LLM override support ---
        llm_action = intent.entities.get("llm_action_name")

        if llm_action:
            return ActionRequest(
                action_name=llm_action,
                parameters={k: v for k, v in intent.entities.items() if k not in ["llm_action_name", "llm_response_text", "llm_emotion", "llm_raw"]},
                source_intent=intent.intent,
            )

        # --- Rule-based mapping fallback ---
        if intent.intent == "time_query":
            return ActionRequest(action_name="get_time")

        if intent.intent == "date_query":
            return ActionRequest(action_name="get_date")

        if intent.intent == "echo_request":
            return ActionRequest(
                action_name="echo_text",
                parameters={"text": intent.entities.get("text", "")},
            )

        if intent.intent == "session_summary_request":
            return ActionRequest(action_name="get_session_summary")

        if intent.intent == "last_intent_query":
            return ActionRequest(action_name="get_last_intent")

        if intent.intent == "clear_session_memory":
            return ActionRequest(action_name="clear_session_memory")

        if intent.intent == "list_actions":
            return ActionRequest(action_name="list_available_actions")

        if intent.intent == "memory_size_query":
            return ActionRequest(action_name="get_memory_size")

        if intent.intent == "last_user_message_query":
            return ActionRequest(action_name="get_last_user_message")

        if intent.intent == "open_url_request":
            return ActionRequest(
                action_name="open_url",
                parameters={"url": intent.entities.get("url", "")},
            )

        if intent.intent == "open_app_request":
            return ActionRequest(
                action_name="open_app",
                parameters={"app_name": intent.entities.get("app_name", "")},
            )

        if intent.intent == "notification_request":
            return ActionRequest(
                action_name="show_notification",
                parameters={"text": intent.entities.get("text", "")},
            )

        if intent.intent == "system_info_query":
            return ActionRequest(action_name="get_system_info")

        return None

    def _build_response(self, user_input: UserInput) -> BrainResponse:
        intent = self.infer_intent(user_input)
        self.memory.set_last_intent(intent)
        self.event_bus.emit("intent_inferred", intent)

        action_request = self.build_action_request(intent)
        action_result = None

        if action_request is not None:
            action_result = self.action_executor.execute(action_request)

        response = self.build_response(intent, user_input, action_result)
        self.memory.add_response(response)

        return response

    # ============================================================
    # MEMORY HELPERS
    # ============================================================

    def get_recent_history(self, limit: int | None = None):
        return self.memory.get_recent_history(limit)

    def get_last_intent(self) -> IntentResult | None:
        return self.memory.last_intent