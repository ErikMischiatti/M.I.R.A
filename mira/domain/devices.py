"""UI-independent vocabulary for what kind of device something is.

A source that can tell what a device is for — the USB adapter reads it from
the interface classes the device itself declares — says so with a
`DeviceCategory`. The categories are deliberately coarse and in user terms;
a source that cannot tell says UNKNOWN (or nothing at all), never a guess.
"""

from __future__ import annotations

from enum import StrEnum


class DeviceCategory(StrEnum):
    """What a device is, as far as its own metadata says."""

    SMARTPHONE = "smartphone"
    STORAGE = "storage"
    CAMERA = "camera"
    AUDIO = "audio"
    INPUT = "input"
    UNKNOWN = "unknown"
