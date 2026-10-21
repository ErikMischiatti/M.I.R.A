"""USB device monitoring configuration read from the environment at composition time."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEVICE_MONITOR_ENV = "MIRA_DEVICE_MONITOR"
DEVICE_MONITOR_DEBUG_ENV = "MIRA_DEVICE_MONITOR_DEBUG"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class DeviceMonitorConfiguration:
    """Whether the desktop runtime watches USB devices, and how loudly."""

    enabled: bool = True
    debug: bool = False


def device_monitor_configuration_from_environment() -> DeviceMonitorConfiguration:
    """Read the `MIRA_DEVICE_MONITOR*` variables once, at composition time."""

    return parse_device_monitor_configuration(
        enabled=os.getenv(DEVICE_MONITOR_ENV),
        debug=os.getenv(DEVICE_MONITOR_DEBUG_ENV),
    )


def parse_device_monitor_configuration(
    *, enabled: str | None = None, debug: str | None = None
) -> DeviceMonitorConfiguration:
    """Parse raw values; absent or unrecognised ones fall back to their defaults.

    Monitoring is on unless ``enabled`` is 0, false, no or off
    (case-insensitive): the runtime watched USB devices before this setting
    existed. Debug logging is opt-in, like the camera.
    """

    return DeviceMonitorConfiguration(
        enabled=(enabled or "").strip().lower() not in _FALSE_VALUES,
        debug=(debug or "").strip().lower() in _TRUE_VALUES,
    )
