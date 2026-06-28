from __future__ import annotations

import pytest

from mira.actions.action_models import ActionContract
from mira.actions.action_registry import ActionRegistry
from mira.cognition.llm_client import LLMClientError
from mira.cognition.llm_intent_engine import LLMIntentEngine
from mira.cognition.session_context_builder import SessionContextBuilder
from mira.core.models import IntentResult, UserInput
from mira.core.session_memory import MemoryMessage, SessionMemory


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



def test_llm_action_validation_uses_supplied_action_metadata():
    registry = ActionRegistry()
    registry.register_contract(
        ActionContract(
            name="custom_echo",
            compatible_intents=frozenset({"echo_request"}),
            required_params={"text": str},
        )
    )
    client = FakeClient(
        {
            "intent": "echo_request",
            "confidence": 0.8,
            "emotion": "neutral",
            "action_name": "custom_echo",
            "parameters": {"text": "ciao"},
            "response_text": "",
        }
    )
    engine = LLMIntentEngine(
        client=client,
        fallback_engine=FakeFallbackEngine(),
        action_registry=registry,
    )

    result = engine.infer(UserInput(text="ripeti ciao"))

    assert result.intent == "echo_request"
    assert result.entities["llm_action_name"] == "custom_echo"
    assert result.entities["text"] == "ciao"
    assert "llm_action_validation_failed" not in result.entities


def test_prompt_action_list_and_required_params_come_from_action_metadata():
    registry = ActionRegistry()
    registry.register_contract(
        ActionContract(
            name="custom_echo",
            compatible_intents=frozenset({"echo_request"}),
            required_params={"text": str},
        )
    )
    client = FakeClient(
        {
            "intent": "unknown",
            "confidence": 0.5,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        }
    )
    engine = LLMIntentEngine(
        client=client,
        fallback_engine=FakeFallbackEngine(),
        action_registry=registry,
    )

    engine.infer(UserInput(text="ripeti ciao"))

    prompt = client.calls[0]["prompt"]
    allowed_actions_section = prompt.split("Allowed actions:\n", 1)[1].split("\n\nRules:", 1)[0]
    assert allowed_actions_section == "custom_echo"
    assert "- echo_request: custom_echo" in prompt
    assert "- For custom_echo, parameters must contain: {\"text\"}" in prompt
    assert "open_url" not in allowed_actions_section

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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "unsupported_intent"


def test_unsupported_intent_without_action_gets_fallback_diagnostics():
    result, _, _ = infer_with(
        {
            "intent": "unsupported_smalltalk",
            "confidence": 0.7,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "Non sono sicuro.",
        }
    )

    assert result.intent == "unknown"
    assert result.confidence == 0.25
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_response_text"] == "Non sono sicuro."
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "unsupported_intent"


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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "unknown_action"


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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "intent_action_mismatch"


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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "invalid_parameters"


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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "intent_action_mismatch"


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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "invalid_parameters"


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
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "invalid_parameters"


def test_invalid_response_falls_back_to_rule_engine_with_diagnostics():
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

    assert result.intent == fallback_result.intent
    assert result.confidence == fallback_result.confidence
    assert result.entities["source"] == "fallback"
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "invalid_response"
    assert fallback.calls == [user_input]


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (LLMClientError("Ollama returned invalid JSON"), "invalid_json"),
        (LLMClientError("Ollama request timed out."), "client_error"),
        (LLMClientError("Ollama returned an empty response."), "invalid_response"),
    ],
)
def test_client_error_falls_back_to_rule_engine_with_diagnostics(error, expected_reason):
    fallback_result = IntentResult(intent="greeting", confidence=0.95)
    fallback = FakeFallbackEngine(fallback_result)
    engine = LLMIntentEngine(client=FakeClient(error=error), fallback_engine=fallback)
    user_input = UserInput(text="ciao")

    result = engine.infer(user_input)

    assert result.intent == fallback_result.intent
    assert result.confidence == fallback_result.confidence
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == expected_reason
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


def test_prompt_includes_empty_context_marker_without_session_memory():
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
    assert "Recent conversation context:\n(no previous conversation context)" in prompt
    assert "Current user input:\ncome mi chiamo?" in prompt


def test_prompt_includes_sanitized_recent_session_context_separate_from_current_input():
    memory = SessionMemory()
    memory.history.append(MemoryMessage(role="user", text="mi chiamo Erik"))
    memory.history.append(
        MemoryMessage(
            role="assistant",
            text="Va bene, Erik.",
            metadata={"llm_raw": "{secret}", "action_name": "get_system_info"},
        )
    )
    memory.history.append(MemoryMessage(role="user", text="come mi chiamo?"))
    client = FakeClient(
        {
            "intent": "unknown",
            "confidence": 0.5,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        }
    )
    engine = LLMIntentEngine(
        client=client,
        fallback_engine=FakeFallbackEngine(),
        session_memory=memory,
    )

    engine.infer(UserInput(text="come mi chiamo?"))

    prompt = client.calls[0]["prompt"]
    context_section = prompt.split("Recent conversation context:\n", 1)[1].split(
        "\n\nCurrent user input:", 1
    )[0]
    assert "User: mi chiamo Erik" in context_section
    assert "Assistant: Va bene, Erik." in context_section
    assert "come mi chiamo?" not in context_section
    assert "llm_raw" not in context_section
    assert "secret" not in context_section
    assert "get_system_info" not in context_section
    assert "Current user input:\ncome mi chiamo?" in prompt


