"""SecretRedactor — prevent secrets from leaking into logs, TTS, or planner prompts.

Extends the existing audit.redact_secrets() with patterns for:
- API keys (sk-*, AKIA*, ghp_*, etc.)
- Bearer/Authorization headers
- Password field values
- Private keys (BEGIN ... PRIVATE KEY)
- Session tokens and JWTs
- Credit card numbers
- SSNs
"""

from __future__ import annotations

import re
from typing import Any


# Patterns that match secret values in free text
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # API keys
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgho_[A-Za-z0-9]{36,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_GITLAB_TOKEN]"),
    (re.compile(r"\bxox[bpsar]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),

    # Authorization headers
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_.~+/=-]{20,}\b"), "[REDACTED_BEARER]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{10,}\b"), "[REDACTED_BASIC_AUTH]"),
    (re.compile(r"(?i)Authorization:\s*\S+"), "[REDACTED_AUTH_HEADER]"),

    # JWTs
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
     "[REDACTED_JWT]"),

    # Private keys
    (re.compile(r"-----BEGIN\s+(?:RSA\s+)?(?:EC\s+)?PRIVATE\s+KEY-----.*?-----END\s+(?:RSA\s+)?(?:EC\s+)?PRIVATE\s+KEY-----",
                re.DOTALL), "[REDACTED_PRIVATE_KEY]"),

    # Connection strings with passwords
    (re.compile(r"(?i)(?:password|pwd)\s*[=:]\s*\S+"), "[REDACTED_PASSWORD]"),

    # Credit card numbers (basic pattern — 13-19 digits with optional separators)
    (re.compile(r"\b(?:\d{4}[-\s]?){3,4}\d{1,4}\b"), "[REDACTED_CARD]"),

    # SSN patterns
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
]

# Keys in dicts whose values should always be redacted
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd",
    "api_key", "apikey", "api-key",
    "secret", "secret_key", "secretkey",
    "token", "access_token", "refresh_token", "auth_token",
    "authorization", "auth",
    "private_key", "privatekey",
    "session_id", "session_token",
    "cookie", "cookies",
    "credit_card", "card_number", "cvv", "cvc",
    "ssn", "social_security",
    "pin", "otp", "one_time_password",
    "recovery_code",
})


def redact_text(text: str) -> str:
    """Redact secret patterns from a string."""
    if not text:
        return text
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_dict(data: Any) -> Any:
    """Recursively redact sensitive values from dicts/lists."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            key_lower = str(k).lower().replace("-", "_").replace(" ", "_")
            if key_lower in _SENSITIVE_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_dict(v)
        return out
    if isinstance(data, list):
        return [redact_dict(x) for x in data]
    if isinstance(data, str):
        return redact_text(data)
    return data


def is_sensitive_for_tts(text: str) -> bool:
    """True if the text contains patterns that should not be spoken aloud."""
    if not text:
        return False
    for pattern, _ in _SECRET_PATTERNS:
        if pattern.search(text):
            return True
    return False


def redact_for_tts(text: str) -> str:
    """Redact secrets and replace with speech-safe placeholders."""
    if not text:
        return text
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        # For TTS, use a spoken description instead of bracket notation
        spoken = replacement.replace("[", "").replace("]", "").replace("_", " ").lower()
        result = pattern.sub(f"a {spoken}", result)
    return result
