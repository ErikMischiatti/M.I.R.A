"""Conversation listening: activation in, a capture lease and LISTENING out.

`ListeningController` is the one place that decides whether MIRA is
listening. Activation sources ask on the bus (``"listening_toggle_requested"``
with an `ActivationSource`); the controller applies the toggle, holds the
conversation capture lease from `AudioInputService` while listening, and
announces every change as ``"listening_changed"`` (`ListeningStatus`).
`InteractionManager` turns that into the LISTENING activity through the
activity authority, as it does for typing — there is no second listening
state anywhere.

Listening never pretends to work. If the microphone cannot be opened the
toggle is refused, and if capture is lost while listening, listening ends;
either way one WARNING notice says so (``"notice"``), only in answer to that
event, never repeatedly.

Not here: speech detection, transcription, wake words. Those will consume
the same capture or become activation sources.
"""

from __future__ import annotations

from mira.core.audio_input import AUDIO_STATUS_EVENT, AudioInputService, AudioLease, AudioStatus
from mira.core.notice_producers import NOTICE_EVENT
from mira.domain.audio import CapturePurpose, CaptureStatus
from mira.domain.listening import ActivationSource, ListeningState, ListeningStatus, toggled
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.messaging.events import EventBus

TOGGLE_EVENT = "listening_toggle_requested"
LISTENING_EVENT = "listening_changed"


class ListeningController:
    def __init__(self, event_bus: EventBus, audio: AudioInputService):
        self._bus = event_bus
        self._audio = audio
        self._lease: AudioLease | None = None
        self._status = ListeningStatus(ListeningState.OFF)
        self._stopped = False
        event_bus.subscribe(TOGGLE_EVENT, self.on_toggle_requested)
        event_bus.subscribe(AUDIO_STATUS_EVENT, self.on_audio_status)

    @property
    def status(self) -> ListeningStatus:
        return self._status

    @property
    def listening(self) -> bool:
        return self._status.listening

    def on_toggle_requested(self, source) -> None:
        self.toggle(source if isinstance(source, ActivationSource) else ActivationSource.KEYBOARD)

    def toggle(self, source: ActivationSource) -> ListeningStatus:
        if self._stopped:
            return self._status
        if toggled(self._status.state) is ListeningState.LISTENING:
            self._start(source)
        else:
            self._stop(source)
        return self._status

    def shutdown(self) -> None:
        self._stopped = True
        if self._lease is not None:
            self._lease.release()
            self._lease = None

    def _start(self, source: ActivationSource) -> None:
        lease = self._audio.acquire(CapturePurpose.CONVERSATION)
        if lease is None:
            reason = self._audio.snapshot.reason or "the microphone is unavailable"
            print(f"[listening] not started ({source.value}): {reason}")
            self._warn("Microphone unavailable")
            return
        self._lease = lease
        print(f"[listening] on ({source.value})")
        self._announce(ListeningStatus(ListeningState.LISTENING, source, capturing=True))

    def _stop(self, source: ActivationSource | None, reason: str | None = None) -> None:
        if self._lease is not None:
            self._lease.release()
            self._lease = None
        print(f"[listening] off ({source.value if source else reason})")
        self._announce(ListeningStatus(ListeningState.OFF, source, capturing=False, reason=reason))

    def on_audio_status(self, status: AudioStatus) -> None:
        if self.listening and status.status in (CaptureStatus.FAILED, CaptureStatus.UNAVAILABLE):
            self._lease = None  # already void
            self._stop(None, reason=status.reason or "capture stopped")
            self._warn("Microphone disconnected")

    def _warn(self, text: str) -> None:
        self._bus.emit(NOTICE_EVENT, Notice(NoticeCategory.DEVICE, NoticeTone.WARNING, text))

    def _announce(self, status: ListeningStatus) -> None:
        self._status = status
        self._bus.emit(LISTENING_EVENT, status)
