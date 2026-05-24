from __future__ import annotations

from mira.cognition.session_context import build_session_context
from mira.core.models import BrainResponse, UserInput
from mira.core.session_memory import MemoryMessage, SessionMemory
from mira.ui.face.face_state import FaceState


def test_session_context_empty_when_memory_empty():
    memory = SessionMemory()

    context = build_session_context(memory)

    assert context.is_empty
    assert context.text == ""
    assert context.message_count == 0
    assert context.truncated is False


def test_session_context_includes_recent_user_and_assistant_messages():
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="Mi chiamo Erik"))
    memory.add_response(
        BrainResponse(
            text="Piacere, Erik.",
            face_state=FaceState.SPEAKING,
        )
    )

    context = build_session_context(memory)

    assert context.text == "- user: Mi chiamo Erik\n- assistant: Piacere, Erik."
    assert context.message_count == 2
    assert context.truncated is False


def test_session_context_respects_max_messages():
    memory = SessionMemory(max_history=10)
    for index in range(5):
        memory.history.append(MemoryMessage(role="user", text=f"message {index}"))

    context = build_session_context(memory, max_messages=2)

    assert context.text == "- user: message 3\n- user: message 4"
    assert context.message_count == 2
    assert context.truncated is True


def test_session_context_respects_max_chars():
    memory = SessionMemory()
    memory.history.append(MemoryMessage(role="user", text="abcdefghijklmnopqrstuvwxyz"))

    context = build_session_context(memory, max_chars=18)

    assert len(context.text) <= 18
    assert context.text == "- user: abcdefg..."
    assert context.message_count == 1
    assert context.truncated is True


def test_session_context_does_not_include_internal_metadata():
    memory = SessionMemory()
    memory.history.append(
        MemoryMessage(
            role="assistant",
            text="Risposta sintetica.",
            metadata={
                "llm_raw": "secret raw json",
                "llm_action_validation_failed": True,
                "face_state": "SPEAKING",
            },
        )
    )

    context = build_session_context(memory)

    assert context.text == "- assistant: Risposta sintetica."
    assert "llm_raw" not in context.text
    assert "secret raw json" not in context.text
    assert "llm_action_validation_failed" not in context.text
    assert "face_state" not in context.text


def test_session_context_excludes_current_user_message_from_prompt_context():
    memory = SessionMemory()
    memory.history.append(MemoryMessage(role="user", text="Mi chiamo Erik"))
    memory.history.append(MemoryMessage(role="user", text="Come mi chiamo?"))

    context = build_session_context(memory, current_user_text="Come mi chiamo?")

    assert "Mi chiamo Erik" in context.text
    assert "Come mi chiamo?" not in context.text


def test_session_context_does_not_mutate_memory():
    memory = SessionMemory()
    original = MemoryMessage(
        role="user",
        text="Mi chiamo Erik",
        metadata={"llm_raw": "raw"},
    )
    memory.history.append(original)

    build_session_context(memory, max_chars=12)

    assert memory.history == [original]
    assert memory.history[0].metadata == {"llm_raw": "raw"}
