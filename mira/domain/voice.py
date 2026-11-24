"""Voice activation and utterance segmentation, independent of any engine.

Four things are kept apart, and none implies another:

    audio capture        samples exist (`AudioInputService` holds a lease)
    wake monitoring      samples are inspected for the wake word, while MIRA
                         stays idle
    listening session    MIRA waits for, then records, one user utterance
    speech active        the user is speaking right now, inside a session

Engines sit behind two narrow ports and report only semantic values:
`WakeWordDetector` → `WakeWordDetection`, `VoiceActivityDetector` →
`SpeechWindow`. `ListeningSession` is the pure, deterministic state machine
that turns speech windows over time into one `Utterance`:

    IDLE ──start──▶ WAITING_FOR_SPEECH ──onset──▶ SPEECH_ACTIVE ──silence──▶ utterance ready
                        │ timeout                      │ max duration
                        ▼                              ▼
                      ended (no utterance)          ended (no utterance)
    any state ── cancel / microphone lost / shutdown ──▶ ended (no utterance)

Its clock is the *sample count*, not wall time: every timeout, the pre-roll
and the hangover are exact numbers of samples, so behaviour depends only on
the audio given. Memory is bounded: while waiting only the last
`pre_roll_s` (plus the onset window) is kept, and an utterance never exceeds
`max_utterance_s`. Nothing is written anywhere.
"""

from __future__ import annotations

import math
from array import array
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from mira.domain.audio import FULL_SCALE, AudioFrame
from mira.domain.listening import ActivationSource


# --- Engines --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WakeWordDetection:
    """The configured wake phrase was heard; in the frame it was reported for."""

    phrase: str


@dataclass(frozen=True, slots=True)
class SpeechWindow:
    """One VAD window: whether it holds speech, and where it ends.

    ``end_sample`` counts samples given to the detector since its last
    `reset`; ``length`` is the window's size in samples.
    """

    speech: bool
    end_sample: int
    length: int

    @property
    def start_sample(self) -> int:
        return self.end_sample - self.length


class WakeWordDetector(Protocol):
    """Listens for one configured phrase in 16 kHz mono frames.

    `process` is called with consecutive frames on one worker at a time and
    returns a detection for each time the phrase ends in that frame. `reset`
    forgets everything heard (after a detection, so the same audio cannot
    fire twice).
    """

    name: str

    def process(self, frame: AudioFrame) -> list[WakeWordDetection]: ...

    def reset(self) -> None: ...


class VoiceActivityDetector(Protocol):
    """Classifies consecutive windows of 16 kHz mono audio as speech or not."""

    name: str

    def process(self, frame: AudioFrame) -> list[SpeechWindow]: ...

    def reset(self) -> None: ...


# Measured on this project's target machine: an Italian-spoken "mira" was
# heard 4 times out of about 5 at 0.8 and once at 0.5, with no false wake on
# ordinary speech at either. A starting point, not a benchmark.
DEFAULT_WAKE_SENSITIVITY = 0.8


@dataclass(frozen=True, slots=True)
class WakeWordConfig:
    """What to listen for. ``phrase`` is text, so a new name needs no new code.

    ``sensitivity`` in [0, 1]: higher catches the phrase more readily, at the
    cost of more false activations. How it maps onto an engine is the
    adapter's business.
    """

    phrase: str = "mira"
    sensitivity: float = DEFAULT_WAKE_SENSITIVITY

    def __post_init__(self):
        phrase = self.phrase.strip() if isinstance(self.phrase, str) else ""
        if not phrase or len(phrase) > 32 or not all(c.isalpha() or c in " -'" for c in phrase):
            raise ValueError(f"the wake phrase must be 1–32 letters, got {self.phrase!r}")
        if not (isinstance(self.sensitivity, (int, float)) and 0.0 <= self.sensitivity <= 1.0):
            raise ValueError(f"wake sensitivity must be within 0..1, got {self.sensitivity!r}")
        object.__setattr__(self, "phrase", phrase.lower())


# --- A dependency-free VAD ----------------------------------------------------------------------


