"""PermissionContext — session/task-scoped grants with expiration.

Tracks what the user has authorized, for which task, and when it expires.
A grant is always specific, bounded, and revocable.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ScopedGrant:
    """A bounded authorization for specific actions/targets/durations."""

    id: str
    task_id: str
    actions: frozenset[str]       # e.g. {"move", "rename"} or {"*"} for any
    target_pattern: str = ""      # e.g. "C:\\Users\\...\\Documents\\*" or "" for any
    granted_at: float = field(default_factory=time.time)
    expires_at: float = 0.0      # 0 = expires when task completes
    revoked: bool = False
    reason: str = ""

    def is_valid(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at > 0 and time.time() > self.expires_at:
            return False
        return True

    def covers(self, action: str, target: str = "", task_id: str = "") -> bool:
        """Does this grant authorize the given action on the given target?"""
        if not self.is_valid():
            return False
        if task_id and self.task_id and task_id != self.task_id:
            return False
        if "*" not in self.actions and action not in self.actions:
            return False
        if self.target_pattern and target:
            # Simple prefix match — not glob
            pattern = self.target_pattern.rstrip("*").rstrip("\\").rstrip("/")
            if not target.lower().startswith(pattern.lower()):
                return False
        return True


@dataclass
class ApprovalRecord:
    """A record of a user approval decision."""

    approval_id: str
    action_id: str
    task_id: str
    tool: str
    target: str
    arguments_hash: str
    approved: bool
    timestamp: float = field(default_factory=time.time)


class PermissionContext:
    """Tracks temporary user authorizations for a session.

    Thread-safe.  Grants are always specific, bounded, and revocable.
    """

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._grants: list[ScopedGrant] = []
        self._approvals: list[ApprovalRecord] = []
        self._denied: set[str] = set()  # action_ids explicitly denied
        self._lock = threading.Lock()

    # --- Grants ---

    def add_grant(self, grant: ScopedGrant) -> None:
        with self._lock:
            self._grants.append(grant)

    def has_grant(self, action: str, target: str = "", task_id: str = "") -> Optional[ScopedGrant]:
        """Return the first valid grant covering this action, or None."""
        with self._lock:
            for g in self._grants:
                if g.covers(action, target, task_id):
                    return g
        return None

    def revoke_grant(self, grant_id: str) -> bool:
        with self._lock:
            for g in self._grants:
                if g.id == grant_id:
                    g.revoked = True
                    return True
        return False

    def revoke_task_grants(self, task_id: str) -> int:
        """Revoke all grants for a specific task."""
        count = 0
        with self._lock:
            for g in self._grants:
                if g.task_id == task_id and not g.revoked:
                    g.revoked = True
                    count += 1
        return count

    def revoke_all(self) -> int:
        """Revoke all active grants."""
        count = 0
        with self._lock:
            for g in self._grants:
                if not g.revoked:
                    g.revoked = True
                    count += 1
        return count

    def active_grants(self) -> list[ScopedGrant]:
        with self._lock:
            return [g for g in self._grants if g.is_valid()]

    # --- Approvals ---

    def record_approval(self, record: ApprovalRecord) -> None:
        with self._lock:
            self._approvals.append(record)
            if not record.approved:
                self._denied.add(record.action_id)

    def was_denied(self, action_id: str) -> bool:
        with self._lock:
            return action_id in self._denied

    def recent_approvals(self, limit: int = 20) -> list[ApprovalRecord]:
        with self._lock:
            return list(self._approvals[-limit:])

    # --- Cleanup ---

    def expire_stale(self) -> int:
        """Remove expired grants. Called periodically."""
        count = 0
        now = time.time()
        with self._lock:
            for g in self._grants:
                if not g.revoked and g.expires_at > 0 and now > g.expires_at:
                    g.revoked = True
                    count += 1
        return count
