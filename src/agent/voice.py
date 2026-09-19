"""
High-quality continuous voice capture for Jarvis (local).

Records full utterances, calibrates to YOUR microphone noise floor,
and only commits speech after sustained voice — not a single click/noise.
"""

from __future__ import annotations

import collections
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from src.agent.stt import STTProvider, build_stt_provider


HALLUCINATIONS = {
    "",
    ".",
    "you",
    "thank you",
    "thank you.",
    "thanks for watching",
    "thanks for watching.",
    "subtitle",
    "subtitles by",
    "thanks for watching everybody",
}


def _clamp_threshold(thr: float, base: float = 0.008) -> float:
    """Keep VAD sensitive enough that the first word is not dropped."""
    return float(np.clip(thr, 0.002, max(base * 1.8, 0.014)))


class VoiceCommander:
    """
    Continuous mic listening with calibrated energy VAD + full-utterance STT.
    """

    SAMPLE_RATE = 16000
    BLOCK = 512

    def __init__(
        self,
        audio_config: dict,
        on_transcript: Callable[[str], None],
        on_state: Optional[Callable[[str], None]] = None,
        on_hearing: Optional[Callable[[str], None]] = None,
        voice_config: Optional[dict] = None,
        transcriber=None,
        silence_seconds: Optional[float] = None,
        min_chars: int = 2,
        profile_path: Optional[Path] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        session=None,
    ):
        voice_config = voice_config or {}
        self._voice_config = voice_config
        self._on_transcript = on_transcript
        self._on_state = on_state or (lambda _s: None)
        self._on_hearing = on_hearing or (lambda _s: None)
        self._on_barge_in = on_barge_in or (lambda: None)
        self._session = session
        self._min_chars = min_chars
        self._barge_silence_sec = float(voice_config.get("barge_in_silence_seconds", 0.4))
        self._barge_min_speech_sec = float(voice_config.get("barge_in_min_speech_seconds", 0.28))
        self._echo_window_s = float(voice_config.get("echo_window_ms", 550)) / 1000.0

        self._silence_sec = float(
            silence_seconds
            if silence_seconds is not None
            else voice_config.get("silence_seconds", 1.6)
        )
        self._min_speech_sec = float(voice_config.get("min_speech_seconds", 0.7))
        self._max_utterance_sec = float(voice_config.get("max_utterance_seconds", 45))
        self._base_threshold = float(voice_config.get("energy_threshold", 0.008))
        self._energy_threshold = self._base_threshold
        self._pre_roll_sec = float(voice_config.get("pre_roll_seconds", 0.45))
        # Require N consecutive voiced blocks before "Hearing" (avoids click→drop)
        self._start_frames = int(voice_config.get("start_voiced_frames", 4))
        self._end_hang_frames = int(voice_config.get("end_hang_frames", 2))

        data_dir = Path(voice_config.get("profile_dir") or "data")
        if not data_dir.is_absolute():
            data_dir = Path(__file__).resolve().parents[2] / data_dir
        self._profile_path = profile_path or (data_dir / "voice_profile.json")
        self._load_profile()

        self._stt: STTProvider = build_stt_provider(voice_config, audio_config)
        self._stt_ready = False

        self._listening = False
        self._calibrating = False
        self._in_speech = False
        self._speech_started_at = 0.0
        self._last_voice_ts = 0.0
        self._voiced_run = 0
        self._silent_run = 0
        self._utterance: list[np.ndarray] = []
        self._pre_roll: collections.deque = collections.deque(
            maxlen=max(1, int(self.SAMPLE_RATE * self._pre_roll_sec / self.BLOCK))
        )
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self._transcribe_lock = threading.Lock()
        self._transcriber = transcriber
        self._hold = False
        self._utterance_during_tts = False

    @property
    def is_listening(self) -> bool:
        return self._listening

    def attach_session(self, session) -> None:
        self._session = session

    def _active_silence(self) -> float:
        if self._session is not None and getattr(self._session.gate.tts, "speaking", False):
            return self._barge_silence_sec
        return self._silence_sec

    def _active_min_speech(self) -> float:
        if self._session is not None and getattr(self._session.gate.tts, "speaking", False):
            return self._barge_min_speech_sec
        return self._min_speech_sec

    @property
    def stt_name(self) -> str:
        return getattr(self._stt, "name", "unknown")

    def _load_profile(self) -> None:
        try:
            if self._profile_path.exists():
                data = json.loads(self._profile_path.read_text(encoding="utf-8"))
                thr = float(data.get("energy_threshold") or 0)
                if 0.001 <= thr <= 0.2:
                    self._energy_threshold = _clamp_threshold(thr, self._base_threshold)
                    print(f"[Voice] Loaded calibrated threshold={self._energy_threshold:.4f}")
        except Exception as e:
            print(f"[Voice] Profile load failed: {e}")

    def _save_profile(self, extra: Optional[dict] = None) -> None:
        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "energy_threshold": self._energy_threshold,
            "calibrated_at": time.time(),
            **(extra or {}),
        }
        self._profile_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[Voice] Saved voice profile threshold={self._energy_threshold:.4f}")

    def ensure_stt_warm(self) -> None:
        """Load Whisper weights once so the first command isn't silently dropped."""
        if self._stt_ready:
            return
        self._on_hearing("Loading local speech model (one-time)…")
        try:
            # Tiny silent clip forces model init for local whisper
            silence = np.zeros(int(self.SAMPLE_RATE * 0.3), dtype=np.float32)
            self._stt.transcribe(silence, self.SAMPLE_RATE)
        except Exception as e:
            print(f"[Voice] STT warm-up note: {e}")
        self._stt_ready = True

    def calibrate(self, seconds: float = 4.0, speak_prompt: bool = True) -> dict:
        """
        Measure ambient noise, then ask user to speak a phrase to set voice level.
        Call while NOT in continuous listen mode (or pause listen first).
        """
        was_listening = self._listening
        if was_listening:
            self.stop()

        self._calibrating = True
        self._on_state("listening")
        self._on_hearing("Calibration: stay quiet for 2 seconds…")
        noise_samples: list[float] = []
        voice_samples: list[float] = []
        state = {"phase": "noise", "voice_started": 0.0}
        started = time.time()

        def callback(indata, frames, time_info, status):  # noqa: ARG002
            mono = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)
            rms = float(np.sqrt(np.mean(np.square(mono)) + 1e-12))
            elapsed = time.time() - started
            if state["phase"] == "noise":
                noise_samples.append(rms)
                if elapsed >= 2.0:
                    state["phase"] = "voice"
                    state["voice_started"] = time.time()
                    self._on_hearing('Now say clearly: "Jarvis, open Chrome"')
            elif state["phase"] == "voice":
                voice_samples.append(rms)
                if time.time() - state["voice_started"] >= max(2.5, seconds - 2.0):
                    state["phase"] = "done"

        stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=self.BLOCK,
            callback=callback,
        )
        stream.start()
        deadline = time.time() + seconds + 2.0
        while state["phase"] != "done" and time.time() < deadline:
            time.sleep(0.05)
        stream.stop()
        stream.close()
        self._calibrating = False

        noise = float(np.percentile(noise_samples, 90)) if noise_samples else 0.002
        voice = float(np.percentile(voice_samples, 75)) if voice_samples else noise * 4
        # Threshold between noise floor and voice level
        thr = noise * 2.8 + 0.002
        if voice > noise * 1.5:
            thr = min(max(noise * 2.5, (noise + voice) * 0.35), voice * 0.55)
        thr = float(np.clip(thr, 0.002, 0.08))
        self._energy_threshold = _clamp_threshold(thr, self._base_threshold)
        self._save_profile({"noise_p90": noise, "voice_p75": voice})

        result = {
            "ok": True,
            "energy_threshold": thr,
            "noise_level": noise,
            "voice_level": voice,
            "message": (
                f"Voice trained. Threshold {thr:.4f} "
                f"(noise {noise:.4f}, your voice {voice:.4f})."
            ),
        }
        self._on_hearing(result["message"])
        if was_listening:
            self.start()
        return result

    def start(self) -> bool:
        if self._listening:
            return True
        self._listening = True
        self._hold = False
        self._reset_utterance()
        self._on_state("listening")
        self._on_hearing(
            f"Listening ({self.stt_name}) thr={self._energy_threshold:.3f} — speak, then pause"
        )

        def _start_stream():
            self.ensure_stt_warm()
            if not self._listening:
                return
            self._stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self.BLOCK,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._on_hearing(
                f"Listening ({self.stt_name}) — speak naturally, pause when done"
            )
            print(
                f"[Voice] Listen started STT={self.stt_name} "
                f"threshold={self._energy_threshold:.4f}"
            )

        threading.Thread(target=_start_stream, daemon=True).start()
        if self._session is not None:
            self._session.on_listen_started()
        return True

    def stop(self) -> bool:
        if not self._listening:
            return False
        self._listening = False
        self._hold = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            has = bool(self._utterance)
        if has:
            threading.Thread(target=self._finalize_utterance, daemon=True).start()
        else:
            self._on_state("idle")
            self._on_hearing("")
        if self._session is not None:
            self._session.on_listen_stopped()
        return False

    def toggle(self) -> bool:
        if self._listening:
            self.stop()
            return False
        self.start()
        return True

    def _reset_utterance(self) -> None:
        with self._lock:
            self._in_speech = False
            self._utterance = []
            self._speech_started_at = 0.0
            self._last_voice_ts = 0.0
            self._voiced_run = 0
            self._silent_run = 0

    def hold(self) -> None:
        """Stop capturing while Jarvis thinks or speaks."""
        self._hold = True

    def release(self) -> None:
        """Resume capturing after a reply (if still in listen mode)."""
        self._hold = False
        if self._listening:
            self._on_state("listening")
            self._on_hearing(f"Listening ({self.stt_name})…")

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if not self._listening or self._calibrating:
            return
        # Hold only when the session is processing a command and TTS is not playing.
        if self._hold:
            sess = self._session
            speaking = sess is not None and getattr(sess.gate.tts, "speaking", False)
            if not speaking:
                return
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy().reshape(-1)
        rms = float(np.sqrt(np.mean(np.square(mono)) + 1e-12))
        peak = float(np.max(np.abs(mono)))
        is_voice = rms >= self._energy_threshold or peak >= self._energy_threshold * 3.0
        now = time.time()

        with self._lock:
            if not self._in_speech:
                self._pre_roll.append(mono)
                if is_voice:
                    self._voiced_run += 1
                else:
                    self._voiced_run = 0
                if self._voiced_run >= self._start_frames:
                    self._in_speech = True
                    self._speech_started_at = now
                    self._last_voice_ts = now
                    self._silent_run = 0
                    self._utterance = list(self._pre_roll)
                    self._pre_roll.clear()
                    self._utterance_during_tts = False
                    if self._session is not None:
                        tts = self._session.gate.tts
                        self._utterance_during_tts = bool(tts.speaking or tts.in_echo_window())
                        self._session.on_user_speech_start()
                    self._on_hearing("Hearing you… keep talking")
                return

            self._utterance.append(mono)
            if is_voice:
                self._last_voice_ts = now
                self._silent_run = 0
            else:
                self._silent_run += 1

            duration = now - self._speech_started_at
            silent_for = now - self._last_voice_ts
            min_sp = self._active_min_speech()
            sil = self._active_silence()
            ended = (duration >= min_sp and silent_for >= sil) or duration >= self._max_utterance_sec

            if ended:
                self._in_speech = False
                self._voiced_run = 0
                chunks = list(self._utterance)
                self._utterance = []
                threading.Thread(
                    target=self._transcribe_chunks, args=(chunks, now - self._speech_started_at, silent_for), daemon=True
                ).start()

    def _finalize_utterance(self) -> None:
        with self._lock:
            chunks = list(self._utterance)
            self._utterance = []
            self._in_speech = False
        if chunks:
            self._transcribe_chunks(chunks)
        else:
            self._on_state("idle")

    @staticmethod
    def _normalize_audio(audio: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(audio)) + 1e-8)
        if peak < 0.01:
            return audio
        # Boost quiet mics toward ~0.25 peak without clipping
        target = 0.28
        gain = min(12.0, target / peak)
        return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)

    def _transcribe_chunks(
        self,
        chunks: list[np.ndarray],
        speech_dur: float = 0.0,
        silent_for: float = 0.0,
    ) -> None:
        if not chunks:
            return
        audio = np.concatenate(chunks).astype(np.float32)
        duration = audio.size / float(self.SAMPLE_RATE)
        min_sp = self._active_min_speech()
        if duration < min_sp * 0.75:
            print(f"[Voice] discard TOO_SHORT ({duration:.2f}s)")
            if self._listening:
                self._on_state("listening")
            return

        audio = self._normalize_audio(audio)
        self._on_state("understanding")
        self._on_hearing(f"Transcribing {duration:.1f}s…")
        t0 = time.perf_counter()
        confidence = 1.0
        with self._transcribe_lock:
            try:
                detailed = getattr(self._stt, "transcribe_detailed", None)
                if callable(detailed):
                    info = detailed(audio, self.SAMPLE_RATE) or {}
                    text = str(info.get("text") or "")
                    confidence = float(info.get("confidence") or 0.0)
                else:
                    text = self._stt.transcribe(audio, self.SAMPLE_RATE)
            except Exception as e:
                print(f"[Voice] STT error: {e}")
                if self._listening:
                    self._on_hearing(f"Speech engine error: {e}")
                    self._on_state("listening")
                return
        stt_ms = (time.perf_counter() - t0) * 1000
        cleaned = (text or "").strip()
        lower = cleaned.lower().strip(" .")
        if lower in HALLUCINATIONS:
            print(f"[Voice] Ignored hallucination: {cleaned!r}")
            if self._listening:
                self._on_state("listening")
            return

        print(f"[Voice] Heard ({self.stt_name}): {cleaned}")
        self._on_hearing(f"Heard: {cleaned}")
        if self._session is not None:
            event = self._session.admit(
                cleaned,
                confidence=confidence,
                is_final=True,
                duration_s=duration,
                tts_active=self._utterance_during_tts,
                stt_ms=stt_ms,
                endpoint_ms=silent_for * 1000,
            )
            if event.accepted:
                self.hold()
            return
        if len(cleaned) < self._min_chars:
            if self._listening:
                self._on_state("listening")
            return
        self.hold()
        self._on_state("working")
        try:
            self._on_transcript(cleaned)
        except Exception as e:
            print(f"[Voice] transcript callback error: {e}")
            self.release()
