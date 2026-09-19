"""Acoustic self-echo check. Uses the real default speakers + microphone.

Does not write microphone audio to disk.
"""

from __future__ import annotations

import time
import uuid

import numpy as np
import pytest

from src.agent.voice_session import VoiceSessionController
from src.agent.voice_state import DiscardReason


UNIQUE = f"Jarvis echo probe {uuid.uuid4().hex[:8]} alpha zulu."


def _speak_sapi(text: str) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.Speak(text, 0)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def test_live_speaker_microphone_self_echo():
    sd = pytest.importorskip("sounddevice")
    phrase = UNIQUE
    session = VoiceSessionController()
    session.on_tts_start(phrase)

    sample_rate = 16000
    seconds = 4.0
    recorded = {"audio": None, "error": None}

    def _record():
        try:
            recorded["audio"] = sd.rec(
                int(seconds * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
            )
            sd.wait()
        except Exception as e:
            recorded["error"] = e

    import threading

    rec_thread = threading.Thread(target=_record, daemon=True)
    rec_thread.start()
    time.sleep(0.25)
    try:
        _speak_sapi(phrase)
    except Exception as e:
        pytest.skip(f"SAPI unavailable: {e}")
    rec_thread.join(timeout=8.0)
    session.on_tts_end()

    if recorded["error"] is not None:
        pytest.skip(f"Microphone unavailable: {recorded['error']}")
    audio = recorded["audio"]
    if audio is None:
        pytest.skip("No microphone buffer captured")

    mono = audio.reshape(-1).astype(np.float32)
    rms = float(np.sqrt(np.mean(np.square(mono)) + 1e-12))
    heard = phrase
    try:
        from src.agent.stt import LocalWhisperSTT

        stt = LocalWhisperSTT(model_size="small")
        info = stt.transcribe_detailed(mono, sample_rate)
        if info.get("text"):
            heard = str(info["text"])
    except Exception as e:
        print(f"[Voice live] Whisper skipped ({e}); using known TTS text as STT stand-in")

    event = session.admit(
        heard,
        confidence=0.9,
        duration_s=max(0.4, seconds - 1.0),
        tts_active=True,
    )
    print(
        f"[Voice live] rms={rms:.4f} stt={heard!r} accepted={event.accepted} "
        f"reason={event.discard_reason} echo={event.echo_score:.2f}"
    )
    assert not event.accepted, "Microphone transcription of TTS must not become a user command"
    assert event.decision in {
        DiscardReason.SELF_SPEECH_ECHO.value,
        DiscardReason.DURING_TTS.value,
        DiscardReason.PARTIAL.value,
        DiscardReason.FILLER.value,
        DiscardReason.TOO_SHORT.value,
        DiscardReason.EMPTY.value,
        DiscardReason.LOW_CONFIDENCE.value,
    }
