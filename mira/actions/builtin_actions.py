from __future__ import annotations

from datetime import datetime

from mira.actions.action_models import ActionResult
from mira.memory.session_memory import SessionMemory


def make_get_time_action():
    def handler(parameters: dict) -> ActionResult:
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")

        return ActionResult(
            success=True,
            action_name="get_time",
            message=f"Ora corrente: {current_time}",
            data={"time": current_time},
        )

    return handler


def make_get_date_action():
    def handler(parameters: dict) -> ActionResult:
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")

        return ActionResult(
            success=True,
            action_name="get_date",
            message=f"Data corrente: {current_date}",
            data={"date": current_date},
        )

    return handler


def make_echo_text_action():
    def handler(parameters: dict) -> ActionResult:
        text = str(parameters.get("text", "")).strip()

        if not text:
            return ActionResult(
                success=False,
                action_name="echo_text",
                message="Nessun testo da ripetere.",
            )

        return ActionResult(
            success=True,
            action_name="echo_text",
            message=text,
            data={"text": text},
        )

    return handler


def make_get_last_intent_action(memory: SessionMemory):
    def handler(parameters: dict) -> ActionResult:
        if memory.last_intent is None:
            return ActionResult(
                success=False,
                action_name="get_last_intent",
                message="Non ho ancora rilevato nessun intent in questa sessione.",
            )

        return ActionResult(
            success=True,
            action_name="get_last_intent",
            message=f"Ultimo intent rilevato: {memory.last_intent.intent}",
            data={
                "intent": memory.last_intent.intent,
                "confidence": memory.last_intent.confidence,
                "entities": memory.last_intent.entities,
            },
        )

    return handler


def make_get_session_summary_action(memory: SessionMemory):
    def handler(parameters: dict) -> ActionResult:
        recent_history = memory.get_recent_history(limit=10)

        if not recent_history:
            return ActionResult(
                success=True,
                action_name="get_session_summary",
                message="La sessione è ancora vuota.",
                data={"history_count": 0, "summary": []},
            )

        summary_lines = [f"{msg.role}: {msg.text}" for msg in recent_history]

        return ActionResult(
            success=True,
            action_name="get_session_summary",
            message="Riassunto sessione disponibile.",
            data={
                "history_count": len(memory.history),
                "summary": summary_lines,
            },
        )

    return handler


def make_clear_session_memory_action(memory: SessionMemory):
    def handler(parameters: dict) -> ActionResult:
        memory.clear()

        return ActionResult(
            success=True,
            action_name="clear_session_memory",
            message="Memoria di sessione cancellata.",
        )

    return handler

def make_list_available_actions_action(registry):
    def handler(parameters: dict) -> ActionResult:
        actions = registry.list_actions()

        return ActionResult(
            success=True,
            action_name="list_available_actions",
            message="Elenco azioni disponibili.",
            data={"actions": actions},
        )

    return handler


def make_get_memory_size_action(memory: SessionMemory):
    def handler(parameters: dict) -> ActionResult:
        size = len(memory.history)

        return ActionResult(
            success=True,
            action_name="get_memory_size",
            message=f"La memoria contiene {size} messaggi.",
            data={"size": size},
        )

    return handler


def make_get_last_user_message_action(memory: SessionMemory):
    def handler(parameters: dict) -> ActionResult:
        for msg in reversed(memory.history):
            if msg.role == "user":
                return ActionResult(
                    success=True,
                    action_name="get_last_user_message",
                    message=f"Il tuo ultimo messaggio era: {msg.text}",
                    data={"text": msg.text},
                )

        return ActionResult(
            success=False,
            action_name="get_last_user_message",
            message="Non ho trovato messaggi utente nella memoria.",
        )

    return handler