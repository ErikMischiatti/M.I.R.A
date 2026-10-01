"""Developer tuning window for a live `NotchWindow`.

Developer tooling only. The window owns no notch state and draws no notch: its
controls read the notch's current settings, build one immutable
`NotchVisualConfig` from all of them and hand it to
`NotchWindow.apply_visual_config`, which validates the eyes' whole motion
envelope before applying it through the production pipeline. A combination
that would clip the eyes is shown as invalid and not applied; the notch keeps
its last valid configuration. Presence changes go through the production
presence API, expressions through the `FaceController` and the monitor
choice through `NotchWindow.set_monitor_preference`.

Given a `ConfigSession`, the window also persists: Save writes the runtime
settings, Reload reads the file again, and Reset defaults restores the
production defaults at run time only (Save then persists them). Without a
session those buttons are disabled.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mira.domain.embodiment import ActivityState, EmbodimentIntent, ExpressionKey
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.config_store import ConfigSession, ConfigSource, NotchSettings
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize
from mira.ui.notch.presentation import NotchPresenter
from mira.ui.notch.screen_selection import MonitorPreference, SelectionMode
from mira.ui.notch.screens import monitor_identity
from mira.ui.notch.visual_config import (
    MAX_TRANSITION_DURATION_MS,
    InvalidVisualConfig,
    NotchVisualConfig,
)


def number(minimum: float, maximum: float, *, decimals: int = 1, step: float = 1.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setKeyboardTracking(False)
    return box


SOURCE_LABELS = {
    ConfigSource.SAVED: "Saved",
    ConfigSource.DEFAULTS: "Defaults",
    ConfigSource.INVALID_FALLBACK: "Invalid fallback",
}


class NotchDebugWindow(QWidget):
    """Live controls for one notch, the controller its eyes show and its file."""

    closed = Signal()

    def __init__(
        self,
        notch: NotchWindow,
        controller: FaceController | None = None,
        session: ConfigSession | None = None,
        presenter: NotchPresenter | None = None,
    ):
        super().__init__()
        self.setWindowTitle("M.I.R.A. notch tuning")
        self.notch = notch
        self.controller = controller
        self.session = session
        self.presenter = presenter
        self._visual_valid = True
        # The monitor preference behind each entry of `monitor`, by index.
        self._monitor_choices: list[MonitorPreference] = []

        # The controls scroll; the persistence bar and messages stay in view.
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self._status_group())
        layout.addWidget(self._presence_group())
        layout.addWidget(self._display_group())

        self.compact_width = number(1, 2000)
        self.compact_height = number(1, 400)
        self.compact_radius = number(0, 200)
        layout.addWidget(self._group("Compact geometry", (
            ("Width (px)", self.compact_width),
            ("Height (px)", self.compact_height),
            ("Corner radius (px)", self.compact_radius),
        )))

        self.expanded_width = number(1, 2000)
        self.expanded_height = number(1, 400)
        self.expanded_radius = number(0, 200)
        layout.addWidget(self._group("Expanded geometry", (
            ("Width (px)", self.expanded_width),
            ("Height (px)", self.expanded_height),
            ("Corner radius (px)", self.expanded_radius),
        )))

        self.offset_x = number(-4000, 4000)
        self.top_margin = number(0, 400)
        layout.addWidget(self._group("Placement (from the selected screen's top centre)", (
            ("Horizontal offset (px)", self.offset_x),
            ("Top margin (px)", self.top_margin),
        )))

        self.duration = QSpinBox()
        self.duration.setRange(0, MAX_TRANSITION_DURATION_MS)
        self.duration.setSingleStep(10)
        self.duration.setKeyboardTracking(False)
        layout.addWidget(self._group("Animation", (("Transition duration (ms)", self.duration),)))

        self.eye_height_share = number(0.01, 3.0, decimals=3, step=0.005)
        self.eye_top_offset = number(-200, 200, step=0.1)
        self.eye_center_offset = number(-1000, 1000, step=0.5)
        self.eye_spacing = number(0, 3000, step=5)
        self.eye_motion_gain = number(0.05, 1.0, decimals=2, step=0.05)
        self.eye_layout_label = QLabel()
        layout.addWidget(self._group("Eyes", (
            ("Scale: base eye / compact height", self.eye_height_share),
            ("Top offset (px)", self.eye_top_offset),
            ("Pair centre offset X (px)", self.eye_center_offset),
            ("Spacing (face units)", self.eye_spacing),
            ("Motion gain (offset / size scale)", self.eye_motion_gain),
            ("Derived layout", self.eye_layout_label),
        )))

        self.expression = QComboBox()
        # Qt keeps item data as plain strings, so store the key's value and
        # convert back on selection.
        for key in ExpressionKey:
            self.expression.addItem(key.name, key.value)
        self.expression.setEnabled(controller is not None)
        layout.addWidget(self._group("Expression (not persisted)", (("Expression", self.expression),)))
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.scroll_area = scroll

        self.save_button = QPushButton("Save")
        self.reload_button = QPushButton("Reload")
        self.reset_button = QPushButton("Reset defaults")
        for button in (self.save_button, self.reload_button):
            button.setEnabled(session is not None)
        buttons = QHBoxLayout()
        for button in (self.save_button, self.reload_button, self.reset_button):
            buttons.addWidget(button)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c0392b;")
        self.error_label.setWordWrap(True)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addLayout(buttons)
        outer.addWidget(self.error_label)
        outer.addWidget(self.message_label)

        if session is not None and session.diagnostic:
            self.message_label.setText(f"Saved configuration ignored: {session.diagnostic}")

        self.sync_from_notch()
        self._fit_to_screen()

        for box in (
            self.compact_width, self.compact_height, self.compact_radius,
            self.expanded_width, self.expanded_height, self.expanded_radius,
            self.offset_x, self.top_margin,
            self.eye_height_share, self.eye_top_offset, self.eye_center_offset,
            self.eye_spacing, self.eye_motion_gain, self.duration,
        ):
            box.valueChanged.connect(self._apply_visual_config)
        self.monitor.currentIndexChanged.connect(self._apply_monitor)
        self.expression.currentIndexChanged.connect(self._apply_expression)
        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.reload)
        self.reset_button.clicked.connect(self.reset_defaults)
        notch.screen_selection_changed.connect(self._on_screen_selection_changed)

    # --- Construction helpers ------------------------------------------------

    def _group(self, title: str, rows) -> QGroupBox:
        group = QGroupBox(title)
        form = QFormLayout(group)
        for label, widget in rows:
            form.addRow(label, widget)
        return group

    def _status_group(self) -> QGroupBox:
        self.loaded_label = QLabel()
        self.monitor_mode_label = QLabel()
        self.selected_monitor_label = QLabel()
        self.monitor_count_label = QLabel()
        self.visual_label = QLabel()
        self.unsaved_label = QLabel()
        self.window_label = QLabel()
        self.config_path_label = QLabel()
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return self._group("Status", (
            ("Loaded config:", self.loaded_label),
            ("Monitor mode:", self.monitor_mode_label),
            ("Selected monitor:", self.selected_monitor_label),
            ("Available monitors:", self.monitor_count_label),
            ("Visual config:", self.visual_label),
            ("Unsaved changes:", self.unsaved_label),
            ("Window:", self.window_label),
            ("File:", self.config_path_label),
        ))

    def _presence_group(self) -> QGroupBox:
        group = QGroupBox("Presence")
        row = QHBoxLayout(group)
        self.hidden_button = QPushButton("Hidden")
        self.compact_button = QPushButton("Compact")
        self.expanded_button = QPushButton("Expanded")
        self.presence_label = QLabel()
        for button in (self.hidden_button, self.compact_button, self.expanded_button):
            row.addWidget(button)
        row.addWidget(self.presence_label)
        self.hidden_button.clicked.connect(self.show_hidden)
        self.compact_button.clicked.connect(self.show_compact)
        self.expanded_button.clicked.connect(self.show_expanded)
        return group

    def _display_group(self) -> QGroupBox:
        self.monitor = QComboBox()
        self.monitors_label = QLabel()
        self.monitors_label.setTextFormat(Qt.PlainText)
        self.monitors_label.setStyleSheet("font-family: monospace;")
        return self._group("Display", (
            ("Monitor", self.monitor),
            ("Connected", self.monitors_label),
        ))

    def _fit_to_screen(self):
        # Tall content scrolls: never open taller than most of the screen.
        screen = self.screen() or QGuiApplication.primaryScreen()
        width = max(self.sizeHint().width(), 440)
        if screen is None:
            self.resize(width, 700)
            return
        available = screen.availableGeometry()
        self.resize(min(width, available.width()), min(900, int(available.height() * 0.85)))

    # --- Presence, through the presenter or the production API ---------------

    # With a presenter, presence is its decision: Expanded pins the notch open
    # (one expansion reason among others) and Compact releases the pin, so
    # hover and notices keep working while tuning. Without one, the buttons
    # use the notch's own presence API.

    def show_hidden(self):
        if self.presenter is not None:
            self.presenter.set_pinned(False)
            self.presenter.hide()
        else:
            self.notch.hide_notch()
        self._refresh_status()

    def show_compact(self):
        if self.presenter is not None:
            self.presenter.set_pinned(False)
            self.presenter.show()
        elif self.notch.presence is NotchPresence.HIDDEN:
            self.notch.show_notch()
        else:
            self.notch.collapse_notch()
        self._refresh_status()

    def show_expanded(self):
        if self.presenter is not None:
            self.presenter.show()
            self.presenter.set_pinned(True)
        else:
            self.notch.expand_notch()
        self._refresh_status()

    # --- Controls ← notch ---------------------------------------------------

    @property
    def runtime_settings(self) -> NotchSettings:
        """What the notch uses now: the settings Save would write."""
        return NotchSettings(visual=self.notch.visual_config, monitor=self.notch.monitor_preference)

    def sync_from_notch(self):
        """Show the values the notch really uses, without applying anything."""

        geometry, style = self.notch.notch_geometry, self.notch.eye_style
        values = {
            self.compact_width: geometry.compact.width,
            self.compact_height: geometry.compact.height,
            self.compact_radius: geometry.compact.corner_radius,
            self.expanded_width: geometry.expanded.width,
            self.expanded_height: geometry.expanded.height,
            self.expanded_radius: geometry.expanded.corner_radius,
            self.offset_x: geometry.offset_x,
            self.top_margin: geometry.top_margin,
            self.duration: self.notch.transition_duration_ms,
            self.eye_height_share: style.height_share,
            self.eye_top_offset: style.top_offset,
            self.eye_center_offset: style.center_offset,
            self.eye_spacing: style.spacing,
            self.eye_motion_gain: style.motion_gain,
        }
        for box, value in values.items():
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        if self.controller is not None:
            self.expression.blockSignals(True)
            self.expression.setCurrentIndex(self.expression.findData(self.controller.expression_key.value))
            self.expression.blockSignals(False)
        self._visual_valid = True
        self.error_label.clear()
        self._rebuild_monitor_list()
        self._refresh_status()

    def _rebuild_monitor_list(self):
        """List Automatic, every connected monitor and an absent preference."""

        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        tracked = self.notch.tracked_screen
        preference = self.notch.monitor_preference

        choices: list[tuple[str, MonitorPreference]] = [("Automatic", MonitorPreference())]
        lines = []
        for screen in screens:
            identity = monitor_identity(screen)
            choices.append((identity.label, MonitorPreference(identity)))
            geometry = screen.geometry()
            marks = [mark for mark, on in (("primary", screen is primary), ("selected", screen is tracked)) if on]
            lines.append(
                f"{identity.label}  {geometry.width()}×{geometry.height()} at "
                f"{geometry.x()},{geometry.y()}" + (f"  [{', '.join(marks)}]" if marks else "")
            )
        if preference.monitor is not None and not any(
            preference.monitor.matches(choice.monitor) for _, choice in choices[1:]
        ):
            choices.append((f"{preference.monitor.label} — not connected", preference))

        self.monitor.blockSignals(True)
        self.monitor.clear()
        self._monitor_choices = []
        selected = 0
        for index, (label, choice) in enumerate(choices):
            self.monitor.addItem(label)
            self._monitor_choices.append(choice)
            if choice.monitor is not None and preference.monitor is not None and preference.monitor.matches(choice.monitor):
                selected = index
        self.monitor.setCurrentIndex(selected)
        self.monitor.blockSignals(False)
        self.monitors_label.setText("\n".join(lines) or "none")

    def _refresh_status(self):
        notch = self.notch
        screen = notch.tracked_screen
        bounds = notch.geometry()
        self.presence_label.setText(f"target: {notch.presence.value}")

        if self.session is None:
            self.loaded_label.setText("No file (not persisted)")
            self.config_path_label.setText("-")
            self.unsaved_label.setText("-")
        else:
            self.loaded_label.setText(SOURCE_LABELS[self.session.source])
            self.config_path_label.setText(str(self.session.path))
            unsaved = self.session.has_unsaved_changes(self.runtime_settings)
            self.unsaved_label.setText("Yes" if unsaved else "No")

        selection = notch.selection
        if notch.monitor_preference.is_automatic:
            mode = "Automatic"
        elif selection is not None and selection.mode is SelectionMode.FALLBACK:
            mode = f"Preferred {notch.monitor_preference.monitor.label} (not connected → automatic)"
        else:
            mode = "Preferred"
        self.monitor_mode_label.setText(mode)
        self.selected_monitor_label.setText(monitor_identity(screen).label if screen is not None else "-")
        self.monitor_count_label.setText(str(len(QGuiApplication.screens())))
        self.visual_label.setText("Valid" if self._visual_valid else "Invalid (not applied)")
        self.save_button.setEnabled(self.session is not None and self._visual_valid)
        self.window_label.setText(f"{bounds.x()},{bounds.y()} {bounds.width()}×{bounds.height()}")

        eyes = notch.eye_layout
        self.eye_layout_label.setText(
            f"scale {eyes.scale:.4f} px/unit · offsets {eyes.offset_scale:.4f} px/unit · "
            f"top {eyes.top:.2f} · centre x {eyes.center_x:.1f}"
        )

    def _on_screen_selection_changed(self):
        self._rebuild_monitor_list()
        self._refresh_status()

    # --- Controls → notch ---------------------------------------------------

    def _control_config(self) -> NotchVisualConfig:
        """The configuration the controls describe; ValueError if impossible."""

        return NotchVisualConfig(
            geometry=NotchGeometry(
                compact=NotchSize(
                    self.compact_width.value(), self.compact_height.value(), self.compact_radius.value()
                ),
                expanded=NotchSize(
                    self.expanded_width.value(), self.expanded_height.value(), self.expanded_radius.value()
                ),
                top_margin=self.top_margin.value(),
                offset_x=self.offset_x.value(),
            ),
            eye_style=NotchEyeStyle(
                height_share=self.eye_height_share.value(),
                top_offset=self.eye_top_offset.value(),
                center_offset=self.eye_center_offset.value(),
                spacing=self.eye_spacing.value(),
                motion_gain=self.eye_motion_gain.value(),
            ),
            transition_duration_ms=self.duration.value(),
        )

    def _apply_visual_config(self):
        try:
            self.notch.apply_visual_config(self._control_config())
        except InvalidVisualConfig as error:
            # The eyes would leave the notch: keep the last valid settings.
            self._visual_valid = False
            self.error_label.setText("Not applied — eye motion would be clipped: " + "; ".join(error.problems))
        except ValueError as error:
            self._visual_valid = False
            self.error_label.setText(f"Not applied: {error}")
        else:
            self._visual_valid = True
            self.error_label.clear()
        self._refresh_status()

    def _apply_monitor(self, index: int):
        if 0 <= index < len(self._monitor_choices):
            self.notch.set_monitor_preference(self._monitor_choices[index])

    @property
    def selected_expression(self) -> ExpressionKey | None:
        value = self.expression.currentData()
        return None if value is None else ExpressionKey(value)

    def _apply_expression(self, _index: int):
        key = self.selected_expression
        if self.controller is not None and key is not None:
            self.controller.set_intent(EmbodimentIntent(ActivityState.IDLE, expression=key))

    # --- Persistence --------------------------------------------------------

    def apply_settings(self, settings: NotchSettings):
        """Make ``settings`` the runtime settings and show them."""

        self.notch.apply_visual_config(settings.visual)
        self.notch.set_monitor_preference(settings.monitor)
        self.sync_from_notch()

    def save(self):
        if self.session is None or not self._visual_valid:
            return
        try:
            path = self.session.save(self.runtime_settings)
        except (OSError, InvalidVisualConfig) as error:
            self.message_label.setText(f"Not saved: {error}")
        else:
            self.message_label.setText(f"Saved to {path}")
        self._refresh_status()

    def reload(self):
        """Read the file again; an invalid file leaves the runtime as it is."""

        if self.session is None:
            return
        loaded = self.session.reload()
        if loaded.source is ConfigSource.INVALID_FALLBACK:
            self.message_label.setText(f"Not reloaded, runtime settings kept: {loaded.diagnostic}")
            self._refresh_status()
            return
        self.apply_settings(loaded.settings)
        self.message_label.setText(
            f"Reloaded from {loaded.path}" if loaded.source is ConfigSource.SAVED
            else "No saved configuration: production defaults"
        )

    def reset_defaults(self):
        """Restore the production defaults at run time; nothing is written."""

        self.apply_settings(NotchSettings())
        self.message_label.setText("Production defaults restored (not saved)")

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
