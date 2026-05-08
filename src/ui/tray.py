import threading

from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication, QInputDialog
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from pynput import keyboard

from .settings import SettingsDialog


class HotkeyListener(QObject):
    """Listens for global hotkeys in a background thread."""

    capture_triggered = pyqtSignal()
    audio_toggled = pyqtSignal()
    overlay_toggled = pyqtSignal()
    question_triggered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._listener = None

    def start(self):
        hotkeys = keyboard.GlobalHotKeys({
            "<ctrl>+<shift>+s": self._on_capture,
            "<ctrl>+<shift>+z": self._on_audio_toggle,
            "<ctrl>+<shift>+h": self._on_overlay_toggle,
            "<ctrl>+<shift>+q": self._on_question,
        })
        self._listener = hotkeys
        hotkeys.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _on_capture(self):
        self.capture_triggered.emit()

    def _on_audio_toggle(self):
        self.audio_toggled.emit()

    def _on_overlay_toggle(self):
        self.overlay_toggled.emit()

    def _on_question(self):
        self.question_triggered.emit()


class SystemTray(QObject):
    """System tray icon with menu and hotkey integration."""

    def __init__(self, app: QApplication, controller, config: dict):
        super().__init__()
        self._app = app
        self._controller = controller
        self._config = config

        self._tray = QSystemTrayIcon(self._create_icon(), app)
        self._setup_menu()
        self._setup_hotkeys()

        self._audio_active = False
        self._auto_capture_timer = None

    def _create_icon(self) -> QIcon:
        """Create a simple colored icon for the tray."""
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setBrush(QColor(0, 200, 100))
        painter.setPen(QColor(0, 150, 75))
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        return QIcon(pixmap)

    def _setup_menu(self):
        menu = QMenu()

        self._capture_action = QAction("Capture && Analyze (Ctrl+Shift+S)")
        self._capture_action.triggered.connect(self._on_capture)
        menu.addAction(self._capture_action)

        self._question_action = QAction("Ask Question (Ctrl+Shift+Q)")
        self._question_action.triggered.connect(self._on_question)
        menu.addAction(self._question_action)

        menu.addSeparator()

        self._audio_action = QAction("Start Audio Listening (Ctrl+Shift+Z)")
        self._audio_action.triggered.connect(self._on_audio_toggle)
        menu.addAction(self._audio_action)

        self._overlay_action = QAction("Toggle Overlay (Ctrl+Shift+H)")
        self._overlay_action.triggered.connect(self._on_overlay_toggle)
        menu.addAction(self._overlay_action)

        menu.addSeparator()

        auto_menu = menu.addMenu("Auto-Capture")
        self._auto_off = QAction("Off")
        self._auto_off.triggered.connect(lambda: self._set_auto_capture(0))
        auto_menu.addAction(self._auto_off)

        for seconds in [5, 10, 30, 60]:
            action = QAction(f"Every {seconds}s")
            action.triggered.connect(lambda checked, s=seconds: self._set_auto_capture(s))
            auto_menu.addAction(action)

        menu.addSeparator()

        output_menu = menu.addMenu("Output Mode")
        for mode in ["overlay", "clipboard", "second_monitor", "speech", "all"]:
            action = QAction(mode.replace("_", " ").title())
            action.triggered.connect(lambda checked, m=mode: self._set_output_mode(m))
            output_menu.addAction(action)

        menu.addSeparator()

        settings_action = QAction("Settings...")
        settings_action.triggered.connect(self._on_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("Quit")
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)

    def _setup_hotkeys(self):
        self._hotkey_listener = HotkeyListener()
        self._hotkey_listener.capture_triggered.connect(self._on_capture)
        self._hotkey_listener.audio_toggled.connect(self._on_audio_toggle)
        self._hotkey_listener.overlay_toggled.connect(self._on_overlay_toggle)
        self._hotkey_listener.question_triggered.connect(self._on_question)
        self._hotkey_listener.start()

    def show(self):
        self._tray.show()
        self._tray.showMessage(
            "Notes",
            "Ready.",
            QSystemTrayIcon.MessageIcon.Information,
            1500,
        )

    def _on_capture(self):
        """Capture screen and analyze."""
        print("[Hotkey] Ctrl+Shift+S pressed - capturing...")
        self._tray.setToolTip("Working...")
        threading.Thread(target=self._do_capture, daemon=True).start()

    def _do_capture(self):
        try:
            answer = self._controller.capture_and_analyze()
            self._tray.setToolTip("Notes")
        except Exception as e:
            print(f"[Error] Capture failed: {e}")
            self._tray.setToolTip("Notes")

    def _on_question(self):
        """Open quick question dialog."""
        text, ok = QInputDialog.getText(
            None, "Quick Question", "Ask about what's on screen:"
        )
        if ok and text.strip():
            threading.Thread(
                target=self._do_question, args=(text,), daemon=True
            ).start()

    def _do_question(self, question: str):
        try:
            self._controller.ask_question(question)
        except Exception as e:
            print(f"[Tray] Question error: {e}")

    def _on_audio_toggle(self):
        """Toggle audio listening."""
        is_active = self._controller.toggle_audio()
        self._audio_active = is_active
        if is_active:
            self._audio_action.setText("Stop Audio Listening (Ctrl+Shift+Z)")
            self._tray.showMessage("Audio", "Listening...", QSystemTrayIcon.MessageIcon.Information, 1000)
        else:
            self._audio_action.setText("Start Audio Listening (Ctrl+Shift+Z)")
            self._tray.showMessage("Audio", "Stopped", QSystemTrayIcon.MessageIcon.Information, 1000)

    def _on_overlay_toggle(self):
        """Toggle overlay visibility."""
        if self._controller.overlay:
            self._controller.overlay.toggle_visibility()

    def _set_auto_capture(self, interval_seconds: int):
        """Set up auto-capture timer."""
        if self._auto_capture_timer:
            self._auto_capture_timer.stop()
            self._auto_capture_timer = None

        if interval_seconds > 0:
            self._auto_capture_timer = QTimer()
            self._auto_capture_timer.timeout.connect(self._on_capture)
            self._auto_capture_timer.start(interval_seconds * 1000)

    def _on_settings(self):
        """Open the settings dialog."""
        dialog = SettingsDialog(self._config)
        if dialog.exec():
            self._config = dialog.get_config()
            self._controller.config = self._config

    def _set_output_mode(self, mode: str):
        """Change the output mode at runtime."""
        self._config["output"]["mode"] = mode
        self._controller.config["output"]["mode"] = mode

    def _quit(self):
        """Clean shutdown."""
        self._hotkey_listener.stop()
        self._controller.shutdown()
        if self._auto_capture_timer:
            self._auto_capture_timer.stop()
        self._tray.hide()
        self._app.quit()
