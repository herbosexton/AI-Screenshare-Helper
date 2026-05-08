from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QSlider, QLineEdit, QPushButton,
    QGroupBox, QFormLayout, QCheckBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class SettingsDialog(QDialog):
    """Settings dialog for configuring the application."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Settings")
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)

        layout.addWidget(self._create_ai_group())
        layout.addWidget(self._create_capture_group())
        layout.addWidget(self._create_audio_group())
        layout.addWidget(self._create_output_group())
        layout.addWidget(self._create_humanize_group())

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def _create_ai_group(self) -> QGroupBox:
        group = QGroupBox("AI Provider")
        form = QFormLayout(group)

        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["anthropic", "openai"])
        self._provider_combo.setCurrentText(self.config["ai"]["provider"])
        form.addRow("Provider:", self._provider_combo)

        self._model_input = QLineEdit(self.config["ai"]["model"])
        form.addRow("Model:", self._model_input)

        self._fallback_combo = QComboBox()
        self._fallback_combo.addItems(["openai", "anthropic"])
        self._fallback_combo.setCurrentText(self.config["ai"]["fallback_provider"])
        form.addRow("Fallback:", self._fallback_combo)

        return group

    def _create_capture_group(self) -> QGroupBox:
        group = QGroupBox("Screen Capture")
        form = QFormLayout(group)

        self._capture_mode = QComboBox()
        self._capture_mode.addItems(["manual", "auto", "smart"])
        self._capture_mode.setCurrentText(self.config["capture"]["mode"])
        form.addRow("Mode:", self._capture_mode)

        self._interval_input = QLineEdit(str(self.config["capture"]["auto_interval_seconds"]))
        form.addRow("Auto interval (sec):", self._interval_input)

        return group

    def _create_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio")
        form = QFormLayout(group)

        self._audio_enabled = QCheckBox()
        self._audio_enabled.setChecked(self.config["audio"]["enabled"])
        form.addRow("Enabled:", self._audio_enabled)

        self._system_audio = QCheckBox()
        self._system_audio.setChecked(self.config["audio"]["system_audio"])
        form.addRow("System Audio:", self._system_audio)

        self._mic_audio = QCheckBox()
        self._mic_audio.setChecked(self.config["audio"]["microphone"])
        form.addRow("Microphone:", self._mic_audio)

        self._whisper_model = QComboBox()
        self._whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self._whisper_model.setCurrentText(self.config["audio"]["transcription_model"])
        form.addRow("Whisper Model:", self._whisper_model)

        return group

    def _create_output_group(self) -> QGroupBox:
        group = QGroupBox("Output")
        form = QFormLayout(group)

        self._output_mode = QComboBox()
        self._output_mode.addItems(["overlay", "clipboard", "second_monitor", "all"])
        self._output_mode.setCurrentText(self.config["output"]["mode"])
        form.addRow("Mode:", self._output_mode)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(10, 100)
        self._opacity_slider.setValue(int(self.config["output"]["overlay_opacity"] * 100))
        form.addRow("Overlay Opacity:", self._opacity_slider)

        self._position_combo = QComboBox()
        self._position_combo.addItems(["top_left", "top_right", "bottom_left", "bottom_right"])
        self._position_combo.setCurrentText(self.config["output"]["overlay_position"])
        form.addRow("Overlay Position:", self._position_combo)

        return group

    def _create_humanize_group(self) -> QGroupBox:
        group = QGroupBox("Humanization")
        form = QFormLayout(group)

        self._humanize_enabled = QCheckBox()
        self._humanize_enabled.setChecked(self.config["humanize"]["enabled"])
        form.addRow("Enabled:", self._humanize_enabled)

        self._style_combo = QComboBox()
        self._style_combo.addItems(["casual", "formal", "casual_technical"])
        self._style_combo.setCurrentText(self.config["humanize"]["style"])
        form.addRow("Style:", self._style_combo)

        self._imperfection_slider = QSlider(Qt.Orientation.Horizontal)
        self._imperfection_slider.setRange(0, 100)
        self._imperfection_slider.setValue(int(self.config["humanize"]["imperfection_level"] * 100))
        form.addRow("Imperfection Level:", self._imperfection_slider)

        self._code_style = QComboBox()
        self._code_style.addItems(["clean", "slightly_messy", "personal"])
        self._code_style.setCurrentText(self.config["humanize"]["code_style"])
        form.addRow("Code Style:", self._code_style)

        return group

    def _save(self):
        """Save settings back to config dict."""
        self.config["ai"]["provider"] = self._provider_combo.currentText()
        self.config["ai"]["model"] = self._model_input.text()
        self.config["ai"]["fallback_provider"] = self._fallback_combo.currentText()

        self.config["capture"]["mode"] = self._capture_mode.currentText()
        try:
            self.config["capture"]["auto_interval_seconds"] = int(self._interval_input.text())
        except ValueError:
            pass

        self.config["audio"]["enabled"] = self._audio_enabled.isChecked()
        self.config["audio"]["system_audio"] = self._system_audio.isChecked()
        self.config["audio"]["microphone"] = self._mic_audio.isChecked()
        self.config["audio"]["transcription_model"] = self._whisper_model.currentText()

        self.config["output"]["mode"] = self._output_mode.currentText()
        self.config["output"]["overlay_opacity"] = self._opacity_slider.value() / 100.0
        self.config["output"]["overlay_position"] = self._position_combo.currentText()

        self.config["humanize"]["enabled"] = self._humanize_enabled.isChecked()
        self.config["humanize"]["style"] = self._style_combo.currentText()
        self.config["humanize"]["imperfection_level"] = self._imperfection_slider.value() / 100.0
        self.config["humanize"]["code_style"] = self._code_style.currentText()

        self.accept()

    def get_config(self) -> dict:
        return self.config
