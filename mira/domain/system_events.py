"""UI-independent vocabulary for changes in the system MIRA runs on.

A platform adapter (`mira.adapters`) watches some concrete facility — kernel
uevents, the audio server, the network manager — and reports what happened
in these terms only. Producers in `mira.core` decide which changes are worth
a `Notice`; embodiments decide how notices look. No adapter knows about
notices, and nothing here knows about Linux, D-Bus or any embodiment, so a
robot or a headless runtime can supply other sources, or none.

Only the kinds that have a source today are listed. `device_notice` and
`connectivity_notice` say which notice each change becomes; *whether* a
change is announced at all (settling, repeats, debounce) is the producers'
decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Union

from mira.domain.devices import DeviceCategory
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone


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
    is what a person would call it, or empty when the device does not say.

    ``category`` and ``manufacturer`` are what the source could read, and
    None when it reads no such thing (audio routes are not classified); a
    source that classifies but cannot tell says `DeviceCategory.UNKNOWN`.
    ``kind`` is the facility it was seen through, so for USB it is also the
    connection. ``key`` is for correlation only and is never a serial number.
    """

    kind: DeviceKind
    change: DeviceChange
    name: str
    key: str
    category: DeviceCategory | None = None
    manufacturer: str | None = None


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


# --- The notice each change becomes -----------------------------------------------

_DEVICE_NOUN = {
    DeviceKind.AUDIO_OUTPUT: "Output",
    DeviceKind.AUDIO_INPUT: "Microphone",
    DeviceKind.USB: "USB",
}


# What to call a classified device that brings no name of its own.
_CATEGORY_NAME = {
    DeviceCategory.SMARTPHONE: "Smartphone",
    DeviceCategory.STORAGE: "USB storage",
    DeviceCategory.CAMERA: "Camera",
    DeviceCategory.AUDIO: "Audio device",
    DeviceCategory.INPUT: "Input device",
}


def device_notice(event: DeviceEvent) -> Notice:
    name = (
        event.name.strip()
        or _CATEGORY_NAME.get(event.category)
        or f"{_DEVICE_NOUN[event.kind]} device"
    )
    if event.change is DeviceChange.CONNECTED:
        text = f"{name} connected"
    elif event.change is DeviceChange.DISCONNECTED:
        text = f"{name} disconnected"
    else:
        text = f"{_DEVICE_NOUN[event.kind]}: {name}"
    device = None
    if event.category is not None and event.change is not DeviceChange.DEFAULT_CHANGED:
        device = NoticeDevice(event.category, event.change is DeviceChange.CONNECTED)
    return Notice(NoticeCategory.DEVICE, NoticeTone.INFO, text, device)


_CONNECTIVITY_NOTICE = {
    Connectivity.OFFLINE: Notice(NoticeCategory.CONNECTIVITY, NoticeTone.WARNING, "Network lost"),
    Connectivity.LIMITED: Notice(NoticeCategory.CONNECTIVITY, NoticeTone.WARNING, "Limited connectivity"),
    Connectivity.ONLINE: Notice(NoticeCategory.CONNECTIVITY, NoticeTone.SUCCESS, "Back online"),
}


def connectivity_notice(state: Connectivity) -> Notice:
    return _CONNECTIVITY_NOTICE[state]
