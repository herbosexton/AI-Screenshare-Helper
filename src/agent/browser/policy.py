"""URL scheme/host allow/deny for browser tools. Enforced outside the LLM."""

from __future__ import annotations

import re
from urllib.parse import urlparse


class UrlPolicyError(Exception):
    pass


# "google.com", "www.example.co.uk/a?b=c", "localhost:3000" — a host the user or the
# planner wrote the way people say it. Anything carrying its own scheme, including
# javascript: and data:, fails to match here and goes on to the scheme check unchanged.
_BARE_HOST = re.compile(
    r"^(?:(?:[a-z0-9-]+\.)+[a-z]{2,}|localhost)(?::\d{1,5})?(?:[/?#].*)?$",
    re.I,
)


ALLOWED_SCHEMES = frozenset({"http", "https"})
DENIED_SCHEMES = frozenset(
    {"file", "javascript", "data", "chrome", "about", "vbscript", "blob"}
)


def _host(parsed) -> str:
    return (parsed.hostname or "").lower().rstrip(".")


def _host_matches(host: str, pattern: str) -> bool:
    pat = (pattern or "").lower().strip().rstrip(".")
    if not pat or not host:
        return False
    return host == pat or host.endswith("." + pat)


class UrlPolicy:
    def __init__(
        self,
        allowed_hosts: list[str] | None = None,
        blocked_hosts: list[str] | None = None,
    ):
        self.allowed_hosts = [h.strip() for h in (allowed_hosts or []) if h and h.strip()]
        self.blocked_hosts = [h.strip() for h in (blocked_hosts or []) if h and h.strip()]

    def check(self, url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            raise UrlPolicyError("URL is empty")

        lower = raw.lower()
        if lower in {"about:blank", "about://blank"}:
            return "about:blank"

        # "go to google.com" is a URL to everyone except urlparse, which reads it as a
        # path. Supplying the scheme here keeps the allow/deny rules below untouched.
        if "://" not in raw and _BARE_HOST.match(raw):
            raw = f"https://{raw}"

        parsed = urlparse(raw)
        scheme = (parsed.scheme or "").lower()
        if not scheme:
            raise UrlPolicyError("URL must include a scheme (http or https)")

        if scheme == "about" and (parsed.path or parsed.netloc) == "blank":
            return "about:blank"

        if scheme in DENIED_SCHEMES or scheme not in ALLOWED_SCHEMES:
            raise UrlPolicyError(f"Blocked URL scheme: {scheme or 'unknown'}")

        host = _host(parsed)
        if not host:
            raise UrlPolicyError("URL is missing a host")

        for blocked in self.blocked_hosts:
            if _host_matches(host, blocked):
                raise UrlPolicyError(f"Host is blocked: {host}")

        if self.allowed_hosts:
            if not any(_host_matches(host, allowed) for allowed in self.allowed_hosts):
                raise UrlPolicyError(f"Host is not in the allowlist: {host}")

        return raw
