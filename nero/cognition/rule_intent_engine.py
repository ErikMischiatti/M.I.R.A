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

        if "che ore sono" in text or "dimmi l'ora" in text or "dimmi ora" in text:
            return IntentResult(intent="time_query", confidence=0.95)

        if "che giorno è" in text or "che giorno e" in text or "dimmi la data" in text or "data di oggi" in text:
            return IntentResult(intent="date_query", confidence=0.95)

        if text.startswith("ripeti "):
            echoed = user_input.text[len("ripeti "):].strip()
            return IntentResult(
                intent="echo_request",
                confidence=0.95,
                entities={"text": echoed},
            )

        if text.startswith("echo "):
            echoed = user_input.text[len("echo "):].strip()
            return IntentResult(
                intent="echo_request",
                confidence=0.95,
                entities={"text": echoed},
            )

        if "riassumi la sessione" in text or "cosa ci siamo detti" in text:
            return IntentResult(intent="session_summary_request", confidence=0.90)

        if "ultimo intent" in text or "qual è stato l'ultimo intent" in text or "qual e stato l'ultimo intent" in text:
            return IntentResult(intent="last_intent_query", confidence=0.90)

        if "cancella memoria sessione" in text or "resetta memoria sessione" in text:
            return IntentResult(intent="clear_session_memory", confidence=0.90)
        
        if "cosa sai fare" in text or "che azioni sai fare" in text or "lista azioni" in text:
            return IntentResult(intent="list_actions", confidence=0.90)

        if "quanti messaggi" in text or "dimensione memoria" in text or "quanti dati hai" in text:
            return IntentResult(intent="memory_size_query", confidence=0.90)

        if "ultimo messaggio" in text or "cosa ho detto prima" in text:
            return IntentResult(intent="last_user_message_query", confidence=0.90)

        return IntentResult(intent="unknown", confidence=0.50)
        