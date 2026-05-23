from dataclasses import dataclass, field
from typing import Any

from mira.core.models import UserInput, IntentResult, BrainResponse


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
                    "face_state": response.face_state.name,
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

    def get_llm_context(
        self,
        history_limit: int = 8,
        max_message_chars: int = 320,
        min_last_intent_confidence: float = 0.5,
    ) -> dict[str, Any]:
        """
        Return a bounded, deterministic snapshot suitable for an LLM prompt.

        The snapshot intentionally excludes message metadata and arbitrary
        session context values so internal state is not exposed to the model.
        """
        bounded_history = [
            {
                "role": message.role,
                "text": self._truncate_text(message.text, max_message_chars),
            }
            for message in self.get_recent_history(history_limit)
        ]

        last_intent = None
        if (
            self.last_intent is not None
            and self.last_intent.confidence >= min_last_intent_confidence
        ):
            last_intent = {
                "intent": self.last_intent.intent,
                "confidence": round(self.last_intent.confidence, 3),
            }

        return {
            "history": bounded_history,
            "last_intent": last_intent,
        }

    def clear(self) -> None:
        self.history.clear()
        self.last_intent = None
        self.context.clear()

    def _append(self, message: MemoryMessage) -> None:
        self.history.append(message)

        if len(self.history) > self.max_history:
            overflow = len(self.history) - self.max_history
            self.history = self.history[overflow:]

    def _truncate_text(self, text: str, max_chars: int) -> str:
        normalized_text = " ".join(str(text).split())
        if max_chars <= 0:
            return ""
        if len(normalized_text) <= max_chars:
            return normalized_text

        suffix = "..."
        if max_chars <= len(suffix):
            return suffix[:max_chars]

        return normalized_text[: max_chars - len(suffix)].rstrip() + suffix
