import threading
import queue
import re
from typing import Optional

import pyttsx3


class SpeechOutput:
    """
    Text-to-speech output - speaks answers through the default audio device.
    Only speaks the LATEST response - if a new one arrives, it cancels the old one.
    """

    def __init__(self, config: dict):
        self._rate = config.get("speech_rate", 175)
        self._volume = config.get("speech_volume", 0.9)
        self._voice_index = config.get("speech_voice", 0)

        self._latest_text: Optional[str] = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._running = True
        self._speaking = False

        self._thread = threading.Thread(target=self._speech_loop, daemon=True)
        self._thread.start()

    def _speech_loop(self):
        """Run TTS engine in its own thread."""
        engine = pyttsx3.init()
        engine.setProperty("rate", self._rate)
        engine.setProperty("volume", self._volume)

        voices = engine.getProperty("voices")
        if voices and self._voice_index < len(voices):
            engine.setProperty("voice", voices[self._voice_index].id)

        while self._running:
            self._event.wait(timeout=1.0)
            self._event.clear()

            with self._lock:
                text = self._latest_text
                self._latest_text = None

            if text and self._running:
                try:
                    self._speaking = True
                    engine.say(text)
                    engine.runAndWait()
                    self._speaking = False
                except Exception as e:
                    self._speaking = False
                    print(f"[Speech] Error: {e}")
                    try:
                        engine = pyttsx3.init()
                        engine.setProperty("rate", self._rate)
                        engine.setProperty("volume", self._volume)
                    except Exception:
                        pass

    def speak(self, text: str):
        """Speak text. Replaces any pending speech (only latest matters)."""
        clean_text = self._clean_for_speech(text)
        if not clean_text:
            return

        with self._lock:
            self._latest_text = clean_text

        self._event.set()
        print(f"[Speech] Queued: {clean_text[:60]}...")

    def stop_speaking(self):
        """Clear pending speech."""
        with self._lock:
            self._latest_text = None

    def shutdown(self):
        """Stop the speech thread."""
        self._running = False
        self._event.set()

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """Clean text for natural-sounding speech."""
        text = re.sub(r'```[\w]*\n?.*?```', 'Check the code on screen.', text, flags=re.DOTALL)
        text = re.sub(r'#{1,6}\s*', '', text)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        text = re.sub(r'`(.*?)`', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'\n{2,}', '. ', text)
        text = re.sub(r'\n', ' ', text)
        text = re.sub(r'\s{2,}', ' ', text)

        if len(text) > 400:
            sentences = text.split('. ')
            shortened = []
            total = 0
            for s in sentences:
                if total + len(s) > 350:
                    break
                shortened.append(s)
                total += len(s)
            text = '. '.join(shortened)
            if not text.endswith('.'):
                text += '.'

        return text.strip()
