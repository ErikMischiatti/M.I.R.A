"""UI-independent vocabulary for what MIRA's embodiment should express."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

class ActivityState(StrEnum):
    """What the assistant is doing, independent of emotional colouring."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class AffectState(StrEnum):
    """Affects produced by current runtime behaviour—no speculative emotions."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    CONFUSED = "confused"


class ExpressionKey(StrEnum):
    """Semantic override keys for the current compatibility profile inventory.

    This migration vocabulary is renderer-independent at its use sites; its
    members intentionally preserve every selectable legacy profile until the
    presentation-only keys can be retired with that renderer.
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    TIRED = "tired"
    ANGRY = "angry"
    CONFUSED = "confused"


@dataclass(frozen=True, slots=True)
class EmbodimentIntent:
    """The smallest current request to the embodiment presentation boundary."""

    activity: ActivityState
    affect: AffectState = AffectState.NEUTRAL
    expression: ExpressionKey | None = None


_ACTIVITY_EXPRESSION = {
    ActivityState.IDLE: ExpressionKey.IDLE,
    ActivityState.LISTENING: ExpressionKey.LISTENING,
    ActivityState.THINKING: ExpressionKey.THINKING,
    ActivityState.SPEAKING: ExpressionKey.SPEAKING,
}

_AFFECT_EXPRESSION = {
    AffectState.HAPPY: ExpressionKey.HAPPY,
    AffectState.CONFUSED: ExpressionKey.CONFUSED,
}


def resolve_expression_key(intent: EmbodimentIntent) -> ExpressionKey:
    """Choose an expression using override, affect, then activity precedence."""

    if intent.expression is not None:
        return intent.expression
    if intent.affect is not AffectState.NEUTRAL:
        return _AFFECT_EXPRESSION[intent.affect]
    return _ACTIVITY_EXPRESSION[intent.activity]
