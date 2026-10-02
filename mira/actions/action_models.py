from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionEffect(str, Enum):
    READ_ONLY = "read_only"
    SIDE_EFFECT = "side_effect"
    DENIED = "denied"


@dataclass
class ActionRequest:
    action_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    source_intent: str | None = None
    # Set by `ActionExecutor` when it starts running the request: identifies
    # this one execution across action_started and its result. Not part of
    # what was asked, so not part of equality.
    run_id: int | None = field(default=None, compare=False, kw_only=True)


@dataclass(frozen=True)
class ActionContract:
    name: str
    effect: ActionEffect
    compatible_intents: frozenset[str] = field(default_factory=frozenset)
    required_params: dict[str, type] = field(default_factory=dict)
    requires_confirmation: bool = False


@dataclass
class ActionResult:
    success: bool
    action_name: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    # The execution this result ends; None for a request refused before it
    # started (invalid, unknown, blocked, awaiting confirmation).
    run_id: int | None = field(default=None, compare=False, kw_only=True)
