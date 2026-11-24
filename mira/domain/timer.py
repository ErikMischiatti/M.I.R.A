"""UI-independent vocabulary for MIRA's countdown timer.

One countdown at a time. Its lifecycle:

    IDLE ──start──▶ RUNNING ──deadline──▶ FINISHED ──acknowledge──▶ IDLE
                     │  ▲                    │
                pause│  │resume              │start (again)
                     ▼  │                    ▼
                    PAUSED                RUNNING

    reset: RUNNING / PAUSED / FINISHED ──▶ IDLE

FINISHED is the countdown having run out *and the user not having said so
yet*: it lasts until `acknowledge` (or a reset or new start), with no
timeout, and embodiments keep showing it for exactly that long.

`TimerSnapshot` is what anyone outside the service sees: a value read at
one moment. While running, `remaining_s` is only true for the moment it was
read; a display that wants the live value reads a new snapshot each time it
draws instead of being told on every second.

Durations are whole seconds, at least one and short of a full day, so the
remaining time always reads as ``MM:SS`` or ``HH:MM:SS``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

MIN_TIMER_SECONDS = 1
MAX_TIMER_SECONDS = 24 * 3600 - 1


class TimerState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class TimerSnapshot:
    """The timer at one moment; ``duration_s`` is 0 until one was started."""

    state: TimerState = TimerState.IDLE
    duration_s: int = 0
    remaining_s: float = 0.0

    @property
    def active(self) -> bool:
        """Counting down or paused: a countdown is under way."""
        return self.state in (TimerState.RUNNING, TimerState.PAUSED)


def validate_duration(seconds) -> int:
    """``seconds`` as a timer duration, or ValueError."""
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError(f"timer duration must be whole seconds, got {seconds!r}")
    if not MIN_TIMER_SECONDS <= seconds <= MAX_TIMER_SECONDS:
        raise ValueError(
            f"timer duration must be within {MIN_TIMER_SECONDS}..{MAX_TIMER_SECONDS} s, got {seconds}"
        )
    return seconds


def format_remaining(seconds: float) -> str:
    """``MM:SS`` below an hour, ``HH:MM:SS`` from one.

    Rounded up, so the last second reads ``00:01`` and ``00:00`` means done.
    """
    total = max(math.ceil(seconds), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
