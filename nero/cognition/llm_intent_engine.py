from __future__ import annotations

from nero.cognition.intent_engine import IntentEngine
from nero.core.models import IntentResult, UserInput


class LLMIntentEngine(IntentEngine):
    """Placeholder for a future LLM-backed intent inference engine."""

    def infer(self, user_input: UserInput) -> IntentResult:
        return IntentResult(
            intent="llm_not_implemented",
            confidence=0.0,
            entities={"raw_text": user_input.text},
        )
