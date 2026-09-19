import sys
import threading
import yaml
from pathlib import Path

# Diagnostic output contains characters a legacy console code page cannot encode. A log
# line must never be able to abort a user request, so degrade the character instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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
from src.ui.jarvis_window import JarvisWindow
from src.agent.emergency import GLOBAL_EMERGENCY_STOP
from src.agent.factory import build_agent_stack
from src.agent.voice import VoiceCommander


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
        self.agent = None
        self.voice_commander = None
        self.voice_session = None

        self._audio_listening = False
        self._conversation_buffer: list[str] = []
        self._last_speech_time: float = 0.0
        self._response_pending: bool = False
        self._agent_lock = threading.Lock()

    def init_ui(self, app: QApplication):
        output_mode = self.config["output"]["mode"]

        if output_mode in ("overlay", "all"):
            self.overlay = OverlayWindow(self.config["output"])

        if output_mode in ("second_monitor", "all"):
            self.monitor_window = MonitorWindow(self.config["output"])

        # Always enable TTS for Jarvis speak-back
        self.speech_out = SpeechOutput(self.config["output"])
        self.speech_out.set_idle_callback(self._on_speech_idle)
        self._ensure_voice_session()

        if (self.config.get("agent") or {}).get("enabled", True):
            self.agent = build_agent_stack(
                self.config,
                screen_capture=self.screen_capture,
                clipboard_out=self.clipboard_out,
                speech_out_getter=lambda: self.speech_out,
            )
            print("[Jarvis] Local agent ready (Ollama provider)")

        self.jarvis_window = None

    def show_jarvis_window(self):
        """Open (or focus) the main Jarvis HUD window."""
        if self.jarvis_window is None:
            self.jarvis_window = JarvisWindow(
                send_command=self.handle_agent_command,
                emergency_stop=self.emergency_stop,
                toggle_voice=self.toggle_jarvis_voice,
                speak=self.speak,
                train_voice=self.train_jarvis_voice,
                resume_voice=self._resume_jarvis_voice,
                stop_speaking=lambda: self.speech_out.stop_speaking() if self.speech_out else None,
                config=self.config,
            )
            self.voice_commander = self._make_voice_commander()
            if self.agent is not None:
                self.agent.on_task_progress = self._on_task_progress
                self.agent.on_approval = self._on_approval
        self.jarvis_window.show()
        self.jarvis_window.raise_()
        self.jarvis_window.activateWindow()
        return self.jarvis_window

    def _ensure_voice_session(self):
        from src.agent.voice_session import VoiceSessionController

        if self.voice_session is not None:
            return self.voice_session
        echo_ms = float((self.config.get("voice") or {}).get("echo_window_ms", 550))
        self.voice_session = VoiceSessionController(
            on_command=lambda e: self._on_jarvis_voice_transcript(e.text, stt_confidence=getattr(e, "confidence", 1.0)),
            on_state=self._on_voice_state,
            on_hearing=self._on_hearing,
            cancel_tts=self._cancel_tts,
            echo_window_s=max(0.3, echo_ms / 1000.0),
        )
        if self.speech_out is not None:
            self.speech_out.set_start_callback(self.voice_session.on_tts_start)
        print("[Voice] Session controller ready — AEC unavailable, controlled full duplex")
        return self.voice_session

    def _cancel_tts(self) -> None:
        if self.speech_out:
            self.speech_out.cancel_current()
            self.speech_out.clear_queue()

    def _make_voice_commander(self):
        session = self._ensure_voice_session()
        commander = VoiceCommander(
            audio_config=self.config["audio"],
            voice_config=self.config.get("voice") or {},
            on_transcript=self._on_jarvis_voice_transcript,
            on_state=self._on_voice_state,
            on_hearing=self._on_hearing,
            session=session,
        )
        commander.attach_session(session)
        return commander

    def speak(self, text: str) -> None:
        if self.speech_out is None:
            self.speech_out = SpeechOutput(self.config["output"])
        self.speech_out.speak(text)

    def train_jarvis_voice(self) -> dict:
        if self.voice_commander is None:
            self.voice_commander = self._make_voice_commander()
        result = self.voice_commander.calibrate(seconds=5.0)
        if result.get("ok") and self.speech_out:
            self.speak("Voice training complete. You can press Voice and speak now.")
        return result

    def toggle_jarvis_voice(self) -> bool:
        if self.voice_commander is None:
            self.voice_commander = self._make_voice_commander()
        active = self.voice_commander.toggle()
        print(f"[Voice] STT backend: {self.voice_commander.stt_name}")
        return active

    def _resume_jarvis_voice(self) -> None:
        if self.voice_commander is not None:
            self.voice_commander.release()

    def _on_speech_idle(self) -> None:
        if self.voice_session is not None:
            self.voice_session.on_tts_end()
        delay = float((self.config.get("voice") or {}).get("echo_window_ms", 550)) / 1000.0

        def _resume():
            if self.jarvis_window is not None:
                self.jarvis_window.on_speech_idle()
            elif self.voice_commander is not None:
                self.voice_commander.release()

        threading.Timer(max(0.3, min(0.8, delay)), _resume).start()

    def _on_task_progress(self, progress: dict) -> None:
        if self.jarvis_window is not None and hasattr(self.jarvis_window, "set_task_progress"):
            self.jarvis_window.set_task_progress(progress)

    def _on_approval(self, data: dict) -> None:
        """Phase 7: forward approval events to the HUD."""
        if self.jarvis_window is None:
            return
        status = data.get("status", "")
        if status:
            self.jarvis_window.update_approval(data)
        else:
            self.jarvis_window.show_approval(data)

    def _on_hearing(self, text: str) -> None:
        if self.jarvis_window is not None and hasattr(self.jarvis_window, "set_hearing"):
            self.jarvis_window.set_hearing(text)
    def _on_voice_state(self, state: str) -> None:
        if self.jarvis_window is not None:
            self.jarvis_window.set_agent_state(state)

    def _on_jarvis_voice_transcript(self, text: str, stt_confidence: float = 1.0) -> None:
        # Interrupt phrases
        lower = text.lower().strip()
        if lower in {"stop", "jarvis stop", "stop everything", "cancel"}:
            self.emergency_stop()
            if self.jarvis_window is not None:
                self.jarvis_window._append("system", "Voice stop received.")
            self.speak("Stopping.")
            return
        if self.jarvis_window is not None:
            self.jarvis_window.on_voice_transcript(text, stt_confidence=stt_confidence)
        else:
            result = self.handle_agent_command(text, stt_confidence=stt_confidence)
            msg = (result or {}).get("message") or ""
            if msg:
                self.speak(msg)
            else:
                self._resume_jarvis_voice()

    def handle_agent_command(self, text: str, stt_confidence: float = 1.0) -> dict:
        """Route a natural-language command to the Jarvis orchestrator."""
        if self.agent is None:
            return {"ok": False, "message": "Agent is disabled in config."}
        print(f"[Jarvis] Handling command: {text[:160]}")
        if hasattr(self.agent, "cancel_stale"):
            self.agent.cancel_stale()
        result = self.agent.handle_user_message(text, stt_confidence=stt_confidence)
        message = result.get("message") or ""
        if message:
            if self.overlay:
                self.overlay.show_answer(f"[Jarvis]\n{message}")
            if self.monitor_window:
                self.monitor_window.show_answer(f"[Jarvis] {message}")
        print(f"[Jarvis] {message}")
        return result

    def emergency_stop(self):
        GLOBAL_EMERGENCY_STOP.engage("hotkey")
        if self.speech_out:
            self.speech_out.stop_speaking()
        if self.voice_commander and self.voice_commander.is_listening:
            self.voice_commander.stop()
        if self.agent:
            if hasattr(self.agent, "cancel_stale"):
                self.agent.cancel_stale()
            task = self.agent.get_active_task()
            if task is not None:
                from src.agent.models.task import TaskStatus

                if task.status.value in {
                    "running",
                    "pending",
                    "waiting_for_approval",
                    "waiting_for_user",
                }:
                    task.status = TaskStatus.PAUSED
                    task.summary = "Paused by emergency stop"
                    self.agent.store.save(task)
        return {"ok": True, "message": "Emergency stop engaged."}

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

    # Open the clickable Jarvis window on launch
    controller.show_jarvis_window()

    print("[AI Helper] ============================")
    print("[Jarvis] APP IS RUNNING!")
    print("[Jarvis] Conversation window opened")
    print("[Jarvis] Desktop shortcut: run install_desktop_shortcut.ps1 once")
    print("[Jarvis] Hotkeys:")
    print("[Jarvis]   Ctrl+Shift+J = Focus Ask Jarvis")
    print("[Jarvis]   Ctrl+Shift+Esc = Emergency stop")
    print("[Jarvis]   Ctrl+Shift+S = Capture & Analyze (legacy)")
    print("[Jarvis] ============================")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
