from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from mira.actions.action_models import ActionRequest, ActionResult
from mira.actions.action_registry import ActionRegistry

if TYPE_CHECKING:
    from mira.messaging.events import EventBus


logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self, registry: ActionRegistry, event_bus: EventBus | None = None):
        self.registry = registry
        self.event_bus = event_bus

    def execute(self, request: ActionRequest | Any) -> ActionResult:
        validation_error = self._validate_request(request)
        if validation_error is not None:
            logger.warning("Action rejected: %s", validation_error.message)
            self._emit("action_failed", validation_error)
            return validation_error

        action_name = request.action_name
        logger.info("Action started: %s", action_name)

        self._emit("action_started", request)

        handler = self.registry.get(action_name)
        if handler is None:
            result = ActionResult(
                success=False,
                action_name=action_name,
                message=f"Azione '{action_name}' non disponibile.",
                data={"reason": "action_unknown"},
            )

            logger.warning("Action failed: %s is not registered", action_name)
            self._emit("action_failed", result)
            return result

        try:
            raw_result = handler(request.parameters)
            result = self._normalize_result(action_name, raw_result)

            if result.success:
                logger.info("Action completed: %s", action_name)
            else:
                logger.warning("Action failed: %s: %s", action_name, result.message)

            event_name = "action_completed" if result.success else "action_failed"
            self._emit(event_name, result)
            return result

        except Exception as exc:
            result = ActionResult(
                success=False,
                action_name=action_name,
                message=f"Errore durante l'esecuzione di '{action_name}': {exc}",
                data={"reason": "action_exception"},
            )

            logger.exception("Action failed with exception: %s", action_name)
            self._emit("action_failed", result)
            return result

    def _validate_request(self, request: ActionRequest | Any) -> ActionResult | None:
        if not isinstance(request, ActionRequest):
            return ActionResult(
                success=False,
                action_name="<invalid>",
                message="Richiesta azione non valida.",
                data={"reason": "request_type"},
            )

        if not isinstance(request.action_name, str) or not request.action_name.strip():
            return ActionResult(
                success=False,
                action_name="<invalid>",
                message="Nome azione non valido.",
                data={"reason": "action_name"},
            )

        request.action_name = request.action_name.strip()

        if not isinstance(request.parameters, dict):
            return ActionResult(
                success=False,
                action_name=request.action_name,
                message="Parametri azione non validi.",
                data={"reason": "parameters"},
            )

        return None

    def _normalize_result(self, action_name: str, result: ActionResult | Any) -> ActionResult:
        if not isinstance(result, ActionResult):
            return ActionResult(
                success=False,
                action_name=action_name,
                message=f"Azione '{action_name}' ha restituito un risultato non valido.",
                data={"reason": "result_type"},
            )

        data = result.data if isinstance(result.data, dict) else {}

        return ActionResult(
            success=bool(result.success),
            action_name=action_name,
            message=str(result.message),
            data=data,
        )

    def _emit(self, event_name: str, payload: ActionRequest | ActionResult) -> None:
        if self.event_bus is not None:
            self.event_bus.emit(event_name, payload)
