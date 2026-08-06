from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSplitter

from mira.ui.face.face_widget import FaceWidget
from mira.ui.debug_panel import DebugPanel
from mira.ui.chat_panel import ChatPanel

from mira.adapters.qt_scheduler import QtScheduler
from mira.core.events import EventBus
from mira.core.state_manager import StateManager
from mira.core.brain import Brain
from mira.core.interaction_manager import InteractionManager

from mira.core.embodied_behavior import EmbodiedBehavior

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("M.I.R.A. - Modular Interactive Responsive Agent")
        self.compact_size = QSize(860, 680)
        self.debug_drawer_width = 340
        self.resize(self.compact_size)

        # --- Core systems ---
        self.event_bus = EventBus()
        self.state_manager = StateManager(self.event_bus)
        self.scheduler = QtScheduler()
        self.brain = Brain(
            self.event_bus, self.state_manager, scheduler=self.scheduler
        )
        self.interaction_manager = InteractionManager(self.event_bus, self.state_manager)
        self.embodied_behavior = EmbodiedBehavior(
            self.event_bus, self.state_manager, scheduler=self.scheduler
        )

        # --- UI root ---
        central_widget = QWidget()
        central_widget.setObjectName("AppRoot")
        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)
        central_widget.setLayout(root_layout)

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.content_splitter)

        # --- Compact companion area ---
        main_panel = QWidget()
        main_panel.setObjectName("CompanionPanel")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)
        main_panel.setLayout(main_layout)

        top_bar = QWidget()
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar.setLayout(top_bar_layout)

        self.debug_toggle = QPushButton("Debug")
        self.debug_toggle.setObjectName("DebugToggle")
        self.debug_toggle.setCheckable(True)

        top_bar_layout.addStretch()
        top_bar_layout.addWidget(self.debug_toggle)
        main_layout.addWidget(top_bar)

        self.face_widget = FaceWidget()
        self.chat_panel = ChatPanel()

        main_layout.addWidget(self.face_widget, stretch=3)
        main_layout.addWidget(self.chat_panel, stretch=2)

        self.content_splitter.addWidget(main_panel)

        # --- Hidden developer drawer ---
        self.debug_panel = DebugPanel(self.face_widget)
        self.debug_panel.setMinimumWidth(320)
        self.debug_panel.setMaximumWidth(self.debug_drawer_width)
        self.debug_panel.setVisible(False)
        self.debug_toggle.toggled.connect(self.on_debug_toggled)

        self.content_splitter.addWidget(self.debug_panel)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)

        self.setCentralWidget(central_widget)
        self.apply_visual_style()

        # --- Event wiring ---
        self.event_bus.subscribe("state_changed", self.on_state_changed)
        self.event_bus.subscribe("action_started", self.on_action_started)
        self.event_bus.subscribe("action_completed", self.on_action_completed)
        self.event_bus.subscribe("action_failed", self.on_action_failed)

        self.chat_panel.message_submitted.connect(self.on_user_message_submitted)
        self.chat_panel.input_focused.connect(lambda: self.event_bus.emit("input_focused"))
        self.chat_panel.input_unfocused.connect(lambda: self.event_bus.emit("input_unfocused"))
        self.chat_panel.input_text_changed.connect(
            lambda text: self.event_bus.emit("input_text_changed", text)
        )

    def on_debug_toggled(self, checked: bool):
        self.debug_panel.setVisible(checked)
        self.debug_toggle.setText("Hide Debug" if checked else "Debug")

        splitter_width = max(self.content_splitter.width(), self.width())
        if checked:
            drawer_width = min(self.debug_drawer_width, max(260, splitter_width // 3))
            self.content_splitter.setSizes([splitter_width - drawer_width, drawer_width])
        else:
            self.content_splitter.setSizes([splitter_width, 0])

    def apply_visual_style(self):
        self.setStyleSheet("""
            QWidget#AppRoot {
                background: #121417;
            }

            QWidget#CompanionPanel {
                background: transparent;
            }

            QPushButton#DebugToggle {
                padding: 5px 12px;
                border: 1px solid #3a4048;
                border-radius: 6px;
                background: #1c2026;
                color: #d8dde4;
            }

            QPushButton#DebugToggle:checked {
                background: #27313d;
                border-color: #566274;
            }

            QSplitter::handle {
                background: #20242a;
                width: 1px;
            }
        """)

    def on_state_changed(self, payload):
        new_state = payload["new_state"]

        print(f"[STATE] {new_state}")

        self.face_widget.controller.set_state(new_state)
        self.face_widget.update()

    def on_action_started(self, payload):
        action_name = getattr(payload, "action_name", "azione")
        self.chat_panel.set_action_status(f"Esecuzione azione: {action_name}")

    def on_action_completed(self, payload):
        action_name = getattr(payload, "action_name", "azione")
        message = getattr(payload, "message", "Azione completata.")
        self.chat_panel.set_action_status(
            f"Completata: {action_name} - {message}",
            success=True,
        )

    def on_action_failed(self, payload):
        action_name = getattr(payload, "action_name", "azione")
        message = getattr(payload, "message", "Azione non riuscita.")
        self.chat_panel.set_action_status(
            f"Non riuscita: {action_name} - {message}",
            success=False,
        )

    def on_user_message_submitted(self, text: str):
        self.chat_panel.add_user_message(text)
        self.brain.process_text_async(text, self.on_brain_response)

    def on_brain_response(self, response):
        self.chat_panel.add_mira_message(response.text)
        print(f"[mira] {response.text}")


