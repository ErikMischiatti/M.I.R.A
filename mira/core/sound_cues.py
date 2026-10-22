"""Turn application events into short sounds: ``"timer_finished"`` ──▶ `Sound.TIMER_DONE`.

The only listener to ``"timer_finished"``, which `CountdownTimer` emits
exactly once per countdown that runs out — never on pause, reset,
acknowledgement or any redraw — so the chime plays once per finish with no
bookkeeping here. The timer itself knows nothing about sound.

The bus delivers synchronously, from inside the timer's own finish: an
output that raises would break the countdown's fan-out. Any failure is
therefore logged and swallowed here; the visual completion never depends on
a speaker. After `shutdown` nothing more is played.
"""

from __future__ import annotations

import sys

from mira.domain.sound import Sound, SoundOutput
from mira.messaging.events import EventBus


class SoundCues:
    def __init__(self, event_bus: EventBus, output: SoundOutput):
        self._output = output
        self._stopped = False
        event_bus.subscribe("timer_finished", self.on_timer_finished)

    def on_timer_finished(self, payload) -> None:
        self._play(Sound.TIMER_DONE)

    def shutdown(self) -> None:
        self._stopped = True

    def _play(self, sound: Sound) -> None:
        if self._stopped:
            return
        try:
            self._output.play(sound)
        except Exception as error:  # a speaker must never break the caller
            print(f"[sound] could not play {sound.value}: {error}", file=sys.stderr)
