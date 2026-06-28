from __future__ import annotations

import json
import logging
import os
from typing import Any

from mira.actions.action_contracts import build_action_contract_registry
from mira.actions.action_registry import ActionRegistry
from mira.cognition.intent_engine import IntentEngine
from mira.cognition.llm_client import LLMClientError, OllamaClient
from mira.cognition.llm_schema import (
    ALLOWED_INTENTS,
    LLM_INTENT_SCHEMA,
    describe_action_intent_compatibility,
    describe_required_action_params,
    validate_llm_action_for_intent,
)
from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.cognition.session_context_builder import SessionContextBuilder
from mira.core.models import IntentResult, UserInput
from mira.core.session_memory import SessionMemory


DEFAULT_LLM_ACTION_MIN_CONFIDENCE = 0.65
LLM_ACTION_MIN_CONFIDENCE_ENV = "MIRA_LLM_ACTION_MIN_CONFIDENCE"

LLM_VALIDATION_FALLBACK_REASONS = {
    "intent_unknown": "unsupported_intent",
    "action_unknown": "unknown_action",
    "intent_action_mismatch": "intent_action_mismatch",
    "parameters_type": "invalid_parameters",
    "action_name": "unknown_action",
}

logger = logging.getLogger(__name__)


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
        action_registry: ActionRegistry | None = None,
        session_memory: SessionMemory | None = None,
        context_builder: SessionContextBuilder | None = None,
    ):
        self.client = client or OllamaClient()
        self.fallback_engine = fallback_engine or RuleIntentEngine()
        self.action_registry = action_registry or build_action_contract_registry()
        self.context_builder = context_builder
        if self.context_builder is None and session_memory is not None:
            self.context_builder = SessionContextBuilder(session_memory)
        self.action_min_confidence = self._action_min_confidence_from_environment()

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
            reason = self._fallback_reason_from_client_error(exc)
            logger.warning("Falling back to rule intent engine: %s", exc)
            return self._fallback_with_reason(user_input, reason)

        if not isinstance(raw_result, dict):
            logger.warning("Falling back to rule intent engine: invalid LLM response type")
            return self._fallback_with_reason(user_input, "invalid_response")

        return self._to_intent_result(raw_result, user_input)

    def _fallback_with_reason(
        self,
        user_input: UserInput,
        reason: str,
    ) -> IntentResult:
        result = self.fallback_engine.infer(user_input)
        entities = dict(result.entities)
        entities["llm_fallback_used"] = True
        entities["llm_fallback_reason"] = reason

        return IntentResult(
            intent=result.intent,
            confidence=result.confidence,
            entities=entities,
        )

    def _fallback_reason_from_client_error(self, exc: LLMClientError) -> str:
        message = str(exc).lower()
        if "invalid json" in message:
            return "invalid_json"
        if "empty response" in message:
            return "invalid_response"
        return "client_error"

    def _build_prompt(self, user_input: UserInput) -> str:
        allowed_intents = ", ".join(ALLOWED_INTENTS)
        allowed_actions = ", ".join(self.action_registry.list_contract_names())
        compatibility = "\n".join(
            describe_action_intent_compatibility(self.action_registry)
        )
        required_params = "\n".join(describe_required_action_params(self.action_registry))
        session_context = self._build_session_context_block(user_input)

        return f"""
You are the intent parser for N.E.R.O, a provisional cognitive name inside M.I.R.A., a modular embodied robotic assistant.

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
- If the user says their name, use intent "set_user_name" and put the first-name-like value in parameters.user_name.
- If the user asks what their name is, use intent "ask_user_name".
- If the user asks who you are, use intent "identity_query".
- If the user asks what project or context you are discussing, use intent "project_context".
- If the user asks to open a website, use intent "open_url_request".
- If the user asks to open a local app, use intent "open_app_request".
- If the user asks to open a local folder or directory, use intent "open_directory_request".
- If the user asks for time, use intent "time_query".
- If the user asks for date, use intent "date_query".
- If the user asks for a notification, use intent "notification_request".
- If the user asks about available actions/capabilities, use intent "list_actions".
- If the user asks for system information, use intent "system_info_query".
- If the user asks to repeat something, use intent "echo_request".
- If the user asks about session memory, use the most appropriate memory-related intent.
- Use only actions compatible with the selected intent.
- For unknown or unsupported requests, use intent "unknown", action_name null, and parameters {{}}.

Action compatibility:
{compatibility}

Entity extraction:
{required_params}

Recent conversation context:
{session_context}

Current user input:
{user_input.text}
""".strip()

    def _build_session_context_block(self, user_input: UserInput) -> str:
        if self.context_builder is None:
            return "(no previous conversation context)"

        snapshot = self.context_builder.build(current_input=user_input)
        if not snapshot.text:
            return "(no previous conversation context)"

        suffix = "\n[context truncated]" if snapshot.truncated else ""
        return f"{snapshot.text}{suffix}"

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

        fallback_reason = None
        validation_reason = None
        raw_action_requested = action_name is not None

        if intent not in ALLOWED_INTENTS:
            intent = "unknown"
            confidence = 0.25
            fallback_reason = "unsupported_intent"
            if raw_action_requested:
                validation_reason = "intent_unknown"
            action_name = None

        if validation_reason is None:
            action_name, parameters, validation_reason = validate_llm_action_for_intent(
                intent,
                action_name,
                parameters,
                self.action_registry,
            )
        elif not isinstance(parameters, dict):
            parameters = {}

        action_suppressed_reason = None
        if action_name is not None and confidence < self.action_min_confidence:
            action_name = None
            parameters = {}
            action_suppressed_reason = "low_confidence"

        entities = {
            **parameters,
            "llm_action_name": action_name,
            "llm_response_text": response_text,
            "llm_emotion": emotion,
            "llm_raw": json.dumps(raw_result, ensure_ascii=False),
        }

        if validation_reason is not None:
            entities["llm_action_validation_failed"] = True
            entities["llm_action_validation_reason"] = validation_reason
            fallback_reason = self._fallback_reason_from_validation(validation_reason)

        if action_suppressed_reason is not None:
            entities["action_suppressed_reason"] = action_suppressed_reason
            entities["action_min_confidence"] = self.action_min_confidence
            fallback_reason = "low_confidence_action"

        if fallback_reason is not None:
            entities["llm_fallback_used"] = True
            entities["llm_fallback_reason"] = fallback_reason

        return IntentResult(
            intent=intent,
            confidence=confidence,
            entities=entities,
        )

    def _fallback_reason_from_validation(self, validation_reason: str) -> str:
        if validation_reason.startswith("missing_or_invalid_param:"):
            return "invalid_parameters"
        return LLM_VALIDATION_FALLBACK_REASONS.get(
            validation_reason,
            "invalid_schema",
        )

    def _safe_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.50

        return max(0.0, min(1.0, confidence))

    def _action_min_confidence_from_environment(self) -> float:
        raw_value = os.getenv(LLM_ACTION_MIN_CONFIDENCE_ENV)
        if raw_value is None:
            return DEFAULT_LLM_ACTION_MIN_CONFIDENCE

        try:
            threshold = float(raw_value)
        except ValueError:
            logger.warning(
                "Invalid %s; using %s.",
                LLM_ACTION_MIN_CONFIDENCE_ENV,
                DEFAULT_LLM_ACTION_MIN_CONFIDENCE,
            )
            return DEFAULT_LLM_ACTION_MIN_CONFIDENCE

        if threshold != threshold:
            logger.warning(
                "Invalid %s; using %s.",
                LLM_ACTION_MIN_CONFIDENCE_ENV,
                DEFAULT_LLM_ACTION_MIN_CONFIDENCE,
            )
            return DEFAULT_LLM_ACTION_MIN_CONFIDENCE

        return max(0.0, min(1.0, threshold))
