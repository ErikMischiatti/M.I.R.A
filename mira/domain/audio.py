"""UI-independent audio input vocabulary: frames, levels and a waveform.

Captured audio enters MIRA as `AudioFrame` values — signed 16-bit mono PCM
held in memory, never written anywhere — produced by a capture backend
(`mira.adapters.qt_audio` on the desktop) behind the `AudioCaptureBackend`
port. Nothing here knows Qt, PipeWire or any device.

Three derived values, all pure and deterministic:

- `measure`: the frame's RMS and peak as fractions of full scale (raw, linear);
- `LevelEnvelope`: a presentation level in [0, 1] on a decibel scale, with a
  fast attack and a slower release, advanced by frame timestamps rather
  than wall-clock time, so speech dynamics stay visible without flicker;
- `WaveformReducer`: a bounded history of per-bucket peaks (`AudioWaveform`)
  for drawing, a few dozen values per second instead of thousands of
  samples.

Capture is a *capability*, used by whoever holds a lease on it
(`CapturePurpose`); it is not the same thing as MIRA listening to a
conversation, and nothing here assumes it is.
"""

from __future__ import annotations

import math
from array import array
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

FULL_SCALE = 32768.0

# The decibel window mapped onto [0, 1] for presentation: quieter than
# FLOOR_DB shows as nothing, full scale as everything.
FLOOR_DB = -60.0


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """A run of captured samples: signed 16-bit little-endian mono PCM."""

    pcm: bytes
    sample_rate: int
    timestamp_s: float

    def __post_init__(self):
        if self.sample_rate <= 0:
            raise ValueError(f"sample rate must be positive, got {self.sample_rate}")
        if len(self.pcm) % 2:
            raise ValueError("16-bit PCM must have an even number of bytes")

    @property
    def sample_count(self) -> int:
        return len(self.pcm) // 2

    @property
    def duration_s(self) -> float:
        return self.sample_count / self.sample_rate

    def samples(self) -> array:
        values = array("h")
        values.frombytes(self.pcm)
        return values


@dataclass(frozen=True, slots=True)
class AudioInputLevel:
    """RMS and peak of some samples, as fractions of full scale (0 to 1)."""

    rms: float
    peak: float
    timestamp_s: float


def measure(frame: AudioFrame) -> AudioInputLevel:
    """The raw level of ``frame``; silence (or no samples) is zero."""

    samples = frame.samples()
    if not samples:
        return AudioInputLevel(0.0, 0.0, frame.timestamp_s)
    squares = math.fsum(value * value for value in samples)
    peak = max(max(samples), -min(samples))
    return AudioInputLevel(
        rms=min(math.sqrt(squares / len(samples)) / FULL_SCALE, 1.0),
        peak=min(peak / FULL_SCALE, 1.0),
        timestamp_s=frame.timestamp_s,
    )


def to_display(linear: float) -> float:
    """A linear amplitude (0 to 1) on the presentation scale (0 to 1).

    Decibels, because loudness is perceived logarithmically: a raw PCM
    magnitude would leave ordinary speech near the bottom.
    """

    if linear <= 0.0:
        return 0.0
    decibels = 20.0 * math.log10(min(linear, 1.0))
    return min(max((decibels - FLOOR_DB) / -FLOOR_DB, 0.0), 1.0)


class LevelEnvelope:
    """Fast attack, slower release, on the presentation scale.

    Each `update` moves the level towards the new frame's display RMS by an
    exponential factor for the time since the previous frame: rising with
    time constant ``attack_s``, falling with ``release_s``. Gaps longer than
    a frame are therefore handled exactly, and the result depends only on
    the frames given.
    """

    def __init__(self, *, attack_s: float = 0.03, release_s: float = 0.35):
        self.attack_s = attack_s
        self.release_s = release_s
        self.level = 0.0
        self._last_s: float | None = None

    def reset(self) -> None:
        self.level = 0.0
        self._last_s = None

    def update(self, measured: AudioInputLevel) -> float:
        target = to_display(measured.rms)
        if self._last_s is None:
            dt = 0.0
            self.level = target if target > self.level else self.level
        else:
            dt = max(measured.timestamp_s - self._last_s, 0.0)
        self._last_s = measured.timestamp_s
        if dt > 0.0:
            tau = self.attack_s if target > self.level else self.release_s
            self.level += (target - self.level) * (1.0 - math.exp(-dt / tau))
        return self.level


@dataclass(frozen=True, slots=True)
class AudioWaveform:
    """Recent per-bucket peaks on the presentation scale, oldest first."""

    buckets: tuple[float, ...]
    bucket_s: float
    timestamp_s: float


class WaveformReducer:
    """Reduce frames to a bounded history of bucket peaks.

    Samples are grouped into consecutive buckets of ``bucket_s`` seconds; a
    finished bucket contributes its peak, on the display scale. At most
    ``size`` buckets are kept (``size × bucket_s`` seconds), so memory never
    grows with capture time. `waveform` is None until a bucket exists: no
    samples, no waveform.
    """

    def __init__(self, *, bucket_s: float = 0.025, size: int = 40):
        self.bucket_s = bucket_s
        self.size = size
        self._buckets: deque[float] = deque(maxlen=size)
        self._pending_peak = 0
        self._pending_count = 0
        self._last_s = 0.0

    def reset(self) -> None:
        self._buckets.clear()
        self._pending_peak = 0
        self._pending_count = 0

    def add(self, frame: AudioFrame) -> None:
        per_bucket = max(int(round(frame.sample_rate * self.bucket_s)), 1)
        peak, count = self._pending_peak, self._pending_count
        for value in frame.samples():
            magnitude = value if value >= 0 else -value
            if magnitude > peak:
                peak = magnitude
            count += 1
            if count >= per_bucket:
                self._buckets.append(to_display(peak / FULL_SCALE))
                peak, count = 0, 0
        self._pending_peak, self._pending_count = peak, count
        self._last_s = frame.timestamp_s

    def waveform(self) -> AudioWaveform | None:
        if not self._buckets:
            return None
        return AudioWaveform(tuple(self._buckets), self.bucket_s, self._last_s)


class CaptureStatus(StrEnum):
    IDLE = "idle"
    """Available, not capturing: nobody holds a lease."""
    CAPTURING = "capturing"
    UNAVAILABLE = "unavailable"
    """No backend or no input device."""
    FAILED = "failed"
    """Capture could not start, or stopped by itself (device lost)."""


class CapturePurpose(StrEnum):
    """Why someone needs the microphone; one lease per purpose holder.

    Conversation listening, and an explicit microphone test in the settings.
    A wake-word detector would hold its own lease, so capture can run while
    MIRA is not listening.
    """

    CONVERSATION = "conversation"
    DIAGNOSTICS = "microphone test"


@dataclass(frozen=True, slots=True)
class AudioInputInfo:
    """What a backend is capturing from, for diagnostics."""

    backend: str
    device: str | None = None
    sample_rate: int | None = None
    channels: int | None = None


FrameListener = Callable[[AudioFrame], None]
FailureListener = Callable[[str], None]


class AudioCaptureBackend(Protocol):
    """Captures from the system's default input; delivers on the caller's thread.

    `start` returns False (and calls nothing) when capture cannot begin; a
    failure after starting — the device disappears, the server stops — is
    reported once through ``on_failure``. `stop` is idempotent and stops
    every delivery.
    """

    name: str

    def start(self, on_frame: FrameListener, on_failure: FailureListener) -> bool: ...

    def stop(self) -> None: ...

    @property
    def info(self) -> AudioInputInfo: ...
