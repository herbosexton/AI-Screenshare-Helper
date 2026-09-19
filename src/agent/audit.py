from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: Optional[str] = None
    tool: str
    action: str
    target: str = ""
    result: str = ""
    permission_level: int = 0
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


SENSITIVE_KEYS = {
    "password",
    "api_key",
    "apikey",
    "token",
    "secret",
    "authorization",
    "private_key",
    "access_token",
    "refresh_token",
}


def redact_secrets(data: Any) -> Any:
    """Recursively redact sensitive values from structures before logging/LLM."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if any(s in str(k).lower() for s in SENSITIVE_KEYS):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(data, list):
        return [redact_secrets(x) for x in data]
    if isinstance(data, str):
        lower = data.lower()
        if "sk-" in lower or "api_key=" in lower or "bearer " in lower:
            return "[REDACTED]"
        return data
    return data


class AuditLog:
    """In-memory + optional file audit trail (no credentials)."""

    def __init__(self, max_entries: int = 5000):
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()
        self._max = max_entries

    def record(
        self,
        tool: str,
        action: str,
        *,
        task_id: Optional[str] = None,
        target: str = "",
        result: Any = "",
        permission_level: int = 0,
        duration_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ) -> AuditEntry:
        safe_result = redact_secrets(result)
        if not isinstance(safe_result, str):
            safe_result = str(safe_result)[:2000]
        else:
            safe_result = safe_result[:2000]

        entry = AuditEntry(
            task_id=task_id,
            tool=tool,
            action=action,
            target=target[:500],
            result=safe_result,
            permission_level=permission_level,
            duration_ms=duration_ms,
            success=success,
            error=error,
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max:
                self._entries = self._entries[-self._max :]
        return entry

    def list_recent(self, limit: int = 50) -> list[AuditEntry]:
        with self._lock:
            return list(self._entries[-limit:])

    def for_task(self, task_id: str) -> list[AuditEntry]:
        with self._lock:
            return [e for e in self._entries if e.task_id == task_id]
