from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout

from nero.ui.face.face_widget import FaceWidget
from nero.ui.debug_panel import DebugPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("N.E.R.O")
        self.resize(1280, 700)

        central_widget = QWidget()
        layout = QHBoxLayout()
        central_widget.setLayout(layout)

        self.face_widget = FaceWidget()
        self.debug_panel = DebugPanel(self.face_widget)

        layout.addWidget(self.face_widget, stretch=3)
        layout.addWidget(self.debug_panel, stretch=1)

        self.setCentralWidget(central_widget)