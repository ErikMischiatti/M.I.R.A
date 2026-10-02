"""Microphone capture through Qt Multimedia's `QAudioSource`.

Why this backend: Qt Multimedia ships with PySide6 (``PySide6_Addons``), so
capture adds no dependency, runs in process, and delivers data through the
Qt event loop on the main thread — no capture thread to bridge, no child
process to supervise. On Linux it reaches PipeWire through its PulseAudio
interface, like any desktop application, so the system's own microphone
indicator shows when MIRA captures. (``pw-record`` on a pipe would work too;
it would add a second supervised child next to ``pw-dump``.)

The backend captures from the system's *default* input. It asks for 16 kHz
signed 16-bit mono — plenty for level, waveform and later speech — and, if
the device cannot provide that, takes its preferred format and converts to
16-bit mono itself. `QMediaDevices` reports input changes: when the default
input changes or the captured one disappears, capture moves to the new
default; with no input left it fails, once. Audio is processed in memory
only.

PySide6 6.11 cannot deliver `QAudioSource.stateChanged` to Python (its
`QAudio::State` argument does not convert), so a stopped or stalled source
is found by a 1 Hz watchdog instead: an error state, or no data for
`STALL_S` seconds while capturing, ends capture as lost. A muted microphone
still delivers frames (of silence), so it is never mistaken for a stall.

`QtMultimedia` is imported on first use, so a runtime that never captures
never loads it.
"""

from __future__ import annotations

import sys
import time
from array import array

from mira.domain.audio import AudioFrame, AudioInputInfo, FailureListener, FrameListener

REQUESTED_RATE = 16000
WATCHDOG_MS = 1000
STALL_S = 3.0


def _name(value) -> str:
    # Qt 6 moved these enums from `QAudio` to `QtAudio`; the two classes do
    # not compare equal, their member names do.
    return getattr(value, "name", str(value))


