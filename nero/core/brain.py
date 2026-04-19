from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer

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

        self.listening_delay_ms = 500
        self.thinking_delay_ms = 900
        self.speaking_reset_delay_ms = 1200

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

    def build_response(self, intent: IntentResult, user_input: UserInput) -> BrainResponse:
        return self.response_builder.build(intent, user_input)

    def _build_response(self, user_input: UserInput) -> BrainResponse:
        intent = self.infer_intent(user_input)
        self.memory.set_last_intent(intent)
        self.event_bus.emit("intent_inferred", intent)

        response = self.build_response(intent, user_input)
        self.memory.add_response(response)

        self.memory.set_context("last_user_text", user_input.text)
        self.memory.set_context("last_response_text", response.text)
        self.memory.set_context("last_face_state", response.face_state.name)
        self.memory.set_context("last_intent_name", intent.intent)

        return response

    def get_recent_history(self, limit: int | None = None):
        return self.memory.get_recent_history(limit)

    def get_last_intent(self) -> IntentResult | None:
        return self.memory.last_intent