from __future__ import annotations

from dataclasses import dataclass

from mira.domain.models import UserInput
from mira.core.session_memory import SessionMemory


DEFAULT_MAX_CONTEXT_MESSAGES = 8
DEFAULT_MAX_CONTEXT_CHARS = 1200


@dataclass(frozen=True)
class SessionContextSnapshot:
    text: str
    message_count: int
    truncated: bool = False


class SessionContextBuilder:
    """Builds a bounded, sanitized prompt context from session memory."""

    def __init__(
        self,
        memory: SessionMemory,
        max_messages: int = DEFAULT_MAX_CONTEXT_MESSAGES,
        max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ):
        if max_messages < 1:
            raise ValueError("max_messages must be positive.")
        if max_chars < 1:
            raise ValueError("max_chars must be positive.")

        self.memory = memory
        self.max_messages = max_messages
        self.max_chars = max_chars

    def build(self, current_input: UserInput | None = None) -> SessionContextSnapshot:
        messages = [
            message
            for message in self.memory.get_recent_history(self.max_messages + 1)
            if message.role in {"user", "assistant"} and message.text.strip()
        ]

        if (
            current_input is not None
            and messages
            and messages[-1].role == "user"
            and messages[-1].text == current_input.text
        ):
            messages = messages[:-1]

        messages = messages[-self.max_messages :]

        lines = [
            f"{self._format_role(message.role)}: {self._sanitize_text(message.text)}"
            for message in messages
        ]

        text = "\n".join(line for line in lines if line.strip())
        if not text:
            return SessionContextSnapshot(text="", message_count=0)

        truncated = False
        if len(text) > self.max_chars:
            truncated = True
            text = text[-self.max_chars :].lstrip()

            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1 :]

        return SessionContextSnapshot(
            text=text,
            message_count=len(messages),
            truncated=truncated,
        )

    def _sanitize_text(self, text: str) -> str:
        return " ".join(text.split())

    def _format_role(self, role: str) -> str:
        if role == "user":
            return "User"
        if role == "assistant":
            return "Assistant"
        return "Message"