class EnergyVoiceActivityDetector:
    """Speech where the level rises well above the recent noise floor.

    The fallback when no model-based detector is installed, and a real
    detector for tests. Windows of ``window`` samples; a window is speech
    when it is at least ``margin_db`` above the noise floor and above
    ``min_db``. The floor is the quietest window of the last ``floor_s``
    seconds: speech always has gaps between syllables, so it does not lift
    the floor, while a steady noise (a fan) becomes the floor within
    ``floor_s`` and stops counting as speech.

    `reset` (a new session) restarts the window count but keeps the floor:
    the room's noise belongs to the room, not to a session, and forgetting it
    would make the first words after activation the floor. Before anything
    was measured the floor is `UNMEASURED_FLOOR_DB`.
    """

    UNMEASURED_FLOOR_DB = -60.0

    name = "energy (built-in)"

    def __init__(self, *, window: int = 512, margin_db: float = 12.0, min_db: float = -50.0,
                 floor_s: float = 2.0, sample_rate: int = 16000):
        self.window = window
        self.margin_db = margin_db
        self.min_db = min_db
        self._floor_windows = max(int(floor_s * sample_rate / window), 1)
        # Filled with the unmeasured floor, so a level must hold for the
        # whole floor span — a fan, never the first words — to become it.
        self._levels: deque[float] = deque([self.UNMEASURED_FLOOR_DB] * self._floor_windows,
                                           maxlen=self._floor_windows)
        self.reset()

    def reset(self) -> None:
        self._pending = array("h")
        self._samples = 0

    @property
    def noise_db(self) -> float:
        return min(self._levels)

    def process(self, frame: AudioFrame) -> list[SpeechWindow]:
        self._pending.frombytes(frame.pcm)
        windows = []
        while len(self._pending) >= self.window:
            chunk = self._pending[: self.window]
            del self._pending[: self.window]
            self._samples += self.window
            rms = math.sqrt(float(math.sumprod(chunk, chunk)) / self.window) / FULL_SCALE
            level = 20.0 * math.log10(rms) if rms > 0 else -120.0
            floor = self.noise_db
            self._levels.append(level)
            speech = level > self.min_db and level > floor + self.margin_db
            windows.append(SpeechWindow(speech, self._samples, self.window))
        return windows


# --- The session ---------------------------------------------------------------------------------


# The largest VAD window a detector may use, in samples (Silero uses 512 at
# 16 kHz); bounds how much audio the onset window can need.
MAX_WINDOW_SAMPLES = 1024


class ListeningSessionState(StrEnum):
    IDLE = "idle"
    WAITING_FOR_SPEECH = "waiting for speech"
    SPEECH_ACTIVE = "speech active"


class SessionEnd(StrEnum):
    """Why a session ended; only UTTERANCE_READY carries an utterance."""

    UTTERANCE_READY = "utterance ready"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed out"
    MAX_DURATION = "maximum duration"
    MICROPHONE_LOST = "microphone lost"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class SegmentationPolicy:
    """How speech is found in the windows. Values are in seconds/windows.

    - onset: ``onset_windows`` speech windows among the last ``onset_span``
      start speech, so one noisy window does not;
    - end: ``end_silence_s`` without a speech window ends it, so natural
      pauses between words do not;
    - ``tail_s`` of audio is kept after the last speech, for trailing sounds;
    - ``pre_roll_s`` of audio before the onset is included, so the first
      phoneme survives the detector's latency — never audio from before
      the session started;
    - ``wait_timeout_s`` without speech ends the session quietly;
    - ``max_utterance_s`` bounds an utterance (and memory); reaching it
      discards the utterance (a stuck detector or constant noise).
    """

    onset_windows: int = 3
    onset_span: int = 5
    end_silence_s: float = 0.8
    tail_s: float = 0.25
    pre_roll_s: float = 0.3
    wait_timeout_s: float = 8.0
    max_utterance_s: float = 30.0

    def __post_init__(self):
        if not 1 <= self.onset_windows <= self.onset_span:
            raise ValueError("onset_windows must be within 1..onset_span")
        if not 0.2 <= self.end_silence_s <= 3.0:
            raise ValueError(f"end of speech silence must be within 0.2..3 s, got {self.end_silence_s}")
        if not 2.0 <= self.wait_timeout_s <= 30.0:
            raise ValueError(f"waiting timeout must be within 2..30 s, got {self.wait_timeout_s}")
        if not 2.0 <= self.max_utterance_s <= 120.0:
            raise ValueError(f"maximum utterance must be within 2..120 s, got {self.max_utterance_s}")
        if self.tail_s < 0 or self.tail_s > self.end_silence_s or self.pre_roll_s < 0 or self.pre_roll_s > 1.0:
            raise ValueError("tail and pre-roll must be small and non-negative")


@dataclass(frozen=True, slots=True)
class Utterance:
    """One completed stretch of user speech: 16-bit mono PCM, in memory only.

    The input to a future speech recogniser, which needs to know neither how
    listening started (``activation`` is for diagnostics and policy) nor
    which backend captured it.
    """

    id: int
    pcm: bytes = field(repr=False)
    sample_rate: int
    activation: ActivationSource
    speech_started_s: float
    speech_ended_s: float

    @property
    def sample_count(self) -> int:
        return len(self.pcm) // 2

    @property
    def duration_s(self) -> float:
        return self.sample_count / self.sample_rate


@dataclass(frozen=True, slots=True)
class SpeechStarted:
    at_s: float


@dataclass(frozen=True, slots=True)
class SessionEnded:
    reason: SessionEnd
    utterance: Utterance | None = None


SessionEvent = SpeechStarted | SessionEnded


