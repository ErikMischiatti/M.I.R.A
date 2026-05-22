from collections.abc import Callable

from mira.actions.action_models import ActionResult


ActionHandler = Callable[[dict], ActionResult]


class ActionRegistry:
    def __init__(self):
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, name: str, handler: ActionHandler) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Action name must be a non-empty string.")

        if not callable(handler):
            raise TypeError("Action handler must be callable.")

        self._handlers[name] = handler

    def get(self, name: str) -> ActionHandler | None:
        return self._handlers.get(name)

    def has(self, name: str) -> bool:
        return name in self._handlers

    def list_actions(self) -> list[str]:
        return sorted(self._handlers.keys())