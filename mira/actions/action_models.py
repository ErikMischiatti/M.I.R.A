from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionRequest:
    action_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    source_intent: str | None = None


@dataclass(frozen=True)
class ActionContract:
    name: str
    compatible_intents: frozenset[str] = field(default_factory=frozenset)
    required_params: dict[str, type] = field(default_factory=dict)
    requires_confirmation: bool = False


@dataclass
class ActionResult:
    success: bool
    action_name: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)