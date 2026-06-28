from __future__ import annotations

import pytest

from mira.cognition.session_context_builder import SessionContextBuilder
from mira.core.models import UserInput
from mira.core.session_memory import MemoryMessage, SessionMemory


def add_message(memory: SessionMemory, role: str, text: str, metadata=None) -> None:
    memory.history.append(MemoryMessage(role=role, text=text, metadata=metadata or {}))


def test_context_builder_includes_recent_user_and_assistant_messages():
    memory = SessionMemory()
    add_message(memory, "user", "ciao")
    add_message(memory, "assistant", "Ciao. Sono M.I.R.A.")

    snapshot = SessionContextBuilder(memory).build()

    assert snapshot.text == "User: ciao\nAssistant: Ciao. Sono M.I.R.A."
    assert snapshot.message_count == 2
    assert snapshot.truncated is False


def test_context_builder_excludes_current_user_input_when_already_in_memory():
    memory = SessionMemory()
    add_message(memory, "user", "mi chiamo Erik")
    add_message(memory, "assistant", "Terrò conto del nome in questa sessione.")
    add_message(memory, "user", "come mi chiamo?")

    snapshot = SessionContextBuilder(memory).build(
        current_input=UserInput(text="come mi chiamo?")
    )

    assert "User: mi chiamo Erik" in snapshot.text
    assert "Assistant: Terrò conto del nome in questa sessione." in snapshot.text
    assert "come mi chiamo?" not in snapshot.text


def test_context_builder_omits_metadata_and_llm_raw():
    memory = SessionMemory()
    add_message(
        memory,
        "assistant",
        "Risposta visibile.",
        metadata={
            "llm_raw": '{"secret": true}',
            "action_name": "get_system_info",
            "path": "/tmp/private",
        },
    )

    snapshot = SessionContextBuilder(memory).build()

    assert snapshot.text == "Assistant: Risposta visibile."
    assert "llm_raw" not in snapshot.text
    assert "secret" not in snapshot.text
    assert "get_system_info" not in snapshot.text
    assert "/tmp/private" not in snapshot.text


def test_context_builder_bounds_message_count_and_characters():
    memory = SessionMemory()
    for index in range(5):
        add_message(memory, "user", f"message-{index} " + ("x" * 30))

    snapshot = SessionContextBuilder(
        memory,
        max_messages=3,
        max_chars=80,
    ).build()

    assert "message-0" not in snapshot.text
    assert "message-1" not in snapshot.text
    assert "message-4" in snapshot.text
    assert len(snapshot.text) <= 80
    assert snapshot.message_count == 3
    assert snapshot.truncated is True


def test_context_builder_returns_empty_snapshot_for_empty_history():
    snapshot = SessionContextBuilder(SessionMemory()).build()

    assert snapshot.text == ""
    assert snapshot.message_count == 0
    assert snapshot.truncated is False


@pytest.mark.parametrize("max_messages,max_chars", [(0, 100), (3, 0)])
def test_context_builder_rejects_invalid_bounds(max_messages, max_chars):
    with pytest.raises(ValueError):
        SessionContextBuilder(
            SessionMemory(),
            max_messages=max_messages,
            max_chars=max_chars,
        )
