"""Temporary projection from semantic embodiment intent to the current face."""

from __future__ import annotations

from mira.domain.embodiment import EmbodimentIntent, ExpressionKey, resolve_expression_key
from mira.domain.state import FaceState


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

    return _EXPRESSION_FACE_STATE[resolve_expression_key(intent)]
