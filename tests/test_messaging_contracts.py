"""Characterization tests for event-bus behaviour.

Written against the pre-relocation implementation and expected to hold unchanged
after it moves. These describe what the code *does*, including behaviours that
are deliberately preserved rather than fixed in this tranche:

- `emit` on a name nobody subscribed to creates an empty registry entry, because
  the backing store is a `defaultdict`, so a typo grows the registry silently;
- there is no unsubscribe;
- a subscriber added *during* dispatch is called in that same dispatch;
- one raising subscriber aborts the remaining ones and propagates.

All are reported as findings, not corrected.
"""

from __future__ import annotations

import pytest

from mira.messaging.events import EventBus


# --- delivery basics ----------------------------------------------------


def test_subscriber_receives_the_payload_synchronously():
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe("thing_happened", seen.append)

    bus.emit("thing_happened", {"a": 1})

    # Already delivered by the time emit returns: dispatch is synchronous.
    assert seen == [{"a": 1}]


def test_payload_defaults_to_none():
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe("e", seen.append)

    bus.emit("e")

    assert seen == [None]




def test_only_matching_subscribers_are_called():
    bus = EventBus()
    a: list[object] = []
    b: list[object] = []
    bus.subscribe("a", a.append)
    bus.subscribe("b", b.append)

    bus.emit("a", 1)

    assert a == [1]
    assert b == []


# --- ordering and duplicates -------------------------------------------


def test_subscribers_are_called_in_registration_order():
    bus = EventBus()
    order: list[str] = []
    bus.subscribe("e", lambda payload: order.append("first"))
    bus.subscribe("e", lambda payload: order.append("second"))
    bus.subscribe("e", lambda payload: order.append("third"))

    bus.emit("e")

    assert order == ["first", "second", "third"]


def test_the_same_callback_subscribed_twice_is_called_twice():
    """Preserved: subscribe appends without de-duplicating."""
    bus = EventBus()
    calls: list[int] = []

    def listener(payload: object) -> None:
        calls.append(1)

    bus.subscribe("e", listener)
    bus.subscribe("e", listener)
    bus.emit("e")

    assert calls == [1, 1]



# --- registry growth on unknown events ---------------------------------


def test_emitting_an_unsubscribed_event_delivers_nothing_but_grows_the_registry():
    """Preserved: the defaultdict means emit is not read-only."""
    bus = EventBus()

    bus.emit("never_subscribed", "payload")

    assert "never_subscribed" in bus._subscribers
    assert bus._subscribers["never_subscribed"] == []


# --- mutation during dispatch ------------------------------------------


def test_a_subscriber_added_during_dispatch_runs_in_the_same_dispatch():
    """Preserved: emit iterates the live list by index."""
    bus = EventBus()
    seen: list[str] = []

    def first(payload: object) -> None:
        seen.append("first")
        bus.subscribe("e", lambda _: seen.append("added-during-dispatch"))

    bus.subscribe("e", first)
    bus.emit("e")

    assert seen == ["first", "added-during-dispatch"]


def test_re_emitting_from_within_a_subscriber_recurses_immediately():
    bus = EventBus()
    depth: list[int] = []

    def listener(payload: object) -> None:
        depth.append(len(depth))
        if len(depth) < 3:
            bus.emit("e")

    bus.subscribe("e", listener)
    bus.emit("e")

    assert depth == [0, 1, 2]


# --- exception handling -------------------------------------------------


def test_a_raising_subscriber_aborts_the_rest_and_propagates():
    """Preserved: there is no per-subscriber error isolation."""
    bus = EventBus()
    order: list[str] = []

    def boom(payload: object) -> None:
        raise RuntimeError("subscriber failed")

    bus.subscribe("e", lambda payload: order.append("before"))
    bus.subscribe("e", boom)
    bus.subscribe("e", lambda payload: order.append("after"))

    with pytest.raises(RuntimeError, match="subscriber failed"):
        bus.emit("e")

    assert order == ["before"]


def test_the_bus_stays_usable_after_a_subscriber_raised():
    bus = EventBus()
    calls: list[str] = []
    def boom(payload: object) -> None:
        raise RuntimeError("x")

    bus.subscribe("bad", boom)
    bus.subscribe("good", lambda payload: calls.append("good"))

    with pytest.raises(RuntimeError):
        bus.emit("bad")

    bus.emit("good")
    assert calls == ["good"]


# --- ownership and lifetime --------------------------------------------


def test_buses_are_independent_instances_with_no_shared_registry():
    """Preserved: no module-level singleton or shared state."""
    first = EventBus()
    second = EventBus()
    seen: list[str] = []
    first.subscribe("e", lambda payload: seen.append("first-bus"))

    second.emit("e")

    assert seen == []