def to_mono_s16(data: bytes, channels: int, sample_format: str) -> bytes:
    """Interleaved PCM of ``sample_format`` ("int16", "int32", "float") to mono s16.

    Channels are averaged. Pure, so it is tested without a device.
    """

    if sample_format == "int16":
        values = array("h")
        values.frombytes(data[: len(data) // 2 * 2])
        scale = 1.0
    elif sample_format == "int32":
        values = array("i")
        values.frombytes(data[: len(data) // 4 * 4])
        scale = 1.0 / 65536.0
    elif sample_format == "float":
        values = array("f")
        values.frombytes(data[: len(data) // 4 * 4])
        scale = 32767.0
    else:
        raise ValueError(f"unsupported sample format {sample_format!r}")
    if channels <= 1 and sample_format == "int16":
        return values.tobytes()
    mono = array("h")
    usable = len(values) - len(values) % max(channels, 1)
    for start in range(0, usable, max(channels, 1)):
        total = sum(values[start:start + channels]) * scale / channels
        mono.append(int(max(-32768, min(32767, round(total)))))
    return mono.tobytes()


class QtAudioCaptureBackend:
    """An `AudioCaptureBackend` for the default input; see the module."""

    name = "Qt Multimedia (QAudioSource)"

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._source = None
        self._io = None
        self._devices = None
        self._device_id: bytes | None = None
        self._on_frame: FrameListener | None = None
        self._on_failure: FailureListener | None = None
        self._format = None
        self._sample_format = "int16"
        self._channels = 1
        self._rate = REQUESTED_RATE
        self._device_name: str | None = None
        self._watchdog = None
        self._last_data_s = 0.0

    @property
    def info(self) -> AudioInputInfo:
        return AudioInputInfo(self.name, self._device_name, self._rate if self._source else None,
                              self._channels if self._source else None)

    def start(self, on_frame: FrameListener, on_failure: FailureListener) -> bool:
        if self._source is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaDevices
        except ImportError as error:
            print(f"[audio] Qt Multimedia unavailable: {error}", file=sys.stderr)
            return False
        self._on_frame, self._on_failure = on_frame, on_failure
        if self._devices is None:
            self._devices = QMediaDevices()
            self._devices.audioInputsChanged.connect(self._on_inputs_changed)
        return self._open(QMediaDevices.defaultAudioInput())

    def stop(self) -> None:
        self._close()
        self._on_frame = self._on_failure = None

    # --- The source -------------------------------------------------------------

    def _open(self, device) -> bool:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource

        if device is None or device.isNull():
            print("[audio] no microphone (no default audio input)", file=sys.stderr)
            return False
        wanted = QAudioFormat()
        wanted.setSampleRate(REQUESTED_RATE)
        wanted.setChannelCount(1)
        wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        fmt = wanted if device.isFormatSupported(wanted) else device.preferredFormat()
        names = {
            QAudioFormat.SampleFormat.Int16: "int16",
            QAudioFormat.SampleFormat.Int32: "int32",
            QAudioFormat.SampleFormat.Float: "float",
        }
        if fmt.sampleFormat() not in names:
            print(f"[audio] {device.description()}: unsupported sample format {fmt.sampleFormat()}", file=sys.stderr)
            return False
        source = QAudioSource(device, fmt)
        io = source.start()
        if io is None or _name(source.error()) != "NoError":
            print(f"[audio] {device.description()} could not be opened ({_name(source.error())})", file=sys.stderr)
            source.deleteLater()
            return False
        self._source, self._io, self._format = source, io, fmt
        self._sample_format, self._channels, self._rate = names[fmt.sampleFormat()], fmt.channelCount(), fmt.sampleRate()
        self._device_id, self._device_name = bytes(device.id()), device.description()
        io.readyRead.connect(self._on_ready)
        self._last_data_s = self._clock()
        if self._watchdog is None:
            from PySide6.QtCore import QTimer

            self._watchdog = QTimer()
            self._watchdog.timeout.connect(self.check)
        self._watchdog.start(WATCHDOG_MS)
        print(f"[audio] capturing {self._device_name} at {self._rate} Hz, {self._channels} ch, {self._sample_format}")
        return True

    def _close(self) -> None:
        source, io, self._source, self._io = self._source, self._io, None, None
        if self._watchdog is not None:
            self._watchdog.stop()
        if source is None:
            return
        try:
            io.readyRead.disconnect(self._on_ready)
        except (RuntimeError, TypeError, AttributeError):
            pass
        source.stop()
        source.deleteLater()

    def _on_ready(self) -> None:
        if self._io is None or self._on_frame is None:
            return
        data = bytes(self._io.readAll())
        if not data:
            return
        self._last_data_s = self._clock()
        pcm = data if (self._sample_format, self._channels) == ("int16", 1) else to_mono_s16(
            data, self._channels, self._sample_format)
        self._on_frame(AudioFrame(pcm, self._rate, self._clock()))

    def check(self) -> None:
        """The watchdog: a source that stopped with an error, or went silent
        (no data at all, not merely quiet data), is lost."""
        if self._source is None:
            return
        error = _name(self._source.error())
        if error != "NoError":
            self._lost(f"capture stopped: {error}")
        elif self._clock() - self._last_data_s > STALL_S:
            self._lost(f"no audio data for {STALL_S:g} s")

    def _on_inputs_changed(self) -> None:
        from PySide6.QtMultimedia import QMediaDevices

        if self._source is None:
            return
        default = QMediaDevices.defaultAudioInput()
        present = {bytes(device.id()) for device in QMediaDevices.audioInputs()}
        if not default.isNull() and bytes(default.id()) == self._device_id and self._device_id in present:
            return  # still capturing the default, which is still there
        previous = self._device_name
        self._close()
        if not default.isNull() and self._open(default):
            print(f"[audio] input changed: {previous} → {self._device_name}")
            return
        self._lost("the microphone was disconnected")

    def _lost(self, reason: str) -> None:
        self._close()
        listener, self._on_failure = self._on_failure, None
        self._on_frame = None
        print(f"[audio] {reason}", file=sys.stderr)
        if listener is not None:
            listener(reason)
