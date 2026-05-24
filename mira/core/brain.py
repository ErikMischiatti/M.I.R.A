from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from mira.actions.action_contracts import get_builtin_action_contract
from mira.actions.action_executor import ActionExecutor
from mira.actions.action_models import ActionRequest, ActionResult
from mira.actions.action_registry import ActionRegistry
from mira.actions.builtin_actions import (
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
from mira.actions.desktop_actions import (
    make_open_url_action,
    make_open_app_action,
    make_show_notification_action,
    make_open_directory_action,
    make_get_system_info_action,
)

from mira.cognition.response_builder import ResponseBuilder
from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.cognition.llm_intent_engine import LLMIntentEngine  # ✅ NEW

from mira.core.events import EventBus
from mira.core.models import BrainResponse, IntentResult, UserInput
from mira.core.session_memory import SessionMemory
from mira.core.state_manager import StateManager
from mira.ui.face.face_state import FaceState


@dataclass
class BrainComputationResult:
    request_id: int
    user_input: UserInput
    intent: IntentResult
    action_request: ActionRequest | None
    error_response: BrainResponse | None = None


class _BrainWorkerSignals(QObject):
    completed = Signal(object)


class _BrainResponseWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        user_input: UserInput,
        compute_response: Callable[[int, UserInput], BrainComputationResult],
    ):
        super().__init__()
        self.request_id = request_id
        self.user_input = user_input
        self.compute_response = compute_response
        self.signals = _BrainWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.compute_response(self.request_id, self.user_input)
        except Exception as exc:
            intent = IntentResult(
                intent="processing_error",
                confidence=0.0,
                entities={"error": str(exc)},
            )
            response = BrainResponse(
                text="Si è verificato un errore durante l'elaborazione.",
                face_state=FaceState.CONFUSED,
                metadata={"intent": intent.intent, "error": str(exc)},
            )
            result = BrainComputationResult(
                request_id=self.request_id,
                user_input=self.user_input,
                intent=intent,
                action_request=None,
                error_response=response,
            )

        self.signals.completed.emit(result)


