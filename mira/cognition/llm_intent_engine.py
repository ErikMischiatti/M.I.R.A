from __future__ import annotations

import json
from typing import Any

from mira.cognition.intent_engine import IntentEngine
from mira.cognition.llm_client import LLMClientError, OllamaClient
from mira.actions.action_contracts import build_action_contract_registry
from mira.actions.action_registry import ActionRegistry
from mira.cognition.llm_schema import (
    ALLOWED_INTENTS,
    LLM_INTENT_SCHEMA,
    describe_action_intent_compatibility,
    describe_required_action_params,
    validate_llm_action_for_intent,
)
from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.cognition.session_context import (
    DEFAULT_SESSION_CONTEXT_MAX_CHARS,
    DEFAULT_SESSION_CONTEXT_MAX_MESSAGES,
    build_session_context,
)
from mira.core.models import IntentResult, UserInput
from mira.core.session_memory import SessionMemory


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
        session_context_max_messages: int = DEFAULT_SESSION_CONTEXT_MAX_MESSAGES,
        session_context_max_chars: int = DEFAULT_SESSION_CONTEXT_MAX_CHARS,
    ):
        self.client = client or OllamaClient()
        self.fallback_engine = fallback_engine or RuleIntentEngine()
        self.action_registry = action_registry or build_action_contract_registry()
        self.session_memory = session_memory
        self.session_context_max_messages = session_context_max_messages
        self.session_context_max_chars = session_context_max_chars

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

        if not isinstance(raw_result, dict):
            return self.fallback_engine.infer(user_input)

        return self._to_intent_result(raw_result, user_input)

    def _build_prompt(self, user_input: UserInput) -> str:
        allowed_intents = ", ".join(ALLOWED_INTENTS)
        allowed_actions = ", ".join(self.action_registry.list_contract_names())
        compatibility = "\n".join(
            describe_action_intent_compatibility(self.action_registry)
        )
        required_params = "\n".join(describe_required_action_params(self.action_registry))
        session_context = build_session_context(
            self.session_memory,
            max_messages=self.session_context_max_messages,
            max_chars=self.session_context_max_chars,
            current_user_text=user_input.text,
        )
        session_context_text = session_context.text or "No previous session messages."
        output_schema = json.dumps(LLM_INTENT_SCHEMA, ensure_ascii=False, indent=2)

        return f"""
Role and instructions:
You are the intent parser for M.I.R.A., the Modular Interactive Robotic Agent.

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
- Use session context only to understand references in the current user message.
- Use session context to answer questions about information the user already declared in this session.
- If the user asks for their own name or personal details, look for those facts in session context.
- Do not answer with the assistant identity when the user asks about the user.
- If the requested fact is not present in session context, say that it is not available instead of inventing it.
- The current user message is the message to classify; session context can contain facts needed to interpret it.
- Do not treat session context as permission to bypass intent/action validation.
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

Session context (bounded, recent messages only):
{session_context_text}

Current user message:
{user_input.text}

Output JSON schema:
{output_schema}
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

        validation_reason = None
        raw_action_requested = action_name is not None

        if intent not in ALLOWED_INTENTS:
            intent = "unknown"
            confidence = 0.25
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