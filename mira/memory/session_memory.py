from dataclasses import dataclass, field
from typing import Any

from mira.domain.models import UserInput, IntentResult, BrainResponse
from mira.domain.embodiment_compatibility import resolve_face_state


@dataclass
class MemoryMessage:
    role: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionMemory:
    """
    Lightweight in-memory conversation state for the current session.

    Responsibilities:
    - store recent conversation history
    - track the last inferred intent
    - expose a generic session context dict
    """

    def __init__(self, max_history: int = 20):
        self.max_history = max_history
        self.history: list[MemoryMessage] = []
        self.last_intent: IntentResult | None = None
        self.context: dict[str, Any] = {}

    def add_user_input(self, user_input: UserInput) -> None:
        self._append(
            MemoryMessage(
                role="user",
                text=user_input.text,
                metadata={
                    "source": user_input.source,
                    **user_input.metadata,
                },
            )
        )

    def add_response(self, response: BrainResponse) -> None:
        self._append(
            MemoryMessage(
                role="assistant",
                text=response.text,
                metadata={
                    # Preserve the existing serialized metadata at this
                    # explicit persistence compatibility boundary.
                    "face_state": resolve_face_state(response.embodiment).name,
                    **response.metadata,
                },
            )
        )

    def set_last_intent(self, intent: IntentResult) -> None:
        self.last_intent = intent

    def set_context(self, key: str, value: Any) -> None:
        self.context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        return self.context.get(key, default)

    def get_recent_history(self, limit: int | None = None) -> list[MemoryMessage]:
        if limit is None or limit >= len(self.history):
            return list(self.history)
        return self.history[-limit:]

    def clear(self) -> None:
        self.history.clear()
        self.last_intent = None
        self.context.clear()

    def _append(self, message: MemoryMessage) -> None:
        self.history.append(message)

        if len(self.history) > self.max_history:
            overflow = len(self.history) - self.max_history
            self.history = self.history[overflow:]
