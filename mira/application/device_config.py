"""USB device monitoring configuration read from the environment at composition time."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEVICE_MONITOR_ENV = "MIRA_DEVICE_MONITOR"
DEVICE_MONITOR_DEBUG_ENV = "MIRA_DEVICE_MONITOR_DEBUG"
DEVICE_INFO_ENV = "MIRA_DEVICE_INFO"
DEVICE_INFO_REFRESH_ENV = "MIRA_DEVICE_INFO_REFRESH_S"

# A battery refresh is one short subprocess; never more often than this.
MIN_REFRESH_S, MAX_REFRESH_S = 30, 3600

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class DeviceMonitorConfiguration:
    """Whether the desktop runtime watches USB devices, and how loudly.

    ``info`` asks device information providers (adb, UPower, sysfs) about
    connected devices; ``refresh_s`` re-reads a battery that often, 0 never.
    """

    enabled: bool = True
    debug: bool = False
    info: bool = True
    refresh_s: int = 0


def device_monitor_configuration_from_environment() -> DeviceMonitorConfiguration:
    """Read the `MIRA_DEVICE_MONITOR*` variables once, at composition time."""

    return parse_device_monitor_configuration(
        enabled=os.getenv(DEVICE_MONITOR_ENV),
        debug=os.getenv(DEVICE_MONITOR_DEBUG_ENV),
        info=os.getenv(DEVICE_INFO_ENV),
        refresh_s=os.getenv(DEVICE_INFO_REFRESH_ENV),
    )


def parse_device_monitor_configuration(
    *,
    enabled: str | None = None,
    debug: str | None = None,
    info: str | None = None,
    refresh_s: str | None = None,
) -> DeviceMonitorConfiguration:
    """Parse raw values; absent or unrecognised ones fall back to their defaults.

    Monitoring is on unless ``enabled`` is 0, false, no or off
    (case-insensitive): the runtime watched USB devices before this setting
    existed. Debug logging is opt-in, like the camera. Device information is
    on unless ``info`` is a false value; it only ever asks a phone that
    already offers USB debugging or Apple's multiplexer, or reads a storage
    device's size. ``refresh_s`` is a whole number of seconds, held to
    `MIN_REFRESH_S`..`MAX_REFRESH_S`; absent, 0 or invalid means no refresh.
    """

    return DeviceMonitorConfiguration(
        enabled=(enabled or "").strip().lower() not in _FALSE_VALUES,
        debug=(debug or "").strip().lower() in _TRUE_VALUES,
        info=(info or "").strip().lower() not in _FALSE_VALUES,
        refresh_s=_refresh(refresh_s),
    )


def _refresh(raw: str | None) -> int:
    try:
        seconds = int((raw or "").strip())
    except ValueError:
        return 0
    if seconds <= 0:
        return 0
    return min(max(seconds, MIN_REFRESH_S), MAX_REFRESH_S)
