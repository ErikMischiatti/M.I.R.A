from __future__ import annotations

from nero.cognition.intent_engine import IntentEngine
from nero.core.models import IntentResult, UserInput


class RuleIntentEngine(IntentEngine):
    """Simple rule-based intent inference for the current prototype."""

    def infer(self, user_input: UserInput) -> IntentResult:
        text = user_input.text.lower().strip()

        if not text:
            return IntentResult(intent="empty_input", confidence=1.0)

        if any(word in text for word in ["ciao", "salve", "hey", "hello"]):
            return IntentResult(intent="greeting", confidence=0.95)

        if "come stai" in text:
            return IntentResult(intent="status_query", confidence=0.95)

        if "chi sei" in text:
            return IntentResult(intent="identity_query", confidence=0.95)

        return IntentResult(intent="unknown", confidence=0.50)
