import threading
import collections
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel


class Transcriber:
    """
    Local speech-to-text using faster-whisper.
    Runs entirely on-device for privacy.
    """

    def __init__(self, config: dict):
        self.model_size = config.get("transcription_model", "large-v3")
        self._model: Optional[WhisperModel] = None
        self._lock = threading.Lock()
        self._recent_transcriptions: collections.deque = collections.deque(maxlen=50)
        self._initialized = False

    def _ensure_model(self):
        """Lazy-load the whisper model on first use. Uses CPU to avoid CUDA DLL issues."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                )
                print(f"[Transcriber] Loaded {self.model_size} on CPU")
            except Exception as e:
                print(f"[Transcriber] Failed to load model: {e}")
            self._initialized = True

    def transcribe(self, audio_data: np.ndarray) -> str:
        """
        Transcribe a numpy array of audio data (float32, 16kHz mono).
        Returns the transcribed text.
        """
        self._ensure_model()

        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        if np.max(np.abs(audio_data)) < 0.01:
            return ""

        try:
            segments, info = self._model.transcribe(
                audio_data,
                beam_size=5,
                language="en",
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=300,
                ),
            )

            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            full_text = " ".join(text_parts)

            if full_text:
                self._recent_transcriptions.append(full_text)

            return full_text

        except Exception as e:
            print(f"[Transcriber] Error during transcription: {e}")
            return ""

    def get_recent_text(self, last_n: int = 10) -> str:
        """Get the most recent transcriptions joined together."""
        recent = list(self._recent_transcriptions)[-last_n:]
        return " ".join(recent)

    def clear_history(self):
        """Clear transcription history."""
        self._recent_transcriptions.clear()

    def shutdown(self):
        """Release model resources."""
        with self._lock:
            self._model = None
            self._initialized = False


class TranscriberCPU(Transcriber):
    """CPU-only variant for systems without CUDA."""

    def _ensure_model(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
            )
            self._initialized = True
