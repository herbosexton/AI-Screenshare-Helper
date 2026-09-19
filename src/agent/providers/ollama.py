from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional
from uuid import uuid4

import httpx

from src.agent.ollama_manager import ensure_ollama_running, is_ollama_up
from src.agent.providers.base import AIProvider, ProviderResponse, ToolCallRequest


class LocalOllamaProvider(AIProvider):
    """
    Local agent brain via Ollama's OpenAI-compatible API.
    Auto-starts the Ollama app/server when it is not running.
    """

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = "qwen3:8b",
        vision_model: str = "llava",
        timeout_s: float = 25.0,
        api_key: str = "ollama",
        auto_start: bool = True,
        startup_timeout_s: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.vision_model = vision_model
        self.timeout_s = timeout_s
        self.api_key = api_key
        self.auto_start = auto_start
        self.startup_timeout_s = startup_timeout_s
        self._last_prompt_tokens = 0
        self._last_tool_tokens = 0
        self._warmed = False
        self._cancel = threading.Event()
        self._active_client: Optional[httpx.Client] = None

    def cancel_inflight(self) -> None:
        """Abort a blocking planner HTTP call so Stop stays responsive."""
        self._cancel.set()
        client = self._active_client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def ensure_ready(self) -> dict[str, Any]:
        return ensure_ollama_running(
            self.base_url,
            model=self.model,
            startup_timeout_s=self.startup_timeout_s,
            auto_start=self.auto_start,
        )

    def health_check(self) -> dict[str, Any]:
        status = self.ensure_ready()
        if status.get("ok"):
            return {
                "ok": True,
                "provider": self.name,
                "base_url": self.base_url,
                "models": status.get("models") or [],
                "started": bool(status.get("started")),
                "message": status.get("message"),
                "model_missing": status.get("model_missing"),
            }
        return {
            "ok": False,
            "provider": self.name,
            "base_url": self.base_url,
            "error": status.get("error")
            or (
                f"Ollama is not reachable at {self.base_url}. "
                "Install Ollama from https://ollama.com/download"
            ),
        }

    def _post_chat(self, body: dict[str, Any], timeout_s: Optional[float] = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self._cancel.clear()
        with httpx.Client(timeout=timeout_s or self.timeout_s) as client:
            self._active_client = client
            try:
                r = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.TimeoutException):
                if self._cancel.is_set():
                    raise RuntimeError("Planner cancelled.")
                raise
            finally:
                self._active_client = None

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        images_base64: Optional[list[str]] = None,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> ProviderResponse:
        if not is_ollama_up(self.base_url):
            ready = self.ensure_ready()
            if not ready.get("ok"):
                raise RuntimeError(ready.get("error") or "Ollama failed to start")

        model = self.vision_model if images_base64 else self.model
        payload_messages = list(messages)

        if images_base64:
            last = (
                dict(payload_messages[-1])
                if payload_messages
                else {"role": "user", "content": ""}
            )
            content_parts: list[dict[str, Any]] = []
            text = last.get("content", "")
            if isinstance(text, str):
                content_parts.append({"type": "text", "text": text})
            elif isinstance(text, list):
                content_parts.extend(text)
            for b64 in images_base64:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    }
                )
            last["content"] = content_parts
            if payload_messages:
                payload_messages[-1] = last
            else:
                payload_messages = [last]

        body: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.3, "num_ctx": 4096},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            # Constrained decoding: the plan comes back parseable and generation stops sooner.
            body["response_format"] = {"type": "json_object"}
            body["options"]["temperature"] = 0.1
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
            body["options"]["num_predict"] = int(max_tokens)

        tool_json = json.dumps(tools or [])
        msg_json = json.dumps(payload_messages)
        self._last_tool_tokens = max(1, len(tool_json) // 4)
        self._last_prompt_tokens = max(1, (len(msg_json) + len(tool_json)) // 4)
        print(
            f"[Perf] model={model} prompt~{self._last_prompt_tokens} tok "
            f"tools~{self._last_tool_tokens} tok n_tools={len(tools or [])}"
        )

        budget = timeout_s or self.timeout_s
        try:
            data = self._post_chat(body, timeout_s=budget)
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"Planner timed out after {budget:.0f}s. Try a shorter command or the fast path."
            ) from e
        except httpx.HTTPError as e:
            if not self.auto_start:
                raise RuntimeError(
                    f"Local Ollama request failed. Is Ollama running? {e}"
                ) from e
            print(f"[Ollama] Request failed ({e}); ensuring Ollama is running and retrying…")
            ready = self.ensure_ready()
            if not ready.get("ok"):
                raise RuntimeError(
                    ready.get("error") or f"Local Ollama request failed: {e}"
                ) from e
            try:
                data = self._post_chat(body, timeout_s=budget)
            except httpx.HTTPError as e2:
                raise RuntimeError(
                    f"Local Ollama request failed after restart attempt: {e2}"
                ) from e2

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        # Qwen3 / thinking models may stash reasoning separately
        if not content and message.get("reasoning"):
            content = ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        tool_calls_raw = message.get("tool_calls") or []
        tool_calls: list[ToolCallRequest] = []
        for tc in tool_calls_raw:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            else:
                args = raw_args
            tool_calls.append(
                ToolCallRequest(
                    id=tc.get("id") or f"call_{uuid4().hex[:8]}",
                    name=fn.get("name") or "",
                    arguments=args if isinstance(args, dict) else {},
                )
            )

        return ProviderResponse(content=content, tool_calls=tool_calls, raw=data)

    def warmup(self) -> None:
        if self._warmed:
            return
        try:
            self.chat([{"role": "user", "content": "ping"}], tools=None)
            self._warmed = True
            print(f"[Ollama] Warm keep-alive set for {self.model}")
        except Exception as e:
            print(f"[Ollama] Warmup skipped: {e}")
