"""UI-independent vocabulary for MIRA listening to the user.

Three separate things, on purpose:

    audio capture capability   the microphone can be opened (`mira.domain.audio`)
    listening activation       something asks MIRA to start or stop listening
    conversation LISTENING     MIRA is listening to the user right now

An activation *source* (`ActivationSource`) only asks; the listening
controller (`mira.core.listening`) decides, takes a capture lease, and the
activity authority shows LISTENING. No capture backend knows what triggered
it, and "the microphone is open" does not mean "MIRA is listening": a
future wake-word detector will hold the microphone while MIRA is not
listening, and turn into an activation source when it hears its word.

`ListeningActivationMode` is the user's choice of how activation works.
Only KEYBOARD is implemented; WAKE_WORD is part of the model so settings can
be persisted and shown as coming later, never selectable while it would do
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ListeningActivationMode(StrEnum):
    KEYBOARD = "keyboard"
    WAKE_WORD = "wake word"


IMPLEMENTED_MODES = frozenset({ListeningActivationMode.KEYBOARD})


class ActivationSource(StrEnum):
    """What asked for the change; for logs and future policy, never for capture."""

    KEYBOARD = "keyboard"


class ListeningState(StrEnum):
    OFF = "off"
    LISTENING = "listening"


def toggled(state: ListeningState) -> ListeningState:
    """The keyboard toggle: OFF ↔ LISTENING."""
    return ListeningState.OFF if state is ListeningState.LISTENING else ListeningState.LISTENING


@dataclass(frozen=True, slots=True)
class ListeningStatus:
    """What the controller announces on every change (``"listening_changed"``).

    ``capturing`` says whether real audio is arriving, so a presentation can
    show it — and show nothing in its place when it is not. ``reason``
    explains a change the user did not ask for (the microphone went away).
    """

    state: ListeningState
    source: ActivationSource | None = None
    capturing: bool = False
    reason: str | None = None

    @property
    def listening(self) -> bool:
        return self.state is ListeningState.LISTENING
