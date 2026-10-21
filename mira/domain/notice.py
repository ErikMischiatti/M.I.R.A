"""UI-independent vocabulary for transient things MIRA wants to bring forward.

A `Notice` is something worth a glance right now — an action finished, a
device appeared, something went wrong. Whoever notices it (a service, an
action, a future sensor) says *what kind* of thing it is and how it went; it
never says how it is shown. Each embodiment decides that for itself: the
desktop notch may expand and draw a symbol, a small display may flash, an
embodiment without a display may ignore it entirely.

Two independent axes, both deliberately small:

- `NoticeCategory` is *why* it matters, and is what user preferences are
  keyed on ("expand automatically for device changes"). Categories are stable
  groups, not individual events, so a new event type normally needs no new
  preference.
- `NoticeTone` is *how it went*: neutral information, success, warning or
  error.

Only categories with a producer or a presentation today are listed; new ones
are added when something emits them. `text` is optional and short: an
embodiment that cannot show text, or a notice whose tone says enough, needs
none. `device` is optional too: a DEVICE notice whose source could tell what
kind of device it is about, and whether it arrived or left, says so, and an
embodiment may show it (the notch draws a phone for a phone).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mira.domain.devices import DeviceCategory


class NoticeCategory(StrEnum):
    """Why a notice matters; the unit user preferences are expressed in."""

    ALERT = "alert"
    """Warnings and errors that are not tied to a task or device."""

    DEVICE = "device"
    """A device or input/output route appeared, disappeared or changed."""

    TASK = "task"
    """Something MIRA was asked to do started, finished or failed."""

    CONNECTIVITY = "connectivity"
    """The network became unavailable, limited, or available again."""


class NoticeTone(StrEnum):
    """How the noticed thing went."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class NoticeDevice:
    """The device a DEVICE notice is about, when its source could tell."""

    category: DeviceCategory
    connected: bool
    """True when it arrived, False when it left."""
    key: str | None = None
    """Which device (`DeviceEvent.key`), to correlate later information
    with this notice; never shown."""


@dataclass(frozen=True, slots=True)
class Notice:
    """One transient notice; a value, not a command."""

    category: NoticeCategory
    tone: NoticeTone = NoticeTone.INFO
    text: str | None = None
    device: NoticeDevice | None = None
