"""Wake-word and voice-activity inference, off the UI thread, in order.

Captured frames arrive on the Qt main thread (`AudioInputService`). Running
a keyword spotter and a speech detector there would cost the UI a few
milliseconds per frame, so `VoiceProcessor` hands them to a worker through
the existing `Scheduler` port (`submit`: work on the thread pool, completion
back on the main thread) — no new thread primitive:

- **Serialized.** At most one batch is in flight; the next is submitted when
  it completes. The engines keep streaming state, and this guarantees no two
  threads ever touch them, and that frames are processed in capture order.
- **Bounded.** Frames waiting for the worker sit in a deque of at most
  ``max_pending`` items; if the worker falls behind, the *oldest* are
  dropped (and counted), never accumulated.
- **Ordered resets.** Resetting an engine (a new session's VAD, the spotter
  after a detection) is queued with the frames, so it happens on the worker
  between exactly the right frames.
- **Failures are contained.** An engine that raises is reported once with
  the error; the batch's other results still arrive.

Results come back as one `VoiceResult` per frame: the wake detections and
speech windows found in it.
"""

from __future__ import annotations

import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from mira.domain.audio import AudioFrame
from mira.domain.scheduler import Scheduler
from mira.domain.voice import SpeechWindow, VoiceActivityDetector, WakeWordDetection, WakeWordDetector

DEFAULT_MAX_PENDING = 64  # about 2 s of 30 ms frames


@dataclass(frozen=True, slots=True)
class VoiceJob:
    frame: AudioFrame
    detect: bool = False
    segment: bool = False
    reset_vad: bool = False


@dataclass(frozen=True, slots=True)
class VoiceResult:
    job: VoiceJob
    detections: list[WakeWordDetection] = field(default_factory=list)
    windows: list[SpeechWindow] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VoiceBatch:
    results: list[VoiceResult]
    errors: list[str]


class VoiceProcessor:
    def __init__(self, scheduler: Scheduler, *, detector: WakeWordDetector | None,
                 vad: VoiceActivityDetector, on_results: Callable[[VoiceBatch], None],
                 max_pending: int = DEFAULT_MAX_PENDING):
        self._scheduler = scheduler
        self.detector = detector
        self.vad = vad
        self._on_results = on_results
        self._pending: deque[VoiceJob] = deque()
        self._max_pending = max_pending
        self._busy = False
        self._stopped = False
        self.dropped = 0
        self.processed = 0

    @property
    def pending(self) -> int:
        return len(self._pending)

    def submit(self, job: VoiceJob) -> None:
        """Queue one frame's work, after everything queued."""
        if self._stopped:
            return
        self._pending.append(job)
        self._trim()
        self._dispatch()

    def resegment(self, frames: list[AudioFrame]) -> bool:
        """A session just began: route audio captured since into its VAD.

        ``frames`` (processed already, after the activating one) and every
        frame still queued become segmentation jobs, in capture order, the
        first resetting the VAD — all placed before dispatching, so the order
        and the reset are exact. Returns whether any job was queued (if not,
        the next captured frame must reset the VAD).
        """
        if self._stopped:
            return False
        jobs = [*frames, *(job.frame for job in self._pending)]
        self._pending = deque(VoiceJob(frame, segment=True, reset_vad=(i == 0)) for i, frame in enumerate(jobs))
        self._trim()
        self._dispatch()
        return bool(jobs)

    def _trim(self) -> None:
        while len(self._pending) > self._max_pending:
            # Behind: the oldest audio is the least useful. Never a backlog.
            self._pending.popleft()
            self.dropped += 1

    def shutdown(self) -> None:
        self._stopped = True
        self._pending.clear()

    def _dispatch(self) -> None:
        if self._busy or self._stopped or not self._pending:
            return
        batch = list(self._pending)
        self._pending.clear()
        self._busy = True
        # The engines are bound now: replacing one later (new settings)
        # affects the next batch, never one the worker is running.
        detector, vad = self.detector, self.vad
        self._scheduler.submit(lambda: self._run(batch, detector, vad), self._done)

    def _run(self, jobs: list[VoiceJob], detector, vad) -> VoiceBatch:
        """On the worker: the engines, in order. Touches nothing else."""
        results, errors = [], []
        for job in jobs:
            detections, windows = [], []
            if job.reset_vad:
                vad.reset()
            if job.detect and detector is not None:
                try:
                    detections = detector.process(job.frame)
                except Exception as error:  # an engine bug must not stop audio
                    errors.append(f"wake-word detector: {error}")
            if job.segment:
                try:
                    windows = vad.process(job.frame)
                except Exception as error:
                    errors.append(f"voice activity detector: {error}")
            results.append(VoiceResult(job, detections, windows))
        return VoiceBatch(results, errors)

    def _done(self, batch: VoiceBatch) -> None:
        self._busy = False
        if self._stopped:
            return
        self.processed += len(batch.results)
        for error in batch.errors[:1]:
            print(f"[voice] {error}", file=sys.stderr)
        self._on_results(batch)
        self._dispatch()
