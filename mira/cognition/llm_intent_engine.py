from __future__ import annotations

import json
from typing import Any

from mira.cognition.intent_engine import IntentEngine
from mira.cognition.llm_client import LLMClientError, OllamaClient
from mira.cognition.llm_schema import ALLOWED_ACTIONS, ALLOWED_INTENTS, LLM_INTENT_SCHEMA
from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.core.models import IntentResult, UserInput


class LLMIntentEngine(IntentEngine):
    """
    LLM-backed intent engine.

    It converts natural language into the same IntentResult format used by
    the rule-based engine, so the rest of the architecture remains unchanged.
    """

    def __init__(
        self,
        client: OllamaClient | None = None,
        fallback_engine: IntentEngine | None = None,
    ):
        self.client = client or OllamaClient()
        self.fallback_engine = fallback_engine or RuleIntentEngine()

    def infer(self, user_input: UserInput) -> IntentResult:
        if not user_input.text.strip():
            return IntentResult(intent="empty_input", confidence=1.0)

        prompt = self._build_prompt(user_input)

        try:
            raw_result = self.client.generate_structured(
                prompt=prompt,
                schema=LLM_INTENT_SCHEMA,
                temperature=0.0,
            )
        except LLMClientError as exc:
            print(f"[LLM] Falling back to rule engine: {exc}")
            return self.fallback_engine.infer(user_input)

        return self._to_intent_result(raw_result, user_input)

    def _build_prompt(self, user_input: UserInput) -> str:
        allowed_intents = ", ".join(ALLOWED_INTENTS)
        allowed_actions = ", ".join(ALLOWED_ACTIONS)

        return f"""
You are the intent parser for N.E.R.O, a modular embodied robotic assistant.

Your task is NOT to answer conversationally.
Your task is to classify the user's input into a structured JSON object.

Allowed intents:
{allowed_intents}

Allowed actions:
{allowed_actions}

Rules:
- Return only JSON matching the provided schema.
- Do not invent intents outside the allowed list.
- Do not invent actions outside the allowed list.
- If no action is needed, set action_name to null and parameters to {{}}.
- If the user asks to open a website, use intent "open_url_request" and action "open_url".
- If the user asks to open a local app, use intent "open_app_request" and action "open_app".
- If the user asks for time, use intent "time_query" and action "get_time".
- If the user asks for date, use intent "date_query" and action "get_date".
- If the user asks for a notification, use intent "notification_request" and action "show_notification".
- If the user asks about available actions/capabilities, use intent "list_actions" and action "list_available_actions".
- If the user asks for system information, use intent "system_info_query" and action "get_system_info".
- If the user asks to repeat something, use intent "echo_request" and action "echo_text".
- If the user asks about session memory, use the most appropriate memory-related intent/action.
- For unknown or unsupported requests, use intent "unknown", action_name null, and parameters {{}}.

Entity extraction:
- For open_url, parameters must contain: {{"url": "..."}}
- For open_app, parameters must contain: {{"app_name": "..."}}
- For echo_text, parameters must contain: {{"text": "..."}}
- For show_notification, parameters must contain: {{"text": "..."}}

User input:
{user_input.text}
""".strip()

    def _to_intent_result(
        self,
        raw_result: dict[str, Any],
        user_input: UserInput,
    ) -> IntentResult:
        intent = str(raw_result.get("intent", "unknown")).strip()
        confidence = self._safe_confidence(raw_result.get("confidence", 0.50))
        action_name = raw_result.get("action_name")
        parameters = raw_result.get("parameters", {})
        response_text = str(raw_result.get("response_text", "")).strip()
        emotion = str(raw_result.get("emotion", "neutral")).strip()

        if intent not in ALLOWED_INTENTS:
            intent = "unknown"
            confidence = 0.25

        if not isinstance(parameters, dict):
            parameters = {}

        if action_name is not None and action_name not in ALLOWED_ACTIONS:
            action_name = None

        entities = {
            **parameters,
            "llm_action_name": action_name,
            "llm_response_text": response_text,
            "llm_emotion": emotion,
            "llm_raw": json.dumps(raw_result, ensure_ascii=False),
        }

        return IntentResult(
            intent=intent,
            confidence=confidence,
            entities=entities,
        )

    def _safe_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.50

        return max(0.0, min(1.0, confidence))