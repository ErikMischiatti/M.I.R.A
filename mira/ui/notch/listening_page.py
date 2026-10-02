"""The settings page for listening and the audio input's live diagnostics.

Two halves:

- **Listening** — how listening is activated (`ListeningSettings`). Only
  keyboard activation exists; wake word is listed as coming later and
  cannot be chosen. The in-window key and the desktop-wide shortcut take
  effect at once through ``apply`` (the runtime re-registers its keys) and
  are written by the window's Save.
- **Audio input** — what the runtime's one `AudioInputService` is doing:
  backend, device, format, status, what it captures for, and the live RMS,
  peak and level. "Test microphone" takes a DIAGNOSTICS lease on that same
  service, so it never opens a second capture and never puts MIRA in
  LISTENING; it ends when unchecked or when the window hides.

The readout refreshes five times a second, only while the page is visible.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
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
from mira.ui.listening_settings import ListeningConfigError, ListeningSession, ListeningSettings

REFRESH_MS = 200

_MODE_LABELS = {
    ListeningActivationMode.KEYBOARD: "Keyboard",
    ListeningActivationMode.WAKE_WORD: "Wake word — coming later",
}


class ListeningPage(QWidget):
    def __init__(self, *, audio=None, session: ListeningSession | None = None,
                 apply: Callable[[ListeningSettings], None] | None = None):
        super().__init__()
        self.audio = audio
        self.session = session
        self._apply = apply
        self._lease = None
        self._settings = session.settings if session is not None else ListeningSettings()

        self.activation = QComboBox()
        for mode, label in _MODE_LABELS.items():
            self.activation.addItem(label, mode.value)
        model = self.activation.model()
        if isinstance(model, QStandardItemModel):
            for row, mode in enumerate(_MODE_LABELS):
                model.item(row).setEnabled(mode in IMPLEMENTED_MODES)
        self.in_window_key = QLineEdit()
        self.in_window_key.setMaxLength(1)
        self.in_window_key.setToolTip("Toggles listening in M.I.R.A.'s windows, never while typing in a text field")
        self.global_shortcut = QKeySequenceEdit()
        self.global_shortcut.setMaximumSequenceLength(1)
        self.global_enabled = QCheckBox("Desktop-wide shortcut")
        self.listening_error = QLabel()
        self.listening_error.setStyleSheet("color: #c0392b;")
        self.listening_error.setWordWrap(True)
        editable = session is not None
        for control in (self.activation, self.in_window_key, self.global_shortcut, self.global_enabled):
            control.setEnabled(editable)

        listening = QGroupBox("Listening")
        form = QFormLayout(listening)
        form.addRow("Activation", self.activation)
        form.addRow("Key in M.I.R.A.'s windows", self.in_window_key)
        form.addRow(self.global_enabled, self.global_shortcut)
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

        layout = QVBoxLayout(self)
        layout.addWidget(listening)
        layout.addWidget(audio_group)
        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.sync()
        self.activation.currentIndexChanged.connect(self._edited)
        self.in_window_key.textEdited.connect(self._edited)
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
        for control in (self.activation, self.in_window_key, self.global_shortcut, self.global_enabled):
            control.blockSignals(True)
        self.activation.setCurrentIndex(self.activation.findData(settings.activation.value))
        self.in_window_key.setText(settings.in_window_key)
        self.global_enabled.setChecked(settings.global_shortcut is not None)
        self.global_shortcut.setKeySequence(QKeySequence(settings.global_shortcut or ""))
        self.global_shortcut.setEnabled(self.session is not None and settings.global_shortcut is not None)
        for control in (self.activation, self.in_window_key, self.global_shortcut, self.global_enabled):
            control.blockSignals(False)
        self.listening_error.clear()
        self.refresh()

    def _from_controls(self) -> ListeningSettings:
        shortcut = self.global_shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        return ListeningSettings(
            activation=ListeningActivationMode(self.activation.currentData()),
            in_window_key=self.in_window_key.text().upper() or self._settings.in_window_key,
            global_shortcut=(shortcut or None) if self.global_enabled.isChecked() else None,
        )

    def _edited(self, *_):
        self.global_shortcut.setEnabled(self.global_enabled.isChecked())
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
