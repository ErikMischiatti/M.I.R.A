from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QComboBox,
    QSlider,
    QCheckBox,
    QGroupBox,
    QFormLayout,
    QPushButton,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
)

from mira.domain.state import FaceState


class DebugPanel(QWidget):
    def __init__(self, face_widget):
        super().__init__()

        self.face_widget = face_widget
        self.controller = face_widget.controller

        self.setMinimumWidth(320)

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.setLayout(outer_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(6)
        content_widget.setLayout(self.main_layout)

        self.scroll_area.setWidget(content_widget)
        outer_layout.addWidget(self.scroll_area)

        self.build_state_selector()
        self.build_expression_controls()
        self.build_animation_controls()
        self.build_asymmetry_controls()
        self.build_action_buttons()

        self.main_layout.addStretch()

        self.load_profile_into_controls()

    def build_state_selector(self):
        group = QGroupBox("State")
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.state_combo = QComboBox()
        for state in FaceState:
            self.state_combo.addItem(state.name, state)

        self.state_combo.currentIndexChanged.connect(self.on_state_changed)

        layout.addWidget(QLabel("Active face state"))
        layout.addWidget(self.state_combo)
        group.setLayout(layout)

        self.main_layout.addWidget(group)

    def build_expression_controls(self):
        group = QGroupBox("Expression Tuning")
        form = QFormLayout()
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)

        self.width_slider = self.make_slider(50, 200, 100, self.on_slider_changed)
        self.height_slider = self.make_slider(20, 200, 100, self.on_slider_changed)
        self.offset_x_slider = self.make_slider(-100, 100, 0, self.on_slider_changed)
        self.offset_y_slider = self.make_slider(-100, 100, 0, self.on_slider_changed)
        self.corner_radius_slider = self.make_slider(0, 100, 28, self.on_slider_changed)

        form.addRow("Width scale", self.width_slider)
        form.addRow("Height scale", self.height_slider)
        form.addRow("Offset X", self.offset_x_slider)
        form.addRow("Offset Y", self.offset_y_slider)
        form.addRow("Corner radius", self.corner_radius_slider)

        group.setLayout(form)
        self.main_layout.addWidget(group)

    def build_animation_controls(self):
        group = QGroupBox("Animation Controls")
        form = QFormLayout()
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)

        self.idle_checkbox = QCheckBox()
        self.idle_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.blink_checkbox = QCheckBox()
        self.blink_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.speaking_pulse_checkbox = QCheckBox()
        self.speaking_pulse_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.thinking_drift_checkbox = QCheckBox()
        self.thinking_drift_checkbox.stateChanged.connect(self.on_checkbox_changed)

        self.idle_amp_x_slider = self.make_slider(0, 100, 18, self.on_slider_changed)
        self.idle_amp_y_slider = self.make_slider(0, 100, 10, self.on_slider_changed)

        self.blink_min_slider = self.make_slider(1, 400, 70, self.on_slider_changed)
        self.blink_max_slider = self.make_slider(1, 400, 170, self.on_slider_changed)
        self.blink_duration_slider = self.make_slider(1, 30, 5, self.on_slider_changed)

        form.addRow("Idle enabled", self.idle_checkbox)
        form.addRow("Blink enabled", self.blink_checkbox)
        form.addRow("Speaking pulse", self.speaking_pulse_checkbox)
        form.addRow("Thinking drift", self.thinking_drift_checkbox)
        form.addRow("Idle amplitude X", self.idle_amp_x_slider)
        form.addRow("Idle amplitude Y", self.idle_amp_y_slider)
        form.addRow("Blink min frames", self.blink_min_slider)
        form.addRow("Blink max frames", self.blink_max_slider)
        form.addRow("Blink duration", self.blink_duration_slider)

        group.setLayout(form)
        self.main_layout.addWidget(group)

    def build_asymmetry_controls(self):
        group = QGroupBox("Asymmetry")
        form = QFormLayout()
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)

        self.asym_offset_left_slider = self.make_slider(-50, 50, 0, self.on_slider_changed)
        self.asym_offset_right_slider = self.make_slider(-50, 50, 0, self.on_slider_changed)
        self.asym_height_left_slider = self.make_slider(20, 200, 100, self.on_slider_changed)
        self.asym_height_right_slider = self.make_slider(20, 200, 100, self.on_slider_changed)

        form.addRow("Left Y offset", self.asym_offset_left_slider)
        form.addRow("Right Y offset", self.asym_offset_right_slider)
        form.addRow("Left height scale", self.asym_height_left_slider)
        form.addRow("Right height scale", self.asym_height_right_slider)

        group.setLayout(form)
        self.main_layout.addWidget(group)

    def build_action_buttons(self):
        group = QGroupBox("Profile Actions")
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.on_save_clicked)

        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self.on_reload_clicked)

        self.reset_button = QPushButton("Reset Current")
        self.reset_button.clicked.connect(self.on_reset_clicked)

        layout.addWidget(self.save_button)
        layout.addWidget(self.reload_button)
        layout.addWidget(self.reset_button)

        group.setLayout(layout)
        self.main_layout.addWidget(group)

    def make_slider(self, min_value, max_value, initial_value, callback):
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(min_value)
        slider.setMaximum(max_value)
        slider.setValue(initial_value)
        slider.valueChanged.connect(callback)
        return slider

    def on_state_changed(self):
        state = self.state_combo.currentData()
        if state is None:
            return

        self.controller.set_state(state)
        self.load_profile_into_controls()
        self.face_widget.update()

    def on_slider_changed(self):
        profile = self.controller.get_profile()

        profile.width_scale = self.width_slider.value() / 100.0
        profile.height_scale = self.height_slider.value() / 100.0
        profile.offset_x = float(self.offset_x_slider.value())
        profile.offset_y = float(self.offset_y_slider.value())
        profile.corner_radius = float(self.corner_radius_slider.value())

        profile.idle_amplitude_x = float(self.idle_amp_x_slider.value())
        profile.idle_amplitude_y = float(self.idle_amp_y_slider.value())

        profile.blink_min_interval_frames = int(self.blink_min_slider.value())
        profile.blink_max_interval_frames = int(self.blink_max_slider.value())
        profile.blink_duration_frames = int(self.blink_duration_slider.value())

        profile.asymmetry_offset_y_left = float(self.asym_offset_left_slider.value())
        profile.asymmetry_offset_y_right = float(self.asym_offset_right_slider.value())
        profile.asymmetry_height_left = self.asym_height_left_slider.value() / 100.0
        profile.asymmetry_height_right = self.asym_height_right_slider.value() / 100.0

        self.controller.refresh_profile_targets()
        self.face_widget.update()

    def on_checkbox_changed(self):
        profile = self.controller.get_profile()

        profile.idle_enabled = self.idle_checkbox.isChecked()
        profile.blink_enabled = self.blink_checkbox.isChecked()
        profile.speaking_pulse = self.speaking_pulse_checkbox.isChecked()
        profile.thinking_drift = self.thinking_drift_checkbox.isChecked()

        self.controller.refresh_profile_targets()
        self.face_widget.update()

    def on_save_clicked(self):
        self.controller.save_profiles()
        QMessageBox.information(self, "Saved", "Expression profiles saved successfully.")

    def on_reload_clicked(self):
        self.controller.reload_profiles()
        self.load_profile_into_controls()
        self.face_widget.update()
        QMessageBox.information(self, "Reloaded", "Expression profiles reloaded from file.")

    def on_reset_clicked(self):
        self.controller.reset_current_profile()
        self.load_profile_into_controls()
        self.face_widget.update()
        QMessageBox.information(self, "Reset", "Current expression reset to default values.")

    def load_profile_into_controls(self):
        profile = self.controller.get_profile()

        controls = [
            self.width_slider,
            self.height_slider,
            self.offset_x_slider,
            self.offset_y_slider,
            self.corner_radius_slider,
            self.idle_amp_x_slider,
            self.idle_amp_y_slider,
            self.blink_min_slider,
            self.blink_max_slider,
            self.blink_duration_slider,
            self.asym_offset_left_slider,
            self.asym_offset_right_slider,
            self.asym_height_left_slider,
            self.asym_height_right_slider,
            self.idle_checkbox,
            self.blink_checkbox,
            self.speaking_pulse_checkbox,
            self.thinking_drift_checkbox,
        ]

        for control in controls:
            control.blockSignals(True)

        self.width_slider.setValue(int(profile.width_scale * 100))
        self.height_slider.setValue(int(profile.height_scale * 100))
        self.offset_x_slider.setValue(int(profile.offset_x))
        self.offset_y_slider.setValue(int(profile.offset_y))
        self.corner_radius_slider.setValue(int(profile.corner_radius))

        self.idle_amp_x_slider.setValue(int(profile.idle_amplitude_x))
        self.idle_amp_y_slider.setValue(int(profile.idle_amplitude_y))

        self.blink_min_slider.setValue(int(profile.blink_min_interval_frames))
        self.blink_max_slider.setValue(int(profile.blink_max_interval_frames))
        self.blink_duration_slider.setValue(int(profile.blink_duration_frames))

        self.asym_offset_left_slider.setValue(int(profile.asymmetry_offset_y_left))
        self.asym_offset_right_slider.setValue(int(profile.asymmetry_offset_y_right))
        self.asym_height_left_slider.setValue(int(profile.asymmetry_height_left * 100))
        self.asym_height_right_slider.setValue(int(profile.asymmetry_height_right * 100))

        self.idle_checkbox.setChecked(profile.idle_enabled)
        self.blink_checkbox.setChecked(profile.blink_enabled)
        self.speaking_pulse_checkbox.setChecked(profile.speaking_pulse)
        self.thinking_drift_checkbox.setChecked(profile.thinking_drift)

        for control in controls:
            control.blockSignals(False)