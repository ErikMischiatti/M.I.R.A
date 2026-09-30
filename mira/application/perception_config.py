"""Person-detection configuration read from the environment at composition time."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass


PERSON_DETECTION_ENABLED_ENV = "MIRA_PERSON_DETECTION"
PERSON_DETECTION_MODEL_ENV = "MIRA_PERSON_DETECTION_MODEL"
PERSON_DETECTION_CONFIDENCE_ENV = "MIRA_PERSON_DETECTION_CONFIDENCE"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class PersonDetectionConfiguration:
    """Whether to detect people, with which model, and how strictly.

    ``model_path`` None selects the detector's default model. ``min_confidence``
    None keeps the detector's default threshold.
    """

    enabled: bool = False
    model_path: str | None = None
    min_confidence: float | None = None


def person_detection_configuration_from_environment() -> PersonDetectionConfiguration:
    """Read the `MIRA_PERSON_DETECTION*` variables once, at composition time."""

    return parse_person_detection_configuration(
        enabled=os.getenv(PERSON_DETECTION_ENABLED_ENV),
        model=os.getenv(PERSON_DETECTION_MODEL_ENV),
        confidence=os.getenv(PERSON_DETECTION_CONFIDENCE_ENV),
    )


def parse_person_detection_configuration(
    *,
    enabled: str | None = None,
    model: str | None = None,
    confidence: str | None = None,
) -> PersonDetectionConfiguration:
    """Parse raw values; absent or invalid ones fall back to their defaults.

    Detection is opt-in (1, true, yes or on) and also needs the camera. A
    confidence outside [0, 1] or not a number is ignored.
    """

    return PersonDetectionConfiguration(
        enabled=(enabled or "").strip().lower() in _TRUE_VALUES,
        model_path=(model or "").strip() or None,
        min_confidence=_unit_float(confidence),
    )


def _unit_float(raw: str | None) -> float | None:
    try:
        value = float(raw.strip()) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) and 0.0 <= value <= 1.0 else None
