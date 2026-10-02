"""UI-independent vocabulary for changes in the system MIRA runs on.

A platform adapter (`mira.adapters`) watches some concrete facility — kernel
uevents, the audio server, the network manager — and reports what happened
in these terms only. Producers in `mira.core` decide which changes are worth
a `Notice`; embodiments decide how notices look. No adapter knows about
notices, and nothing here knows about Linux, D-Bus or any embodiment, so a
robot or a headless runtime can supply other sources, or none.

Only the kinds that have a source today are listed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Union


class DeviceKind(StrEnum):
    """What kind of device changed, in user terms."""

    AUDIO_OUTPUT = "audio output"
    AUDIO_INPUT = "audio input"
    USB = "usb"


class DeviceChange(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEFAULT_CHANGED = "default changed"
    """The device became the one the system uses by default (audio only)."""


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    """One device change. ``key`` identifies the device across events; ``name``
    is what a person would call it."""

    kind: DeviceKind
    change: DeviceChange
    name: str
    key: str


class Connectivity(StrEnum):
    """Whether the system can reach the network, as a person cares about it."""

    ONLINE = "online"
    LIMITED = "limited"
    """Connected, but without full access (a captive portal, no route out)."""
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class ConnectivityEvent:
    """The connectivity the system reports now (sent at start, then on change)."""

    state: Connectivity


SystemEvent = Union[DeviceEvent, ConnectivityEvent]
SystemEventListener = Callable[[SystemEvent], None]


class SystemEventSource(Protocol):
    """Something that watches one system facility and reports its changes.

    `start` begins watching and returns False when the facility is not
    available here — a missing tool, no bus, another platform — which is a
    status, never an exception. Events are delivered on the thread that
    called `start`, one at a time. `shutdown` stops watching and releases
    every resource; it is idempotent.
    """

    name: str

    def start(self, listener: SystemEventListener) -> bool: ...

    def shutdown(self) -> None: ...
