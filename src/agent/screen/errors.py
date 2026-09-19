"""Normalized Phase 3 screen-understanding errors."""

from __future__ import annotations

from typing import Any, Optional


class ScreenError(Exception):
    def __init__(
        self,
        message: str,
        code: str = "SCREEN_ERROR",
        *,
        retryable: bool = True,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "details": self.details,
        }


SCREEN_CAPTURE_UNAVAILABLE = "SCREEN_CAPTURE_UNAVAILABLE"
ACTIVE_WINDOW_UNKNOWN = "ACTIVE_WINDOW_UNKNOWN"
MONITOR_NOT_FOUND = "MONITOR_NOT_FOUND"
SCREEN_ANALYSIS_TIMEOUT = "SCREEN_ANALYSIS_TIMEOUT"
VISION_PROVIDER_ERROR = "VISION_PROVIDER_ERROR"
ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
AMBIGUOUS_ELEMENT = "AMBIGUOUS_ELEMENT"
STALE_SCREEN_STATE = "STALE_SCREEN_STATE"
WINDOW_NOT_FOCUSED = "WINDOW_NOT_FOCUSED"
COORDINATE_CONVERSION_ERROR = "COORDINATE_CONVERSION_ERROR"
UIA_TIMEOUT = "UIA_TIMEOUT"
UIA_UNAVAILABLE = "UIA_UNAVAILABLE"
VISUAL_VERIFICATION_FAILED = "VISUAL_VERIFICATION_FAILED"
