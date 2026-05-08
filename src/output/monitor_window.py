from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel,
    QScrollArea, QFrame, QApplication, QPushButton, QHBoxLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette, QScreen


class MonitorWindow(QWidget):
    """
    Answer display window designed to live on a second monitor
    that isn't being shared. Shows scrollable Q&A history.
    """

    answer_received = pyqtSignal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._target_monitor = config.get("target_monitor", 1)
        self._answers: list[str] = []

        self._setup_window()
        self._setup_ui()
        self.answer_received.connect(self._on_answer)

    def _setup_window(self):
        self.setWindowTitle("Untitled - Notepad")
        self.setMinimumSize(500, 400)
        self.resize(600, 800)

        self._move_to_target_monitor()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setFixedHeight(36)
        header.setStyleSheet("QFrame { background-color: #2d2d2d; }")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("My Notes")
        title.setFont(QFont("Segoe UI", 10))
        title.setStyleSheet("color: #ccc;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        clear_btn = QPushButton("New")
        clear_btn.setStyleSheet(
            "QPushButton { background: #383838; color: #aaa; border: none; padding: 4px 10px; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #555; color: #fff; }"
        )
        clear_btn.clicked.connect(self.clear)
        header_layout.addWidget(clear_btn)

        layout.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background-color: #0f0f1a; }"
        )

        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content_layout.setSpacing(12)
        self._content_layout.setContentsMargins(12, 12, 12, 12)

        self._scroll.setWidget(self._content_widget)
        layout.addWidget(self._scroll)

        self.setStyleSheet("QWidget { background-color: #0f0f1a; }")

    def _move_to_target_monitor(self):
        """Move window to the target monitor (non-shared screen)."""
        screens = QApplication.screens()
        if self._target_monitor < len(screens):
            target_screen = screens[self._target_monitor]
        elif len(screens) > 1:
            target_screen = screens[-1]
        else:
            target_screen = screens[0]

        geo = target_screen.availableGeometry()
        self.move(geo.x() + 50, geo.y() + 50)

    def show_answer(self, answer: str):
        """Display a new answer in the window."""
        self.answer_received.emit(answer)

    def _on_answer(self, answer: str):
        self._answers.append(answer)
        self._add_answer_card(answer, len(self._answers))

        if not self.isVisible():
            self.show()

        QApplication.processEvents()
        scrollbar = self._scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _add_answer_card(self, text: str, number: int):
        """Add a note entry to the scrollable area."""
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #252525; border-radius: 4px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 10, 6)

        text_display = QTextEdit()
        text_display.setReadOnly(True)
        text_display.setPlainText(text)
        text_display.setFont(QFont("Segoe UI", 9))
        text_display.setStyleSheet(
            "QTextEdit { background: transparent; color: #d4d4d4; border: none; }"
        )

        doc_height = text_display.document().size().height()
        text_display.setFixedHeight(min(int(doc_height) + 16, 400))

        card_layout.addWidget(text_display)
        self._content_layout.addWidget(card)

    def clear(self):
        """Clear all answers from the display."""
        self._answers.clear()
        while self._content_layout.count():
            child = self._content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
