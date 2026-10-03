"""The settings page for listening and the audio input's live diagnostics.

Two halves:

- **Listening** — how listening is activated (`ListeningSettings`):
  keyboard, or wake word where its engine and model are installed (else it
  is listed as not installed and cannot be chosen); the wake phrase and its
  sensitivity; when speech ends and how long a session waits for it. Changes
  apply at once through ``apply`` (the runtime re-registers its keys and
  reconfigures listening) and are written by the window's Save.
- **Audio input** — what the runtime's one `AudioInputService` is doing:
  backend, device, format, status, what it captures for, and the live RMS,
  peak and level. "Test microphone" takes a DIAGNOSTICS lease on that same
  service, so it never opens a second capture and never puts MIRA in
  LISTENING; it ends when unchecked or when the window hides.
- **Voice** — the listening controller's live state: activation mode, wake
  engine and whether it is monitoring, detections, the speech detector, the
  session's state and duration, speech activity, and the last utterance's
  length and source. Metadata only: no audio is shown or kept.

The readout refreshes five times a second, only while the page is visible.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from mira.domain.audio import CapturePurpose
from mira.domain.listening import IMPLEMENTED_MODES, ListeningActivationMode
from mira.domain.voice import WakeWordConfig
from mira.ui.listening_settings import ListeningConfigError, ListeningSession, ListeningSettings

REFRESH_MS = 200

_MODE_LABELS = {
    ListeningActivationMode.KEYBOARD: "Keyboard",
    ListeningActivationMode.WAKE_WORD: "Wake word",
}
_UNAVAILABLE = " — not installed"


class ListeningPage(QWidget):
    def __init__(self, *, audio=None, session: ListeningSession | None = None,
                 apply: Callable[[ListeningSettings], None] | None = None, wake_available: bool = False,
                 listening=None):
        super().__init__()
        self.audio = audio
        self.listening = listening
        self.session = session
        self._apply = apply
        self._lease = None
        self._settings = session.settings if session is not None else ListeningSettings()

        self.activation = QComboBox()
        self.wake_available = wake_available
        for mode, label in _MODE_LABELS.items():
            usable = mode is not ListeningActivationMode.WAKE_WORD or wake_available
            self.activation.addItem(label + ("" if usable else _UNAVAILABLE), mode.value)
        model = self.activation.model()
        if isinstance(model, QStandardItemModel):
            for row, mode in enumerate(_MODE_LABELS):
                model.item(row).setEnabled(mode in IMPLEMENTED_MODES and (
                    mode is not ListeningActivationMode.WAKE_WORD or wake_available))
        self.in_window_key = QLineEdit()
        self.in_window_key.setMaxLength(1)
        self.in_window_key.setToolTip("Toggles listening in M.I.R.A.'s windows, never while typing in a text field")
        self.global_shortcut = QKeySequenceEdit()
        self.global_shortcut.setMaximumSequenceLength(1)
        self.global_enabled = QCheckBox("Desktop-wide shortcut")
        self.listening_error = QLabel()
        self.listening_error.setStyleSheet("color: #c0392b;")
        self.listening_error.setWordWrap(True)
        self.wake_phrase = QLineEdit()
        self.wake_phrase.setMaxLength(32)
        self.wake_phrase.setToolTip("Any name or short phrase; letters only. Detected locally, on this computer.")
        self.wake_sensitivity = QDoubleSpinBox()
        self.wake_sensitivity.setRange(0.0, 1.0)
        self.wake_sensitivity.setSingleStep(0.05)
        self.wake_sensitivity.setDecimals(2)
        self.wake_sensitivity.setToolTip("Higher wakes more readily, and falsely more often")
        self.speech_end = QDoubleSpinBox()
        self.speech_end.setRange(0.4, 2.0)
        self.speech_end.setSingleStep(0.1)
        self.speech_end.setSuffix(" s")
        self.speech_end.setToolTip("Silence that ends what you say; pauses between words are shorter")
        self.wait_timeout = QDoubleSpinBox()
        self.wait_timeout.setRange(3.0, 20.0)
        self.wait_timeout.setSingleStep(1.0)
        self.wait_timeout.setSuffix(" s")
        self.wait_timeout.setToolTip("How long MIRA waits for you to start speaking")
        for box in (self.wake_sensitivity, self.speech_end, self.wait_timeout):
            box.setKeyboardTracking(False)
        editable = session is not None
        for control in (self.activation, self.in_window_key, self.global_shortcut, self.global_enabled,
                        self.wake_phrase, self.wake_sensitivity, self.speech_end, self.wait_timeout):
            control.setEnabled(editable)

        listening = QGroupBox("Listening")
        form = QFormLayout(listening)
        form.addRow("Activation", self.activation)
        form.addRow("Key in M.I.R.A.'s windows", self.in_window_key)
        form.addRow(self.global_enabled, self.global_shortcut)
        form.addRow("Wake phrase", self.wake_phrase)
        form.addRow("Wake sensitivity", self.wake_sensitivity)
        form.addRow("Speech ends after", self.speech_end)
        form.addRow("Wait for speech", self.wait_timeout)
        note = QLabel("" if editable else "Listening settings belong to the running M.I.R.A.; open them from its notch.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(self.listening_error)

        self.backend_label, self.device_label, self.format_label = QLabel(), QLabel(), QLabel()
        self.status_label, self.purposes_label, self.waveform_label = QLabel(), QLabel(), QLabel()
        self.rms_label, self.peak_label = QLabel(), QLabel()
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.test_microphone = QCheckBox("Test microphone (captures while checked; MIRA does not listen)")
        self.test_microphone.setEnabled(audio is not None)
        audio_group = QGroupBox("Audio input")
        audio_form = QFormLayout(audio_group)
        for label, widget in (
            ("Capture backend", self.backend_label), ("Input device", self.device_label),
            ("Format", self.format_label), ("Status", self.status_label),
            ("Capturing for", self.purposes_label), ("RMS", self.rms_label), ("Peak", self.peak_label),
            ("Level", self.level_bar), ("Waveform", self.waveform_label),
        ):
            audio_form.addRow(label, widget)
        audio_form.addRow(self.test_microphone)

        self.voice_labels = {name: QLabel() for name in (
            "Activation", "Wake engine", "Wake monitoring", "Wake detections", "Speech detection",
            "Session", "Speech", "Last utterance", "Last session ended", "Dropped frames")}
        voice_group = QGroupBox("Voice (local; no audio is kept)")
        voice_form = QFormLayout(voice_group)
        for name, label in self.voice_labels.items():
            label.setWordWrap(True)
            voice_form.addRow(name, label)

        layout = QVBoxLayout(self)
        layout.addWidget(listening)
        layout.addWidget(audio_group)
        layout.addWidget(voice_group)
        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.sync()
        self.activation.currentIndexChanged.connect(self._edited)
        self.in_window_key.textEdited.connect(self._edited)
        self.wake_phrase.editingFinished.connect(self._edited)
        for box in (self.wake_sensitivity, self.speech_end, self.wait_timeout):
            box.valueChanged.connect(self._edited)
        self.global_shortcut.keySequenceChanged.connect(self._edited)
        self.global_enabled.toggled.connect(self._edited)
        self.test_microphone.toggled.connect(self.set_testing)

    # --- Listening settings ---------------------------------------------------------

    @property
    def runtime_settings(self) -> ListeningSettings:
        return self._settings

    @property
    def has_unsaved_changes(self) -> bool:
        return self.session is not None and self._settings != self.session.settings

    def sync(self):
        """Show the settings in use, without applying anything."""
        settings = self._settings
        controls = (self.activation, self.in_window_key, self.global_shortcut, self.global_enabled,
                    self.wake_phrase, self.wake_sensitivity, self.speech_end, self.wait_timeout)
        for control in controls:
            control.blockSignals(True)
        self.activation.setCurrentIndex(self.activation.findData(settings.activation.value))
        self.wake_phrase.setText(settings.wake_word.phrase)
        self.wake_sensitivity.setValue(settings.wake_word.sensitivity)
        self.speech_end.setValue(settings.speech_end_s)
        self.wait_timeout.setValue(settings.wait_timeout_s)
        self.in_window_key.setText(settings.in_window_key)
        self.global_enabled.setChecked(settings.global_shortcut is not None)
        self.global_shortcut.setKeySequence(QKeySequence(settings.global_shortcut or ""))
        self.global_shortcut.setEnabled(self.session is not None and settings.global_shortcut is not None)
        self._enable_wake_controls()
        for control in controls:
            control.blockSignals(False)
        self.listening_error.clear()
        self.refresh()

    def _enable_wake_controls(self):
        wake = ListeningActivationMode(self.activation.currentData()) is ListeningActivationMode.WAKE_WORD
        for control in (self.wake_phrase, self.wake_sensitivity):
            control.setEnabled(self.session is not None and self.wake_available and wake)

    def _from_controls(self) -> ListeningSettings:
        shortcut = self.global_shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        return ListeningSettings(
            activation=ListeningActivationMode(self.activation.currentData()),
            in_window_key=self.in_window_key.text().upper() or self._settings.in_window_key,
            global_shortcut=(shortcut or None) if self.global_enabled.isChecked() else None,
            wake_word=WakeWordConfig(self.wake_phrase.text() or self._settings.wake_word.phrase,
                                     round(self.wake_sensitivity.value(), 2)),
            speech_end_s=round(self.speech_end.value(), 2),
            wait_timeout_s=round(self.wait_timeout.value(), 2),
        )

    def _edited(self, *_):
        self.global_shortcut.setEnabled(self.global_enabled.isChecked())
        self._enable_wake_controls()
        try:
            settings = self._from_controls()
        except (ListeningConfigError, ValueError) as error:
            self.listening_error.setText(f"Not applied: {error}")
            return
        self.listening_error.clear()
        if settings != self._settings:
            self._settings = settings
            if self._apply is not None:
                self._apply(settings)

    def set_settings(self, settings: ListeningSettings):
        self._settings = settings
        if self._apply is not None:
            self._apply(settings)
        self.sync()

    def save(self) -> str | None:
        if self.session is None:
            return None
        return str(self.session.save(self._settings))

    def reload(self):
        if self.session is not None:
            self.set_settings(self.session.reload())

    def reset(self):
        self.set_settings(ListeningSettings())

    # --- Audio diagnostics ------------------------------------------------------------

    def set_testing(self, testing: bool):
        if self.audio is None:
            return
        if testing and self._lease is None:
            self._lease = self.audio.acquire(CapturePurpose.DIAGNOSTICS)
            if self._lease is None:
                self.test_microphone.blockSignals(True)
                self.test_microphone.setChecked(False)
                self.test_microphone.blockSignals(False)
        elif not testing and self._lease is not None:
            self._lease.release()
            self._lease = None
        self.refresh()

    def refresh(self):
        audio = self.audio
        if audio is None:
            for label in (self.backend_label, self.device_label, self.format_label, self.status_label,
                          self.purposes_label, self.rms_label, self.peak_label, self.waveform_label):
                label.setText("-")
            self.level_bar.setValue(0)
            self._refresh_voice()
            return
        snapshot = audio.snapshot
        info = snapshot.info
        self.backend_label.setText(info.backend)
        self.device_label.setText(info.device or "system default")
        self.format_label.setText(
            f"{info.sample_rate} Hz, {info.channels} ch" if info.sample_rate else "-"
        )
        self.status_label.setText(snapshot.status.value + (f" — {snapshot.reason}" if snapshot.reason else ""))
        purposes = audio.lease_purposes
        self.purposes_label.setText(", ".join(p.value for p in purposes) if purposes else "nobody")
        raw = audio.raw_level
        self.rms_label.setText(f"{raw.rms:.4f}" if raw else "-")
        self.peak_label.setText(f"{raw.peak:.4f}" if raw else "-")
        self.level_bar.setValue(round(audio.level * 100))
        waveform = audio.waveform
        self.waveform_label.setText(f"{len(waveform.buckets)} buckets (real samples)" if waveform else "none")
        self._refresh_voice()

    def _refresh_voice(self):
        labels, listening = self.voice_labels, self.listening
        if listening is None:
            for label in labels.values():
                label.setText("-")
            return
        detector = listening.detector
        labels["Activation"].setText(listening.mode.value)
        labels["Wake engine"].setText(detector.name if detector is not None else "none")
        labels["Wake monitoring"].setText(
            "on — MIRA stays idle until it hears the phrase" if listening.monitoring
            else "off" + (f" — {listening.monitoring_problem}" if listening.monitoring_problem else ""))
        labels["Wake detections"].setText(str(listening.detections))
        labels["Speech detection"].setText(listening.vad.name)
        state = listening.session_state
        labels["Session"].setText(state.value + (f", {listening.session_elapsed_s:.1f} s" if listening.listening else ""))
        labels["Speech"].setText("active" if listening.status.speech_active else "-")
        last = listening.last_utterance
        labels["Last utterance"].setText(
            f"#{last.id}: {last.duration_s:.2f} s ({last.activation.value})" if last is not None else "none yet")
        labels["Last session ended"].setText(listening.last_end.value if listening.last_end else "-")
        labels["Dropped frames"].setText(str(listening.processor.dropped))

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def sync_test_checkbox(self):
        self.test_microphone.blockSignals(True)
        self.test_microphone.setChecked(self._lease is not None)
        self.test_microphone.blockSignals(False)

    def hideEvent(self, event):
        self._timer.stop()
        self.set_testing(False)
        self.sync_test_checkbox()
        super().hideEvent(event)

    def shutdown(self):
        self._timer.stop()
        self.set_testing(False)
