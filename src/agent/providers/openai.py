"""OpenAI-compatible cloud provider for JARVIS.

Used as the planner brain in hybrid mode: cloud quality for multi-step planning,
local speed for everything else.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional
from uuid import uuid4

import httpx

from src.agent.providers.base import AIProvider, ProviderResponse, ToolCallRequest


class OpenAIProvider(AIProvider):
    """Cloud provider using the OpenAI chat completions API."""

    name = "openai"

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._last_prompt_tokens = 0
        self._last_tool_tokens = 0
        self._cancel = threading.Event()
        self._active_client: Optional[httpx.Client] = None

    def cancel_inflight(self) -> None:
        self._cancel.set()
        client = self._active_client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "ok": False,
                "provider": self.name,
                "error": "No OPENAI_API_KEY set. Add it to your environment or config.yaml.",
            }
        return {"ok": True, "provider": self.name, "model": self.model}

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
        if not self.api_key:
            raise RuntimeError(
                "No OPENAI_API_KEY configured. Set it as an environment variable "
                "or add cloud_api_key to the agent section of config.yaml."
            )

        payload_messages = list(messages)
        if images_base64:
            last = dict(payload_messages[-1]) if payload_messages else {"role": "user", "content": ""}
            content_parts: list[dict[str, Any]] = []
            text = last.get("content", "")
            if isinstance(text, str):
                content_parts.append({"type": "text", "text": text})
            elif isinstance(text, list):
                content_parts.extend(text)
            for b64 in images_base64:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
            last["content"] = content_parts
            if payload_messages:
                payload_messages[-1] = last
            else:
                payload_messages = [last]

        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": 0.3,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
            body["temperature"] = 0.1
        if max_tokens:
            body["max_tokens"] = int(max_tokens)

        tool_json = json.dumps(tools or [])
        msg_json = json.dumps(payload_messages)
        self._last_tool_tokens = max(1, len(tool_json) // 4)
        self._last_prompt_tokens = max(1, (len(msg_json) + len(tool_json)) // 4)
        print(
            f"[Perf] cloud model={self.model} prompt~{self._last_prompt_tokens} tok "
            f"tools~{self._last_tool_tokens} tok n_tools={len(tools or [])}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        budget = timeout_s or self.timeout_s
        self._cancel.clear()

        try:
            with httpx.Client(timeout=budget) as client:
                self._active_client = client
                try:
                    r = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=body,
                    )
                    r.raise_for_status()
                    data = r.json()
                except (httpx.HTTPError, httpx.TimeoutException):
                    if self._cancel.is_set():
                        raise RuntimeError("Cloud request cancelled.")
                    raise
                finally:
                    self._active_client = None
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"Cloud planner timed out after {budget:.0f}s."
            ) from e
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 401:
                raise RuntimeError(
                    "OpenAI API key is invalid. Check your OPENAI_API_KEY."
                ) from e
            if status == 429:
                raise RuntimeError(
                    "OpenAI rate limit reached. Wait a moment or add billing credits."
                ) from e
            raise RuntimeError(f"OpenAI API error ({status}): {e}") from e

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = (message.get("content") or "").strip()

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
