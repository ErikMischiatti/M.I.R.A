"""Domain vocabulary and the temporary legacy-face compatibility boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mira.domain.embodiment import (
    ActivityState,
    AffectState,
    EmbodimentIntent,
    ExpressionKey,
)
from mira.domain.embodiment_compatibility import resolve_face_state
from mira.domain.state import FaceState


@pytest.mark.parametrize(
    "intent,expected",
    [
        (EmbodimentIntent(ActivityState.LISTENING), FaceState.LISTENING),
        (
            EmbodimentIntent(ActivityState.LISTENING, AffectState.HAPPY),
            FaceState.HAPPY,
        ),
        (
            EmbodimentIntent(ActivityState.THINKING, AffectState.CONFUSED),
            FaceState.CONFUSED,
        ),
        (EmbodimentIntent(ActivityState.IDLE, AffectState.HAPPY), FaceState.HAPPY),
    ],
)
def test_activity_and_affect_combinations_resolve_explicitly(intent, expected):
    assert resolve_face_state(intent) is expected


def test_expression_override_has_precedence_over_affect_and_activity():
    intent = EmbodimentIntent(
        activity=ActivityState.THINKING,
        affect=AffectState.HAPPY,
        expression=ExpressionKey.ANGRY,
    )
    assert resolve_face_state(intent) is FaceState.ANGRY


def test_all_current_activity_affect_combinations_are_resolvable():
    resolved = {
        (activity, affect): resolve_face_state(EmbodimentIntent(activity, affect))
        for activity in ActivityState
        for affect in AffectState
    }
    assert len(resolved) == len(ActivityState) * len(AffectState)
    assert all(isinstance(state, FaceState) for state in resolved.values())


def test_migration_expression_keys_cover_the_current_profile_inventory():
    # A compatibility guarantee for this tranche, not a requirement that the
    # independent vocabularies remain one-for-one after the legacy face retires.
    assert {key.name for key in ExpressionKey} == {state.name for state in FaceState}


def test_expression_override_is_presentation_only_for_decay_timing():
    """Overrides do not invent runtime timing semantics in this tranche."""
    from mira.core.embodied_behavior import EmbodiedBehavior

    intent = EmbodimentIntent(
        ActivityState.THINKING,
        AffectState.HAPPY,
        ExpressionKey.SPEAKING,
    )
    behavior = object.__new__(EmbodiedBehavior)
    assert behavior._get_decay_delay(intent) == 2200


def test_embodiment_intent_has_value_semantics_and_is_immutable():
    first = EmbodimentIntent(ActivityState.LISTENING, AffectState.HAPPY)
    second = EmbodimentIntent(ActivityState.LISTENING, AffectState.HAPPY)
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        first.affect = AffectState.NEUTRAL  # type: ignore[misc]
