"""Ollama auto-start manager tests."""

from __future__ import annotations

from src.agent.ollama_manager import model_available, _ollama_root


def test_ollama_root_strips_v1():
    assert _ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert _ollama_root("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"


def test_model_available_matches_tags():
    models = ["qwen3:8b", "llava:latest", "gemma3:latest"]
    assert model_available("qwen3:8b", models) is True
    assert model_available("llava", models) is True
    assert model_available("missing-model", models) is False
