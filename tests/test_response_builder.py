from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any


@dataclass
class ActionResult:
    success: bool
    action_name: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class FaceState(Enum):
    IDLE = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()
    HAPPY = auto()
    TIRED = auto()
    ANGRY = auto()
    CONFUSED = auto()


@dataclass
class UserInput:
    text: str
    source: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentResult:
    intent: str
    confidence: float = 1.0
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainResponse:
    text: str
    face_state: FaceState
    metadata: dict[str, Any] = field(default_factory=dict)


def _load_response_builder_module():
    modules = {
        "mira.actions.action_models": types.ModuleType("mira.actions.action_models"),
        "mira.core.models": types.ModuleType("mira.core.models"),
        "mira.ui.face.face_state": types.ModuleType("mira.ui.face.face_state"),
    }
    modules["mira.actions.action_models"].ActionResult = ActionResult
    modules["mira.core.models"].BrainResponse = BrainResponse
    modules["mira.core.models"].IntentResult = IntentResult
    modules["mira.core.models"].UserInput = UserInput
    modules["mira.ui.face.face_state"].FaceState = FaceState

    original_modules = {
        name: sys.modules.get(name)
        for name in modules
    }
    sys.modules.update(modules)
    try:
        module_path = Path(__file__).resolve().parents[1] / "mira/cognition/response_builder.py"
        spec = importlib.util.spec_from_file_location(
            "response_builder_under_test",
            module_path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original_module in original_modules.items():
            if original_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original_module


response_builder = _load_response_builder_module()
ResponseBuilder = response_builder.ResponseBuilder


def build_response(intent: IntentResult, action_result: ActionResult | None = None):
    return ResponseBuilder().build(intent, UserInput(text="ciao"), action_result)


def test_action_result_has_priority_over_llm_response_text():
    intent = IntentResult(
        intent="time_query",
        entities={"llm_response_text": "LLM text", "llm_emotion": "happy"},
    )
    action_result = ActionResult(
        success=True,
        action_name="get_time",
        message="ok",
        data={"time": "12:00"},
    )

    response = build_response(intent, action_result)

    assert response.text == "Sono le 12:00."
    assert response.face_state is FaceState.SPEAKING
    assert response.metadata == {"action_name": "get_time", "time": "12:00"}


def test_non_empty_llm_response_text_is_used_for_non_action_response():
    intent = IntentResult(
        intent="unknown",
        confidence=0.8,
        entities={"llm_response_text": "Risposta LLM", "llm_emotion": "speaking"},
    )

    response = build_response(intent)

    assert response.text == "Risposta LLM"
    assert response.face_state is FaceState.SPEAKING
    assert response.metadata["llm_response_used"] is True


def test_empty_llm_response_text_falls_back_to_deterministic_behavior():
    intent = IntentResult(
        intent="greeting",
        confidence=0.9,
        entities={"llm_response_text": "   ", "llm_emotion": "confused"},
    )

    response = build_response(intent)

    assert response.text == "Ciao. Sono M.I.R.A. Pronto a interagire con te."
    assert response.face_state is FaceState.HAPPY
    assert "llm_response_used" not in response.metadata


def test_empty_input_ignores_llm_response_text():
    intent = IntentResult(
        intent="empty_input",
        confidence=1.0,
        entities={"llm_response_text": "LLM text", "llm_emotion": "happy"},
    )

    response = build_response(intent)

    assert response.text == "Non ho ricevuto alcun input."
    assert response.face_state is FaceState.CONFUSED
    assert "llm_response_used" not in response.metadata


def test_llm_emotion_happy_maps_to_happy_when_llm_text_is_used():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Felice di aiutare.", "llm_emotion": "happy"},
    )

    response = build_response(intent)

    assert response.face_state is FaceState.HAPPY
    assert response.metadata["llm_emotion_used"] == "happy"


def test_llm_emotion_confused_maps_to_confused_when_llm_text_is_used():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Non sono sicuro.", "llm_emotion": "confused"},
    )

    response = build_response(intent)

    assert response.face_state is FaceState.CONFUSED
    assert response.metadata["llm_emotion_used"] == "confused"


def test_llm_emotion_thinking_maps_to_speaking_for_final_response():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Ci penso io.", "llm_emotion": "thinking"},
    )

    response = build_response(intent)

    assert response.face_state is FaceState.SPEAKING
    assert response.metadata["llm_emotion_used"] == "thinking"


def test_invalid_or_missing_llm_emotion_falls_back_to_speaking():
    invalid_intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Va bene.", "llm_emotion": "excited"},
    )
    missing_intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Va bene."},
    )

    invalid_response = build_response(invalid_intent)
    missing_response = build_response(missing_intent)

    assert invalid_response.face_state is FaceState.SPEAKING
    assert missing_response.face_state is FaceState.SPEAKING
    assert "llm_emotion_used" not in invalid_response.metadata
    assert "llm_emotion_used" not in missing_response.metadata


def test_rule_style_intent_without_llm_response_text_behaves_as_before():
    intent = IntentResult(intent="status_query", confidence=1.0)

    response = build_response(intent)

    assert response.text == "Sto funzionando correttamente. Il mio layer cognitivo è attivo."
    assert response.face_state is FaceState.SPEAKING
    assert response.metadata == {"intent": "status_query", "confidence": 1.0}