class ListeningSession:
    """One listening session's segmentation; see the module.

    `feed` takes each captured frame with the VAD windows computed for it
    (windows counted from the session's start, `reset` the VAD then) and
    returns what happened. Times in events are seconds since the session
    started.
    """

    def __init__(self, source: ActivationSource, *, utterance_id: int, sample_rate: int = 16000,
                 policy: SegmentationPolicy = SegmentationPolicy()):
        self.source = source
        self.policy = policy
        self.rate = sample_rate
        self._id = utterance_id
        self.state = ListeningSessionState.WAITING_FOR_SPEECH
        self._fed = 0                       # samples given so far
        self._held = deque()                # (start sample, pcm) while waiting
        self._held_samples = 0
        self._recent = deque(maxlen=policy.onset_span)
        self._audio = bytearray()           # the utterance, from its first sample
        self._audio_start = 0
        self._speech_start = 0
        self._last_speech_end = 0

    def _samples(self, seconds: float) -> int:
        return int(round(seconds * self.rate))

    @property
    def elapsed_s(self) -> float:
        return self._fed / self.rate

    @property
    def held_samples(self) -> int:
        """Audio kept right now, waiting or recording; bounded by the policy."""
        return self._held_samples if self.state is ListeningSessionState.WAITING_FOR_SPEECH else len(self._audio) // 2

    @property
    def active(self) -> bool:
        return self.state is not ListeningSessionState.IDLE

    def end(self, reason: SessionEnd) -> SessionEnded | None:
        """End from outside (cancel, microphone lost, shutdown); no utterance."""
        if not self.active:
            return None
        self._close()
        return SessionEnded(reason)

    def feed(self, frame: AudioFrame, windows: list[SpeechWindow]) -> list[SessionEvent]:
        if not self.active:
            return []
        start = self._fed
        self._fed += frame.sample_count
        events: list[SessionEvent] = []
        if self.state is ListeningSessionState.WAITING_FOR_SPEECH:
            self._hold(start, frame.pcm)
        else:
            self._audio += frame.pcm
        for window in windows:
            events += self._window(window)
            if not self.active:
                return events
        events += self._limits()
        return events

    # --- internals ----------------------------------------------------------------------

    def _hold(self, start: int, pcm: bytes) -> None:
        self._held.append((start, pcm))
        self._held_samples += len(pcm) // 2
        keep = self._samples(self.policy.pre_roll_s) + MAX_WINDOW_SAMPLES * (self.policy.onset_span + 1)
        while self._held and self._held_samples - len(self._held[0][1]) // 2 >= keep:
            _, old = self._held.popleft()
            self._held_samples -= len(old) // 2

    def _window(self, window: SpeechWindow) -> list[SessionEvent]:
        if self.state is ListeningSessionState.WAITING_FOR_SPEECH:
            self._recent.append(window)
            if sum(w.speech for w in self._recent) >= self.policy.onset_windows:
                first = next(w for w in self._recent if w.speech)
                return [self._begin_speech(first.start_sample, window.end_sample)]
            return []
        if window.speech:
            self._last_speech_end = window.end_sample
        elif window.end_sample - self._last_speech_end >= self._samples(self.policy.end_silence_s):
            return [self._finish()]
        return []

    def _begin_speech(self, speech_start: int, onset_end: int) -> SpeechStarted:
        begin = max(speech_start - self._samples(self.policy.pre_roll_s), 0)
        audio = bytearray()
        audio_start = None
        for start, pcm in self._held:
            end = start + len(pcm) // 2
            if end <= begin:
                continue
            skip = max(begin - start, 0)
            if audio_start is None:
                audio_start = start + skip
            audio += pcm[skip * 2:]
        self._audio = audio
        self._audio_start = audio_start if audio_start is not None else begin
        self._held.clear()
        self._held_samples = 0
        self._speech_start = speech_start
        self._last_speech_end = onset_end
        self.state = ListeningSessionState.SPEECH_ACTIVE
        return SpeechStarted(speech_start / self.rate)

    def _finish(self) -> SessionEnded:
        keep_until = self._last_speech_end + self._samples(self.policy.tail_s)
        length = max(min(keep_until, self._fed) - self._audio_start, 0)
        pcm = bytes(self._audio[: length * 2])
        utterance = Utterance(self._id, pcm, self.rate, self.source,
                              self._speech_start / self.rate, self._last_speech_end / self.rate)
        self._close()
        return SessionEnded(SessionEnd.UTTERANCE_READY, utterance)

    def _limits(self) -> list[SessionEvent]:
        if self.state is ListeningSessionState.WAITING_FOR_SPEECH and self._fed >= self._samples(self.policy.wait_timeout_s):
            self._close()
            return [SessionEnded(SessionEnd.TIMED_OUT)]
        if self.state is ListeningSessionState.SPEECH_ACTIVE and \
                len(self._audio) // 2 >= self._samples(self.policy.max_utterance_s):
            self._close()
            return [SessionEnded(SessionEnd.MAX_DURATION)]
        return []

    def _close(self) -> None:
        self.state = ListeningSessionState.IDLE
        self._held.clear()
        self._held_samples = 0
        self._audio = bytearray()
