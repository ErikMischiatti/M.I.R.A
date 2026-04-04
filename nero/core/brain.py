from collections.abc import Callable

from PySide6.QtCore import QTimer

from nero.core.events import EventBus
from nero.core.models import UserInput, IntentResult, BrainResponse
from nero.core.state_manager import StateManager
from nero.ui.face.face_state import FaceState


class Brain:
    def __init__(self, event_bus: EventBus, state_manager: StateManager):
        self.event_bus = event_bus
        self.state_manager = state_manager

        self.listening_delay_ms = 500
        self.thinking_delay_ms = 900
        self.speaking_reset_delay_ms = 1200

    def process_text(self, text: str) -> BrainResponse:
        user_input = UserInput(text=text.strip())

        self.event_bus.emit("user_input_received", user_input)
        self.state_manager.set_state(FaceState.LISTENING)

        self.event_bus.emit("processing_started", user_input)
        self.state_manager.set_state(FaceState.THINKING)

        intent = self.detect_intent(user_input)
        response = self.build_response(intent, user_input)

        self.event_bus.emit("response_ready", response)
        self.state_manager.set_state(response.face_state)

        return response

    def process_text_async(
        self,
        text: str,
        on_response: Callable[[BrainResponse], None] | None = None,
    ) -> None:
        user_input = UserInput(text=text.strip())

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
        intent = self.detect_intent(user_input)
        response = self.build_response(intent, user_input)

        self.event_bus.emit("response_ready", response)
        self.state_manager.set_state(response.face_state)

        if on_response is not None:
            on_response(response)


    def detect_intent(self, user_input: UserInput) -> IntentResult:
        text = user_input.text.lower()

        if not text:
            return IntentResult(intent="empty_input", confidence=1.0)

        if any(word in text for word in ["ciao", "salve", "hey", "hello"]):
            return IntentResult(intent="greeting", confidence=0.95)

        if "come stai" in text:
            return IntentResult(intent="status_query", confidence=0.95)

        if "chi sei" in text:
            return IntentResult(intent="identity_query", confidence=0.95)

        return IntentResult(intent="unknown", confidence=0.50)

    def build_response(self, intent: IntentResult, user_input: UserInput) -> BrainResponse:
        if intent.intent == "empty_input":
            return BrainResponse(
                text="Non ho ricevuto alcun input.",
                face_state=FaceState.CONFUSED,
            )

        if intent.intent == "greeting":
            return BrainResponse(
                text="Ciao. Sono N.E.R.O. Pronto a interagire con te.",
                face_state=FaceState.HAPPY,
            )

        if intent.intent == "status_query":
            return BrainResponse(
                text="Sto funzionando correttamente. Il mio layer cognitivo è attivo.",
                face_state=FaceState.SPEAKING,
            )

        if intent.intent == "identity_query":
            return BrainResponse(
                text="Sono N.E.R.O, il nucleo cognitivo embodied progettato per H.A.R.O.",
                face_state=FaceState.SPEAKING,
            )

        return BrainResponse(
            text=f"Ho ricevuto: '{user_input.text}', ma non so ancora interpretarlo bene.",
            face_state=FaceState.CONFUSED,
        )