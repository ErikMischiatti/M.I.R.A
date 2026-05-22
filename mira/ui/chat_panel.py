from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QLineEdit,
    QPushButton,
)


class ChatInputLine(QLineEdit):
    focused = Signal()
    unfocused = Signal()

    def focusInEvent(self, event):
        self.focused.emit()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.unfocused.emit()
        super().focusOutEvent(event)


class ChatPanel(QWidget):
    message_submitted = Signal(str)
    input_focused = Signal()
    input_unfocused = Signal()
    input_text_changed = Signal(str)

    def __init__(self):
        super().__init__()

        self.setObjectName("ChatPanel")
        self.setMinimumWidth(320)

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(6)
        self.setLayout(self.main_layout)

        self.build_chat_history()
        self.build_action_status()
        self.build_input_row()
        self.apply_visual_style()

    def build_chat_history(self):
        label = QLabel("Conversation")
        label.setObjectName("ChatSectionLabel")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.chat_history = QTextEdit()
        self.chat_history.setObjectName("ChatHistory")
        self.chat_history.setReadOnly(True)
        self.chat_history.setMinimumHeight(110)

        layout.addWidget(label)
        layout.addWidget(self.chat_history)

        self.main_layout.addLayout(layout, stretch=1)

    def build_action_status(self):
        self.action_status = QLabel("")
        self.action_status.setObjectName("ActionStatus")
        self.action_status.setWordWrap(True)
        self.action_status.setVisible(False)

        self.main_layout.addWidget(self.action_status)

    def build_input_row(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.input_line = ChatInputLine()
        self.input_line.setObjectName("ChatInput")
        self.input_line.setMinimumHeight(34)
        self.input_line.setPlaceholderText("Scrivi un messaggio a M.I.R.A...")
        self.input_line.returnPressed.connect(self.submit_message)
        self.input_line.textChanged.connect(self.input_text_changed.emit)
        self.input_line.focused.connect(self.input_focused.emit)
        self.input_line.unfocused.connect(self.input_unfocused.emit)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("SendButton")
        self.send_button.setMinimumHeight(34)
        self.send_button.clicked.connect(self.submit_message)

        layout.addWidget(self.input_line, stretch=1)
        layout.addWidget(self.send_button)

        self.main_layout.addLayout(layout)

    def apply_visual_style(self):
        self.setStyleSheet("""
            QWidget#ChatPanel {
                background: transparent;
            }

            QLabel#ChatSectionLabel {
                color: #aeb6c2;
                font-size: 11px;
                font-weight: 600;
            }

            QTextEdit#ChatHistory {
                border: 1px solid #2b3038;
                border-radius: 6px;
                background: #171a1f;
                color: #e7eaee;
                padding: 6px;
            }

            QLabel#ActionStatus {
                border: 1px solid #2f3844;
                border-radius: 6px;
                background: #1a2028;
                color: #cbd5e1;
                padding: 5px 7px;
                font-size: 11px;
            }

            QLineEdit#ChatInput {
                border: 1px solid #343a43;
                border-radius: 6px;
                background: #1b1f25;
                color: #eef1f5;
                padding: 5px 8px;
            }

            QPushButton#SendButton {
                padding: 5px 12px;
                border: 1px solid #3a4048;
                border-radius: 6px;
                background: #242a32;
                color: #eef1f5;
            }

            QPushButton#SendButton:hover {
                background: #2b333e;
            }
        """)

    def submit_message(self):
        text = self.input_line.text().strip()
        if not text:
            return

        self.message_submitted.emit(text)
        self.input_line.clear()

    def add_user_message(self, text: str):
        self.chat_history.append(f"<b>You:</b> {text}")

    def add_mira_message(self, text: str):
        self.chat_history.append(f"<b>M.I.R.A.:</b> {text}")

    def add_system_message(self, text: str):
        self.chat_history.append(f"<i>{text}</i>")

    def set_action_status(self, text: str, success: bool | None = None):
        if not text:
            self.action_status.clear()
            self.action_status.setVisible(False)
            return

        colors = {
            True: ("#183325", "#2c8f5a", "#c8f7dc"),
            False: ("#351f24", "#9b3d4c", "#ffd7dc"),
            None: ("#1a2028", "#2f3844", "#cbd5e1"),
        }
        background, border, foreground = colors[success]
        self.action_status.setStyleSheet(
            f"""
            QLabel#ActionStatus {{
                border: 1px solid {border};
                border-radius: 6px;
                background: {background};
                color: {foreground};
                padding: 5px 7px;
                font-size: 11px;
            }}
            """
        )
        self.action_status.setText(text)
        self.action_status.setVisible(True)
