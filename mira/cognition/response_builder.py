from __future__ import annotations

from mira.actions.action_models import ActionResult
from mira.core.models import BrainResponse, IntentResult, UserInput
from mira.ui.face.face_state import FaceState


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
                face_state=FaceState.CONFUSED,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        llm_response_text = self._get_llm_response_text(intent)
        if llm_response_text:
            return BrainResponse(
                text=llm_response_text,
                face_state=FaceState.SPEAKING,
                metadata={
                    "intent": intent.intent,
                    "confidence": intent.confidence,
                    "llm_response_used": True,
                },
            )

        if intent.intent == "greeting":
            return BrainResponse(
                text="Ciao. Sono M.I.R.A. Pronto a interagire con te.",
                face_state=FaceState.HAPPY,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "status_query":
            return BrainResponse(
                text="Sto funzionando correttamente. Il mio layer cognitivo è attivo.",
                face_state=FaceState.SPEAKING,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "identity_query":
            return BrainResponse(
                text="Sono N.E.R.O, il nucleo cognitivo embodied progettato per H.A.R.O.",
                face_state=FaceState.SPEAKING,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "llm_not_implemented":
            return BrainResponse(
                text="Il backend LLM è previsto, ma non è ancora attivo in questa build.",
                face_state=FaceState.CONFUSED,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        return BrainResponse(
            text=f"Ho ricevuto: '{user_input.text}', ma non so ancora interpretarlo bene.",
            face_state=FaceState.CONFUSED,
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

    def _build_action_response(self, action_result: ActionResult) -> BrainResponse:
        if not action_result.success:
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.CONFUSED,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_time":
            return BrainResponse(
                text=f"Sono le {action_result.data.get('time', 'sconosciute')}.",
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_date":
            return BrainResponse(
                text=f"La data di oggi è {action_result.data.get('date', 'sconosciuta')}.",
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "echo_text":
            return BrainResponse(
                text=f"Hai chiesto di ripetere: {action_result.data.get('text', '')}",
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_last_intent":
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_session_summary":
            summary_lines = action_result.data.get("summary", [])

            if not summary_lines:
                return BrainResponse(
                    text="La sessione è ancora vuota.",
                    face_state=FaceState.SPEAKING,
                    metadata={"action_name": action_result.action_name, **action_result.data},
                )

            summary_text = " | ".join(summary_lines)
            return BrainResponse(
                text=f"Ecco un breve riassunto: {summary_text}",
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "clear_session_memory":
            return BrainResponse(
                text="Ho cancellato la memoria della sessione corrente.",
                face_state=FaceState.HAPPY,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )
    
        if action_result.action_name == "list_available_actions":
            actions = action_result.data.get("actions", [])
            return BrainResponse(
                text=f"Posso eseguire queste azioni: {', '.join(actions)}",
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_memory_size":
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_last_user_message":
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )
    
        if action_result.action_name == "open_url":
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
        )

        if action_result.action_name == "open_app":
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "show_notification":
            return BrainResponse(
                text="Notifica inviata.",
                face_state=FaceState.HAPPY,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )

        if action_result.action_name == "get_system_info":
            return BrainResponse(
                text=action_result.message,
                face_state=FaceState.SPEAKING,
                metadata={"action_name": action_result.action_name, **action_result.data},
            )
        
        return BrainResponse(
            text=action_result.message,
            face_state=FaceState.SPEAKING,
            metadata={"action_name": action_result.action_name, **action_result.data},
        )
