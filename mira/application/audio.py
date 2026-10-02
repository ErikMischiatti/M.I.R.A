"""Which audio capture the desktop uses, apart from the application graph.

Small on purpose: `MIRA-TUNE`, a development tool outside the runtime, needs
an audio service for its explicit microphone test without loading the whole
application graph (`composition`), which the runtime builds once.
"""

from __future__ import annotations

from mira.core.audio_input import AudioInputService
from mira.domain.audio import AudioCaptureBackend
from mira.messaging.events import EventBus


def desktop_audio_backend() -> AudioCaptureBackend:
    """The desktop's capture backend; opens nothing until capture starts."""

    from mira.adapters.qt_audio import QtAudioCaptureBackend

    return QtAudioCaptureBackend()


def standalone_audio_input() -> AudioInputService:
    """An audio service of its own, for a tool outside the runtime.

    Only `MIRA-TUNE` uses it, for its microphone test; the runtime's one
    service is `Application.audio`.
    """

    return AudioInputService(EventBus(), desktop_audio_backend())
