from __future__ import annotations

import pytest

from mira.cognition.llm_client import LLMClientError
from mira.cognition.llm_intent_engine import LLMIntentEngine
from mira.core.models import IntentResult, UserInput


class FakeClient:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeFallbackEngine:
    def __init__(self, result: IntentResult | None = None):
        self.result = result or IntentResult(
            intent="time_query",
            confidence=0.95,
            entities={"source": "fallback"},
        )
        self.calls = []

    def infer(self, user_input: UserInput) -> IntentResult:
        self.calls.append(user_input)
        return self.result


def infer_with(raw_result, text: str = "che ore sono"):
    client = FakeClient(raw_result)
    fallback = FakeFallbackEngine()
    engine = LLMIntentEngine(client=client, fallback_engine=fallback)

    result = engine.infer(UserInput(text=text))

    return result, client, fallback


def test_valid_llm_json_is_converted_to_intent_result():
    result, client, fallback = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.87,
            "emotion": "speaking",
            "action_name": "open_url",
            "parameters": {"url": "example.com"},
            "response_text": "",
        },
        text="apri example.com",
    )

    assert result.intent == "open_url_request"
    assert result.confidence == 0.87
    assert result.entities["url"] == "example.com"
    assert result.entities["llm_action_name"] == "open_url"
    assert result.entities["llm_response_text"] == ""
    assert result.entities["llm_emotion"] == "speaking"
    assert result.entities["llm_raw"]
    assert client.calls
    assert fallback.calls == []


def test_unknown_or_disallowed_intent_is_normalized_and_cannot_execute_action():
    result, _, _ = infer_with(
        {
            "intent": "delete_files",
            "confidence": 0.99,
            "emotion": "neutral",
            "action_name": "get_time",
            "parameters": {},
            "response_text": "",
        }
    )

    assert result.intent == "unknown"
    assert result.confidence == 0.25
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "intent_unknown"


def test_unknown_or_disallowed_action_name_is_removed():
    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.8,
            "emotion": "neutral",
            "action_name": "launch_missiles",
            "parameters": {"url": "example.com"},
            "response_text": "",
        }
    )

    assert result.intent == "open_url_request"
    assert result.entities["url"] == "example.com"
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "action_unknown"


def test_incompatible_action_for_intent_is_rejected():
    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.8,
            "emotion": "neutral",
            "action_name": "get_time",
            "parameters": {"url": "example.com"},
            "response_text": "",
        }
    )

    assert result.intent == "open_url_request"
    assert result.entities["url"] == "example.com"
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "intent_action_mismatch"


def test_allowed_action_with_wrong_param_type_is_rejected():
    result, _, _ = infer_with(
        {
            "intent": "echo_request",
            "confidence": 0.8,
            "emotion": "neutral",
            "action_name": "echo_text",
            "parameters": {"text": 123},
            "response_text": "",
        }
    )

    assert result.intent == "echo_request"
    assert result.entities["text"] == 123
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "missing_or_invalid_param:text"


def test_known_unknown_intent_with_allowed_action_is_rejected():
    result, _, _ = infer_with(
        {
            "intent": "unknown",
            "confidence": 0.5,
            "emotion": "neutral",
            "action_name": "get_time",
            "parameters": {},
            "response_text": "",
        }
    )

    assert result.intent == "unknown"
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "intent_action_mismatch"


@pytest.mark.parametrize("parameters", [None, [], "url=example.com", 3])
def test_non_dict_action_params_reject_action(parameters):
    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.8,
            "emotion": "neutral",
            "action_name": "open_url",
            "parameters": parameters,
            "response_text": "",
        }
    )

    assert "url" not in result.entities
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "parameters_type"


def test_missing_action_params_reject_action():
    result, _, _ = infer_with(
        {
            "intent": "echo_request",
            "confidence": 0.8,
            "emotion": "neutral",
            "action_name": "echo_text",
            "parameters": {},
            "response_text": "",
        }
    )

    assert result.intent == "echo_request"
    assert result.entities["llm_action_name"] is None
    assert "text" not in result.entities
    assert result.entities["llm_action_validation_failed"] is True
    assert result.entities["llm_action_validation_reason"] == "missing_or_invalid_param:text"


def test_invalid_schema_object_falls_back_to_rule_engine():
    fallback_result = IntentResult(
        intent="date_query",
        confidence=0.9,
        entities={"source": "fallback"},
    )
    fallback = FakeFallbackEngine(fallback_result)
    engine = LLMIntentEngine(
        client=FakeClient(["not", "an", "object"]),
        fallback_engine=fallback,
    )
    user_input = UserInput(text="dimmi la data")

    result = engine.infer(user_input)

    assert result is fallback_result
    assert fallback.calls == [user_input]


@pytest.mark.parametrize(
    "error",
    [
        LLMClientError("Ollama returned invalid JSON"),
        LLMClientError("Ollama request timed out."),
    ],
)
def test_malformed_json_timeout_or_client_error_falls_back_to_rule_engine(error):
    fallback_result = IntentResult(intent="greeting", confidence=0.95)
    fallback = FakeFallbackEngine(fallback_result)
    engine = LLMIntentEngine(client=FakeClient(error=error), fallback_engine=fallback)
    user_input = UserInput(text="ciao")

    result = engine.infer(user_input)

    assert result is fallback_result
    assert fallback.calls == [user_input]


def test_empty_input_does_not_call_llm_or_fallback():
    client = FakeClient({})
    fallback = FakeFallbackEngine()
    engine = LLMIntentEngine(client=client, fallback_engine=fallback)

    result = engine.infer(UserInput(text="   "))

    assert result.intent == "empty_input"
    assert result.confidence == 1.0
    assert client.calls == []
    assert fallback.calls == []


def test_confidence_is_clamped_and_non_numeric_confidence_is_safe():
    high_result, _, _ = infer_with(
        {
            "intent": "greeting",
            "confidence": 3,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        }
    )
    bad_result, _, _ = infer_with(
        {
            "intent": "greeting",
            "confidence": "sure",
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        }
    )

    assert high_result.confidence == 1.0
    assert bad_result.confidence == 0.50


def test_invalid_emotion_is_preserved_only_as_metadata_for_response_builder_safety():
    result, _, _ = infer_with(
        {
            "intent": "unknown",
            "confidence": 0.7,
            "emotion": "excited",
            "action_name": None,
            "parameters": {},
            "response_text": "Va bene.",
        }
    )

    assert result.entities["llm_emotion"] == "excited"
    assert result.entities["llm_response_text"] == "Va bene."


def test_prompt_does_not_include_session_history_context():
    _, client, _ = infer_with(
        {
            "intent": "unknown",
            "confidence": 0.5,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        },
        text="come mi chiamo?",
    )

    prompt = client.calls[0]["prompt"]
    assert "User input:\ncome mi chiamo?" in prompt
    assert "Recent history" not in prompt
    assert "session context" not in prompt.lower()
