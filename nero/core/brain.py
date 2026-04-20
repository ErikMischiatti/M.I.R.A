from __future__ import annotations

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
from nero.cognition.response_builder import ResponseBuilder
from nero.cognition.rule_intent_engine import RuleIntentEngine
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

        self.intent_engine = intent_engine or RuleIntentEngine()
        self.response_builder = response_builder or ResponseBuilder()

        self.action_registry = ActionRegistry()
        self.action_executor = ActionExecutor(self.action_registry, self.event_bus)
        self._register_builtin_actions()

        self.listening_delay_ms = 500
        self.thinking_delay_ms = 900
        self.speaking_reset_delay_ms = 1200

    def _register_builtin_actions(self) -> None:
        self.action_registry.register("get_time", make_get_time_action())
        self.action_registry.register("get_date", make_get_date_action())
        self.action_registry.register("echo_text", make_echo_text_action())
        self.action_registry.register("get_last_intent", make_get_last_intent_action(self.memory))
        self.action_registry.register("get_session_summary", make_get_session_summary_action(self.memory))
        self.action_registry.register("clear_session_memory", make_clear_session_memory_action(self.memory))
        self.action_registry.register("list_available_actions", make_list_available_actions_action(self.action_registry))
        self.action_registry.register("get_memory_size", make_get_memory_size_action(self.memory))
        self.action_registry.register("get_last_user_message", make_get_last_user_message_action(self.memory))
        

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
        if intent.intent == "time_query":
            return ActionRequest(
                action_name="get_time",
                source_intent=intent.intent,
            )

        if intent.intent == "date_query":
            return ActionRequest(
                action_name="get_date",
                source_intent=intent.intent,
            )

        if intent.intent == "echo_request":
            return ActionRequest(
                action_name="echo_text",
                parameters={"text": intent.entities.get("text", "")},
                source_intent=intent.intent,
            )

        if intent.intent == "session_summary_request":
            return ActionRequest(
                action_name="get_session_summary",
                source_intent=intent.intent,
            )

        if intent.intent == "last_intent_query":
            return ActionRequest(
                action_name="get_last_intent",
                source_intent=intent.intent,
            )

        if intent.intent == "clear_session_memory":
            return ActionRequest(
                action_name="clear_session_memory",
                source_intent=intent.intent,
            )
        if intent.intent == "list_actions":
            return ActionRequest(
                action_name="list_available_actions",
                source_intent=intent.intent,
            )

        if intent.intent == "memory_size_query":
            return ActionRequest(
                action_name="get_memory_size",
                source_intent=intent.intent,
            )

        if intent.intent == "last_user_message_query":
            return ActionRequest(
                action_name="get_last_user_message",
                source_intent=intent.intent,
            )

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

        self.memory.set_context("last_user_text", user_input.text)
        self.memory.set_context("last_response_text", response.text)
        self.memory.set_context("last_face_state", response.face_state.name)
        self.memory.set_context("last_intent_name", intent.intent)

        if action_result is not None:
            self.memory.set_context("last_action_name", action_result.action_name)
            self.memory.set_context("last_action_success", action_result.success)

        return response

    def get_recent_history(self, limit: int | None = None):
        return self.memory.get_recent_history(limit)

    def get_last_intent(self) -> IntentResult | None:
        return self.memory.last_intent