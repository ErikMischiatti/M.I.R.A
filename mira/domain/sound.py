"""UI-independent vocabulary for the short sounds MIRA plays, and their output port.

A `Sound` names *what* is to be heard — the timer having finished — never a
file, a format or a device. A `SoundOutput` plays one: it returns at once,
plays asynchronously, and never raises because there is no device or the
sound cannot be loaded; a missing sound is a status, not an error, the same
as a missing microphone or camera. Which embodiment-specific asset a sound
is, and how it reaches a speaker, is the adapter's business
(`mira.adapters.qt_sound`).

`SilentSoundOutput` is the default: a test or a headless runtime plays
nothing and opens nothing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class Sound(StrEnum):
    TIMER_DONE = "timer done"
    """A countdown ran out: a short, friendly chime."""


@runtime_checkable
class SoundOutput(Protocol):
    def prepare(self) -> None:
        """Load every sound ahead of its first play; opens no stream."""

    def play(self, sound: Sound) -> None:
        """Start playing ``sound`` and return at once."""

    def shutdown(self) -> None:
        """Stop playing and release the device; idempotent."""


class SilentSoundOutput:
    """A `SoundOutput` that plays nothing."""

    def prepare(self) -> None:
        pass

    def play(self, sound: Sound) -> None:
        pass

    def shutdown(self) -> None:
        pass
