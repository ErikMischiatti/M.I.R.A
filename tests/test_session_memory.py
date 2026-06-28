from __future__ import annotations

from mira.core.session_memory import SessionMemory


def test_session_context_value_helpers_set_get_and_clear():
    memory = SessionMemory()

    memory.set_context_value("user_name", "Erik")

    assert memory.get_context_value("user_name") == "Erik"
    assert memory.get_context_value("missing", "fallback") == "fallback"

    memory.clear_context_value("user_name")

    assert memory.get_context_value("user_name") is None


def test_clear_session_memory_removes_context_values():
    memory = SessionMemory()
    memory.set_context_value("user_name", "Erik")

    memory.clear()

    assert memory.get_context_value("user_name") is None
    assert memory.context == {}
