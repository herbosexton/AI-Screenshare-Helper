"""Ensure the local Ollama app/server is running for Jarvis."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import httpx


def _ollama_root(base_url: str) -> str:
    return base_url.rstrip("/").replace("/v1", "")


def is_ollama_up(base_url: str = "http://127.0.0.1:11434/v1", timeout_s: float = 2.5) -> bool:
    root = _ollama_root(base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(f"{root}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def find_ollama_executable() -> Optional[str]:
    which = shutil.which("ollama")
    if which:
        return which
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Ollama" / "ollama.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Ollama" / "ollama.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def start_ollama(exe: Optional[str] = None) -> dict[str, Any]:
    """
    Launch Ollama on Windows.
    Prefer the GUI app entry (`ollama app`) so the tray service stays up,
    fall back to `ollama serve`.
    """
    exe = exe or find_ollama_executable()
    if not exe:
        return {
            "ok": False,
            "error": (
                "Ollama is not installed or not on PATH. "
                "Install from https://ollama.com/download then restart Jarvis."
            ),
        }

    creation = 0
    if os.name == "nt":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )

    attempts: list[str] = []
    # 1) ollama app (desktop helper that keeps the server alive)
    try:
        subprocess.Popen(  # noqa: S603
            [exe, "app"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"),
        )
        attempts.append("ollama app")
    except Exception as e:
        attempts.append(f"ollama app failed: {e}")

    # 2) ollama serve as fallback
    try:
        subprocess.Popen(  # noqa: S603
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"),
        )
        attempts.append("ollama serve")
    except Exception as e:
        attempts.append(f"ollama serve failed: {e}")

    return {"ok": True, "exe": exe, "attempts": attempts}


def wait_until_ready(
    base_url: str = "http://127.0.0.1:11434/v1",
    timeout_s: float = 60.0,
    poll_s: float = 0.75,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_ollama_up(base_url):
            return True
        time.sleep(poll_s)
    return False


def list_models(base_url: str = "http://127.0.0.1:11434/v1") -> list[str]:
    root = _ollama_root(base_url)
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{root}/api/tags")
            r.raise_for_status()
            return [m.get("name") for m in (r.json().get("models") or []) if m.get("name")]
    except Exception:
        return []


def model_available(model: str, models: list[str]) -> bool:
    if not model:
        return False
    needle = model.lower()
    for name in models:
        n = name.lower()
        if n == needle or n.startswith(needle + ":") or needle.startswith(n.split(":")[0]):
            # exact or tag-compatible match
            if n == needle or n.rsplit(":", 1)[0] == needle.rsplit(":", 1)[0]:
                return True
            if n == needle:
                return True
    # also accept if any model starts with base name
    base = needle.split(":")[0]
    return any(m.lower().split(":")[0] == base for m in models)


def ensure_ollama_running(
    base_url: str = "http://127.0.0.1:11434/v1",
    *,
    model: Optional[str] = None,
    startup_timeout_s: float = 60.0,
    auto_start: bool = True,
) -> dict[str, Any]:
    """
    Make sure Ollama is reachable. If not, start it and wait.
    Does not auto-pull large models (that can take a long time); reports missing models.
    """
    if is_ollama_up(base_url):
        models = list_models(base_url)
        result: dict[str, Any] = {
            "ok": True,
            "started": False,
            "base_url": base_url,
            "models": models,
            "message": "Ollama is already running",
        }
        if model and not model_available(model, models):
            result["model_missing"] = model
            result["message"] = (
                f"Ollama is running, but model '{model}' was not found. "
                f"Run: ollama pull {model}"
            )
        return result

    if not auto_start:
        return {
            "ok": False,
            "started": False,
            "error": "Ollama is not running and auto_start is disabled",
        }

    print("[Ollama] Not running — starting Ollama…")
    start = start_ollama()
    if not start.get("ok"):
        return {"ok": False, "started": False, "error": start.get("error")}

    ready = wait_until_ready(base_url, timeout_s=startup_timeout_s)
    if not ready:
        return {
            "ok": False,
            "started": True,
            "exe": start.get("exe"),
            "error": (
                f"Started Ollama ({start.get('exe')}) but it did not become ready "
                f"within {startup_timeout_s:.0f}s at {base_url}"
            ),
        }

    models = list_models(base_url)
    result = {
        "ok": True,
        "started": True,
        "exe": start.get("exe"),
        "attempts": start.get("attempts"),
        "base_url": base_url,
        "models": models,
        "message": "Ollama started successfully",
    }
    if model and not model_available(model, models):
        result["model_missing"] = model
        result["message"] = (
            f"Ollama started, but model '{model}' is missing. Run: ollama pull {model}"
        )
    print(f"[Ollama] Ready — models: {', '.join(models[:8]) or '(none)'}")
    return result
