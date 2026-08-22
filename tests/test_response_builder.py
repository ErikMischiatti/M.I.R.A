from __future__ import annotations

from mira.actions.action_models import ActionResult
from mira.cognition.response_builder import ResponseBuilder
from mira.domain.models import IntentResult, UserInput
from mira.domain.embodiment_compatibility import resolve_face_state
from mira.domain.state import FaceState


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
    assert resolve_face_state(response.embodiment) is FaceState.SPEAKING
    assert response.metadata == {"action_name": "get_time", "time": "12:00"}


def test_successful_action_result_produces_user_facing_response():
    intent = IntentResult(intent="system_info_query", confidence=0.9)
    action_result = ActionResult(
        success=True,
        action_name="get_system_info",
        message="Sistema: Linux.",
        data={"platform": "Linux"},
    )

    response = build_response(intent, action_result)

    assert response.text == "Sistema: Linux."
    assert resolve_face_state(response.embodiment) is FaceState.SPEAKING
    assert response.metadata == {"action_name": "get_system_info", "platform": "Linux"}


def test_failed_action_result_produces_user_facing_failure_response():
    intent = IntentResult(intent="open_app_request", confidence=0.9)
    action_result = ActionResult(
        success=False,
        action_name="open_app",
        message="Applicazione non disponibile.",
        data={"requested_app": "missing"},
    )

    response = build_response(intent, action_result)

    assert response.text == "Applicazione non disponibile."
    assert resolve_face_state(response.embodiment) is FaceState.CONFUSED
    assert response.metadata == {
        "action_name": "open_app",
        "requested_app": "missing",
    }


def test_unsupported_action_result_produces_safe_failure_response():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "I should not override the action."},
    )
    action_result = ActionResult(
        success=False,
        action_name="missing_action",
        message="Azione 'missing_action' non disponibile.",
    )

    response = build_response(intent, action_result)

    assert response.text == "Azione 'missing_action' non disponibile."
    assert resolve_face_state(response.embodiment) is FaceState.CONFUSED
    assert response.metadata == {"action_name": "missing_action"}


def test_non_empty_llm_response_text_is_used_for_non_action_response():
    intent = IntentResult(
        intent="unknown",
        confidence=0.8,
        entities={"llm_response_text": "Risposta LLM", "llm_emotion": "speaking"},
    )

    response = build_response(intent)

    assert response.text == "Risposta LLM"
    assert resolve_face_state(response.embodiment) is FaceState.SPEAKING
    assert response.metadata["llm_response_used"] is True


def test_empty_llm_response_text_falls_back_to_deterministic_behavior():
    intent = IntentResult(
        intent="greeting",
        confidence=0.9,
        entities={"llm_response_text": "   ", "llm_emotion": "confused"},
    )

    response = build_response(intent)

    assert response.text == "Ciao. Sono M.I.R.A. Pronto a interagire con te."
    assert resolve_face_state(response.embodiment) is FaceState.HAPPY
    assert "llm_response_used" not in response.metadata


def test_empty_input_ignores_llm_response_text():
    intent = IntentResult(
        intent="empty_input",
        confidence=1.0,
        entities={"llm_response_text": "LLM text", "llm_emotion": "happy"},
    )

    response = build_response(intent)

    assert response.text == "Non ho ricevuto alcun input."
    assert resolve_face_state(response.embodiment) is FaceState.CONFUSED
    assert "llm_response_used" not in response.metadata


def test_llm_emotion_happy_maps_to_happy_when_llm_text_is_used():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Felice di aiutare.", "llm_emotion": "happy"},
    )

    response = build_response(intent)

    assert resolve_face_state(response.embodiment) is FaceState.HAPPY
    assert response.metadata["llm_emotion_used"] == "happy"


def test_llm_emotion_confused_maps_to_confused_when_llm_text_is_used():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Non sono sicuro.", "llm_emotion": "confused"},
    )

    response = build_response(intent)

    assert resolve_face_state(response.embodiment) is FaceState.CONFUSED
    assert response.metadata["llm_emotion_used"] == "confused"


def test_llm_emotion_thinking_maps_to_speaking_for_final_response():
    intent = IntentResult(
        intent="unknown",
        entities={"llm_response_text": "Ci penso io.", "llm_emotion": "thinking"},
    )

    response = build_response(intent)

    assert resolve_face_state(response.embodiment) is FaceState.SPEAKING
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

    assert resolve_face_state(invalid_response.embodiment) is FaceState.SPEAKING
    assert resolve_face_state(missing_response.embodiment) is FaceState.SPEAKING
    assert "llm_emotion_used" not in invalid_response.metadata
    assert "llm_emotion_used" not in missing_response.metadata


def test_rule_style_intent_without_llm_response_text_behaves_as_before():
    intent = IntentResult(intent="status_query", confidence=1.0)

    response = build_response(intent)

    assert response.text == "Sto funzionando correttamente. Il mio layer cognitivo è attivo."
    assert resolve_face_state(response.embodiment) is FaceState.SPEAKING
    assert response.metadata == {"intent": "status_query", "confidence": 1.0}


def test_project_path_action_result_produces_user_facing_response():
    intent = IntentResult(intent="project_path_query", confidence=0.9)
    action_result = ActionResult(
        success=True,
        action_name="get_project_path",
        message="La cartella del progetto è /tmp/project.",
        data={"path": "/tmp/project"},
    )

    response = build_response(intent, action_result)

    assert response.text == "La cartella del progetto è /tmp/project."
    assert resolve_face_state(response.embodiment) is FaceState.SPEAKING
    assert response.metadata == {
        "action_name": "get_project_path",
        "path": "/tmp/project",
    }