def test_prompt_session_context_is_bounded():
    memory = SessionMemory()
    for index in range(6):
        memory.history.append(
            MemoryMessage(role="user", text=f"message-{index} " + ("x" * 40))
        )
    client = FakeClient(
        {
            "intent": "unknown",
            "confidence": 0.5,
            "emotion": "neutral",
            "action_name": None,
            "parameters": {},
            "response_text": "",
        }
    )
    engine = LLMIntentEngine(
        client=client,
        fallback_engine=FakeFallbackEngine(),
        context_builder=SessionContextBuilder(memory, max_messages=2, max_chars=90),
    )

    engine.infer(UserInput(text="continua"))

    prompt = client.calls[0]["prompt"]
    context_section = prompt.split("Recent conversation context:\n", 1)[1].split(
        "\n\nCurrent user input:", 1
    )[0]
    assert "message-0" not in context_section
    assert "message-5" in context_section
    assert len(context_section) <= 110


def test_low_confidence_llm_action_is_suppressed_with_default_threshold(monkeypatch):
    monkeypatch.delenv("MIRA_LLM_ACTION_MIN_CONFIDENCE", raising=False)

    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.64,
            "emotion": "speaking",
            "action_name": "open_url",
            "parameters": {"url": "example.com"},
            "response_text": "Posso aprire quel sito.",
        },
        text="apri example.com",
    )

    assert result.intent == "open_url_request"
    assert result.confidence == 0.64
    assert result.entities["llm_action_name"] is None
    assert "url" not in result.entities
    assert result.entities["llm_response_text"] == "Posso aprire quel sito."
    assert result.entities["action_suppressed_reason"] == "low_confidence"
    assert result.entities["action_min_confidence"] == 0.65
    assert result.entities["llm_fallback_used"] is True
    assert result.entities["llm_fallback_reason"] == "low_confidence_action"


def test_high_confidence_llm_action_is_preserved_at_threshold(monkeypatch):
    monkeypatch.setenv("MIRA_LLM_ACTION_MIN_CONFIDENCE", "0.65")

    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.65,
            "emotion": "speaking",
            "action_name": "open_url",
            "parameters": {"url": "example.com"},
            "response_text": "",
        },
        text="apri example.com",
    )

    assert result.entities["llm_action_name"] == "open_url"
    assert result.entities["url"] == "example.com"
    assert "action_suppressed_reason" not in result.entities


def test_no_action_llm_output_is_not_confidence_suppressed(monkeypatch):
    monkeypatch.setenv("MIRA_LLM_ACTION_MIN_CONFIDENCE", "0.95")

    result, _, _ = infer_with(
        {
            "intent": "greeting",
            "confidence": 0.2,
            "emotion": "happy",
            "action_name": None,
            "parameters": {},
            "response_text": "Ciao.",
        },
        text="ciao",
    )

    assert result.intent == "greeting"
    assert result.entities["llm_action_name"] is None
    assert result.entities["llm_response_text"] == "Ciao."
    assert "action_suppressed_reason" not in result.entities
    assert "action_min_confidence" not in result.entities


def test_custom_llm_action_min_confidence_is_respected(monkeypatch):
    monkeypatch.setenv("MIRA_LLM_ACTION_MIN_CONFIDENCE", "0.9")

    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.89,
            "emotion": "speaking",
            "action_name": "open_url",
            "parameters": {"url": "example.com"},
            "response_text": "Posso aprire quel sito.",
        },
        text="apri example.com",
    )

    assert result.entities["llm_action_name"] is None
    assert "url" not in result.entities
    assert result.entities["action_suppressed_reason"] == "low_confidence"
    assert result.entities["action_min_confidence"] == 0.9


def test_invalid_llm_action_min_confidence_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MIRA_LLM_ACTION_MIN_CONFIDENCE", "not-a-number")

    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.64,
            "emotion": "speaking",
            "action_name": "open_url",
            "parameters": {"url": "example.com"},
            "response_text": "Posso aprire quel sito.",
        },
        text="apri example.com",
    )

    assert result.entities["llm_action_name"] is None
    assert result.entities["action_suppressed_reason"] == "low_confidence"
    assert result.entities["action_min_confidence"] == 0.65


def test_llm_action_min_confidence_is_clamped_to_sane_range(monkeypatch):
    monkeypatch.setenv("MIRA_LLM_ACTION_MIN_CONFIDENCE", "2.0")

    result, _, _ = infer_with(
        {
            "intent": "open_url_request",
            "confidence": 0.99,
            "emotion": "speaking",
            "action_name": "open_url",
            "parameters": {"url": "example.com"},
            "response_text": "Posso aprire quel sito.",
        },
        text="apri example.com",
    )

    assert result.entities["llm_action_name"] is None
    assert result.entities["action_suppressed_reason"] == "low_confidence"
    assert result.entities["action_min_confidence"] == 1.0
