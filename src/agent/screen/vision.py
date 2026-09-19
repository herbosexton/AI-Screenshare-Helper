"""VisionProvider — local (Ollama) or any AIProvider that accepts images. No planner tools."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional, Protocol

from src.agent.screen.errors import SCREEN_ANALYSIS_TIMEOUT, VISION_PROVIDER_ERROR, ScreenError


PROMPT_DESCRIBE = (
    "Describe the visible user interface. Mention the active application, "
    "important visible content, dialogs, buttons, fields, and obvious errors. "
    "Do not infer hidden information. Two short sentences."
)

PROMPT_FIND = (
    "Locate the visible UI element requested. Coordinates are pixels in THIS image, "
    "origin at the top-left of the image. Return JSON only: "
    '{"candidates":[{"label":"","role":"","x":0,"y":0,"width":0,"height":0,"confidence":0.0,"clickable":true}]}'
)

PROMPT_VERIFY = (
    "Compare the before and after screenshots. Did the requested UI action succeed? "
    'JSON only: {"changed":true,"likelySuccess":true,"confidence":0.0,"summary":""}'
)

PROMPT_DIALOG = (
    "If a dialog, popup, or error is visible, extract its title, message, and button labels. "
    'JSON: {"dialogType":"","title":"","message":"","buttons":[]}'
)

PROMPT_ERROR = (
    "Extract the visible error or warning message. If none, say none. One or two sentences. "
    "Do not invent text that is not visible."
)


class VisionProvider(Protocol):
    name: str

    def analyze(
        self,
        image_b64: str,
        prompt: str,
        *,
        timeout_s: Optional[float] = None,
    ) -> dict[str, Any]: ...


def _ns_ms(value: Any) -> float:
    try:
        return round(float(value or 0) / 1_000_000.0, 1)
    except (TypeError, ValueError):
        return 0.0


class LocalVisionProvider:
    """Wraps an existing AIProvider.chat(images_base64=) implementation."""

    name = "local"

    def __init__(self, provider, *, timeout_s: float = 40.0, model: str = ""):
        self._provider = provider
        self.timeout_s = timeout_s
        self.model = model or getattr(provider, "vision_model", "") or "llava"
        self.calls = 0
        self.last_stats: dict[str, Any] = {}

    def analyze(self, image_b64: str, prompt: str, *, timeout_s: Optional[float] = None) -> dict[str, Any]:
        if self._provider is None:
            raise ScreenError("No vision provider configured", VISION_PROVIDER_ERROR, retryable=False)
        if not image_b64:
            raise ScreenError("No screenshot for vision", VISION_PROVIDER_ERROR, retryable=False)
        limit = timeout_s if timeout_s is not None else self.timeout_s
        self.calls += 1
        t0 = time.perf_counter()
        text = ""
        stats: dict[str, Any] = {}
        try:
            native = self._analyze_ollama_native(image_b64, prompt, limit)
            if native is not None:
                text, stats = native
            else:
                response = self._provider.chat(
                    [
                        {"role": "system", "content": "You analyze screenshots. Be concise. Do not plan actions."},
                        {"role": "user", "content": prompt},
                    ],
                    tools=None,
                    images_base64=[image_b64],
                )
                text = (getattr(response, "content", None) or "").strip()
        except ScreenError:
            raise
        except Exception as e:
            name = type(e).__name__
            if "Timeout" in name or "timed out" in str(e).lower():
                raise ScreenError(
                    f"Screen analysis timed out after {limit:.0f}s",
                    SCREEN_ANALYSIS_TIMEOUT,
                    retryable=True,
                ) from e
            raise ScreenError(str(e), VISION_PROVIDER_ERROR, retryable=True) from e
        elapsed = (time.perf_counter() - t0) * 1000
        self.last_stats = {
            **stats,
            "wall_ms": round(elapsed, 1),
            "model": self.model,
        }
        return {
            "text": text,
            "model": self.model,
            "elapsed_ms": round(elapsed, 1),
            "json": _try_json(text),
            "stats": self.last_stats,
        }

    def warmup(self, timeout_s: float = 40.0) -> dict[str, Any]:
        """Keep llava resident. Tiny image; local-only."""
        import base64
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (768, 416), "white").save(buf, format="JPEG", quality=50)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return self.analyze(b64, "Reply with the single word ok.", timeout_s=timeout_s)

    def _analyze_ollama_native(self, image_b64: str, prompt: str, timeout_s: float) -> Optional[tuple[str, dict[str, Any]]]:
        base = getattr(self._provider, "base_url", "") or ""
        if "11434" not in str(base):
            return None
        import httpx

        root = str(base).rstrip("/").removesuffix("/v1")
        raw = image_b64
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            r = httpx.post(
                f"{root}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "images": [raw],
                    "stream": False,
                    "keep_alive": "60m",
                    "options": {"temperature": 0.1, "num_predict": 120},
                },
                timeout=timeout_s,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.TimeoutException as e:
            raise ScreenError(
                f"Screen analysis timed out after {timeout_s:.0f}s",
                SCREEN_ANALYSIS_TIMEOUT,
                retryable=True,
            ) from e
        except Exception:
            return None
        eval_count = int(data.get("eval_count") or 0)
        eval_ns = float(data.get("eval_duration") or 0)
        tokens_per_sec = round(eval_count / (eval_ns / 1e9), 2) if eval_count and eval_ns else 0.0
        stats = {
            "backend": "ollama_native",
            "load_duration_ms": _ns_ms(data.get("load_duration")),
            "prompt_eval_count": int(data.get("prompt_eval_count") or 0),
            "prompt_eval_duration_ms": _ns_ms(data.get("prompt_eval_duration")),
            "eval_count": eval_count,
            "eval_duration_ms": _ns_ms(eval_ns),
            "total_duration_ms": _ns_ms(data.get("total_duration")),
            "tokens_per_sec": tokens_per_sec,
            "done_reason": data.get("done_reason") or "",
        }
        return (data.get("response") or "").strip(), stats


def ollama_ps(base_url: str = "http://127.0.0.1:11434/v1") -> dict[str, Any]:
    """Loaded models / device residency from Ollama."""
    import httpx

    root = str(base_url).rstrip("/").removesuffix("/v1")
    try:
        r = httpx.get(f"{root}/api/ps", timeout=3.0)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        return {"ok": False, "error": str(e), "models": []}
    models = []
    for m in data.get("models") or []:
        models.append(
            {
                "name": m.get("name") or m.get("model"),
                "size": m.get("size"),
                "size_vram": m.get("size_vram"),
                "expires_at": m.get("expires_at"),
                "details": m.get("details") or {},
            }
        )
    return {"ok": True, "models": models}


def _try_json(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None
