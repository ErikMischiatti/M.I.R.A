from dataclasses import dataclass, field
from typing import Any
from mira.ui.face.face_state import FaceState


@dataclass
class UserInput:
    text: str
    source: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentResult:
    intent: str
    confidence: float = 1.0
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainResponse:
    text: str
    face_state: FaceState
    metadata: dict[str, Any] = field(default_factory=dict)