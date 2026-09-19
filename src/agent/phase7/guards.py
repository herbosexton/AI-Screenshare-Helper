"""Safety guards — duplicate action detection, rate protection, dry-run mode.

These sit alongside the PermissionPolicyEngine to catch runaway loops,
accidental double-submissions, and provide a preview-without-execute mode.
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from typing import Any, Optional

from src.agent.phase7.envelope import ActionEnvelope, RiskLevel


class DuplicateActionGuard:
    """Prevents the same external action from executing twice.

    Tracks action fingerprints (tool + target + arguments_hash) and blocks
    duplicates within a configurable window.
    """

    def __init__(self, window_seconds: float = 30.0):
        self._window = window_seconds
        self._recent: dict[str, float] = {}  # fingerprint -> timestamp
        self._lock = threading.Lock()

    def _fingerprint(self, envelope: ActionEnvelope) -> str:
        if not envelope.arguments_hash:
            envelope.compute_arguments_hash()
        return f"{envelope.tool}:{envelope.target}:{envelope.arguments_hash}"

    def check(self, envelope: ActionEnvelope) -> Optional[str]:
        """Return a reason string if this is a duplicate, or None if OK."""
        # Only guard external/destructive actions
        if envelope.risk_level < RiskLevel.DESTRUCTIVE_EXTERNAL:
            return None

        fp = self._fingerprint(envelope)
        now = time.time()

        with self._lock:
            # Clean old entries
            stale = [k for k, t in self._recent.items() if now - t > self._window]
            for k in stale:
                del self._recent[k]

            if fp in self._recent:
                elapsed = now - self._recent[fp]
                return (
                    f"Duplicate action detected: {envelope.tool} on {envelope.target} "
                    f"was already executed {elapsed:.0f}s ago"
                )

        return None

    def record(self, envelope: ActionEnvelope) -> None:
        """Record that this action was executed."""
        if envelope.risk_level < RiskLevel.DESTRUCTIVE_EXTERNAL:
            return
        fp = self._fingerprint(envelope)
        with self._lock:
            self._recent[fp] = time.time()

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()


class RateProtection:
    """Detects repeated external actions and pauses for clarification.

    If the same tool fires more than `max_per_window` times within
    `window_seconds`, further calls are blocked until the user confirms.
    """

    def __init__(self, max_per_window: int = 5, window_seconds: float = 60.0):
        self._max = max_per_window
        self._window = window_seconds
        self._counts: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, envelope: ActionEnvelope) -> Optional[str]:
        """Return a reason string if rate limit hit, or None if OK."""
        # Only rate-limit external actions
        if envelope.risk_level < RiskLevel.DESTRUCTIVE_EXTERNAL:
            return None

        key = envelope.tool
        now = time.time()

        with self._lock:
            timestamps = self._counts[key]
            # Remove old entries
            timestamps[:] = [t for t in timestamps if now - t < self._window]

            if len(timestamps) >= self._max:
                return (
                    f"Rate limit: {envelope.tool} has fired {len(timestamps)} times "
                    f"in the last {self._window:.0f}s. Pausing for safety."
                )

        return None

    def record(self, envelope: ActionEnvelope) -> None:
        """Record that this action was executed."""
        if envelope.risk_level < RiskLevel.DESTRUCTIVE_EXTERNAL:
            return
        with self._lock:
            self._counts[envelope.tool].append(time.time())

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()
