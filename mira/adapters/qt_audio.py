"""Qt Multimedia `AudioOutput`: plays `PcmClip` values through `QAudioSink`.

Same thread rules as `QtScheduler` (`mira/adapters/qt_scheduler.py`): every Qt
object here is created, driven and destroyed on the thread that built the
output — the Qt main thread in production — and every entry point asserts it.
`QAudioSink` in push-from-`QIODevice` mode pulls the buffer from that thread's
event loop, so `play` returns as soon as playback is started and never blocks.

Nothing is touched at construction. `QtMultimedia` is imported and the default
device looked up on first use, so building the application graph opens no
device, and a machine with no sound card — CI, a headless board, a container —
simply reports ``available == False``. That is a status, never an exception.

One sink at a time: `play` stops and releases the previous clip before
starting the next, so even a caller that ignores timing cannot make clips
overlap. The PCM is baked at the voice's volume; the sink plays at unity gain.
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QThread

from mira.domain.vocalization import AudioOutputError, PcmClip


class QtAudioOutput:
    """`AudioOutput` on the Qt main thread. Construct it on that thread."""

    def __init__(self) -> None:
        self._home_thread = QThread.currentThread()
        self._probed = False
        self._device = None
        self._unavailable_reason = ""
        self._owner: QObject | None = None
        self._sink = None
        self._buffer: QBuffer | None = None
        self._closed = False

    # --- AudioOutput ----------------------------------------------------------

    @property
    def available(self) -> bool:
        self._require_home_thread("available")
        if self._closed:
            return False
        self._probe()
        return self._device is not None

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def play(self, clip: PcmClip) -> None:
        self._require_home_thread("play")
        if self._closed:
            raise AudioOutputError("output is closed")
        self._probe()
        if self._device is None:
            raise AudioOutputError(self._unavailable_reason or "no audio output device")

        # `QAudio` is imported for its side effect: until it is, PySide cannot
        # convert the `QAudio::State` argument of `stateChanged`, and the slot
        # fails on every delivery with a TypeError.
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QtAudio  # noqa: F401

        self._release_current()

        audio_format = QAudioFormat()
        audio_format.setSampleRate(clip.sample_rate)
        audio_format.setChannelCount(clip.CHANNELS)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not self._device.isFormatSupported(audio_format):
            raise AudioOutputError(
                f"device {self._device.description()!r} does not support "
                f"{clip.sample_rate} Hz mono int16"
            )

        # Parented, so C++ owns both and deleteLater decides, on this thread,
        # when their destructors run.
        buffer = QBuffer(self._owner)
        buffer.setData(QByteArray(clip.samples))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        sink = QAudioSink(self._device, audio_format, self._owner)
        sink.setVolume(1.0)
        # The connection refers back to this output, so `_release_current`
        # always disconnects it, or the sink and this object would pin each other.
        sink.stateChanged.connect(self._on_state_changed)
        self._buffer, self._sink = buffer, sink
        sink.start(buffer)
        # `QtAudio`, not the deprecated `QAudio`: their enums never compare equal.
        if sink.error() != QtAudio.Error.NoError:
            error = sink.error()
            self._release_current()
            raise AudioOutputError(f"audio sink failed to start: {error}")

    def close(self) -> None:
        if self._closed:
            return
        self._require_home_thread("close")
        self._closed = True
        self._release_current()
        if self._owner is not None:
            self._owner.deleteLater()
            self._owner = None

    # --- internals ------------------------------------------------------------

    def _require_home_thread(self, operation: str) -> None:
        if QThread.currentThread() is not self._home_thread:
            raise RuntimeError(
                f"QtAudioOutput.{operation} must be called from the thread that constructed it"
            )

    def _probe(self) -> None:
        if self._probed:
            return
        self._probed = True
        try:
            from PySide6.QtMultimedia import QMediaDevices

            device = QMediaDevices.defaultAudioOutput()
        except Exception as error:  # noqa: BLE001 - missing module or backend
            self._unavailable_reason = f"Qt Multimedia unavailable: {error!r}"
            print(f"[Voice] {self._unavailable_reason}")
            return
        if device.isNull():
            self._unavailable_reason = "no audio output device"
            print(f"[Voice] {self._unavailable_reason}")
            return
        self._device = device
        self._owner = QObject()
        print(f"[Voice] audio output: {device.description()}")

    def _on_state_changed(self, state) -> None:
        # Idle means the buffer ran dry: the clip has finished. Compared by name
        # because Qt 6 delivers `QtAudio.State`, which is not `QAudio.State`.
        if getattr(state, "name", "") == "IdleState":
            self._release_current()

    def _release_current(self) -> None:
        sink, buffer = self._sink, self._buffer
        self._sink, self._buffer = None, None
        if sink is not None:
            sink.stateChanged.disconnect(self._on_state_changed)
            sink.stop()
            sink.deleteLater()
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()
