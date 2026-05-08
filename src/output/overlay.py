from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QApplication, QPushButton, QLabel,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QSize
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPen


class OverlayWindow(QWidget):
    """
    Disguised overlay that looks like a normal sticky note / notepad.
    Nothing about it suggests AI.
    """

    answer_received = pyqtSignal(str)

    POSITIONS = {
        "top_left": (0, 0),
        "top_right": (1, 0),
        "bottom_left": (0, 1),
        "bottom_right": (1, 1),
    }

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._opacity = config.get("overlay_opacity", 0.85)
        self._position = config.get("overlay_position", "bottom_right")
        self._visible = False
        self._minimized = False
        self._drag_pos: QPoint = QPoint()
        self._full_size = QSize(380, 220)

        self._setup_window()
        self._setup_ui()
        self.answer_received.connect(self._on_answer)

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(80, 28)
        self.resize(self._full_size)
        self._move_to_position()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setFixedHeight(26)
        self._header.setStyleSheet("background: #2b2b2b; border-top-left-radius: 6px; border-top-right-radius: 6px;")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 2, 4, 2)

        title = QLabel("Notes")
        title.setStyleSheet("color: #999; font-size: 11px; background: transparent;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self._minimize_btn = QPushButton("_")
        self._minimize_btn.setFixedSize(20, 16)
        self._minimize_btn.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: #888; border: none; border-radius: 2px; font-size: 11px; }"
            "QPushButton:hover { background: #555; }"
        )
        self._minimize_btn.clicked.connect(self._toggle_minimize)
        header_layout.addWidget(self._minimize_btn)

        self._clear_btn = QPushButton("x")
        self._clear_btn.setFixedSize(20, 16)
        self._clear_btn.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: #888; border: none; border-radius: 2px; font-size: 11px; }"
            "QPushButton:hover { background: #555; }"
        )
        self._clear_btn.clicked.connect(self.toggle_visibility)
        header_layout.addWidget(self._clear_btn)

        layout.addWidget(self._header)

        self._text_area = QTextEdit()
        self._text_area.setReadOnly(True)
        self._text_area.setFont(QFont("Segoe UI", 9))
        self._text_area.setStyleSheet(
            "QTextEdit { background: #1e1e1e; color: #d4d4d4; border: none; "
            "border-bottom-left-radius: 6px; border-bottom-right-radius: 6px; padding: 8px; }"
            "QScrollBar:vertical { background: #1e1e1e; width: 6px; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 3px; }"
        )
        layout.addWidget(self._text_area)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(10, 10, 10, 0)))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.drawRoundedRect(self.rect(), 6, 6)

    def _move_to_position(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.availableGeometry()
        x_factor, y_factor = self.POSITIONS.get(self._position, (1, 1))
        x = screen_geo.x() + int((screen_geo.width() - self.width()) * x_factor)
        y = screen_geo.y() + int((screen_geo.height() - self.height()) * y_factor)
        self.move(x, y)

    def show_answer(self, answer: str):
        self.answer_received.emit(answer)

    def _on_answer(self, answer: str):
        self._text_area.setPlainText(answer)
        if self._minimized:
            self._toggle_minimize()
        if not self._visible:
            self.show()
            self._visible = True

    def _toggle_minimize(self):
        if self._minimized:
            self.resize(self._full_size)
            self._text_area.show()
            self._minimize_btn.setText("_")
            self._minimized = False
        else:
            self._full_size = self.size()
            self._text_area.hide()
            self.resize(self.width(), 26)
            self._minimize_btn.setText("+")
            self._minimized = True

    def toggle_visibility(self):
        if self._visible:
            self.hide()
            self._visible = False
        else:
            self.show()
            self._visible = True

    def set_opacity(self, opacity: float):
        self._opacity = max(0.1, min(1.0, opacity))
        self.setWindowOpacity(self._opacity)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = QPoint()

    def clear(self):
        self._text_area.clear()
