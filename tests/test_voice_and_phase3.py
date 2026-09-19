"""Voice STT + Phase 3 screen tests."""

from __future__ import annotations

import numpy as np

from src.agent.stt import LocalWhisperSTT, float_to_wav_bytes, build_stt_provider
from src.agent.screen.understanding import ScreenUnderstandingService
from src.agent.computer.controller import ComputerController
from tests.test_computer_phase2 import FakePlatform


def test_float_to_wav_bytes_roundtrip_header():
    audio = np.zeros(16000, dtype=np.float32)
    audio[100:500] = 0.2
    wav = float_to_wav_bytes(audio, 16000)
    assert wav[:4] == b"RIFF"
    assert b"WAVE" in wav[:16]


def test_build_stt_prefers_local_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class FakeLocal:
        name = "local"

        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, sample_rate=16000):
            return "hello"

    monkeypatch.setattr("src.agent.stt.LocalWhisperSTT", FakeLocal)
    stt = build_stt_provider({"stt_provider": "auto", "local_model": "medium"}, {})
    assert stt.name == "local"


def test_auto_stays_local_even_with_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class FakeLocal:
        name = "local"

        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, sample_rate=16000):
            return "hello"

    class FakeOpenAI:
        name = "openai"

        def __init__(self, *a, **k):
            raise AssertionError("OpenAI STT must not be used in auto/local-first mode")

    monkeypatch.setattr("src.agent.stt.LocalWhisperSTT", FakeLocal)
    monkeypatch.setattr("src.agent.stt.OpenAIWhisperSTT", FakeOpenAI)
    stt = build_stt_provider({"stt_provider": "auto"}, {})
    assert stt.name == "local"


def test_build_stt_openai_when_forced(monkeypatch):
    class FakeOpenAI:
        name = "openai"

        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, sample_rate=16000):
            return "hi"

    monkeypatch.setattr("src.agent.stt.OpenAIWhisperSTT", FakeOpenAI)
    stt = build_stt_provider({"stt_provider": "openai"}, {})
    assert stt.name == "openai"


def test_screen_state_includes_interactive_field():
    controller = ComputerController(platform=FakePlatform())

    class FakeScreen:
        def capture_all(self):
            return []

    svc = ScreenUnderstandingService(FakeScreen(), controller)
    # Force no UIA dependency for unit test
    state = svc.get_state(include_uia=False)
    assert hasattr(state, "interactive_elements")
    assert state.contains_window("Chrome")


def test_verify_element_api_exists():
    controller = ComputerController(platform=FakePlatform())

    class FakeScreen:
        def capture_all(self):
            return []

    svc = ScreenUnderstandingService(FakeScreen(), controller)
    result = svc.verify_element_present("Submit", kind="button")
    assert "verified" in result


def test_hud_reply_for_tasks(tmp_path):
    from src.ui.dashboard_data import DailyTaskStore, hud_spoken_reply

    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    reply = hud_spoken_reply("What are the three tasks I have to do today?", store)
    assert reply is not None
    assert "Review morning emails" in reply
    reply2 = hud_spoken_reply("What are my tasks?", store)
    assert reply2 is not None
    clipped = hud_spoken_reply("that I have to do to do today", store)
    assert clipped is not None
    assert "Review morning emails" in clipped
    assert hud_spoken_reply("what do I have to do today", store) is not None


def test_hud_reply_ignores_unrelated(tmp_path):
    from src.ui.dashboard_data import DailyTaskStore, hud_spoken_reply

    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert hud_spoken_reply("open notepad", store) is None
    assert hud_spoken_reply("find my resume", store) is None


def test_voice_hold_blocks_then_release():
    from src.agent.voice import VoiceCommander

    commander = VoiceCommander.__new__(VoiceCommander)
    commander._hold = False
    commander._listening = True
    commander._stt = type("S", (), {"name": "local"})()
    states = []
    commander._on_state = states.append
    commander._on_hearing = lambda _t: None
    commander.hold()
    assert commander._hold is True
    commander.release()
    assert commander._hold is False
    assert "listening" in states
