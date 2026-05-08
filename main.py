import sys
import yaml
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from src.capture.screen import ScreenCapture
from src.capture.audio import AudioCapture
from src.processing.transcription import Transcriber
from src.processing.context import ContextBuilder
from src.processing.llm import LLMClient
from src.humanize.engine import HumanizationEngine
from src.output.overlay import OverlayWindow
from src.output.clipboard_out import ClipboardOutput
from src.output.monitor_window import MonitorWindow
from src.output.speech import SpeechOutput
from src.ui.tray import SystemTray


def load_config():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


class AppController:
    """Central controller that wires all components together."""

    def __init__(self, config: dict):
        self.config = config
        self.screen_capture = ScreenCapture(config["capture"])
        self.audio_capture = AudioCapture(config["audio"])
        self.transcriber = Transcriber(config["audio"])
        self.context_builder = ContextBuilder()
        self.llm_client = LLMClient(config["ai"])
        self.humanizer = HumanizationEngine(config["humanize"])

        self.overlay = None
        self.clipboard_out = ClipboardOutput()
        self.monitor_window = None
        self.speech_out = None

        self._audio_listening = False
        self._conversation_buffer: list[str] = []
        self._last_speech_time: float = 0.0
        self._response_pending: bool = False

    def init_ui(self, app: QApplication):
        output_mode = self.config["output"]["mode"]

        if output_mode in ("overlay", "all"):
            self.overlay = OverlayWindow(self.config["output"])

        if output_mode in ("second_monitor", "all"):
            self.monitor_window = MonitorWindow(self.config["output"])

        if output_mode in ("speech", "all"):
            self.speech_out = SpeechOutput(self.config["output"])

    def capture_and_analyze(self):
        """Capture screen(s), send to LLM, humanize, and deliver output."""
        print("[AI Helper] Capturing screens...")
        screenshots = self.screen_capture.capture_all()
        print(f"[AI Helper] Captured {len(screenshots)} screen(s)")

        transcription = self.transcriber.get_recent_text()
        self.context_builder.add_screenshots(screenshots)
        if transcription:
            self.context_builder.add_transcription(transcription)

        prompt = self.context_builder.build_prompt()
        print("[AI Helper] Sending to AI... (this may take a few seconds)")
        raw_answer = self.llm_client.query(prompt, screenshots)
        print(f"[AI Helper] Got response ({len(raw_answer)} chars)")

        if self.config["humanize"]["enabled"]:
            answer = self.humanizer.process(raw_answer)
        else:
            answer = raw_answer

        self._deliver_output(answer)
        self.context_builder.add_qa_pair(prompt, answer)
        print("[AI Helper] Answer delivered!")

        return answer

    def ask_question(self, question: str):
        """Ask a specific question about what's on screen."""
        print(f"[AI Helper] Question: {question}")
        screenshots = self.screen_capture.capture_all()
        print(f"[AI Helper] Captured {len(screenshots)} screen(s)")

        transcription = self.transcriber.get_recent_text()
        self.context_builder.add_screenshots(screenshots)
        if transcription:
            self.context_builder.add_transcription(transcription)

        prompt = self.context_builder.build_prompt(user_question=question)
        print("[AI Helper] Sending to AI...")
        raw_answer = self.llm_client.query(prompt, screenshots)
        print(f"[AI Helper] Got response ({len(raw_answer)} chars)")

        if self.config["humanize"]["enabled"]:
            answer = self.humanizer.process(raw_answer)
        else:
            answer = raw_answer

        self._deliver_output(answer)
        self.context_builder.add_qa_pair(question, answer)
        print("[AI Helper] Answer delivered!")

        return answer

    def toggle_audio(self):
        if self._audio_listening:
            self.audio_capture.stop()
            self._audio_listening = False
        else:
            self.audio_capture.start(callback=self._on_audio_chunk)
            self._audio_listening = True
        return self._audio_listening

    def _on_audio_chunk(self, audio_data):
        import time

        text = self.transcriber.transcribe(audio_data)
        if text.strip():
            print(f"[Audio] Heard: {text}")
            self.context_builder.add_transcription(text)
            self._conversation_buffer.append(text)
            self._last_speech_time = time.time()

            if not self._response_pending:
                self._response_pending = True
                import threading
                threading.Thread(target=self._wait_and_respond, daemon=True).start()
        else:
            if self._conversation_buffer and not self._response_pending:
                if hasattr(self, '_last_speech_time'):
                    import time as t
                    if t.time() - self._last_speech_time > 3:
                        self._trigger_response()

    def _wait_and_respond(self):
        """Wait for a pause in conversation, then respond with full context."""
        import time
        while True:
            time.sleep(2.0)
            elapsed = time.time() - self._last_speech_time
            if elapsed >= 3.0:
                break
            if elapsed >= 8.0:
                break

        self._trigger_response()

    def _trigger_response(self):
        """Send accumulated conversation to AI."""
        if not self._conversation_buffer:
            self._response_pending = False
            return

        full_conversation = " ".join(self._conversation_buffer)
        self._conversation_buffer.clear()
        self._response_pending = False

        if len(full_conversation.strip()) < 5:
            return

        import threading
        threading.Thread(target=self._do_auto_respond, args=(full_conversation,), daemon=True).start()

    def _do_auto_respond(self, transcript: str):
        """Capture screen + use full conversation segment to generate answer."""
        try:
            print(f"[AI Helper] Responding to conversation: {transcript[:100]}...")
            screenshots = self.screen_capture.capture_all()
            self.context_builder.add_screenshots(screenshots)

            prompt = self.context_builder.build_prompt(
                user_question=(
                    f"Here is the conversation I just heard:\n\n\"{transcript}\"\n\n"
                    "This may involve multiple speakers in a meeting/interview. "
                    "Based on this conversation and what's on screen:\n"
                    "1. Identify what's being asked or discussed\n"
                    "2. Give me a concise, helpful response I can use\n"
                    "3. If there's a direct question, answer it\n"
                    "4. If it's a multi-part discussion, summarize the key points and what I should say\n"
                    "Keep it concise (3-4 sentences max) since this will be spoken to me."
                )
            )

            raw_answer = self.llm_client.query(prompt, screenshots)

            if self.config["humanize"]["enabled"]:
                answer = self.humanizer.process(raw_answer)
            else:
                answer = raw_answer

            self._deliver_output(answer)
            self.context_builder.add_qa_pair(transcript, answer)
            print(f"[AI Helper] Response delivered ({len(answer)} chars)")

        except Exception as e:
            print(f"[AI Helper] Auto-respond error: {e}")

    def _deliver_output(self, answer: str):
        mode = self.config["output"]["mode"]
        print(f"[AI Helper] Delivering to mode: {mode}")

        if mode in ("clipboard", "all"):
            self.clipboard_out.copy(answer)
            print("[AI Helper] -> Copied to clipboard")

        if mode in ("overlay", "all") and self.overlay:
            self.overlay.show_answer(answer)
            print("[AI Helper] -> Sent to overlay")

        if mode in ("second_monitor", "all"):
            if self.monitor_window is None:
                from src.output.monitor_window import MonitorWindow
                self.monitor_window = MonitorWindow(self.config["output"])
            self.monitor_window.show_answer(answer)
            print("[AI Helper] -> Sent to Session Notes")

        if mode in ("speech", "all"):
            if self.speech_out is None:
                self.speech_out = SpeechOutput(self.config["output"])
            self.speech_out.speak(answer)
            print("[AI Helper] -> Speaking answer")

    def shutdown(self):
        if self._audio_listening:
            self.audio_capture.stop()
        self.transcriber.shutdown()
        if self.speech_out:
            self.speech_out.shutdown()


def main():
    print("[AI Helper] Loading config...")
    config = load_config()

    print("[AI Helper] Starting application...")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    print("[AI Helper] Initializing components...")
    controller = AppController(config)
    controller.init_ui(app)

    print("[AI Helper] Setting up system tray...")
    tray = SystemTray(app, controller, config)
    tray.show()

    print("[AI Helper] ============================")
    print("[AI Helper] APP IS RUNNING!")
    print("[AI Helper] Look for green icon in system tray")
    print("[AI Helper] Hotkeys:")
    print("[AI Helper]   Ctrl+Shift+S = Capture & Analyze")
    print("[AI Helper]   Ctrl+Shift+Z = Toggle Audio")
    print("[AI Helper]   Ctrl+Shift+H = Toggle Overlay")
    print("[AI Helper]   Ctrl+Shift+Q = Ask Question")
    print("[AI Helper] ============================")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
