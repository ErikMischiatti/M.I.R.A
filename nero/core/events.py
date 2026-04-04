from collections import defaultdict
from typing import Callable, Any


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_name: str, callback: Callable[[Any], None]) -> None:
        self._subscribers[event_name].append(callback)

    def emit(self, event_name: str, payload: Any = None) -> None:
        for callback in self._subscribers[event_name]:
            callback(payload)