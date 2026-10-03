"""Conversation listening: activation in, one session, an utterance out.

`ListeningController` is the one place that decides whether MIRA listens.

    keyboard (toggle request on the bus) ──┐
                                           ├─▶ session ─▶ "listening_changed"
    wake word (detector, while monitoring) ┘        └──▶ "utterance_ready"

- **Activation.** Sources only ask. A keyboard toggle starts a session, or
  cancels the running one; a wake-word detection starts a session and is
  ignored while one runs and for `WAKE_COOLDOWN_S` after it ends, so one
  spoken phrase can never start two. There is never more than one session.
- **Wake monitoring** (WAKE_WORD mode, detector available) holds its own
  capture lease and feeds the detector, while MIRA stays idle: monitoring is
  not listening and is never announced as such.
- **The session** (`mira.domain.voice.ListeningSession`) holds the
  conversation lease, resets the VAD and segments the windows it is fed:
  WAITING_FOR_SPEECH → SPEECH_ACTIVE → utterance. Each change is announced
  as ``"listening_changed"`` (`ListeningStatus`: listening while waiting or
  speaking, with ``speech_active`` as an orthogonal signal);
  `InteractionManager` shows LISTENING through the activity authority. A
  completed utterance is emitted once as ``"utterance_ready"``; nothing
  consumes it yet beyond a log line of its metadata.
- **Wake-word exclusion.** A session started by a detection begins with the
  frame *after* the one the detector reported the phrase in: the phrase's
  audio is not part of the utterance, and frames already captured after it
  are replayed to the VAD so the command's first words are not lost. The
  boundary is the detector's own frame, not a guessed duration; a tail of
  the phrase inside that frame's successor is possible.
- **Never pretending.** A microphone that cannot open refuses the session
  (one WARNING notice); one lost mid-session ends it (one notice). A timeout
  without speech ends quietly. Inference runs in `VoiceProcessor`, off the
  UI thread, bounded.
"""

from __future__ import annotations

import itertools
import sys

from mira.core.audio_input import AUDIO_STATUS_EVENT, AudioInputService, AudioLease, AudioStatus
from mira.core.notice_producers import NOTICE_EVENT
from mira.core.voice_processing import VoiceBatch, VoiceJob, VoiceProcessor
from mira.domain.audio import AudioFrame, CapturePurpose, CaptureStatus
from mira.domain.listening import ActivationSource, ListeningActivationMode, ListeningState, ListeningStatus
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import Scheduler
from mira.domain.voice import (
    EnergyVoiceActivityDetector,
    ListeningSession,
    ListeningSessionState,
    SegmentationPolicy,
    SessionEnd,
    SessionEnded,
    SpeechStarted,
    VoiceActivityDetector,
    WakeWordDetector,
)
from mira.messaging.events import EventBus

TOGGLE_EVENT = "listening_toggle_requested"
LISTENING_EVENT = "listening_changed"
UTTERANCE_EVENT = "utterance_ready"

# After a session, the same spoken phrase still echoing must not start another.
WAKE_COOLDOWN_S = 1.5


