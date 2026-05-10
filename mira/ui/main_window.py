from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout

from mira.ui.face.face_widget import FaceWidget
from mira.ui.debug_panel import DebugPanel
from mira.ui.chat_panel import ChatPanel

from mira.core.events import EventBus
from mira.core.state_manager import StateManager
from mira.core.brain import Brain
from mira.core.interaction_manager import InteractionManager

from mira.core.embodied_behavior import EmbodiedBehavior

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("M.I.R.A. - Modular Interactive Responsive Agent")
        self.resize(1500, 800)

        self.is_processing = False

        # --- Core systems ---
        self.event_bus = EventBus()
        self.state_manager = StateManager(self.event_bus)
        self.brain = Brain(self.event_bus, self.state_manager)
        self.interaction_manager = InteractionManager(self.event_bus, self.state_manager)
        self.embodied_behavior = EmbodiedBehavior(self.event_bus, self.state_manager)

        # --- UI root ---
        central_widget = QWidget()
        root_layout = QHBoxLayout()
        central_widget.setLayout(root_layout)

        # --- Left side: face ---
        self.face_widget = FaceWidget()
        root_layout.addWidget(self.face_widget, stretch=3)

        # --- Right side: chat + debug ---
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_panel.setLayout(right_layout)

        self.chat_panel = ChatPanel()
        self.debug_panel = DebugPanel(self.face_widget)

        right_layout.addWidget(self.chat_panel, stretch=3)
        right_layout.addWidget(self.debug_panel, stretch=2)

        root_layout.addWidget(right_panel, stretch=2)

        self.setCentralWidget(central_widget)

        # --- Event wiring ---
        self.event_bus.subscribe("state_changed", self.on_state_changed)

        self.chat_panel.message_submitted.connect(self.on_user_message_submitted)
        self.chat_panel.input_focused.connect(lambda: self.event_bus.emit("input_focused"))
        self.chat_panel.input_unfocused.connect(lambda: self.event_bus.emit("input_unfocused"))
        self.chat_panel.input_text_changed.connect(
            lambda text: self.event_bus.emit("input_text_changed", text)
)

    def on_state_changed(self, payload):
        new_state = payload["new_state"]

        print(f"[STATE] {new_state}")

        self.face_widget.controller.set_state(new_state)
        self.face_widget.update()

    def on_user_message_submitted(self, text: str):
        self.chat_panel.add_user_message(text)
        self.brain.process_text_async(text, self.on_brain_response)

    def on_brain_response(self, response):
        self.chat_panel.add_mira_message(response.text)
        print(f"[mira] {response.text}")


