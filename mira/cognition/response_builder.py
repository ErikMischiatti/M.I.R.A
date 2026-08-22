from __future__ import annotations

from mira.actions.action_models import ActionResult
from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent
from mira.domain.models import BrainResponse, IntentResult, UserInput


SPEAKING_INTENT = EmbodimentIntent(activity=ActivityState.SPEAKING)
HAPPY_RESPONSE_INTENT = EmbodimentIntent(
    activity=ActivityState.SPEAKING,
    affect=AffectState.HAPPY,
)
CONFUSED_RESPONSE_INTENT = EmbodimentIntent(
    activity=ActivityState.SPEAKING,
    affect=AffectState.CONFUSED,
)

LLM_EMOTION_EMBODIMENT = {
    "neutral": SPEAKING_INTENT,
    "speaking": SPEAKING_INTENT,
    "happy": HAPPY_RESPONSE_INTENT,
    "confused": CONFUSED_RESPONSE_INTENT,
    "thinking": SPEAKING_INTENT,
}


class ResponseBuilder:
    """Builds UI-facing brain responses from normalized intent results."""

    def build(
        self,
        intent: IntentResult,
        user_input: UserInput,
        action_result: ActionResult | None = None,
    ) -> BrainResponse:
        if action_result is not None:
            return self._build_action_response(action_result)

        if intent.intent == "empty_input":
            return BrainResponse(
                text="Non ho ricevuto alcun input.",
                embodiment=CONFUSED_RESPONSE_INTENT,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        llm_response_text = self._get_llm_response_text(intent)
        if llm_response_text:
            embodiment, llm_emotion_used = self._get_llm_embodiment(intent)
            metadata = {
                "intent": intent.intent,
                "confidence": intent.confidence,
                "llm_response_used": True,
            }
            if llm_emotion_used is not None:
                metadata["llm_emotion_used"] = llm_emotion_used

            return BrainResponse(
                text=llm_response_text,
                embodiment=embodiment,
                metadata=metadata,
            )

        if intent.intent == "greeting":
            return BrainResponse(
                text="Ciao. Sono M.I.R.A. Pronto a interagire con te.",
                embodiment=HAPPY_RESPONSE_INTENT,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "status_query":
            return BrainResponse(
                text="Sto funzionando correttamente. Il mio layer cognitivo è attivo.",
                embodiment=SPEAKING_INTENT,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "identity_query":
            return BrainResponse(
                text="Sono N.E.R.O, il nucleo cognitivo embodied progettato per H.A.R.O.",
                embodiment=SPEAKING_INTENT,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "llm_not_implemented":
            return BrainResponse(
                text="Il backend LLM è previsto, ma non è ancora attivo in questa build.",
                embodiment=CONFUSED_RESPONSE_INTENT,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        return BrainResponse(
            text=f"Ho ricevuto: '{user_input.text}', ma non so ancora interpretarlo bene.",
            embodiment=CONFUSED_RESPONSE_INTENT,
            metadata={"intent": intent.intent, "confidence": intent.confidence},
        )

    def _get_llm_response_text(self, intent: IntentResult) -> str | None:
        response_text = intent.entities.get("llm_response_text")
        if not isinstance(response_text, str):
            return None

        response_text = response_text.strip()
        if not response_text:
            return None

        return response_text

    def _get_llm_embodiment(
        self, intent: IntentResult
    ) -> tuple[EmbodimentIntent, str | None]:
        emotion = intent.entities.get("llm_emotion")
        if not isinstance(emotion, str):
            return SPEAKING_INTENT, None

        normalized_emotion = emotion.strip().lower()
        if not normalized_emotion:
            return SPEAKING_INTENT, None

        embodiment = LLM_EMOTION_EMBODIMENT.get(normalized_emotion)
        if embodiment is None:
            return SPEAKING_INTENT, None

        return embodiment, normalized_emotion

    def _build_action_response(self, action_result: ActionResult) -> BrainResponse:
        if not action_result.success:
            return BrainResponse(
                text=action_result.message,
                embodiment=CONFUSED_RESPONSE_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_time":
            return BrainResponse(
                text=f"Sono le {action_result.data.get('time', 'sconosciute')}.",
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_date":
            return BrainResponse(
                text=f"La data di oggi è {action_result.data.get('date', 'sconosciuta')}.",
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "echo_text":
            return BrainResponse(
                text=f"Hai chiesto di ripetere: {action_result.data.get('text', '')}",
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_last_intent":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_session_summary":
            summary_lines = action_result.data.get("summary", [])

            if not summary_lines:
                return BrainResponse(
                    text="La sessione è ancora vuota.",
                    embodiment=SPEAKING_INTENT,
                    metadata={"action_name": action_result.action_name, **action_result.data},
                )

            summary_text = " | ".join(summary_lines)
            return BrainResponse(
                text=f"Ecco un breve riassunto: {summary_text}",
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "clear_session_memory":
            return BrainResponse(
                text="Ho cancellato la memoria della sessione corrente.",
                embodiment=HAPPY_RESPONSE_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "list_available_actions":
            actions = action_result.data.get("actions", [])
            return BrainResponse(
                text=f"Posso eseguire queste azioni: {', '.join(actions)}",
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_memory_size":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_last_user_message":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "open_url":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "open_app":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "show_notification":
            return BrainResponse(
                text="Notifica inviata.",
                embodiment=HAPPY_RESPONSE_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_system_info":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_project_path":
            return BrainResponse(
                text=action_result.message,
                embodiment=SPEAKING_INTENT,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        return BrainResponse(
            text=action_result.message,
            embodiment=SPEAKING_INTENT,
            metadata={"action_name": action_result.action_name, **action_result.data},
        )
