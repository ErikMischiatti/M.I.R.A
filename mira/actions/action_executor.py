from mira.actions.action_models import ActionRequest, ActionResult
from mira.actions.action_registry import ActionRegistry
from mira.core.events import EventBus


class ActionExecutor:
    def __init__(self, registry: ActionRegistry, event_bus: EventBus | None = None):
        self.registry = registry
        self.event_bus = event_bus

    def execute(self, request: ActionRequest) -> ActionResult:
        if self.event_bus is not None:
            self.event_bus.emit("action_started", request)

        handler = self.registry.get(request.action_name)
        if handler is None:
            result = ActionResult(
                success=False,
                action_name=request.action_name,
                message=f"Azione '{request.action_name}' non disponibile.",
            )

            if self.event_bus is not None:
                self.event_bus.emit("action_failed", result)

            return result

        try:
            result = handler(request.parameters)

            if self.event_bus is not None:
                event_name = "action_completed" if result.success else "action_failed"
                self.event_bus.emit(event_name, result)

            return result

        except Exception as exc:
            result = ActionResult(
                success=False,
                action_name=request.action_name,
                message=f"Errore durante l'esecuzione di '{request.action_name}': {exc}",
            )

            if self.event_bus is not None:
                self.event_bus.emit("action_failed", result)

            return result