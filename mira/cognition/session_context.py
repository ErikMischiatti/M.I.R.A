from __future__ import annotations

from dataclasses import dataclass

from mira.core.session_memory import SessionMemory


DEFAULT_SESSION_CONTEXT_MAX_MESSAGES = 6
DEFAULT_SESSION_CONTEXT_MAX_CHARS = 1200


@dataclass(frozen=True)
class SessionContext:
    """Bounded, prompt-ready view of recent session messages."""

    text: str
    message_count: int
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.text


def build_session_context(
    memory: SessionMemory | None,
    *,
    max_messages: int = DEFAULT_SESSION_CONTEXT_MAX_MESSAGES,
    max_chars: int = DEFAULT_SESSION_CONTEXT_MAX_CHARS,
    current_user_text: str | None = None,
) -> SessionContext:
    """
    Build a deterministic, bounded session context for LLM prompts.

    The builder is intentionally read-only: it does not mutate SessionMemory,
    call the LLM, execute actions, or touch UI state.
    """
    if memory is None or max_messages <= 0 or max_chars <= 0:
        truncated = bool(
            memory and memory.history and (max_messages <= 0 or max_chars <= 0)
        )
        return SessionContext(text="", message_count=0, truncated=truncated)

    messages = memory.get_recent_history()
    if current_user_text is not None and messages:
        last_message = messages[-1]
        if last_message.role == "user" and last_message.text == current_user_text:
            messages = messages[:-1]

    if not messages:
        return SessionContext(text="", message_count=0)

    recent_messages = messages[-max_messages:]
    truncated_by_messages = len(messages) > len(recent_messages)
    lines = [
        _format_message_line(message.role, message.text)
        for message in recent_messages
    ]
    text = "\n".join(lines)

    if len(text) <= max_chars:
        return SessionContext(
            text=text,
            message_count=len(recent_messages),
            truncated=truncated_by_messages,
        )

    bounded_text, included_messages = _fit_lines_to_char_limit(lines, max_chars)
    return SessionContext(
        text=bounded_text,
        message_count=included_messages,
        truncated=True,
    )


def _format_message_line(role: str, text: str) -> str:
    safe_role = role.strip() or "unknown"
    safe_text = " ".join(text.split())
    return f"- {safe_role}: {safe_text}"


def _fit_lines_to_char_limit(lines: list[str], max_chars: int) -> tuple[str, int]:
    selected: list[str] = []
    remaining = max_chars

    for line in reversed(lines):
        separator_len = 1 if selected else 0
        required_len = len(line) + separator_len

        if required_len <= remaining:
            selected.insert(0, line)
            remaining -= required_len
            continue

        prefix = line.split(": ", 1)[0] + ": "
        suffix = "..."
        available_text_len = remaining - separator_len - len(prefix) - len(suffix)
        if available_text_len > 0:
            visible_text = line[len(prefix):len(prefix) + available_text_len]
            truncated_line = f"{prefix}{visible_text}{suffix}"
            selected.insert(0, truncated_line)

        break

    return "\n".join(selected), len(selected)
