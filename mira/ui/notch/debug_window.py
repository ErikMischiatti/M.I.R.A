"""Developer tuning window for a live `NotchWindow`.

Developer tooling only. The window owns no notch state and draws no notch: its
controls read the notch's current visual parameters, build new immutable
values from them and hand those to the notch's public setters, which re-derive
bounds, shape, mask and eye layout through the production pipeline. Presence
changes go through the production presence API and expressions through the
`FaceController`. Nothing is persisted.
"""

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mira.domain.embodiment import ActivityState, EmbodimentIntent, ExpressionKey
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize


def number(minimum: float, maximum: float, *, decimals: int = 1, step: float = 1.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setKeyboardTracking(False)
    return box


class NotchDebugWindow(QWidget):
    """Live controls for one notch and the controller its eyes show."""

    closed = Signal()

    def __init__(self, notch: NotchWindow, controller: FaceController | None = None):
        super().__init__()
        self.setWindowTitle("M.I.R.A. notch tuning")
        self.notch = notch
        self.controller = controller

        layout = QVBoxLayout(self)
        layout.addWidget(self._presence_group())

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
        layout.addWidget(self._group("Placement (after centring on the selected screen)", (
            ("Horizontal offset (px)", self.offset_x),
            ("Vertical offset (px)", self.top_margin),
        )))

        self.duration = QSpinBox()
        self.duration.setRange(0, 5000)
        self.duration.setSingleStep(10)
        self.duration.setKeyboardTracking(False)
        layout.addWidget(self._group("Shape", (("Transition duration (ms)", self.duration),)))

        self.eye_height_share = number(0.01, 3.0, decimals=3, step=0.005)
        self.eye_top_offset = number(-200, 200, step=0.5)
        self.eye_center_offset = number(-1000, 1000, step=0.5)
        self.eye_spacing = number(0, 3000, step=5)
        self.eye_layout_label = QLabel()
        layout.addWidget(self._group("Eyes", (
            ("Scale: base eye / compact height", self.eye_height_share),
            ("Top offset (px)", self.eye_top_offset),
            ("Pair centre offset X (px)", self.eye_center_offset),
            ("Spacing (face units)", self.eye_spacing),
            ("Derived layout", self.eye_layout_label),
        )))

        self.expression = QComboBox()
        # Qt keeps item data as plain strings, so store the key's value and
        # convert back on selection.
        for key in ExpressionKey:
            self.expression.addItem(key.name, key.value)
        self.expression.setEnabled(controller is not None)
        layout.addWidget(self._group("Expression", (("Expression", self.expression),)))

        self.reset_button = QPushButton("Reset defaults")
        layout.addWidget(self.reset_button)
        self.status_label = QLabel()
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c0392b;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addWidget(self.error_label)
        layout.addStretch()

        self.sync_from_notch()

        for box in (
            self.compact_width, self.compact_height, self.compact_radius,
            self.expanded_width, self.expanded_height, self.expanded_radius,
            self.offset_x, self.top_margin,
        ):
            box.valueChanged.connect(self._apply_geometry)
        for box in (self.eye_height_share, self.eye_top_offset, self.eye_center_offset, self.eye_spacing):
            box.valueChanged.connect(self._apply_eye_style)
        self.duration.valueChanged.connect(self._apply_duration)
        self.expression.currentIndexChanged.connect(self._apply_expression)
        self.reset_button.clicked.connect(self.reset_defaults)

    # --- Construction helpers ------------------------------------------------

    def _group(self, title: str, rows) -> QGroupBox:
        group = QGroupBox(title)
        form = QFormLayout(group)
        for label, widget in rows:
            form.addRow(label, widget)
        return group

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

    # --- Presence, through the production API ------------------------------

    def show_hidden(self):
        self.notch.hide_notch()
        self._refresh_status()

    def show_compact(self):
        if self.notch.presence is NotchPresence.HIDDEN:
            self.notch.show_notch()
        else:
            self.notch.collapse_notch()
        self._refresh_status()

    def show_expanded(self):
        self.notch.expand_notch()
        self._refresh_status()

    # --- Controls ← notch ---------------------------------------------------

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
        }
        for box, value in values.items():
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        if self.controller is not None:
            self.expression.blockSignals(True)
            self.expression.setCurrentIndex(self.expression.findData(self.controller.expression_key.value))
            self.expression.blockSignals(False)
        self.error_label.clear()
        self._refresh_status()

    def _refresh_status(self):
        screen = self.notch.tracked_screen
        bounds = self.notch.geometry()
        self.presence_label.setText(f"target: {self.notch.presence.value}")
        self.status_label.setText(
            f"screen {screen.name() if screen is not None else '-'!s} · "
            f"window {bounds.x()},{bounds.y()} {bounds.width()}×{bounds.height()}"
        )
        eyes = self.notch.eye_layout
        self.eye_layout_label.setText(
            f"scale {eyes.scale:.4f} px/unit · top {eyes.top:.1f} · centre x {eyes.center_x:.1f}"
        )

    # --- Controls → notch ---------------------------------------------------

    def _apply_geometry(self):
        try:
            geometry = NotchGeometry(
                compact=NotchSize(
                    self.compact_width.value(), self.compact_height.value(), self.compact_radius.value()
                ),
                expanded=NotchSize(
                    self.expanded_width.value(), self.expanded_height.value(), self.expanded_radius.value()
                ),
                top_margin=self.top_margin.value(),
                offset_x=self.offset_x.value(),
            )
        except ValueError as error:
            # Keep the last valid geometry on the notch and say why this isn't.
            self.error_label.setText(f"Not applied: {error}")
            return
        self.error_label.clear()
        self.notch.set_notch_geometry(geometry)
        self._refresh_status()

    def _apply_eye_style(self):
        try:
            style = replace(
                self.notch.eye_style,
                height_share=self.eye_height_share.value(),
                top_offset=self.eye_top_offset.value(),
                center_offset=self.eye_center_offset.value(),
                spacing=self.eye_spacing.value(),
            )
        except ValueError as error:
            self.error_label.setText(f"Not applied: {error}")
            return
        self.error_label.clear()
        self.notch.set_eye_style(style)
        self._refresh_status()

    def _apply_duration(self, value: int):
        self.notch.transition_duration_ms = value

    @property
    def selected_expression(self) -> ExpressionKey | None:
        value = self.expression.currentData()
        return None if value is None else ExpressionKey(value)

    def _apply_expression(self, _index: int):
        key = self.selected_expression
        if self.controller is not None and key is not None:
            self.controller.set_intent(EmbodimentIntent(ActivityState.IDLE, expression=key))

    def reset_defaults(self):
        """Restore the production defaults of every visual parameter."""

        self.notch.set_notch_geometry(NotchGeometry())
        self.notch.set_eye_style(NotchEyeStyle())
        self.notch.transition_duration_ms = NotchWindow.TRANSITION_DURATION_MS
        self.sync_from_notch()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
