from mira.core.events import EventBus
from mira.core.state_manager import StateManager

__all__ = ["Brain", "EventBus", "StateManager"]


def __getattr__(name):
    if name == "Brain":
        from mira.core.brain import Brain

        return Brain

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
