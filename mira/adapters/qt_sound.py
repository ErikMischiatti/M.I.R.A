"""A `SoundOutput` on Qt Multimedia's `QSoundEffect`, for short UI sounds.

`QSoundEffect` is made for exactly this: a small uncompressed WAV, loaded
once and played with low latency, asynchronously on Qt's audio thread, so
`play` returns at once and never blocks the GUI thread. Qt Multimedia is
already a dependency (`mira.adapters.qt_audio` captures with it) and, as
there, it is imported only on first use.

Each `Sound` maps to a packaged WAV (`PACKAGED_SOUNDS`, rendered by
``scripts/generate_sounds.py``). One effect per sound is created and loaded
by `prepare` — when the runtime starts, like the capture backend's slow
first enumeration, so the moment a sound is due never pays for it — or on
its first play otherwise, and reused. A missing file, a sound Qt failed to
load, no output device or a failing backend is reported once on stderr and
otherwise ignored: nothing here ever raises into the caller. The effect is created on
the thread that owns the application's Qt objects, which is the only one
that plays.

`shutdown` stops and releases every effect; afterwards `play` does nothing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from mira.domain.sound import Sound

SOUNDS_DIR = Path(__file__).resolve().parents[1] / "sounds"
PACKAGED_SOUNDS = {Sound.TIMER_DONE: SOUNDS_DIR / "timer_done.wav"}

# Below full scale on top of the asset's own -6 dBFS peak: a notification,
# not an alarm.
DEFAULT_VOLUME = 0.6


def _sound_effect():
    from PySide6.QtMultimedia import QSoundEffect

    return QSoundEffect()


class QtSoundOutput:
    """A `SoundOutput` for the default audio output; see the module."""

    def __init__(
        self,
        sources: Mapping[Sound, Path] = PACKAGED_SOUNDS,
        *,
        volume: float = DEFAULT_VOLUME,
        effect_factory: Callable[[], object] | None = None,
    ):
        self._sources = dict(sources)
        self._volume = volume
        self._make_effect = _sound_effect if effect_factory is None else effect_factory
        self._effects: dict[Sound, object] = {}
        self._reported: set[Sound] = set()
        self._stopped = False

    def prepare(self) -> None:
        if self._stopped:
            return
        for sound in self._sources:
            try:
                self._effect(sound)
            except Exception as error:
                self._report(sound, f"could not be prepared: {error}")

    def play(self, sound: Sound) -> None:
        if self._stopped:
            return
        try:
            effect = self._effect(sound)
            if effect is None:
                return
            if getattr(effect.status(), "name", None) == "Error":
                self._report(sound, "could not be loaded or there is no audio output")
                return
            # Plays as soon as it has loaded, if it has not yet.
            effect.play()
        except Exception as error:
            self._report(sound, f"playback failed: {error}")

    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for effect in self._effects.values():
            try:
                effect.stop()
                effect.deleteLater()
            except Exception:
                pass
        self._effects.clear()

    def _effect(self, sound: Sound):
        effect = self._effects.get(sound)
        if effect is not None:
            return effect
        path = self._sources.get(sound)
        if path is None or not path.is_file():
            self._report(sound, f"no sound file ({path})")
            return None
        from PySide6.QtCore import QUrl

        effect = self._make_effect()
        effect.setSource(QUrl.fromLocalFile(str(path)))
        effect.setVolume(self._volume)
        self._effects[sound] = effect
        return effect

    def _report(self, sound: Sound, problem: str) -> None:
        if sound in self._reported:
            return
        self._reported.add(sound)
        print(f"[sound] {sound.value}: {problem}", file=sys.stderr)
