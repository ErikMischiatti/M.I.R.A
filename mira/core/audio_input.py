"""The runtime's one audio input: who may capture, and what it measured.

`AudioInputService` owns the capture backend for the whole runtime; surfaces
and listening policy are its clients, never owners. Capture is driven by
*leases*: it runs while at least one is held and stops when the last is
released. Conversation listening takes one; a future wake-word detector
would take its own, so "the microphone is open" never has to mean "MIRA is
listening".

Telemetry is pulled, not pushed: each frame updates `level` (smoothed, on
the presentation scale), `raw_level` and `waveform`, and a renderer reads
them on its own clock. Frames never travel on the EventBus; only status
changes do (``"audio_status_changed"``), which are rare. When capture stops
the telemetry is cleared, so nothing stale is ever drawn.

Frames arrive on the thread that started capture (the Qt main thread for
the desktop backend) and are processed at once; there is no queue to fall
behind.
"""

from __future__ import annotations

from dataclasses import dataclass

from mira.domain.audio import (
    AudioCaptureBackend,
    AudioFrame,
    AudioInputInfo,
    AudioInputLevel,
    AudioWaveform,
    CapturePurpose,
    CaptureStatus,
    LevelEnvelope,
    WaveformReducer,
    measure,
)
from mira.messaging.events import EventBus

AUDIO_STATUS_EVENT = "audio_status_changed"


@dataclass(frozen=True, slots=True)
class AudioStatus:
    status: CaptureStatus
    info: AudioInputInfo
    reason: str | None = None


class AudioLease:
    """A claim on capture; `release` once, idempotent."""

    def __init__(self, service: AudioInputService, purpose: CapturePurpose):
        self._service = service
        self.purpose = purpose
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._service._release(self)


class AudioInputService:
    def __init__(self, event_bus: EventBus, backend: AudioCaptureBackend | None = None):
        self._bus = event_bus
        self._backend = backend
        self._leases: list[AudioLease] = []
        self._status = CaptureStatus.IDLE if backend is not None else CaptureStatus.UNAVAILABLE
        self._reason: str | None = None if backend is not None else "no audio capture backend"
        self._envelope = LevelEnvelope()
        self._reducer = WaveformReducer()
        self._raw: AudioInputLevel | None = None
        self._stopped = False
        self.frames_received = 0

    # --- Reading ------------------------------------------------------------------

    @property
    def status(self) -> CaptureStatus:
        return self._status

    @property
    def snapshot(self) -> AudioStatus:
        info = self._backend.info if self._backend is not None else AudioInputInfo("none")
        return AudioStatus(self._status, info, self._reason)

    @property
    def capturing(self) -> bool:
        return self._status is CaptureStatus.CAPTURING

    @property
    def level(self) -> float:
        """The smoothed presentation level, 0 when not capturing."""
        return self._envelope.level if self.capturing else 0.0

    @property
    def raw_level(self) -> AudioInputLevel | None:
        return self._raw if self.capturing else None

    @property
    def waveform(self) -> AudioWaveform | None:
        """Recent real samples to draw; None unless frames have arrived."""
        return self._reducer.waveform() if self.capturing else None

    @property
    def lease_purposes(self) -> list[CapturePurpose]:
        return [lease.purpose for lease in self._leases]

    def prepare(self) -> None:
        """Let the backend do its slow, one-off setup now; captures nothing.

        Backends without such a step (`prepare` absent) need nothing.
        """
        prepare = getattr(self._backend, "prepare", None)
        if prepare is not None and not self._stopped:
            prepare()

    # --- Leases -------------------------------------------------------------------

    def acquire(self, purpose: CapturePurpose) -> AudioLease | None:
        """A lease that keeps capture running; None if capture cannot run.

        The first lease starts the backend. If it cannot start, no lease is
        given and the status says why.
        """
        if self._stopped:
            return None
        lease = AudioLease(self, purpose)
        if not self._leases and not self._start():
            return None
        self._leases.append(lease)
        return lease

    def _release(self, lease: AudioLease) -> None:
        if lease in self._leases:
            self._leases.remove(lease)
        if not self._leases and self._status is CaptureStatus.CAPTURING:
            self._stop(CaptureStatus.IDLE)

    def shutdown(self) -> None:
        """Stop capturing for good and release every resource; idempotent."""
        for lease in list(self._leases):
            lease.released = True
        self._leases.clear()
        if self._backend is not None:
            self._backend.stop()
        self._clear()
        self._stopped = True

    # --- Capture ------------------------------------------------------------------

    def _start(self) -> bool:
        if self._backend is None:
            self._set_status(CaptureStatus.UNAVAILABLE, "no audio capture backend")
            return False
        self._clear()
        if not self._backend.start(self._on_frame, self._on_failure):
            self._set_status(CaptureStatus.FAILED, "the microphone could not be opened")
            return False
        self._set_status(CaptureStatus.CAPTURING)
        return True

    def _stop(self, status: CaptureStatus, reason: str | None = None) -> None:
        if self._backend is not None:
            self._backend.stop()
        self._clear()
        self._set_status(status, reason)

    def _clear(self) -> None:
        self._envelope.reset()
        self._reducer.reset()
        self._raw = None

    def _on_frame(self, frame: AudioFrame) -> None:
        if self._status is not CaptureStatus.CAPTURING:
            return
        self.frames_received += 1
        self._raw = measure(frame)
        self._envelope.update(self._raw)
        self._reducer.add(frame)

    def _on_failure(self, reason: str) -> None:
        """Capture ended by itself: every lease is void; the holders are told."""
        if self._status is not CaptureStatus.CAPTURING:
            return
        for lease in list(self._leases):
            lease.released = True
        self._leases.clear()
        self._stop(CaptureStatus.FAILED, reason)

    def _set_status(self, status: CaptureStatus, reason: str | None = None) -> None:
        if (status, reason) == (self._status, self._reason):
            return
        self._status, self._reason = status, reason
        info = self.snapshot.info
        detail = f" ({reason})" if reason else ""
        device = f" from {info.device}" if info.device and status is CaptureStatus.CAPTURING else ""
        print(f"[audio] {status.value}{device}{detail}")
        self._bus.emit(AUDIO_STATUS_EVENT, self.snapshot)
