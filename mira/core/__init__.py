"""Runtime orchestration.

`Brain` is exposed lazily on purpose. Importing it eagerly here creates a real
circular import: `mira.cognition.session_context_builder` imports
`mira.core.session_memory`, which initialises this package, which would import
`mira.core.brain`, which imports `mira.cognition.llm_intent_engine` while that
module is still partially initialised.

The cycle is a consequence of `SessionMemory` living in this package while
`mira.cognition` depends on it — the same coupling carried as declared debt in
scripts/check_layering.py. Extracting a memory layer removes both, and this
shim can go with them. It is no longer needed for Qt avoidance: since the
scheduler port, nothing under `mira.core` imports a GUI toolkit.
"""

from mira.core.events import EventBus
from mira.core.state_manager import StateManager

__all__ = ["Brain", "EventBus", "StateManager"]


def __getattr__(name):
    if name == "Brain":
        from mira.core.brain import Brain

        return Brain

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
