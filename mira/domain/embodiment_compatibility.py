"""Temporary projection from semantic embodiment intent to the current face."""

from __future__ import annotations

from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent, ExpressionKey
from mira.domain.state import FaceState


_ACTIVITY_FACE_STATE = {
    ActivityState.IDLE: FaceState.IDLE,
    ActivityState.LISTENING: FaceState.LISTENING,
    ActivityState.THINKING: FaceState.THINKING,
    ActivityState.SPEAKING: FaceState.SPEAKING,
}

_AFFECT_FACE_STATE = {
    AffectState.HAPPY: FaceState.HAPPY,
    AffectState.CONFUSED: FaceState.CONFUSED,
}

_EXPRESSION_FACE_STATE = {
    ExpressionKey.IDLE: FaceState.IDLE,
    ExpressionKey.LISTENING: FaceState.LISTENING,
    ExpressionKey.THINKING: FaceState.THINKING,
    ExpressionKey.SPEAKING: FaceState.SPEAKING,
    ExpressionKey.HAPPY: FaceState.HAPPY,
    ExpressionKey.TIRED: FaceState.TIRED,
    ExpressionKey.ANGRY: FaceState.ANGRY,
    ExpressionKey.CONFUSED: FaceState.CONFUSED,
}


def resolve_face_state(intent: EmbodimentIntent) -> FaceState:
    """Collapse semantic state using override, affect, then activity precedence."""

    if intent.expression is not None:
        return _EXPRESSION_FACE_STATE[intent.expression]
    if intent.affect is not AffectState.NEUTRAL:
        return _AFFECT_FACE_STATE[intent.affect]
    return _ACTIVITY_FACE_STATE[intent.activity]
