"""Camera configuration read from the environment at composition time."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from mira.domain.camera import CameraSettings


CAMERA_ENABLED_ENV = "MIRA_CAMERA"
CAMERA_DEVICE_ENV = "MIRA_CAMERA_DEVICE"
CAMERA_WIDTH_ENV = "MIRA_CAMERA_WIDTH"
CAMERA_HEIGHT_ENV = "MIRA_CAMERA_HEIGHT"
CAMERA_FPS_ENV = "MIRA_CAMERA_FPS"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class CameraConfiguration:
    """Whether to use a camera, and what to ask it for."""

    enabled: bool = False
    settings: CameraSettings = CameraSettings()


def camera_configuration_from_environment() -> CameraConfiguration:
    """Read the `MIRA_CAMERA*` variables once, at composition time."""

    return parse_camera_configuration(
        enabled=os.getenv(CAMERA_ENABLED_ENV),
        device=os.getenv(CAMERA_DEVICE_ENV),
        width=os.getenv(CAMERA_WIDTH_ENV),
        height=os.getenv(CAMERA_HEIGHT_ENV),
        fps=os.getenv(CAMERA_FPS_ENV),
    )


def parse_camera_configuration(
    *,
    enabled: str | None = None,
    device: str | None = None,
    width: str | None = None,
    height: str | None = None,
    fps: str | None = None,
) -> CameraConfiguration:
    """Parse raw values; absent or invalid ones fall back to their defaults.

    The camera is opt-in: it is used only when ``enabled`` is 1, true, yes or
    on (case-insensitive). A blank device selects the default device.
    """

    return CameraConfiguration(
        enabled=(enabled or "").strip().lower() in _TRUE_VALUES,
        settings=CameraSettings(
            device=(device or "").strip() or CameraSettings().device,
            width=_positive_int(width),
            height=_positive_int(height),
            fps=_positive_float(fps),
        ),
    )


def _positive_int(raw: str | None) -> int | None:
    try:
        value = int(raw.strip()) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and value > 0 else None


def _positive_float(raw: str | None) -> float | None:
    try:
        value = float(raw.strip()) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) and value > 0 else None
