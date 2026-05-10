from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QLineEdit,
    QPushButton,
    QGroupBox,
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

        self.setMinimumWidth(420)

        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        self.build_chat_group()
        self.build_input_group()

    def build_chat_group(self):
        group = QGroupBox("Conversation")
        layout = QVBoxLayout()

        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)

        layout.addWidget(self.chat_history)
        group.setLayout(layout)

        self.main_layout.addWidget(group, stretch=1)

    def build_input_group(self):
        group = QGroupBox("Input")
        layout = QHBoxLayout()

        self.input_line = ChatInputLine()
        self.input_line.setPlaceholderText("Scrivi un messaggio a N.E.R.O...")
        self.input_line.returnPressed.connect(self.submit_message)
        self.input_line.textChanged.connect(self.input_text_changed.emit)
        self.input_line.focused.connect(self.input_focused.emit)
        self.input_line.unfocused.connect(self.input_unfocused.emit)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.submit_message)

        layout.addWidget(self.input_line, stretch=1)
        layout.addWidget(self.send_button)

        group.setLayout(layout)
        self.main_layout.addWidget(group)

    def submit_message(self):
        text = self.input_line.text().strip()
        if not text:
            return

        self.message_submitted.emit(text)
        self.input_line.clear()

    def add_user_message(self, text: str):
        self.chat_history.append(f"<b>You:</b> {text}")

    def add_mira_message(self, text: str):
        self.chat_history.append(f"<b>N.E.R.O:</b> {text}")

    def add_system_message(self, text: str):
        self.chat_history.append(f"<i>{text}</i>")