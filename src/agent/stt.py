"""High-accuracy speech-to-text providers for Jarvis voice."""

from __future__ import annotations

import io
import os
import wave
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


def float_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert float32 mono [-1,1] audio to 16-bit PCM WAV bytes."""
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


class STTProvider(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        raise NotImplementedError


class OpenAIWhisperSTT(STTProvider):
    """Cloud Whisper — closest quality to ChatGPT voice transcription."""

    name = "openai"

    def __init__(self, model: str = "whisper-1", api_key: Optional[str] = None):
        from openai import OpenAI

        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI Whisper STT")
        self._client = OpenAI(api_key=key)
        self.model = model

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        if audio.size < sample_rate * 0.25:
            return ""
        wav = float_to_wav_bytes(audio, sample_rate)
        bio = io.BytesIO(wav)
        bio.name = "speech.wav"
        result = self._client.audio.transcriptions.create(
            model=self.model,
            file=bio,
            language="en",
            response_format="text",
        )
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        return (text or "").strip()

    def transcribe_detailed(self, audio: np.ndarray, sample_rate: int = 16000) -> dict:
        text = self.transcribe(audio, sample_rate)
        return {"text": text, "confidence": 1.0 if text else 0.0}


class LocalWhisperSTT(STTProvider):
    """Local faster-whisper on a full utterance (not tiny chunks)."""

    name = "local"

    def __init__(self, model_size: str = "medium", device: str = "cpu"):
        from faster_whisper import WhisperModel

        self.model_size = model_size
        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type="int8",
            cpu_threads=max(4, (os.cpu_count() or 4) // 2),
        )
        print(f"[STT] Local Whisper loaded: {model_size} on {device}")

    def transcribe_detailed(self, audio: np.ndarray, sample_rate: int = 16000) -> dict:
        empty = {"text": "", "confidence": 0.0, "avg_logprob": -1.0, "no_speech_prob": 1.0}
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.size < sample_rate * 0.25:
            return empty
        if float(np.max(np.abs(audio))) < 0.005:
            return empty
        segments, info = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        segs = list(segments)
        parts = [seg.text.strip() for seg in segs if seg.text and seg.text.strip()]
        text = " ".join(parts).strip()
        logprobs = [float(getattr(seg, "avg_logprob", -1.0) or -1.0) for seg in segs]
        avg_lp = sum(logprobs) / len(logprobs) if logprobs else -1.0
        no_speech = float(getattr(info, "no_speech_prob", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, (avg_lp + 1.2) / 1.4))
        if no_speech > 0.7:
            confidence = min(confidence, 0.25)
        return {
            "text": text,
            "confidence": round(confidence, 3),
            "avg_logprob": round(avg_lp, 3),
            "no_speech_prob": round(no_speech, 3),
        }

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        return str(self.transcribe_detailed(audio, sample_rate).get("text") or "")


def build_stt_provider(voice_cfg: dict, audio_cfg: dict) -> STTProvider:
    """
    Default is local Whisper (fully on-device).
    OpenAI path exists only if explicitly set to stt_provider: openai.
    """
    provider = (voice_cfg.get("stt_provider") or "local").lower()
    local_model = (
        voice_cfg.get("local_model")
        or audio_cfg.get("transcription_model")
        or "small"
    )
    # CPU default: small is much faster than medium and accurate enough for commands
    if local_model == "base":
        local_model = "small"

    if provider == "openai":
        print("[STT] WARNING: openai STT sends audio to the cloud (explicit override)")
        return OpenAIWhisperSTT(model=voice_cfg.get("openai_model", "whisper-1"))

    # local and auto both stay on-device (auto no longer phones home)
    if provider == "auto":
        print("[STT] auto -> local (cloud STT disabled by local-first policy)")
    print(f"[STT] Using local Whisper model: {local_model}")
    return LocalWhisperSTT(model_size=local_model)
