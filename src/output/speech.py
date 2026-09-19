"""Reliable Windows-first text-to-speech for Jarvis."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Callable, Optional

# How long to wait for SAPI to actually begin an async utterance before giving up on
# tracking it. Measured start-up on this machine is around 150 ms.
SAPI_START_TIMEOUT_S = 1.5

# Bounds how long audio keeps playing after a barge-in, so keep it tight.
SAPI_POLL_INTERVAL_S = 0.02


class SpeechOutput:
    """
    Speaks text aloud. Prefer Windows SAPI (reliable with Qt), fall back to pyttsx3.
    """

    def __init__(self, config: dict):
        self._rate = int(config.get("speech_rate", 175))
        self._volume = float(config.get("speech_volume", 0.9))
        self._voice_index = int(config.get("speech_voice", 0))
        self._queue: deque[tuple[str, str]] = deque()  # (priority, text)
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._running = True
        self._speaking = False
        self._cancel = False
        self._backend = "none"
        self._on_idle: Optional[Callable[[], None]] = None
        self._on_start: Optional[Callable[[str], None]] = None
        self.current_text = ""
        self.started_at = 0.0
        self.ended_at = 0.0
        self._sapi_ref = None
        self._thread = threading.Thread(target=self._speech_loop, daemon=True, name="jarvis-tts")
        self._thread.start()

    def _speech_loop(self) -> None:
        pythoncom = None
        try:
            import pythoncom as _pythoncom

            pythoncom = _pythoncom
            pythoncom.CoInitialize()
        except Exception as e:
            print(f"[Speech] COM init skipped: {e}")

        sapi = self._init_sapi()
        engine = None
        if sapi is None:
            engine = self._init_pyttsx3()

        try:
            while self._running:
                self._event.wait(timeout=0.5)
                while self._running:
                    with self._lock:
                        if not self._queue:
                            self._event.clear()
                            break
                        _prio, text = self._queue.popleft()
                    try:
                        self._speaking = True
                        self._cancel = False
                        self.current_text = str(text)
                        self.started_at = time.time()
                        if self._on_start:
                            try:
                                self._on_start(str(text))
                            except Exception as e:
                                print(f"[Speech] start callback error: {e}")
                        if sapi is not None:
                            self._sapi_ref = sapi
                            sapi.Rate = max(-5, min(5, int((self._rate - 150) / 20)))
                            sapi.Volume = int(max(0, min(100, self._volume * 100)))
                            # 1 = async so barge-in can purge
                            sapi.Speak(str(text), 1)
                            # SAPI reports "not running" for roughly 150 ms before audio
                            # starts. Treating that as finished ends the utterance
                            # immediately, which fires the idle callback and reopens the
                            # microphone while Jarvis is still talking.
                            started = self._await_sapi_start(sapi)
                            while started:
                                try:
                                    running = int(sapi.Status.RunningState) == 2
                                except Exception:
                                    running = False
                                if not running or self._cancel:
                                    if self._cancel:
                                        try:
                                            sapi.Speak("", 2)
                                        except Exception:
                                            pass
                                    break
                                time.sleep(SAPI_POLL_INTERVAL_S)
                            self._backend = "sapi"
                        elif engine is not None:
                            engine.say(text)
                            engine.runAndWait()
                            self._backend = "pyttsx3"
                        else:
                            print(f"[Speech] No TTS backend available: {text[:80]}")
                        print(f"[Speech] Spoke ({self._backend}): {text[:80]}")
                    except Exception as e:
                        print(f"[Speech] Error: {e}")
                        sapi = self._init_sapi()
                        if sapi is None:
                            engine = self._init_pyttsx3()
                    finally:
                        self._speaking = False
                        self.ended_at = time.time()
                        with self._lock:
                            empty = not self._queue
                        if empty and self._on_idle:
                            try:
                                self._on_idle()
                            except Exception as e:
                                print(f"[Speech] idle callback error: {e}")
        finally:
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _await_sapi_start(self, sapi) -> bool:
        """Block until SAPI is actually speaking. False if it never started or was cut."""
        deadline = time.time() + SAPI_START_TIMEOUT_S
        while time.time() < deadline:
            if self._cancel:
                try:
                    sapi.Speak("", 2)
                except Exception:
                    pass
                return False
            try:
                if int(sapi.Status.RunningState) == 2:
                    return True
            except Exception:
                return False
            time.sleep(0.02)
        return False

    def _init_sapi(self):
        try:
            import win32com.client

            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voices = voice.GetVoices()
            if voices.Count > 0:
                idx = min(self._voice_index, voices.Count - 1)
                voice.Voice = voices.Item(idx)
                print(f"[Speech] Using Windows SAPI voice={voices.Item(idx).GetDescription()}")
            else:
                print("[Speech] Using Windows SAPI")
            try:
                out = voice.AudioOutput
                if out is not None:
                    print(f"[Speech] Audio output: {out.GetDescription()}")
            except Exception:
                pass
            return voice
        except Exception as e:
            print(f"[Speech] SAPI unavailable: {e}")
            return None

    def _init_pyttsx3(self):
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            engine.setProperty("volume", self._volume)
            voices = engine.getProperty("voices")
            if voices and self._voice_index < len(voices):
                engine.setProperty("voice", voices[self._voice_index].id)
            print("[Speech] Using pyttsx3")
            return engine
        except Exception as e:
            print(f"[Speech] pyttsx3 unavailable: {e}")
            return None

    def set_idle_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_idle = callback

    def set_start_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_start = callback

    @property
    def is_speaking(self) -> bool:
        return bool(self._speaking)

    def speak(self, text: str, priority: str = "USER_RESPONSE") -> None:
        if priority == "FILLER":
            return
        from src.agent.response_boundary import SAFE_FALLBACK, USER_RESPONSE, sanitize_for_tts

        classified = sanitize_for_tts(text)
        if classified.output_type != USER_RESPONSE or not classified.tts_allowed:
            print(
                f"[Speech] Blocked TTS type={classified.output_type} "
                f"reason={classified.tts_block_reason or classified.output_type}"
            )
            if classified.tts_block_reason == "empty":
                return
            text = SAFE_FALLBACK
        else:
            text = classified.spoken
        clean = self._clean_for_speech(text)
        if not clean:
            return
        rank = {"CRITICAL": 0, "USER_RESPONSE": 1, "TASK_STATUS": 2, "STATUS": 2, "FILLER": 3}.get(priority, 1)
        with self._lock:
            if rank <= 1:
                self._queue = deque(item for item in self._queue if item[0] in {"CRITICAL"})
                self._cancel = True
            elif rank >= 2:
                self._queue = deque(item for item in self._queue if item[0] not in {"TASK_STATUS", "STATUS", "FILLER"})
            self._queue.append((priority, clean))
        self._event.set()
        print(f"[Speech] Queued ({priority}): {clean[:60]}...")

    def cancel_current(self) -> None:
        self.stop_speaking()

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()

    def stop_speaking(self) -> None:
        """Barge-in. Must return immediately: the caller is on the input path.

        The purge itself is left to the speech thread. Calling SAPI from here marshals
        into the worker's COM apartment and blocks until it is free, which put roughly
        700 ms in front of every interruption.
        """
        with self._lock:
            self._queue.clear()
            self._cancel = True
        self._event.set()

    def shutdown(self) -> None:
        self._running = False
        self._event.set()

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        text = re.sub(r"```[\w]*\n?.*?```", "Check the code on screen.", text, flags=re.DOTALL)
        text = re.sub(r"#{1,6}\s*", "", text)
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        text = re.sub(r"`(.*?)`", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        text = re.sub(r"\n{2,}", ". ", text)
        text = re.sub(r"\n", " ", text)
        text = re.sub(r"\s{2,}", " ", text)
        if len(text) > 400:
            sentences = text.split(". ")
            shortened = []
            total = 0
            for s in sentences:
                if total + len(s) > 350:
                    break
                shortened.append(s)
                total += len(s)
            text = ". ".join(shortened)
            if not text.endswith("."):
                text += "."
        return text.strip()
