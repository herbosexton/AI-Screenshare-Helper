from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolCallRequest(BaseModel):
    id: str = "call_0"
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProviderResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    raw: Optional[dict[str, Any]] = None


class AIProvider(ABC):
    """Abstraction for local or cloud chat+tools providers."""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        images_base64: Optional[list[str]] = None,
    ) -> ProviderResponse:
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name}

    def cancel_inflight(self) -> None:
        return
