"""Technology-independent vocabulary for MIRA's nonverbal voice.

The voice communicates intent and emotion, never information: a short burst of
pseudo-syllabic babble whose rhythm and intonation say "hello", "got it" or
"hm?". Precise content stays in the text UI.

A caller asks for a `VocalizationRequest` — a `VocalIntent` with an intensity
and an optional seed. A synthesizer (`mira.domain.vocal_synthesis`) turns that
request and a `VoiceIdentity` into a `PcmClip`, and an `AudioOutput` plays the
clip. None of these types knows which audio library, if any, is behind the
output, so the cognitive layer can express itself the same way through a
desktop speaker today and a robot's amplifier later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class VocalIntent(StrEnum):
    """What a vocalization should convey. Each has its own prosody."""

    GREETING = "greeting"
    ACKNOWLEDGEMENT = "acknowledgement"
    THINKING = "thinking"
    CURIOUS = "curious"
    HAPPY = "happy"
    CONFUSED = "confused"
    ERROR = "error"
    FAREWELL = "farewell"


DEFAULT_INTENSITY = 0.6


class VoiceEngine(StrEnum):
    """Which synthesizer renders the voice. V1 is the default; V2 is opt-in."""

    V1 = "v1"
    V2 = "v2"


DEFAULT_ENGINE = VoiceEngine.V1


@dataclass(frozen=True, slots=True)
class VocalizationRequest:
    """One request to vocalize.

    ``intensity`` in ``0.0..1.0`` scales pitch range, energy and liveliness;
    it never changes which intent is heard. ``seed`` makes the clip exactly
    reproducible; None lets every utterance vary slightly, which is what keeps
    repeated reactions from sounding like a sound file.
    """

    intent: VocalIntent
    intensity: float = DEFAULT_INTENSITY
    seed: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, VocalIntent):
            raise ValueError(f"unknown vocal intent: {self.intent!r}")
        if not (math.isfinite(self.intensity) and 0.0 <= self.intensity <= 1.0):
            raise ValueError(f"intensity must be within 0..1, got {self.intensity!r}")


def _clamp(value: float, low: float, high: float, default: float) -> float:
    if not math.isfinite(value):
        return default
    return min(high, max(low, value))


@dataclass(frozen=True, slots=True)
class VoiceIdentity:
    """The adjustable character of the voice, shared by every intent.

    - ``base_pitch_hz``: the resting fundamental. Small-character territory by
      default; intents move relative to it.
    - ``pitch_variation``: multiplier on every intent's pitch movement.
    - ``speaking_rate``: multiplier on syllable rate; 2.0 is twice as fast.
    - ``brightness``: 0 is soft and round, 1 is crisp; spectral tilt and the
      final low-pass follow it.
    - ``volume``: peak level as a fraction of full scale, before the output.

    `bounded` clamps every field into the range the synthesizer is designed
    for, so a configuration typo produces a plausible voice, not a shriek.
    """

    base_pitch_hz: float = 320.0
    pitch_variation: float = 1.0
    speaking_rate: float = 1.0
    brightness: float = 0.45
    volume: float = 0.5

    PITCH_RANGE_HZ = (120.0, 700.0)
    VARIATION_RANGE = (0.0, 2.0)
    RATE_RANGE = (0.5, 2.0)
    BRIGHTNESS_RANGE = (0.0, 1.0)
    VOLUME_RANGE = (0.0, 1.0)

    def bounded(self) -> VoiceIdentity:
        return VoiceIdentity(
            base_pitch_hz=_clamp(self.base_pitch_hz, *self.PITCH_RANGE_HZ, 320.0),
            pitch_variation=_clamp(self.pitch_variation, *self.VARIATION_RANGE, 1.0),
            speaking_rate=_clamp(self.speaking_rate, *self.RATE_RANGE, 1.0),
            brightness=_clamp(self.brightness, *self.BRIGHTNESS_RANGE, 0.45),
            volume=_clamp(self.volume, *self.VOLUME_RANGE, 0.5),
        )


@dataclass(frozen=True, slots=True)
class PcmClip:
    """Mono signed 16-bit little-endian PCM at ``sample_rate``.

    ``samples`` is immutable bytes, so a clip rendered on a worker thread can be
    handed to the playback thread without copying or locking.
    """

    sample_rate: int
    samples: bytes

    SAMPLE_WIDTH_BYTES = 2
    CHANNELS = 1

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {self.sample_rate}")
        if len(self.samples) % self.SAMPLE_WIDTH_BYTES:
            raise ValueError("samples must hold whole 16-bit frames")

    @property
    def frame_count(self) -> int:
        return len(self.samples) // self.SAMPLE_WIDTH_BYTES

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.sample_rate

    @property
    def duration_ms(self) -> int:
        return math.ceil(self.duration_s * 1000)


class AudioOutput(Protocol):
    """Plays clips without blocking the caller.

    Called only from the serialized context. ``play`` starts a clip and returns
    immediately, replacing anything still sounding; it raises `AudioOutputError`
    when the clip cannot be started. ``available`` is a cheap, non-raising
    check. ``close`` stops playback and releases the device; it is idempotent.
    """

    @property
    def available(self) -> bool: ...

    def play(self, clip: PcmClip) -> None: ...

    def close(self) -> None: ...


class AudioOutputError(Exception):
    """A clip could not be started; the message says why, for diagnostics."""
