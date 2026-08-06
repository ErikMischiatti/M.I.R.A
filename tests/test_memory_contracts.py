"""Characterization tests for session-memory behaviour.

Written against the pre-relocation implementation and expected to hold
unchanged after it moves. These describe what the code *does*, including two
quirks that are deliberately preserved rather than fixed here:

- `get_recent_history(0)` returns the whole history, because `0 >= len` is false
  and `history[-0:]` is `history[0:]`;
- a negative limit drops the *oldest* n messages, because `history[-limit:]`
  slices from the front;
- caller-supplied metadata overrides the keys the store sets itself, because the
  explicit key is written before the `**` spread.

Both are reported as findings, not corrected: this tranche relocates code and
must not change semantics.
"""

from __future__ import annotations

from mira.memory.session_memory import MemoryMessage, SessionMemory
from mira.domain.models import BrainResponse, IntentResult, UserInput
from mira.domain.state import FaceState


# --- tiers and their meanings -------------------------------------------


def test_the_three_tiers_start_empty_and_are_independent():
    memory = SessionMemory()

    assert memory.history == []
    assert memory.last_intent is None
    assert memory.context == {}
    assert memory.max_history == 20


def test_user_input_becomes_an_episodic_message():
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="ciao", source="text", metadata={"k": "v"}))

    assert memory.history == [
        MemoryMessage(role="user", text="ciao", metadata={"source": "text", "k": "v"})
    ]


def test_response_becomes_an_episodic_message_carrying_the_state_name():
    memory = SessionMemory()
    memory.add_response(
        BrainResponse(text="hi", face_state=FaceState.HAPPY, metadata={"intent": "greeting"})
    )

    assert memory.history == [
        MemoryMessage(
            role="assistant",
            text="hi",
            metadata={"face_state": "HAPPY", "intent": "greeting"},
        )
    ]


def test_caller_metadata_overrides_the_keys_the_store_sets():
    """Quirk, preserved: the explicit key is written before the ** spread."""
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="a", source="text", metadata={"source": "OVERRIDE"}))
    memory.add_response(
        BrainResponse(text="b", face_state=FaceState.HAPPY, metadata={"face_state": "OVERRIDE"})
    )

    assert memory.history[0].metadata == {"source": "OVERRIDE"}
    assert memory.history[1].metadata == {"face_state": "OVERRIDE"}


# --- bound and ordering -------------------------------------------------


def test_history_is_bounded_and_keeps_the_newest():
    memory = SessionMemory(max_history=2)
    for index in range(4):
        memory.add_user_input(UserInput(text=str(index)))

    assert [message.text for message in memory.history] == ["2", "3"]


def test_history_preserves_insertion_order_across_roles():
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="u1"))
    memory.add_response(BrainResponse(text="a1", face_state=FaceState.SPEAKING))
    memory.add_user_input(UserInput(text="u2"))

    assert [(m.role, m.text) for m in memory.history] == [
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "u2"),
    ]


# --- key/value tier -----------------------------------------------------


def test_context_set_get_and_overwrite():
    memory = SessionMemory()

    assert memory.get_context("name") is None
    assert memory.get_context("name", "fallback") == "fallback"

    memory.set_context("name", "Erik")
    assert memory.get_context("name") == "Erik"

    memory.set_context("name", "Someone")
    assert memory.get_context("name") == "Someone"


def test_context_accepts_any_value_including_none_and_falsey():
    memory = SessionMemory()
    memory.set_context("none", None)
    memory.set_context("zero", 0)

    # A stored None is indistinguishable from absence through get_context.
    assert memory.get_context("none", "fallback") is None
    assert memory.get_context("zero") == 0
    assert "none" in memory.context


# --- snapshot / exposure ------------------------------------------------


def test_recent_history_returns_a_new_list_sharing_the_messages():
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="a"))

    snapshot = memory.get_recent_history()

    assert snapshot is not memory.history
    assert snapshot[0] is memory.history[0]


def test_recent_history_limit_takes_the_newest():
    memory = SessionMemory()
    for index in range(4):
        memory.add_user_input(UserInput(text=str(index)))

    assert [m.text for m in memory.get_recent_history(2)] == ["2", "3"]
    assert [m.text for m in memory.get_recent_history(99)] == ["0", "1", "2", "3"]
    assert [m.text for m in memory.get_recent_history(None)] == ["0", "1", "2", "3"]


def test_recent_history_negative_limit_drops_the_oldest():
    """Quirk, preserved: a negative limit slices from the front."""
    memory = SessionMemory()
    for index in range(4):
        memory.add_user_input(UserInput(text=str(index)))

    assert [m.text for m in memory.get_recent_history(-1)] == ["1", "2", "3"]
    assert [m.text for m in memory.get_recent_history(-3)] == ["3"]


def test_recent_history_zero_returns_everything():
    """Quirk, preserved: history[-0:] is history[0:], so limit=0 means all."""
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="a"))
    memory.add_user_input(UserInput(text="b"))

    assert [m.text for m in memory.get_recent_history(0)] == ["a", "b"]


# --- interpretive tier and clearing -------------------------------------


def test_last_intent_is_a_single_overwritten_slot():
    memory = SessionMemory()
    first = IntentResult(intent="greeting")
    second = IntentResult(intent="time_query")

    memory.set_last_intent(first)
    assert memory.last_intent is first

    memory.set_last_intent(second)
    assert memory.last_intent is second


def test_clear_resets_all_three_tiers():
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="a"))
    memory.set_last_intent(IntentResult(intent="greeting"))
    memory.set_context("name", "Erik")

    history_ref, context_ref = memory.history, memory.context

    memory.clear()

    assert memory.history == []
    assert memory.last_intent is None
    assert memory.context == {}
    # Preserved: clear() empties the existing containers rather than rebinding,
    # which is the mirror image of the rebinding trim in _append.
    assert memory.history is history_ref
    assert memory.context is context_ref


def test_clear_does_not_affect_a_previously_taken_snapshot():
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="a"))
    snapshot = memory.get_recent_history()

    memory.clear()

    assert [m.text for m in snapshot] == ["a"]


# --- mutability and ownership -------------------------------------------


def test_stored_messages_are_mutable_and_shared_by_reference():
    """Preserved: the store hands out its own message objects, not copies."""
    memory = SessionMemory()
    memory.add_user_input(UserInput(text="a"))

    memory.get_recent_history()[0].text = "mutated"

    assert memory.history[0].text == "mutated"


def test_trimming_rebinds_the_history_list():
    """Preserved: _append rebinds rather than deleting in place."""
    memory = SessionMemory(max_history=1)
    memory.add_user_input(UserInput(text="a"))
    original = memory.history

    memory.add_user_input(UserInput(text="b"))

    assert memory.history is not original
    assert [m.text for m in original] == ["a", "b"]
    assert [m.text for m in memory.history] == ["b"]
