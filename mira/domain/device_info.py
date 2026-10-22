"""UI-independent vocabulary for what MIRA learns about a connected device.

A device arrives with what its connection says (`DeviceEvent`: name,
manufacturer, category). A *provider* may learn more by asking the device or
the system — its model, its battery — and reports it as a `DeviceInfo`. Every
field is optional and means "this was read", never "this is likely": a
provider that cannot read a value leaves it None, and nothing ever fills it
in. A query that learns nothing says why (`InfoResult.problem`) and whether
asking again later may help (`retry`), for example while the user has not
yet accepted a debugging prompt on the phone.

Providers implement `DeviceInfoProvider` in `mira.adapters`; the service in
`mira.core` decides when to ask and which answers are still current; each
embodiment decides how an answer looks. The text below is what a notice
about it says.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mira.domain.devices import DeviceCategory
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone
from mira.domain.system_events import DeviceEvent


class ChargingState(StrEnum):
    """What the device's own battery is doing, as the device reports it."""

    CHARGING = "charging"
    DISCHARGING = "discharging"
    NOT_CHARGING = "not charging"
    """Connected to power but not taking charge (a limit, a cool-down)."""
    FULL = "full"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """What a provider read about one device; None means "not read"."""

    manufacturer: str | None = None
    model: str | None = None
    battery_percent: int | None = None
    charging: ChargingState | None = None
    capacity_bytes: int | None = None
    """Storage size of the medium, for storage devices."""

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in (
            self.manufacturer, self.model, self.battery_percent, self.charging, self.capacity_bytes,
        ))


@dataclass(frozen=True, slots=True)
class InfoResult:
    """One query's answer: the information, or why there is none."""

    info: DeviceInfo | None = None
    problem: str | None = None
    retry: bool = False
    """Asking again later may succeed (not authorized yet, still starting)."""


class DeviceInfoProvider(Protocol):
    """Something that can learn more about some devices.

    `supports` is cheap and decides from the event alone. `query` may block
    (a subprocess, a file read); it runs off the UI thread, must return
    within a bounded time, and must not raise: every failure is an
    `InfoResult` with a ``problem``. It only reads; it never changes the
    device or the system.
    """

    name: str

    def supports(self, device: DeviceEvent) -> bool: ...

    def query(self, device: DeviceEvent) -> InfoResult: ...


@dataclass(frozen=True, slots=True)
class DeviceInfoUpdate:
    """New information about a connected device, for whoever presents it.

    ``announce`` is the service's judgement that it is worth bringing forward
    on its own (the first answer for a connection, or a change of charging
    state); otherwise it only refines a notice about the device that is
    already showing.
    """

    device: DeviceEvent
    info: DeviceInfo
    announce: bool


_CHARGING_TEXT = {
    ChargingState.CHARGING: "Charging",
    ChargingState.DISCHARGING: "Discharging",
    ChargingState.NOT_CHARGING: "Not charging",
    ChargingState.FULL: "Full",
}


def format_capacity(size_bytes: int) -> str:
    """A storage size the way it is sold: decimal units, two significant figures."""
    for unit, scale in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6)):
        if size_bytes >= scale:
            value = size_bytes / scale
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}".replace(".0 ", " ")
    return f"{size_bytes} B"


def _short_name(device: DeviceEvent) -> str:
    """The device's name without its maker in front ("iPhone", not "Apple Inc. iPhone")."""
    name, maker = device.name.strip(), (device.manufacturer or "").strip()
    if maker and name.lower().startswith(maker.lower() + " "):
        return name[len(maker):].strip()
    return name


def device_info_name(device: DeviceEvent, info: DeviceInfo) -> str:
    """What to call the device: the model a provider read, else its USB name.

    Next to a battery reading the maker is dropped ("iPhone", "Pixel 8"): a
    phone's model reads well alone. Anything else keeps it, which is often
    what tells it apart ("SanDisk Ultra").
    """
    name = _short_name(device) if info.battery_percent is not None else device.name.strip()
    return (info.model or "").strip() or name or "Device"


def device_info_detail(info: DeviceInfo) -> str | None:
    """"84% · Charging", "64 GB": only the readings there are; None without any."""
    parts = []
    if info.battery_percent is not None:
        parts.append(f"{info.battery_percent}%")
    if info.charging is not None:
        parts.append(_CHARGING_TEXT[info.charging])
    if info.capacity_bytes is not None:
        parts.append(format_capacity(info.capacity_bytes))
    return " · ".join(parts) or None


def device_info_text(device: DeviceEvent, info: DeviceInfo) -> str:
    """"Pixel 8 — Battery 84% — Charging" in one line, with only the parts that were read."""

    parts = [device_info_name(device, info)]
    if info.battery_percent is not None:
        parts.append(f"Battery {info.battery_percent}%")
    if info.charging is not None:
        parts.append(_CHARGING_TEXT[info.charging])
    if info.capacity_bytes is not None:
        parts.append(format_capacity(info.capacity_bytes))
    return " — ".join(parts)


def device_info_notice(update: DeviceInfoUpdate) -> Notice:
    """The device notice an update refines: the same device, named, with its readings as detail."""

    device, info = update.device, update.info
    category = device.category or DeviceCategory.UNKNOWN
    return Notice(
        NoticeCategory.DEVICE, NoticeTone.INFO, device_info_name(device, info),
        NoticeDevice(category, connected=True, key=device.key), detail=device_info_detail(info),
    )
