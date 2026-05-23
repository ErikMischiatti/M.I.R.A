from __future__ import annotations

import json
import os
from typing import Any

from mira.cognition.intent_engine import IntentEngine
from mira.cognition.llm_client import LLMClientError, OllamaClient
from mira.cognition.llm_schema import ALLOWED_ACTIONS, ALLOWED_INTENTS, LLM_INTENT_SCHEMA
from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.core.models import IntentResult, UserInput


LLM_DEBUG_VALUES = {"1", "true", "yes", "on"}
MIN_PROMPT_LAST_INTENT_CONFIDENCE = 0.5


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

        session_context = self._format_session_context(user_input)
        prompt = self._build_prompt(user_input, session_context=session_context)
        self._debug_prompt(user_input, session_context)

        try:
            raw_result = self.client.generate_structured(
                prompt=prompt,
                schema=LLM_INTENT_SCHEMA,
                temperature=0.0,
            )
        except LLMClientError as exc:
            print(f"[LLM] Falling back to rule engine: {exc}")
            return self.fallback_engine.infer(user_input)

        intent_result = self._to_intent_result(raw_result, user_input)
        self._debug_result(intent_result)
        return intent_result

    def _build_prompt(
        self,
        user_input: UserInput,
        session_context: str | None = None,
    ) -> str:
        allowed_intents = ", ".join(ALLOWED_INTENTS)
        allowed_actions = ", ".join(ALLOWED_ACTIONS)
        if session_context is None:
            session_context = self._format_session_context(user_input)

        return f"""
You are the intent parser for N.E.R.O, a modular embodied robotic assistant.

Your task is NOT to answer conversationally.
Your task is to classify the user's input into exactly one valid JSON object.

Allowed intents:
{allowed_intents}

Allowed actions:
{allowed_actions}

Session context:
{session_context}

Rules:
- Return exactly one JSON object and nothing else.
- Do not include markdown, code fences, prose, comments, or trailing explanation.
- The first character must be {{ and the last character must be }}.
- All JSON strings must be closed and escaped correctly.
- Always answer the CURRENT USER REQUEST, not an earlier request from session context.
- Use session context only as supporting memory for the current request.
- Never repeat an old answer unless the current request asks the same question.
- If the current input is a new declarative personal or project fact, acknowledge that fact briefly in response_text and use no action.
- If the current input asks about a fact stated earlier, answer from the relevant session context in response_text.
- For new fact acknowledgements and session-grounded conversational answers, use intent "unknown", action_name null, and parameters {{}}.
- Do not repeat a generic self-introduction unless the user explicitly asks who or what M.I.R.A. is.
- Do not answer personal memory questions with the assistant identity.
- Use identity_query only for explicit questions about the assistant identity, such as "chi sei?" or "cosa sei?".
- Do not infer time_query or date_query from words like "lavoro", "lavorando", or "progetto".
- The current user input has priority over session context if they conflict.
- Return only JSON matching the provided schema.
- Do not invent intents outside the allowed list.
- Do not invent actions outside the allowed list.
- If no action is needed, set action_name to null and parameters to {{}}.
- If the user asks to open a website, use intent "open_url_request" and action "open_url".
- If the user asks to open a local app, use intent "open_app_request" and action "open_app".
- If the user asks to open a local folder or directory, use intent "open_directory_request" and action "open_directory".
- If the user asks for time, use intent "time_query" and action "get_time".
- If the user asks for date, use intent "date_query" and action "get_date".
- If the user asks for a notification, use intent "notification_request" and action "show_notification".
- If the user asks about available actions/capabilities, use intent "list_actions" and action "list_available_actions".
- If the user asks for system information, use intent "system_info_query" and action "get_system_info".
- If the user asks to repeat something, use intent "echo_request" and action "echo_text".
- If the user asks about session memory, use the most appropriate memory-related intent/action.
- For unknown or unsupported requests, use intent "unknown", action_name null, and parameters {{}}.

Required JSON shape:
{{"intent":"...","confidence":0.0,"emotion":"neutral","action_name":null,"parameters":{{}},"response_text":""}}

Illustrative examples, not facts to repeat unless session context supports them:
- Current user request: "Mi chiamo Erik" -> response_text "Piacere Erik, lo terrò a mente per questa sessione.", action_name null.
- Context says user: "Mi chiamo Erik" and current request: "Come mi chiamo?" -> response_text "Ti chiami Erik.", action_name null.
- Current user request: "Sto lavorando al progetto MIRA" -> response_text "Ho capito, stai lavorando al progetto MIRA.", action_name null.
- Context says user: "Sto lavorando al progetto MIRA" and current request: "Su che progetto sto lavorando?" -> response_text "Stai lavorando al progetto MIRA.", action_name null.

Entity extraction:
- For open_url, parameters must contain: {{"url": "..."}}
- For open_app, parameters must contain: {{"app_name": "..."}}
- For open_directory, parameters must contain: {{"directory": "..."}}
- For echo_text, parameters must contain: {{"text": "..."}}
- For show_notification, parameters must contain: {{"text": "..."}}

Current user request:
{user_input.text}
""".strip()


    def _format_session_context(self, user_input: UserInput) -> str:
        context = user_input.metadata.get("session_context")
        if not isinstance(context, dict):
            return "No prior session context."

        lines: list[str] = []
        history = context.get("history")
        if isinstance(history, list) and history:
            lines.append("Recent messages, oldest to newest:")
            for message in history:
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                text = message.get("text")
                if role not in {"user", "assistant"} or not isinstance(text, str):
                    continue
                text = text.strip()
                if text:
                    lines.append(f"- {role}: {text}")

        last_intent = context.get("last_intent")
        if isinstance(last_intent, dict):
            intent = last_intent.get("intent")
            confidence = self._safe_confidence(last_intent.get("confidence"))
            if (
                isinstance(intent, str)
                and confidence >= MIN_PROMPT_LAST_INTENT_CONFIDENCE
            ):
                lines.append(f"Last inferred intent: {intent} ({confidence:.3g})")

        if not lines:
            return "No prior session context."

        return "\n".join(lines)

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

    def _debug_prompt(self, user_input: UserInput, session_context: str) -> None:
        if not self._debug_enabled():
            return

        print("[LLM DEBUG] Session context:")
        print(session_context)
        print(f"[LLM DEBUG] Current user input: {user_input.text}")

    def _debug_result(self, intent: IntentResult) -> None:
        if not self._debug_enabled():
            return

        print(
            "[LLM DEBUG] Parsed result: "
            f"intent_name={intent.intent!r}, "
            f"confidence={intent.confidence!r}, "
            f"action_name={intent.entities.get('llm_action_name')!r}, "
            f"llm_response_text={intent.entities.get('llm_response_text')!r}, "
            f"llm_emotion={intent.entities.get('llm_emotion')!r}"
        )

    def _debug_enabled(self) -> bool:
        return os.getenv("MIRA_LLM_DEBUG", "").strip().lower() in LLM_DEBUG_VALUES
