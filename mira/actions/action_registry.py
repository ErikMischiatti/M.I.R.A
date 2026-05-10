from collections.abc import Callable

from nero.actions.action_models import ActionResult


ActionHandler = Callable[[dict], ActionResult]


class ActionRegistry:
    def __init__(self):
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, name: str, handler: ActionHandler) -> None:
        self._handlers[name] = handler

    def get(self, name: str) -> ActionHandler | None:
        return self._handlers.get(name)

    def has(self, name: str) -> bool:
        return name in self._handlers

    def list_actions(self) -> list[str]:
        return sorted(self._handlers.keys())