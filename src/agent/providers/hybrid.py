"""Hybrid provider: cloud for planning, local for chat.

The planner needs structured JSON output, long context windows, and reliable
instruction following — that's what a cloud model excels at. Chat, conversation,
and quick single-command responses are fine on a local model and cost nothing.
"""

from __future__ import annotations

from typing import Any, Optional

from src.agent.providers.base import AIProvider, ProviderResponse


class HybridProvider(AIProvider):
    """Routes to cloud or local depending on the call type.

    Heuristic: if ``json_mode=True`` or a ``planner`` hint is in the messages,
    the call goes to the cloud provider. Everything else stays local.
    """

    name = "hybrid"

    def __init__(self, local: AIProvider, cloud: AIProvider):
        self.local = local
        self.cloud = cloud
        self._last_prompt_tokens = 0
        self._last_tool_tokens = 0

    def _pick(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
    ) -> tuple[AIProvider, str]:
        """Decide which provider handles this call."""
        if json_mode:
            return self.cloud, "cloud(json_mode)"

        # The planner system prompt contains "Plan how a Windows desktop agent"
        for msg in messages[:2]:
            content = msg.get("content", "")
            if isinstance(content, str) and "Plan how a Windows desktop agent" in content:
                return self.cloud, "cloud(planner)"

        return self.local, "local"

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
        provider, reason = self._pick(messages, json_mode=json_mode)
        print(f"[Hybrid] -> {reason} ({provider.name}:{getattr(provider, 'model', '?')})")

        kwargs: dict[str, Any] = {}
        if images_base64 is not None:
            kwargs["images_base64"] = images_base64
        if json_mode:
            kwargs["json_mode"] = json_mode
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout_s is not None:
            kwargs["timeout_s"] = timeout_s

        try:
            result = provider.chat(messages, tools, **kwargs)
        except TypeError:
            # Provider doesn't support all kwargs (e.g. old Ollama without json_mode)
            result = provider.chat(messages, tools)

        self._last_prompt_tokens = getattr(provider, "_last_prompt_tokens", 0)
        self._last_tool_tokens = getattr(provider, "_last_tool_tokens", 0)
        return result

    def cancel_inflight(self) -> None:
        self.local.cancel_inflight()
        self.cloud.cancel_inflight()

    def health_check(self) -> dict[str, Any]:
        local_health = self.local.health_check()
        cloud_health = self.cloud.health_check()
        return {
            "ok": local_health.get("ok", False) and cloud_health.get("ok", False),
            "provider": self.name,
            "local": local_health,
            "cloud": cloud_health,
        }

    def warmup(self) -> None:
        """Warm the local model; cloud doesn't need it."""
        warm = getattr(self.local, "warmup", None)
        if callable(warm):
            warm()
