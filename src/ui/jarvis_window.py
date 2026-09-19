"""Jarvis OS HUD — clock, weather, daily tasks, core ring, voice."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ui.dashboard_data import (
    DailyTaskStore,
    fetch_weather,
    format_now,
    geocode_city,
    hud_spoken_reply,
)


class _Bridge(QObject):
    reply_ready = pyqtSignal(str)
    status_ready = pyqtSignal(str)
    transcript_ready = pyqtSignal(str)
    weather_ready = pyqtSignal(dict)
    weather_error = pyqtSignal(str)
    hearing_ready = pyqtSignal(str)
    train_done = pyqtSignal(str)
    speech_idle = pyqtSignal()
    perf_ready = pyqtSignal(str)
    task_progress_ready = pyqtSignal(dict)
    # Phase 7: approval lifecycle events
    approval_show = pyqtSignal(dict)     # show a new approval request
    approval_update = pyqtSignal(dict)   # update status of current approval
    approval_dismiss = pyqtSignal()      # dismiss approval panel


class JarvisRingWidget(QWidget):
    """Animated HUD core with J.A.R.V.I.S. branding."""

    def __init__(self, accent: str = "cyan", parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._angle = 0.0
        self._pulse = 0.0
        self._mode = "idle"
        self._accent = accent
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def _palette(self) -> QColor:
        if self._accent == "red":
            base = {
                "idle": QColor(255, 40, 60, 220),
                "listening": QColor(255, 80, 90, 240),
                "working": QColor(255, 30, 50, 255),
                "speaking": QColor(255, 100, 110, 230),
                "error": QColor(255, 200, 60, 230),
            }
        else:
            base = {
                "idle": QColor(0, 210, 255, 220),
                "listening": QColor(0, 255, 200, 240),
                "working": QColor(0, 180, 255, 255),
                "speaking": QColor(80, 200, 255, 230),
                "error": QColor(255, 60, 80, 230),
            }
        return base.get(self._mode, base["idle"])

    def _tick(self) -> None:
        speed = {"idle": 0.7, "listening": 2.2, "working": 4.0, "speaking": 1.5, "error": 0.4}.get(
            self._mode, 1.0
        )
        self._angle = (self._angle + speed) % 360
        self._pulse = (self._pulse + 0.08) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) * 0.40
        accent = self._palette()
        pulse = 1.0 + (0.08 if self._mode == "listening" else 0.04) * math.sin(self._pulse)

        grad = QRadialGradient(QPointF(cx, cy), radius * 1.4)
        grad.setColorAt(0.0, QColor(40, 8, 12, 200) if self._accent == "red" else QColor(8, 28, 48, 200))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), radius * 1.25, radius * 1.25)

        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 90), 1.5))
        painter.drawEllipse(QPointF(cx, cy), radius * 1.08 * pulse, radius * 1.08 * pulse)

        painter.setPen(QPen(accent, 4))
        for start in (self._angle, self._angle + 130, self._angle + 250):
            painter.drawArc(
                int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2),
                int(start * 16), int(70 * 16),
            )

        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 160), 2))
        for i in range(36):
            a = math.radians(i * 10 - self._angle)
            inner, outer = radius * 0.78, radius * (0.86 if i % 3 else 0.92)
            painter.drawLine(
                QPointF(cx + math.cos(a) * inner, cy + math.sin(a) * inner),
                QPointF(cx + math.cos(a) * outer, cy + math.sin(a) * outer),
            )

        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 180), 2))
        painter.drawEllipse(QPointF(cx, cy), radius * 0.52, radius * 0.52)

        painter.setPen(QColor(245, 248, 255))
        font = QFont("Segoe UI", 16, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.5)
        painter.setFont(font)
        text = "J.A.R.V.I.S."
        tw = painter.fontMetrics().horizontalAdvance(text)
        painter.drawText(QPointF(cx - tw / 2, cy + 6), text)

        painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 210))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        cap = self._mode.upper()
        cw = painter.fontMetrics().horizontalAdvance(cap)
        painter.drawText(QPointF(cx - cw / 2, cy + radius * 0.28), cap)


def _panel() -> QFrame:
    frame = QFrame()
    frame.setObjectName("panel")
    return frame


class ApprovalHUD(QFrame):
    """Phase 7 visual approval panel that appears inside the right column.

    Shows human-readable details about a pending approval and provides
    Approve / Deny buttons.  Voice and HUD share the same ApprovalRequest
    state — when voice says "yes", the HUD updates accordingly.
    """

    approved = pyqtSignal(str)   # emits approval_id
    denied = pyqtSignal(str)     # emits approval_id

    def __init__(self, accent: str = "red", parent=None):
        super().__init__(parent)
        self.setObjectName("approvalPanel")
        self._accent = accent
        self._approval_id: str = ""
        self._apply_style()
        self._build()
        self.setVisible(False)

    def _apply_style(self) -> None:
        if self._accent == "red":
            self.setStyleSheet("""
                QFrame#approvalPanel {
                    background: rgba(60, 10, 18, 210);
                    border: 2px solid rgba(255, 80, 100, 200);
                    border-radius: 10px;
                    padding: 4px;
                }
                QLabel#approvalTitle {
                    color: #ff5a6e; font-size: 13px; font-weight: 700;
                    letter-spacing: 2px;
                }
                QLabel#approvalField { color: #ffd6db; font-size: 12px; }
                QLabel#approvalValue { color: #ffffff; font-size: 12px; font-weight: 600; }
                QLabel#approvalStatus {
                    color: #ff8a97; font-size: 11px; font-weight: 600;
                    letter-spacing: 1px;
                }
                QPushButton#approveBtn {
                    background: rgba(40, 180, 80, 160);
                    border: 1px solid rgba(60, 220, 100, 200);
                    border-radius: 6px; color: #ffffff;
                    padding: 8px 18px; font-weight: 700; font-size: 13px;
                }
                QPushButton#approveBtn:hover { background: rgba(40, 200, 90, 200); }
                QPushButton#approveBtn:disabled { background: rgba(60,60,60,120); color: #888; border-color: #555; }
                QPushButton#denyBtn {
                    background: rgba(180, 30, 50, 140);
                    border: 1px solid rgba(255, 70, 90, 180);
                    border-radius: 6px; color: #ffd0d8;
                    padding: 8px 18px; font-weight: 700; font-size: 13px;
                }
                QPushButton#denyBtn:hover { background: rgba(200, 40, 60, 180); }
                QPushButton#denyBtn:disabled { background: rgba(60,60,60,120); color: #888; border-color: #555; }
            """)
        else:
            self.setStyleSheet("""
                QFrame#approvalPanel {
                    background: rgba(10, 30, 55, 220);
                    border: 2px solid rgba(0, 200, 255, 180);
                    border-radius: 10px;
                    padding: 4px;
                }
                QLabel#approvalTitle {
                    color: #5ce1ff; font-size: 13px; font-weight: 700;
                    letter-spacing: 2px;
                }
                QLabel#approvalField { color: #c8e7f5; font-size: 12px; }
                QLabel#approvalValue { color: #ffffff; font-size: 12px; font-weight: 600; }
                QLabel#approvalStatus {
                    color: #5ce1ff; font-size: 11px; font-weight: 600;
                    letter-spacing: 1px;
                }
                QPushButton#approveBtn {
                    background: rgba(40, 180, 80, 160);
                    border: 1px solid rgba(60, 220, 100, 200);
                    border-radius: 6px; color: #ffffff;
                    padding: 8px 18px; font-weight: 700; font-size: 13px;
                }
                QPushButton#approveBtn:hover { background: rgba(40, 200, 90, 200); }
                QPushButton#approveBtn:disabled { background: rgba(60,60,60,120); color: #888; border-color: #555; }
                QPushButton#denyBtn {
                    background: rgba(180, 30, 50, 140);
                    border: 1px solid rgba(0, 200, 255, 120);
                    border-radius: 6px; color: #c8e7f5;
                    padding: 8px 18px; font-weight: 700; font-size: 13px;
                }
                QPushButton#denyBtn:hover { background: rgba(200, 40, 60, 180); }
                QPushButton#denyBtn:disabled { background: rgba(60,60,60,120); color: #888; border-color: #555; }
            """)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        self._title = QLabel("APPROVAL REQUIRED")
        self._title.setObjectName("approvalTitle")
        lay.addWidget(self._title)

        grid = QGridLayout()
        grid.setSpacing(4)

        self._lbl_action = QLabel("Action:")
        self._lbl_action.setObjectName("approvalField")
        self._val_action = QLabel("")
        self._val_action.setObjectName("approvalValue")
        self._val_action.setWordWrap(True)
        grid.addWidget(self._lbl_action, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._val_action, 0, 1)

        self._lbl_target = QLabel("Target:")
        self._lbl_target.setObjectName("approvalField")
        self._val_target = QLabel("")
        self._val_target.setObjectName("approvalValue")
        self._val_target.setWordWrap(True)
        grid.addWidget(self._lbl_target, 1, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._val_target, 1, 1)

        self._lbl_consequence = QLabel("Effect:")
        self._lbl_consequence.setObjectName("approvalField")
        self._val_consequence = QLabel("")
        self._val_consequence.setObjectName("approvalValue")
        self._val_consequence.setWordWrap(True)
        grid.addWidget(self._lbl_consequence, 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._val_consequence, 2, 1)

        self._lbl_reversible = QLabel("Undo:")
        self._lbl_reversible.setObjectName("approvalField")
        self._val_reversible = QLabel("")
        self._val_reversible.setObjectName("approvalValue")
        grid.addWidget(self._lbl_reversible, 3, 0)
        grid.addWidget(self._val_reversible, 3, 1)

        self._lbl_risk = QLabel("Risk:")
        self._lbl_risk.setObjectName("approvalField")
        self._val_risk = QLabel("")
        self._val_risk.setObjectName("approvalValue")
        grid.addWidget(self._lbl_risk, 4, 0)
        grid.addWidget(self._val_risk, 4, 1)

        lay.addLayout(grid)

        # Status line (shows PENDING / APPROVED / DENIED / EXPIRED)
        self._status_line = QLabel("")
        self._status_line.setObjectName("approvalStatus")
        self._status_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._status_line)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._approve_btn = QPushButton("Approve")
        self._approve_btn.setObjectName("approveBtn")
        self._approve_btn.clicked.connect(self._on_approve)
        self._deny_btn = QPushButton("Deny")
        self._deny_btn.setObjectName("denyBtn")
        self._deny_btn.clicked.connect(self._on_deny)
        btn_row.addWidget(self._approve_btn)
        btn_row.addWidget(self._deny_btn)
        lay.addLayout(btn_row)

    def show_approval(self, data: dict) -> None:
        """Display a new approval request."""
        self._approval_id = data.get("id", "")
        self._val_action.setText(data.get("title", "") or data.get("tool", ""))
        self._val_target.setText(data.get("target", "") or "—")
        self._val_consequence.setText(data.get("consequence", "") or "—")
        reversible = data.get("reversible", True)
        self._val_reversible.setText("Yes — can be undone" if reversible else "No — cannot be undone")
        risk = int(data.get("risk_level", 0))
        risk_labels = {0: "Low", 1: "Low", 2: "Medium", 3: "High", 4: "Critical"}
        self._val_risk.setText(risk_labels.get(risk, f"Level {risk}"))
        self._status_line.setText("⏳  PENDING")
        self._approve_btn.setEnabled(True)
        self._deny_btn.setEnabled(True)
        self.setVisible(True)

    def update_status(self, data: dict) -> None:
        """Update the approval status (e.g. after voice approval or expiry)."""
        status = data.get("status", "").upper()
        approval_id = data.get("id", "")

        # Only update if this matches the currently displayed approval
        if approval_id and approval_id != self._approval_id:
            return

        icons = {
            "APPROVED": "✅  APPROVED",
            "DENIED": "❌  DENIED",
            "EXPIRED": "⏰  EXPIRED",
            "CANCELLED": "🚫  CANCELLED",
            "INVALIDATED": "⚠️  INVALIDATED",
            "EXECUTED": "✅  EXECUTED",
        }
        self._status_line.setText(icons.get(status, status))
        self._approve_btn.setEnabled(False)
        self._deny_btn.setEnabled(False)

        # Auto-dismiss after a short delay
        QTimer.singleShot(3000, self._auto_dismiss)

    def dismiss(self) -> None:
        """Hide the approval panel."""
        self._approval_id = ""
        self.setVisible(False)

    def _auto_dismiss(self) -> None:
        """Dismiss if buttons are already disabled (resolved)."""
        if not self._approve_btn.isEnabled():
            self.dismiss()

    def _on_approve(self) -> None:
        if self._approval_id:
            self._approve_btn.setEnabled(False)
            self._deny_btn.setEnabled(False)
            self.approved.emit(self._approval_id)

    def _on_deny(self) -> None:
        if self._approval_id:
            self._approve_btn.setEnabled(False)
            self._deny_btn.setEnabled(False)
            self.denied.emit(self._approval_id)


class JarvisWindow(QMainWindow):
    """Full Jarvis OS dashboard inspired by the reference HUD screenshots."""

    def __init__(
        self,
        send_command: Callable[[str], dict],
        emergency_stop: Callable[[], dict],
        toggle_voice: Optional[Callable[[], bool]] = None,
        speak: Optional[Callable[[str], None]] = None,
        train_voice: Optional[Callable[[], dict]] = None,
        resume_voice: Optional[Callable[[], None]] = None,
        stop_speaking: Optional[Callable[[], None]] = None,
        config: Optional[dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._send_command = send_command
        self._emergency_stop = emergency_stop
        self._toggle_voice = toggle_voice
        self._speak = speak
        self._train_voice = train_voice
        self._resume_voice = resume_voice
        self._stop_speaking = stop_speaking
        self._config = config or {}
        dash = (self._config.get("dashboard") or {})
        self._accent = dash.get("accent", "red")  # match SHIELD-style red by default
        self._busy = False
        self._voice_on = False
        self._hearing_override = ""
        self._pending_voice = ""
        self._pending_spoken = ""
        self._resume_when_idle = False
        self._last_weather: Optional[dict] = None
        self._stt_confidence = 1.0
        self._pending_stt_confidence = 1.0
        self._bridge = _Bridge()
        queued = Qt.ConnectionType.QueuedConnection
        self._bridge.reply_ready.connect(self._on_reply, queued)
        self._bridge.transcript_ready.connect(self._on_transcript, queued)
        self._bridge.weather_ready.connect(self._on_weather, queued)
        self._bridge.weather_error.connect(self._on_weather_error, queued)
        self._bridge.hearing_ready.connect(self._apply_hearing, queued)
        self._bridge.train_done.connect(self._on_train_done, queued)
        self._bridge.speech_idle.connect(self._on_speech_idle, queued)
        self._bridge.perf_ready.connect(self._on_perf, queued)
        self._bridge.task_progress_ready.connect(self._apply_task_progress, queued)
        # Phase 7 approval signals
        self._bridge.approval_show.connect(self._on_approval_show, queued)
        self._bridge.approval_update.connect(self._on_approval_update, queued)
        self._bridge.approval_dismiss.connect(self._on_approval_dismiss, queued)

        data_dir = Path(self._config.get("agent", {}).get("data_dir") or "data")
        if not data_dir.is_absolute():
            data_dir = Path(__file__).resolve().parents[2] / data_dir
        self._tasks = DailyTaskStore(data_dir / "daily_tasks.json")

        self.setWindowTitle("J.A.R.V.I.S. OS")
        self.setMinimumSize(1100, 720)
        self.resize(1280, 820)
        self._apply_theme()

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        outer.addLayout(self._build_top_bar())
        outer.addLayout(self._build_main_row(), stretch=1)
        outer.addLayout(self._build_bottom())

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._refresh_clock)
        self._clock_timer.start(1000)
        self._refresh_clock()
        self._rebuild_tasks()

        self._append("system", "Systems online. Dashboard linked.")
        QTimer.singleShot(200, self._load_weather)
        # Delayed greeting so TTS thread + audio device are ready
        QTimer.singleShot(900, self._greet)

    def _apply_theme(self) -> None:
        if self._accent == "red":
            css = """
            QMainWindow, QWidget#root { background: #050308; color: #ffe8ea; }
            QFrame#panel {
                background: rgba(30, 6, 10, 180);
                border: 1px solid rgba(255, 50, 70, 110);
                border-radius: 10px;
            }
            QLabel#brand { color: #ff4d62; font-size: 13px; font-weight: 700; letter-spacing: 2px; }
            QLabel#clock { color: #ffffff; font-size: 42px; font-weight: 700; }
            QLabel#dateLbl { color: #ff8a97; font-size: 14px; letter-spacing: 1px; }
            QLabel#panelTitle { color: #ff5a6e; font-size: 12px; font-weight: 700; letter-spacing: 2px; }
            QLabel#weatherTemp { color: #ffffff; font-size: 36px; font-weight: 700; }
            QLabel#weatherMeta { color: #ffb3bb; font-size: 13px; }
            QLabel#status { color: #ff6b7d; font-size: 12px; letter-spacing: 2px; }
            QCheckBox { color: #ffd6db; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QTextEdit, QLineEdit {
                background: rgba(20, 4, 8, 200);
                border: 1px solid rgba(255, 60, 80, 100);
                border-radius: 8px; color: #ffe8ea; padding: 8px; font-size: 13px;
            }
            QPushButton {
                background: rgba(255, 40, 60, 35);
                border: 1px solid rgba(255, 70, 90, 140);
                border-radius: 8px; color: #ffc2c9; padding: 9px 12px; font-weight: 600;
            }
            QPushButton:hover { background: rgba(255, 40, 60, 70); }
            QPushButton#micOn { background: rgba(255, 40, 60, 110); color: white; }
            QPushButton#stopBtn { background: rgba(120, 0, 20, 160); border-color: #ff4060; color: #ffd0d8; }
            """
        else:
            css = """
            QMainWindow, QWidget#root { background: #030912; color: #d7f3ff; }
            QFrame#panel {
                background: rgba(6, 20, 36, 180);
                border: 1px solid rgba(0, 200, 255, 90);
                border-radius: 10px;
            }
            QLabel#brand { color: #5ce1ff; font-size: 13px; font-weight: 700; letter-spacing: 2px; }
            QLabel#clock { color: #ffffff; font-size: 42px; font-weight: 700; }
            QLabel#dateLbl { color: #7ad7ff; font-size: 14px; letter-spacing: 1px; }
            QLabel#panelTitle { color: #5ce1ff; font-size: 12px; font-weight: 700; letter-spacing: 2px; }
            QLabel#weatherTemp { color: #ffffff; font-size: 36px; font-weight: 700; }
            QLabel#weatherMeta { color: #9fe9ff; font-size: 13px; }
            QLabel#status { color: #5ce1ff; font-size: 12px; letter-spacing: 2px; }
            QCheckBox { color: #c8e7f5; spacing: 8px; }
            QTextEdit, QLineEdit {
                background: rgba(8, 24, 40, 200);
                border: 1px solid rgba(0, 200, 255, 90);
                border-radius: 8px; color: #e8f7ff; padding: 8px; font-size: 13px;
            }
            QPushButton {
                background: rgba(0, 140, 180, 40);
                border: 1px solid rgba(0, 210, 255, 120);
                border-radius: 8px; color: #9fe9ff; padding: 9px 12px; font-weight: 600;
            }
            QPushButton:hover { background: rgba(0, 180, 220, 70); }
            QPushButton#micOn { background: rgba(0, 200, 160, 80); color: #eafffa; }
            QPushButton#stopBtn { background: rgba(180, 30, 50, 90); border-color: #ff4060; color: #ffd0d8; }
            """
        self.setStyleSheet(css)

    def _build_top_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        brand = QLabel("J.A.R.V.I.S. OS  ·  LOCAL AGENT")
        brand.setObjectName("brand")
        row.addWidget(brand)
        row.addStretch(1)

        clock_box = QVBoxLayout()
        clock_box.setSpacing(0)
        self._clock = QLabel("--:--")
        self._clock.setObjectName("clock")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._date = QLabel("")
        self._date.setObjectName("dateLbl")
        self._date.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_box.addWidget(self._clock)
        clock_box.addWidget(self._date)
        row.addLayout(clock_box)
        return row

    def _build_main_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        # LEFT — tasks
        left = _panel()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(14, 12, 14, 12)
        title = QLabel("TODAY'S TASKS")
        title.setObjectName("panelTitle")
        left_l.addWidget(title)

        self._task_host = QVBoxLayout()
        self._task_host.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        wrap = QWidget()
        wrap.setLayout(self._task_host)
        scroll.setWidget(wrap)
        left_l.addWidget(scroll, stretch=1)

        add_row = QHBoxLayout()
        self._task_input = QLineEdit()
        self._task_input.setPlaceholderText("Add a task for today…")
        self._task_input.returnPressed.connect(self._add_task)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_task)
        add_row.addWidget(self._task_input, stretch=1)
        add_row.addWidget(add_btn)
        left_l.addLayout(add_row)
        left.setMinimumWidth(280)
        row.addWidget(left, stretch=2)

        # CENTER — ring + status
        center = QVBoxLayout()
        self._ring = JarvisRingWidget(accent=self._accent)
        center.addWidget(self._ring, stretch=1)
        self._status = QLabel("ONLINE — AWAITING COMMAND")
        self._status.setObjectName("status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self._status)
        row.addLayout(center, stretch=3)

        # RIGHT — weather + system
        right = _panel()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(14, 12, 14, 12)
        wtitle = QLabel("LOCAL CONDITIONS")
        wtitle.setObjectName("panelTitle")
        right_l.addWidget(wtitle)
        self._weather_temp = QLabel("--°")
        self._weather_temp.setObjectName("weatherTemp")
        right_l.addWidget(self._weather_temp)
        self._weather_meta = QLabel("Fetching weather…")
        self._weather_meta.setObjectName("weatherMeta")
        self._weather_meta.setWordWrap(True)
        right_l.addWidget(self._weather_meta)
        right_l.addSpacing(16)

        self._task_title = QLabel("CURRENT TASK")
        self._task_title.setObjectName("panelTitle")
        self._task_title.setVisible(False)
        right_l.addWidget(self._task_title)
        self._task_progress = QLabel("")
        self._task_progress.setObjectName("weatherMeta")
        self._task_progress.setWordWrap(True)
        self._task_progress.setVisible(False)
        right_l.addWidget(self._task_progress)

        # Phase 7: Approval HUD
        self._approval_hud = ApprovalHUD(accent=self._accent)
        self._approval_hud.approved.connect(self._on_hud_approve)
        self._approval_hud.denied.connect(self._on_hud_deny)
        right_l.addWidget(self._approval_hud)

        stitle = QLabel("SYSTEM")
        stitle.setObjectName("panelTitle")
        right_l.addWidget(stitle)
        self._system_meta = QLabel(
            "Agent: local Ollama\n"
            "STT: local Whisper (on-device)\n"
            "Computer control: enabled\n"
            "Nothing leaves this PC by default"
        )
        self._system_meta.setObjectName("weatherMeta")
        self._system_meta.setWordWrap(True)
        right_l.addWidget(self._system_meta)
        self._perf = QLabel("")
        self._perf.setObjectName("weatherMeta")
        self._perf.setWordWrap(True)
        self._perf.setVisible(bool((self._config.get("agent") or {}).get("perf_debug", True)))
        right_l.addWidget(self._perf)
        right_l.addStretch(1)
        right.setMinimumWidth(260)
        row.addWidget(right, stretch=2)
        return row

    def _build_bottom(self) -> QVBoxLayout:
        col = QVBoxLayout()
        self._chat = QTextEdit()
        self._chat.setReadOnly(True)
        self._chat.setMaximumHeight(140)
        col.addWidget(self._chat)

        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText('Command…  e.g. "Open Chrome" or tap Voice')
        self._input.returnPressed.connect(self._on_send)
        row.addWidget(self._input, stretch=1)
        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._on_send)
        row.addWidget(self._send_btn)
        col.addLayout(row)

        actions = QHBoxLayout()
        self._mic_btn = QPushButton("Voice")
        self._mic_btn.clicked.connect(self._on_mic)
        actions.addWidget(self._mic_btn)
        self._train_btn = QPushButton("Train Voice")
        self._train_btn.clicked.connect(self._on_train_voice)
        actions.addWidget(self._train_btn)
        pause = QPushButton("Pause")
        pause.clicked.connect(lambda: self._quick("pause"))
        actions.addWidget(pause)
        cont = QPushButton("Continue")
        cont.clicked.connect(lambda: self._quick("continue"))
        actions.addWidget(cont)
        stop = QPushButton("Stop")
        stop.setObjectName("stopBtn")
        stop.clicked.connect(self._on_stop)
        actions.addWidget(stop)
        col.addLayout(actions)
        return col

    def _greet(self) -> None:
        now = format_now()
        remaining = len(self._tasks.remaining())
        msg = f"Jarvis online. It is {now['time']}. You have {remaining} tasks remaining today."
        self._append("assistant", msg)
        if self._speak:
            self._speak(msg)
        self.set_agent_state("speaking")
        QTimer.singleShot(3500, lambda: self.set_agent_state("idle"))

    def _refresh_clock(self) -> None:
        info = format_now()
        self._clock.setText(info["time"])
        self._date.setText(info["date"].upper())

    def _load_weather(self) -> None:
        dash = self._config.get("dashboard") or {}
        if not dash.get("weather_enabled", True):
            self._weather_temp.setText("--")
            self._weather_meta.setText("Weather disabled (local-only mode)")
            return

        def worker():
            try:
                city = dash.get("city") or "Los Angeles"
                unit = dash.get("temperature_unit") or "fahrenheit"
                lat = dash.get("latitude")
                lon = dash.get("longitude")
                label = dash.get("location_label") or city
                if lat is None or lon is None:
                    geo = geocode_city(city)
                    if not geo:
                        self._bridge.weather_error.emit(f"Could not find city: {city}")
                        return
                    lat, lon, label = geo["latitude"], geo["longitude"], geo["label"]
                data = fetch_weather(float(lat), float(lon), temperature_unit=unit, location_label=label)
                self._bridge.weather_ready.emit(data)
            except Exception as e:
                self._bridge.weather_error.emit(str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_weather(self, data: dict) -> None:
        self._last_weather = data
        temp = data.get("temperature")
        unit = data.get("unit") or "°F"
        self._weather_temp.setText(f"{temp}{unit}" if temp is not None else "--")
        self._weather_meta.setText(
            f"{data.get('location')}\n"
            f"{data.get('condition')} · Humidity {data.get('humidity')}% · "
            f"Wind {data.get('wind_mph')} mph"
        )

    def _on_weather_error(self, err: str) -> None:
        self._weather_temp.setText("--")
        self._weather_meta.setText(f"Weather unavailable\n{err}")

    def _rebuild_tasks(self) -> None:
        while self._task_host.count():
            item = self._task_host.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for task in self._tasks.tasks:
            cb = QCheckBox(task.get("text", ""))
            cb.setChecked(bool(task.get("done")))
            tid = task["id"]
            cb.toggled.connect(lambda checked, i=tid: self._toggle_task(i))
            if task.get("done"):
                cb.setStyleSheet("color: #887070; text-decoration: line-through;")
            self._task_host.addWidget(cb)
        self._task_host.addStretch(1)

    def _toggle_task(self, task_id: str) -> None:
        self._tasks.toggle(task_id)
        self._rebuild_tasks()

    def _add_task(self) -> None:
        text = self._task_input.text().strip()
        if not text:
            return
        self._tasks.add(text)
        self._task_input.clear()
        self._rebuild_tasks()

    def set_agent_state(self, state: str) -> None:
        mapping = {
            "idle": "idle",
            "listening": "listening",
            "understanding": "working",
            "planning": "working",
            "working": "working",
            "speaking": "speaking",
            "paused": "idle",
            "error": "error",
            "waiting_for_user": "listening",
            "waiting_for_approval": "listening",
        }
        self._ring.set_mode(mapping.get(state, "idle"))
        labels = {
            "idle": "ONLINE — AWAITING COMMAND",
            "listening": "LISTENING…",
            "understanding": "TRANSCRIBING…",
            "planning": "PLANNING…",
            "working": "EXECUTING…",
            "speaking": "SPEAKING…",
            "paused": "PAUSED",
            "error": "ERROR",
            "waiting_for_user": "WAITING FOR YOU",
            "waiting_for_approval": "APPROVAL REQUIRED",
        }
        if not self._hearing_override:
            self._status.setText(labels.get(state, state.upper()))

    def set_task_progress(self, progress: dict) -> None:
        """Phase 6 task progress — safe to call from agent threads."""
        self._bridge.task_progress_ready.emit(dict(progress or {}))

    def _apply_task_progress(self, progress: dict) -> None:
        steps = progress.get("steps") or []
        status = str(progress.get("status") or "")
        if not steps or status in {"COMPLETED", "CANCELLED", "FAILED"}:
            self._task_title.setVisible(False)
            self._task_progress.setVisible(False)
            return
        marks = {
            "COMPLETED": "\u2713",
            "RUNNING": "\u2192",
            "VERIFYING": "\u2192",
            "SKIPPED": "\u2013",
            "FAILED": "\u2717",
            "BLOCKED": "!",
        }
        lines = [str(progress.get("title") or "")[:60], ""]
        for step in steps:
            mark = marks.get(str(step.get("status")), "\u25cb")
            lines.append(f"{mark} {str(step.get('description') or '')[:52]}")
        self._task_title.setVisible(True)
        self._task_progress.setVisible(True)
        self._task_progress.setText("\n".join(lines))

    def set_hearing(self, text: str) -> None:
        """Live mic feedback — safe to call from audio threads."""
        self._bridge.hearing_ready.emit(text or "")

    def _apply_hearing(self, text: str) -> None:
        self._hearing_override = text or ""
        if text:
            self._status.setText(text[:140])
        elif self._voice_on:
            self._status.setText("LISTENING…")
        else:
            self._status.setText("ONLINE — AWAITING COMMAND")

    def on_voice_transcript(self, text: str, stt_confidence: float = 1.0) -> None:
        self._pending_stt_confidence = float(stt_confidence)
        self._bridge.transcript_ready.emit(text)

    def on_speech_idle(self) -> None:
        self._bridge.speech_idle.emit()

    def _on_transcript(self, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        print(f"[Jarvis] Voice command queued: {cleaned}")
        self._stt_confidence = float(getattr(self, "_pending_stt_confidence", 1.0))
        if self._busy:
            self._pending_voice = cleaned
            self._append("system", "Still working — I will take that next.")
            return
        self._input.setText(cleaned)
        self._on_send()

    def _append(self, role: str, text: str) -> None:
        colors = {"user": "#ffb3bb", "assistant": "#ffffff", "system": "#c08088"}
        if self._accent != "red":
            colors = {"user": "#7ad7ff", "assistant": "#5cffc8", "system": "#6a8aa0"}
        color = colors.get(role, "#ffe8ea")
        label = {"user": "YOU", "assistant": "JARVIS", "system": "SYS"}.get(role, role)
        esc = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self._chat.append(
            f'<p style="margin:4px 0;"><b style="color:{color};">{label}:</b> {esc}</p>'
        )
        self._chat.moveCursor(QTextCursor.MoveOperation.End)

    def _on_reply(self, message: str) -> None:
        self._busy = False
        self._send_btn.setEnabled(True)
        self._input.setEnabled(True)
        if message:
            self._append("assistant", message)
            self.set_agent_state("speaking")
            spoken = getattr(self, "_pending_spoken", "") or message
            self._pending_spoken = ""
            if self._speak:
                self._speak(spoken)
                self._resume_when_idle = bool(self._voice_on)
                QTimer.singleShot(20000, self._resume_listening_fallback)
            else:
                self._finish_reply_state()
        else:
            self._finish_reply_state()
        self._input.setFocus()

    def _on_perf(self, line: str) -> None:
        if hasattr(self, "_perf") and line:
            self._perf.setText(line)

    def _on_speech_idle(self) -> None:
        if self._resume_when_idle and not self._busy:
            self._finish_reply_state()

    def _resume_listening_fallback(self) -> None:
        if self._resume_when_idle and not self._busy:
            self._finish_reply_state()

    def _finish_reply_state(self) -> None:
        self._resume_when_idle = False
        if self._voice_on and self._resume_voice:
            self._resume_voice()
        self.set_agent_state("listening" if self._voice_on else "idle")
        if self._pending_voice and not self._busy:
            nxt = self._pending_voice
            self._pending_voice = ""
            self._input.setText(nxt)
            self._on_send()

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._append("user", text)

        local = hud_spoken_reply(text, self._tasks, self._last_weather, stt_confidence=getattr(self, "_stt_confidence", 1.0))
        if local is not None:
            print(f"[Jarvis] Local HUD reply: {local}")
            self._rebuild_tasks()
            self._on_reply(local)
            self._stt_confidence = 1.0
            self._pending_stt_confidence = 1.0
            return

        self._cmd_gen = getattr(self, "_cmd_gen", 0) + 1
        my_gen = self._cmd_gen
        self._busy = True
        self._send_btn.setEnabled(False)
        self._input.setEnabled(False)
        self.set_agent_state("working")
        print(f"[Jarvis] Sending to agent: {text}")
        if self._stop_speaking:
            try:
                self._stop_speaking()
            except Exception:
                pass

        def worker():
            line = ""
            try:
                try:
                    result = self._send_command(text, stt_confidence=getattr(self, "_stt_confidence", 1.0)) or {}
                except TypeError:
                    result = self._send_command(text) or {}
                if my_gen != self._cmd_gen or result.get("stale"):
                    return
                # A silent acknowledgement carries no message and must stay silent.
                # Falling back to the raw result dict pushes internal state at the user
                # and trips the output boundary, which then speaks its error fallback.
                msg = "" if result.get("discard") else (result.get("message") or "")
                self._pending_spoken = "" if result.get("discard") else (result.get("spoken") or "")
                line = result.get("perf_line") or ""
            except Exception as e:
                msg = f"Error: {e}"
            if my_gen != self._cmd_gen:
                return
            if line:
                self._bridge.perf_ready.emit(line)
            self._bridge.reply_ready.emit(msg)

        threading.Thread(target=worker, daemon=True).start()

    def _quick(self, command: str) -> None:
        self._input.setText(command)
        self._on_send()

    def _on_train_voice(self) -> None:
        if not self._train_voice:
            self._append("system", "Voice training is not available.")
            return
        self._train_btn.setEnabled(False)
        self._append("system", "Training: stay quiet, then say a clear sentence when prompted.")
        if self._speak:
            self._speak("Calibration starting. Stay quiet, then speak when asked.")

        def worker():
            try:
                result = self._train_voice() or {}
                msg = result.get("message") or (
                    "Voice training complete." if result.get("ok") else "Voice training failed."
                )
            except Exception as e:
                msg = f"Training failed: {e}"
            self._bridge.train_done.emit(msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_train_done(self, msg: str) -> None:
        self._append("system", msg)
        self._train_btn.setEnabled(True)
        self.set_hearing(msg)

    def _on_mic(self) -> None:
        if not self._toggle_voice:
            self._append("system", "Voice capture is not available.")
            return
        try:
            active = bool(self._toggle_voice())
        except Exception as e:
            self._append("system", f"Voice error: {e}")
            return
        self._voice_on = active
        if active:
            self._mic_btn.setText("Listening")
            self._mic_btn.setObjectName("micOn")
            self._mic_btn.style().unpolish(self._mic_btn)
            self._mic_btn.style().polish(self._mic_btn)
            self.set_agent_state("listening")
            self._hearing_override = ""
            self._append("system", "Voice channel open — speak naturally, pause when finished.")
            # HUD shows LISTENING. Do not TTS "Listening." — it echoes into the mic.
        else:
            self._mic_btn.setText("Voice")
            self._mic_btn.setObjectName("")
            self._mic_btn.style().unpolish(self._mic_btn)
            self._mic_btn.style().polish(self._mic_btn)
            self._hearing_override = ""
            self.set_agent_state("idle")
            self._append("system", "Voice channel closed.")

    def _on_stop(self) -> None:
        try:
            result = self._emergency_stop() or {}
            msg = result.get("message") or "Emergency stop engaged."
        except Exception as e:
            msg = f"Stop failed: {e}"
        self._append("system", msg)
        self.set_agent_state("error")
        self._busy = False
        self._pending_voice = ""
        self._resume_when_idle = False
        self._send_btn.setEnabled(True)
        self._input.setEnabled(True)
        # Phase 7: dismiss approval on emergency stop
        self._approval_hud.update_status({"status": "CANCELLED"})
        if self._speak:
            self._speak("Stopping.")

    # ------------------------------------------------------------------ #
    # Phase 7: Approval HUD handlers
    # ------------------------------------------------------------------ #

    def show_approval(self, data: dict) -> None:
        """Thread-safe: show an approval request on the HUD.

        Called from agent threads via the bridge signal.
        """
        self._bridge.approval_show.emit(dict(data or {}))

    def update_approval(self, data: dict) -> None:
        """Thread-safe: update approval status on the HUD."""
        self._bridge.approval_update.emit(dict(data or {}))

    def dismiss_approval(self) -> None:
        """Thread-safe: dismiss the approval panel."""
        self._bridge.approval_dismiss.emit()

    def _on_approval_show(self, data: dict) -> None:
        self._approval_hud.show_approval(data)
        self.set_agent_state("waiting_for_approval")
        action = data.get("title", "") or data.get("tool", "")
        self._append("system", f"Approval required: {action}")

    def _on_approval_update(self, data: dict) -> None:
        self._approval_hud.update_status(data)
        status = data.get("status", "").upper()
        if status == "APPROVED":
            self.set_agent_state("working")
        elif status in ("DENIED", "CANCELLED", "EXPIRED", "INVALIDATED"):
            self.set_agent_state("idle")

    def _on_approval_dismiss(self) -> None:
        self._approval_hud.dismiss()

    def _on_hud_approve(self, approval_id: str) -> None:
        """User clicked Approve in the HUD."""
        self._append("user", "Approved.")

        def worker():
            try:
                result = self._send_command("yes") or {}
                msg = result.get("message", "")
                if msg:
                    self._bridge.reply_ready.emit(msg)
            except Exception as e:
                self._bridge.reply_ready.emit(f"Approval failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_hud_deny(self, approval_id: str) -> None:
        """User clicked Deny in the HUD."""
        self._append("user", "Denied.")

        def worker():
            try:
                result = self._send_command("no") or {}
                msg = result.get("message", "")
                if msg:
                    self._bridge.reply_ready.emit(msg)
            except Exception as e:
                self._bridge.reply_ready.emit(f"Denial failed: {e}")

        threading.Thread(target=worker, daemon=True).start()