class ListeningController:
    def __init__(self, event_bus: EventBus, audio: AudioInputService, scheduler: Scheduler, *,
                 vad: VoiceActivityDetector | None = None, detector: WakeWordDetector | None = None,
                 policy: SegmentationPolicy = SegmentationPolicy()):
        self._bus = event_bus
        self._audio = audio
        self._processor = VoiceProcessor(scheduler, detector=detector,
                                         vad=vad if vad is not None else EnergyVoiceActivityDetector(),
                                         on_results=self._on_results)
        self.policy = policy
        self.mode = ListeningActivationMode.KEYBOARD
        self._session: ListeningSession | None = None
        self._lease: AudioLease | None = None
        self._monitor: AudioLease | None = None
        self._status = ListeningStatus(ListeningState.OFF)
        self._ids = itertools.count(1)
        self._reset_vad_next = False
        self._cooldown_until = 0.0
        self._last_frame_s = 0.0
        self._stopped = False
        self.monitoring_problem: str | None = None
        self.last_utterance = None
        self.last_end: SessionEnd | None = None
        self.detections = 0
        audio.subscribe_frames(self._on_frame)
        event_bus.subscribe(TOGGLE_EVENT, self.on_toggle_requested)
        event_bus.subscribe(AUDIO_STATUS_EVENT, self.on_audio_status)

    # --- Reading --------------------------------------------------------------------

    @property
    def status(self) -> ListeningStatus:
        return self._status

    @property
    def listening(self) -> bool:
        return self._status.listening

    @property
    def session_state(self) -> ListeningSessionState:
        return self._session.state if self._session is not None else ListeningSessionState.IDLE

    @property
    def session_elapsed_s(self) -> float:
        return self._session.elapsed_s if self._session is not None else 0.0

    @property
    def monitoring(self) -> bool:
        """The wake word is being listened for (MIRA itself stays idle)."""
        return self._monitor is not None

    @property
    def detector(self) -> WakeWordDetector | None:
        return self._processor.detector

    @property
    def vad(self) -> VoiceActivityDetector:
        return self._processor.vad

    @property
    def processor(self) -> VoiceProcessor:
        return self._processor

    # --- Configuration --------------------------------------------------------------------

    def configure(self, mode: ListeningActivationMode, *, policy: SegmentationPolicy | None = None,
                  detector: WakeWordDetector | None = None, vad: VoiceActivityDetector | None = None,
                  replace_detector: bool = False) -> None:
        """Apply settings: the mode, timing, and (optionally) new engines.

        WAKE_WORD without a detector cannot monitor: it is reported in
        `monitoring_problem` and keyboard activation keeps working.
        """
        engines_change = vad is not None and vad is not self._processor.vad
        if self._session is not None and (engines_change or (policy is not None and policy != self.policy)):
            # A session is measured with one detector and one policy from
            # start to end: new ones apply to the next session.
            self._end(SessionEnded(SessionEnd.CANCELLED), None, detail="listening settings changed")
        if policy is not None:
            self.policy = policy
        if replace_detector or detector is not None:
            self._processor.detector = detector
        if vad is not None:
            self._processor.vad = vad
        self.mode = mode
        self._update_monitoring()

    def _update_monitoring(self) -> None:
        wanted = (not self._stopped and self.mode is ListeningActivationMode.WAKE_WORD
                  and self._processor.detector is not None)
        if wanted and self._monitor is None:
            self._monitor = self._audio.acquire(CapturePurpose.WAKE_MONITORING)
            if self._monitor is None:
                self.monitoring_problem = self._audio.snapshot.reason or "the microphone is unavailable"
                print(f"[listening] wake monitoring off: {self.monitoring_problem}", file=sys.stderr)
            else:
                self.monitoring_problem = None
                print(f"[listening] wake monitoring on ({self._processor.detector.name})")
        elif not wanted and self._monitor is not None:
            self._monitor.release()
            self._monitor = None
            print("[listening] wake monitoring off")
        if self.mode is ListeningActivationMode.WAKE_WORD and self._processor.detector is None:
            self.monitoring_problem = "no wake-word detector is installed"

    # --- Activation ---------------------------------------------------------------------------

    def on_toggle_requested(self, source) -> None:
        self.toggle(source if isinstance(source, ActivationSource) else ActivationSource.KEYBOARD)

    def toggle(self, source: ActivationSource) -> ListeningStatus:
        """Keyboard semantics: start a session, or cancel the running one."""
        if self._stopped:
            return self._status
        if self._session is not None:
            self._end(SessionEnded(SessionEnd.CANCELLED), source)
        else:
            self._start(source)
        return self._status

    def activate(self, source: ActivationSource) -> bool:
        """Start a session unless one runs (a wake word never cancels)."""
        if self._stopped or self._session is not None:
            return False
        return self._start(source)

    def shutdown(self) -> None:
        if self._session is not None:
            self._end(SessionEnded(SessionEnd.SHUTDOWN), None)
        self._stopped = True
        self._processor.shutdown()
        if self._monitor is not None:
            self._monitor.release()
            self._monitor = None

    def _start(self, source: ActivationSource) -> bool:
        lease = self._audio.acquire(CapturePurpose.CONVERSATION)
        if lease is None:
            reason = self._audio.snapshot.reason or "the microphone is unavailable"
            print(f"[listening] not started ({source.value}): {reason}")
            self._warn("Microphone unavailable")
            return False
        self._lease = lease
        self._session = ListeningSession(source, utterance_id=next(self._ids), policy=self.policy)
        self._reset_vad_next = True
        print(f"[listening] on ({source.value}): waiting for speech")
        self._announce(ListeningStatus(ListeningState.LISTENING, source, capturing=True))
        return True

    def _end(self, ended: SessionEnded, source: ActivationSource | None, detail: str | None = None) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        if session.active:
            session.end(ended.reason)
        if self._lease is not None:
            self._lease.release()
            self._lease = None
        self._cooldown_until = self._last_frame_s + WAKE_COOLDOWN_S
        self.last_end = ended.reason
        utterance = ended.utterance
        summary = f"{utterance.duration_s:.2f} s, {utterance.sample_count} samples" if utterance else ""
        print(f"[listening] off: {ended.reason.value}" + (f" ({summary}, {session.source.value})" if summary else ""))
        reason = None if ended.reason is SessionEnd.UTTERANCE_READY else (detail or ended.reason.value)
        self._announce(ListeningStatus(ListeningState.OFF, source or session.source, capturing=False, reason=reason))
        if utterance is not None:
            self.last_utterance = utterance
            self._bus.emit(UTTERANCE_EVENT, utterance)

    # --- Audio ---------------------------------------------------------------------------------

    def _on_frame(self, frame: AudioFrame) -> None:
        self._last_frame_s = frame.timestamp_s
        segment = self._session is not None
        detect = self._monitor is not None and not segment and frame.timestamp_s >= self._cooldown_until
        if not (segment or detect):
            return
        self._processor.submit(VoiceJob(frame, detect=detect, segment=segment, reset_vad=segment and self._reset_vad_next))
        if segment:
            self._reset_vad_next = False

    def _on_results(self, batch: VoiceBatch) -> None:
        results = batch.results
        for index, result in enumerate(results):
            session = self._session
            if session is not None and result.job.segment:
                for event in session.feed(result.job.frame, result.windows):
                    if isinstance(event, SpeechStarted):
                        print(f"[listening] speech started ({event.at_s:.2f} s into the session)")
                        self._announce(ListeningStatus(ListeningState.LISTENING, session.source,
                                                       capturing=True, speech_active=True))
                    elif isinstance(event, SessionEnded):
                        self._end(event, None)
                        break
            elif session is None and result.detections and result.job.detect:
                if result.job.frame.timestamp_s < self._cooldown_until:
                    continue
                self.detections += 1
                print(f"[listening] wake word heard: {result.detections[0].phrase!r}")
                if self.activate(ActivationSource.WAKE_WORD):
                    # The phrase ends in this frame; everything captured after
                    # it — later in this batch or still queued — belongs to
                    # the session: route it to the VAD, in order.
                    later = [r.job.frame for r in results[index + 1:]]
                    if self._processor.resegment(later):
                        self._reset_vad_next = False
                    return

    def on_audio_status(self, status: AudioStatus) -> None:
        if status.status not in (CaptureStatus.FAILED, CaptureStatus.UNAVAILABLE):
            return
        # Every lease is void now.
        self._lease = None
        if self._monitor is not None:
            self._monitor = None
            self.monitoring_problem = status.reason or "capture stopped"
            print(f"[listening] wake monitoring off: {self.monitoring_problem}", file=sys.stderr)
        if self._session is not None:
            self._end(SessionEnded(SessionEnd.MICROPHONE_LOST), None, detail=status.reason)
            self._warn("Microphone disconnected")

    def _warn(self, text: str) -> None:
        self._bus.emit(NOTICE_EVENT, Notice(NoticeCategory.DEVICE, NoticeTone.WARNING, text))

    def _announce(self, status: ListeningStatus) -> None:
        self._status = status
        self._bus.emit(LISTENING_EVENT, status)
