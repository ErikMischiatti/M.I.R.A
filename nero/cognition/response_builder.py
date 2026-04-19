from __future__ import annotations

from nero.core.models import BrainResponse, IntentResult, UserInput
from nero.ui.face.face_state import FaceState


class ResponseBuilder:
    """Builds UI-facing brain responses from normalized intent results."""

    def build(self, intent: IntentResult, user_input: UserInput) -> BrainResponse:
        if intent.intent == "empty_input":
            return BrainResponse(
                text="Non ho ricevuto alcun input.",
                face_state=FaceState.CONFUSED,
                metadata={"intent": intent.intent, "confidence": intent.confidence},
            )

        if intent.intent == "greeting":
            return BrainResponse(
                text="Ciao. Sono N.E.R.O. Pronto a interagire con te.",
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
