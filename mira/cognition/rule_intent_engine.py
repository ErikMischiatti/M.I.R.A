from __future__ import annotations

from mira.cognition.intent_engine import IntentEngine
from mira.domain.models import IntentResult, UserInput


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

        directory_prefixes = [
            "apri cartella ",
            "apri directory ",
            "apri la cartella ",
            "apri il folder ",
        ]
        for prefix in directory_prefixes:
            if text.startswith(prefix):
                directory = user_input.text[len(prefix):].strip()
                return IntentResult(
                    intent="open_directory_request",
                    confidence=0.88,
                    entities={"directory": directory},
                )

        if text in ["apri desktop", "apri download", "apri documenti", "apri home"]:
            directory = user_input.text[len("apri "):].strip()
            return IntentResult(
                intent="open_directory_request",
                confidence=0.88,
                entities={"directory": directory},
            )

        if text.startswith("apri ") or text.startswith("vai su "):
            raw = text.replace("apri ", "").replace("vai su ", "").strip()
            explicit_url_like = "://" in raw or raw.startswith("javascript:")

            if "." in raw or explicit_url_like:  # semplice euristica URL
                return IntentResult(
                    intent="open_url_request",
                    confidence=0.90,
                    entities={"url": raw},
                )
        if text.startswith("apri ") and "." not in text:
            app_name = text.replace("apri ", "").strip()

            return IntentResult(
                intent="open_app_request",
                confidence=0.85,
                entities={"app_name": app_name},
            )
        if text.startswith("notificami ") or text.startswith("mostra notifica"):
            msg = text.replace("notificami ", "").replace("mostra notifica", "").strip()

            return IntentResult(
                intent="notification_request",
                confidence=0.85,
                entities={"text": msg},
            )
        if "info sistema" in text or "che sistema stai usando" in text or "system info" in text:
            return IntentResult(
                intent="system_info_query",
                confidence=0.90,
            )

        project_path_blocked_prefixes = (
            "cambia ",
            "cancella ",
            "sposta ",
            "rinomina ",
            "elimina ",
            "esegui ",
            "scrivi ",
        )
        if any(text.startswith(prefix) for prefix in project_path_blocked_prefixes):
            return IntentResult(intent="unknown", confidence=0.50)

        project_path_patterns = [
            "dove si trova il progetto",
            "dov'è il progetto",
            "dove e il progetto",
            "qual è la cartella del progetto",
            "qual e la cartella del progetto",
            "qual è il project path",
            "qual e il project path",
            "mostra path progetto",
            "mostrami il path del progetto",
            "path del progetto",
            "path progetto",
            "mostra la directory del progetto",
            "cartella progetto",
            "dimmi la cartella del progetto",
            "dove è salvato il progetto",
            "dove e salvato il progetto",
            "dove si trova mira",
            "dove si trova m.i.r.a.",
            "mostrami la root del progetto",
            "root del progetto",
            "project root",
            "project directory",
            "show project path",
            "show project directory",
            "where is the project",
            "where is the project folder",
            "what is the project path",
            "what is the project directory",
            "cartella corrente",
            "current working directory",
        ]
        if any(pattern in text for pattern in project_path_patterns):
            return IntentResult(
                intent="project_path_query",
                confidence=0.90,
            )

        return IntentResult(intent="unknown", confidence=0.50)