class _BrainResultReceiver(QObject):
    def __init__(
        self,
        brain: "Brain",
        on_response: Callable[[BrainResponse], None] | None,
    ):
        super().__init__()
        self.brain = brain
        self.on_response = on_response

    @Slot(object)
    def handle_completed(self, result: BrainComputationResult) -> None:
        self.brain._finalize_async_response(result, self.on_response)
        self.brain._release_async_receiver(result.request_id)


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
        self._thread_pool = QThreadPool.globalInstance()
        self._next_request_id = 0
        self._latest_request_id = 0
        self._async_receivers: dict[int, _BrainResultReceiver] = {}

    # ============================================================
    # ENGINE SELECTION
    # ============================================================

    def _select_intent_engine(self):
        engine_type = os.getenv("MIRA_INTENT_ENGINE", "rule").lower()

        if engine_type == "llm":
            print("[Brain] Using LLMIntentEngine")
            return LLMIntentEngine()

        print("[Brain] Using RuleIntentEngine")
        return RuleIntentEngine()

    # ============================================================
    # ACTION REGISTRATION
    # ============================================================

    def _register_builtin_actions(self) -> None:
        def register_builtin(name, handler):
            self.action_registry.register(
                name,
                handler,
                contract=get_builtin_action_contract(name),
            )

        # Core
        register_builtin("get_time", make_get_time_action())
        register_builtin("get_date", make_get_date_action())
        register_builtin("echo_text", make_echo_text_action())
        register_builtin("get_last_intent", make_get_last_intent_action(self.memory))
        register_builtin("get_session_summary", make_get_session_summary_action(self.memory))
        register_builtin("clear_session_memory", make_clear_session_memory_action(self.memory))

        # Introspection
        register_builtin("list_available_actions", make_list_available_actions_action(self.action_registry))
        register_builtin("get_memory_size", make_get_memory_size_action(self.memory))
        register_builtin("get_last_user_message", make_get_last_user_message_action(self.memory))

        # Desktop
        register_builtin("open_url", make_open_url_action())
        register_builtin("open_app", make_open_app_action())
        register_builtin("show_notification", make_show_notification_action())
        register_builtin("open_directory", make_open_directory_action())
        register_builtin("get_system_info", make_get_system_info_action())

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
        request_id = self._new_request_id()
        user_input = UserInput(text=text.strip())
        self.memory.add_user_input(user_input)

        self.event_bus.emit("user_input_received", user_input)
        self.state_manager.set_state(FaceState.LISTENING)

        QTimer.singleShot(
            self.listening_delay_ms,
            lambda: self._continue_after_listening(request_id, user_input, on_response),
        )

    def _continue_after_listening(
        self,
        request_id: int,
        user_input: UserInput,
        on_response: Callable[[BrainResponse], None] | None,
    ) -> None:
        if request_id != self._latest_request_id:
            print(f"[Brain] Ignoring stale async request {request_id}")
            return

        self.event_bus.emit("processing_started", user_input)
        self.state_manager.set_state(FaceState.THINKING)

        self._start_response_worker(request_id, user_input, on_response)

    def _start_response_worker(
        self,
        request_id: int,
        user_input: UserInput,
        on_response: Callable[[BrainResponse], None] | None,
    ) -> None:
        worker = _BrainResponseWorker(
            request_id=request_id,
            user_input=user_input,
            compute_response=self._compute_response_for_worker,
        )
        receiver = _BrainResultReceiver(self, on_response)
        self._async_receivers[request_id] = receiver
        worker.signals.completed.connect(receiver.handle_completed)
        self._thread_pool.start(worker)

    def _finalize_async_response(
        self,
        result: BrainComputationResult,
        on_response: Callable[[BrainResponse], None] | None,
    ) -> None:
        if result.request_id != self._latest_request_id:
            print(f"[Brain] Ignoring stale async result {result.request_id}")
            return

        response = self._finalize_computation_result(result)

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
        if intent.entities.get("llm_action_validation_failed"):
            return None

        llm_action = intent.entities.get("llm_action_name")

        if llm_action:
            llm_metadata_keys = {
                "llm_action_name",
                "llm_response_text",
                "llm_emotion",
                "llm_raw",
                "llm_action_validation_failed",
                "llm_action_validation_reason",
            }
            return ActionRequest(
                action_name=llm_action,
                parameters={
                    k: v
                    for k, v in intent.entities.items()
                    if k not in llm_metadata_keys
                },
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

        if intent.intent == "open_directory_request":
            return ActionRequest(
                action_name="open_directory",
                parameters={"directory": intent.entities.get("directory", "")},
            )

        if intent.intent == "system_info_query":
            return ActionRequest(action_name="get_system_info")

        return None

    def _new_request_id(self) -> int:
        self._next_request_id += 1
        self._latest_request_id = self._next_request_id
        return self._next_request_id

    def _release_async_receiver(self, request_id: int) -> None:
        self._async_receivers.pop(request_id, None)

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

    def _compute_response_for_worker(
        self,
        request_id: int,
        user_input: UserInput,
    ) -> BrainComputationResult:
        intent = self.infer_intent(user_input)
        action_request = self.build_action_request(intent)

        return BrainComputationResult(
            request_id=request_id,
            user_input=user_input,
            intent=intent,
            action_request=action_request,
        )

    def _finalize_computation_result(
        self,
        result: BrainComputationResult,
    ) -> BrainResponse:
        self.memory.set_last_intent(result.intent)
        self.event_bus.emit("intent_inferred", result.intent)

        action_result = None

        if result.action_request is not None:
            action_result = self.action_executor.execute(result.action_request)

        response = result.error_response or self.build_response(
            result.intent,
            result.user_input,
            action_result,
        )
        self.memory.add_response(response)

        return response

    # ============================================================
    # MEMORY HELPERS
    # ============================================================

    def get_recent_history(self, limit: int | None = None):
        return self.memory.get_recent_history(limit)

    def get_last_intent(self) -> IntentResult | None:
        return self.memory.last_intent
