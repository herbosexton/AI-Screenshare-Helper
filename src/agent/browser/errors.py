"""Normalized browser error codes. No secrets in messages."""

from __future__ import annotations

from typing import Any, Optional


class BrowserUnavailable(Exception):
    def __init__(self, message: str, code: str = "UNSUPPORTED_BROWSER"):
        super().__init__(message)
        self.code = code


class BrowserError(Exception):
    def __init__(
        self,
        message: str,
        code: str = "BROWSER_ERROR",
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


BROWSER_NOT_RUNNING = "BROWSER_NOT_RUNNING"
SESSION_DISCONNECTED = "SESSION_DISCONNECTED"
TAB_NOT_FOUND = "TAB_NOT_FOUND"
ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
ELEMENT_NOT_INTERACTABLE = "ELEMENT_NOT_INTERACTABLE"
AMBIGUOUS_ELEMENT = "AMBIGUOUS_ELEMENT"
NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
PAGE_LOAD_TIMEOUT = "PAGE_LOAD_TIMEOUT"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
UPLOAD_FAILED = "UPLOAD_FAILED"
AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
DIALOG_BLOCKING = "DIALOG_BLOCKING"
PAGE_CRASHED = "PAGE_CRASHED"
UNSUPPORTED_BROWSER = "UNSUPPORTED_BROWSER"
VISUAL_FALLBACK_REQUIRED = "VISUAL_FALLBACK_REQUIRED"
URL_BLOCKED = "URL_BLOCKED"
