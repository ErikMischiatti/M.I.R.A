from PySide6.QtWidgets import QMainWindow
from nero.ui.face.face_widget import FaceWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("N.E.R.O")
        self.resize(600, 350)

        self.face_widget = FaceWidget()
        self.setCentralWidget(self.face_widget)