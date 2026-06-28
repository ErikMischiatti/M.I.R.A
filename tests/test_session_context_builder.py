from __future__ import annotations

from mira.cognition.session_context_builder import SessionContextBuilder
from mira.core.models import UserInput
from mira.core.session_memory import MemoryMessage, SessionMemory


def test_context_builder_includes_safe_user_name_fact():
    memory = SessionMemory()
    memory.set_context_value("user_name", "Erik")

    snapshot = SessionContextBuilder(memory).build()

    assert "User name: Erik" in snapshot.text


def test_context_builder_omits_current_input_from_recent_context():
    memory = SessionMemory()
    memory.history.append(MemoryMessage(role="user", text="mi chiamo Erik"))
    memory.history.append(MemoryMessage(role="assistant", text="Va bene, Erik."))
    memory.history.append(MemoryMessage(role="user", text="come mi chiamo?"))

    snapshot = SessionContextBuilder(memory).build(
        current_input=UserInput(text="come mi chiamo?")
    )

    assert "User: mi chiamo Erik" in snapshot.text
    assert "Assistant: Va bene, Erik." in snapshot.text
    assert "come mi chiamo?" not in snapshot.text


def test_context_builder_omits_metadata_and_llm_raw():
    memory = SessionMemory()
    memory.history.append(
        MemoryMessage(
            role="assistant",
            text="Risposta sicura.",
            metadata={"llm_raw": "{secret}", "action_name": "get_system_info"},
        )
    )

    snapshot = SessionContextBuilder(memory).build()

    assert "Risposta sicura." in snapshot.text
    assert "llm_raw" not in snapshot.text
    assert "secret" not in snapshot.text
    assert "get_system_info" not in snapshot.text


def test_context_builder_bounds_recent_messages_and_chars():
    memory = SessionMemory()
    for index in range(6):
        memory.history.append(
            MemoryMessage(role="user", text=f"message-{index} " + ("x" * 40))
        )

    snapshot = SessionContextBuilder(memory, max_messages=2, max_chars=90).build()

    assert "message-0" not in snapshot.text
    assert "message-5" in snapshot.text
    assert snapshot.truncated is True
    assert len(snapshot.text) <= 90
