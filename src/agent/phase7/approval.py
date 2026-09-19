"""ApprovalRequest lifecycle — creation, expiration, binding, resolution.

An approval request is bound to a specific ActionEnvelope. If the action's
arguments change after approval, the approval is invalidated. Approvals
expire after a configurable timeout.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    EXECUTED = "executed"


class Phase7ApprovalRequest(BaseModel):
    """An approval request bound to a specific action.

    The approval is valid only when:
    - status is PENDING
    - not expired (created_at + ttl_seconds > now)
    - arguments_hash matches the current ActionEnvelope
    - task_id matches the current task
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    step_id: str = ""
    action_id: str = ""           # ActionEnvelope.id this is bound to
    arguments_hash: str = ""      # Must match envelope's hash for TOCTOU

    # Human-readable fields for the approval UI
    title: str = ""               # e.g. "Submit job application"
    description: str = ""         # e.g. "Submit to Example Corp for Data Analyst"
    tool: str = ""
    target: str = ""              # e.g. "person@example.com" or "report.pdf"
    consequence: str = ""         # e.g. "This will send the email"
    reversible: bool = True
    risk_level: int = 0

    # Preview data for the user
    preview: dict[str, Any] = Field(default_factory=dict)

    # Scope the user is granting
    requested_scope: str = ""     # e.g. "send this email"

    # Lifecycle
    status: ApprovalStatus = ApprovalStatus.PENDING
    ttl_seconds: float = 120.0    # 2 minutes default
    created_at: float = Field(default_factory=time.time)
    resolved_at: float = 0.0
    resolved_by: str = ""         # "voice" | "hud" | "timeout"

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING and not self.is_expired

    @property
    def is_expired(self) -> bool:
        if self.status != ApprovalStatus.PENDING:
            return False
        return time.time() > self.created_at + self.ttl_seconds

    def check_expiry(self) -> bool:
        """Mark as EXPIRED if past TTL. Returns True if expired."""
        if self.is_expired:
            self.status = ApprovalStatus.EXPIRED
            self.resolved_at = time.time()
            self.resolved_by = "timeout"
            return True
        return False

    def approve(self, by: str = "voice") -> None:
        if not self.is_pending:
            raise ValueError(f"Cannot approve: status is {self.status}")
        self.status = ApprovalStatus.APPROVED
        self.resolved_at = time.time()
        self.resolved_by = by

    def deny(self, by: str = "voice") -> None:
        if not self.is_pending:
            raise ValueError(f"Cannot deny: status is {self.status}")
        self.status = ApprovalStatus.DENIED
        self.resolved_at = time.time()
        self.resolved_by = by

    def invalidate(self, reason: str = "") -> None:
        """Mark as INVALIDATED because the action changed."""
        if self.status == ApprovalStatus.PENDING:
            self.status = ApprovalStatus.INVALIDATED
            self.resolved_at = time.time()
            self.resolved_by = f"invalidated: {reason}" if reason else "invalidated"

    def cancel(self) -> None:
        if self.status == ApprovalStatus.PENDING:
            self.status = ApprovalStatus.CANCELLED
            self.resolved_at = time.time()
            self.resolved_by = "cancelled"

    def mark_executed(self) -> None:
        if self.status == ApprovalStatus.APPROVED:
            self.status = ApprovalStatus.EXECUTED
            self.resolved_at = time.time()

    def matches_envelope(self, arguments_hash: str) -> bool:
        """True if this approval still matches the action it was created for."""
        return self.arguments_hash == arguments_hash

    def human_summary(self) -> str:
        """Human-readable description for voice/HUD."""
        parts = []
        if self.title:
            parts.append(self.title)
        if self.target:
            parts.append(f"Target: {self.target}")
        if self.consequence:
            parts.append(self.consequence)
        if not self.reversible:
            parts.append("This cannot be undone.")
        return ". ".join(parts) if parts else f"Approve {self.tool}?"


class ApprovalManager:
    """Manages the lifecycle of approval requests.

    At most one voice-addressable approval is active at a time.
    """

    def __init__(self):
        self._pending: Optional[Phase7ApprovalRequest] = None
        self._history: list[Phase7ApprovalRequest] = []

    @property
    def active(self) -> Optional[Phase7ApprovalRequest]:
        """The currently pending approval, or None."""
        if self._pending is not None:
            self._pending.check_expiry()
            if not self._pending.is_pending:
                self._history.append(self._pending)
                self._pending = None
        return self._pending

    def create(
        self,
        *,
        task_id: str = "",
        step_id: str = "",
        action_id: str = "",
        arguments_hash: str = "",
        title: str = "",
        description: str = "",
        tool: str = "",
        target: str = "",
        consequence: str = "",
        reversible: bool = True,
        risk_level: int = 0,
        preview: Optional[dict[str, Any]] = None,
        requested_scope: str = "",
        ttl_seconds: float = 120.0,
    ) -> Phase7ApprovalRequest:
        """Create a new approval request, replacing any existing pending one."""
        # Cancel any existing pending approval
        if self._pending is not None and self._pending.is_pending:
            self._pending.cancel()
            self._history.append(self._pending)

        self._pending = Phase7ApprovalRequest(
            task_id=task_id,
            step_id=step_id,
            action_id=action_id,
            arguments_hash=arguments_hash,
            title=title,
            description=description,
            tool=tool,
            target=target,
            consequence=consequence,
            reversible=reversible,
            risk_level=risk_level,
            preview=preview or {},
            requested_scope=requested_scope,
            ttl_seconds=ttl_seconds,
        )
        return self._pending

    def resolve_voice(self, approved: bool) -> Optional[Phase7ApprovalRequest]:
        """Resolve the active approval via voice yes/no.

        Returns the resolved approval, or None if there's nothing pending.
        Stale-yes protection: if no approval is pending, returns None and
        the "yes" is treated as ordinary speech.
        """
        current = self.active
        if current is None:
            return None  # Stale yes — no pending approval

        if approved:
            current.approve(by="voice")
        else:
            current.deny(by="voice")

        self._history.append(current)
        self._pending = None
        return current

    def resolve_hud(self, approval_id: str, approved: bool) -> Optional[Phase7ApprovalRequest]:
        """Resolve a specific approval via HUD button click."""
        current = self.active
        if current is None or current.id != approval_id:
            # Check history for the ID
            return None

        if approved:
            current.approve(by="hud")
        else:
            current.deny(by="hud")

        self._history.append(current)
        self._pending = None
        return current

    def invalidate_current(self, reason: str = "") -> Optional[Phase7ApprovalRequest]:
        """Invalidate the current approval because the action changed."""
        current = self.active
        if current is None:
            return None
        current.invalidate(reason)
        self._history.append(current)
        self._pending = None
        return current

    def cancel_all(self) -> int:
        """Cancel all pending approvals (e.g. on emergency stop)."""
        count = 0
        if self._pending is not None and self._pending.is_pending:
            self._pending.cancel()
            self._history.append(self._pending)
            self._pending = None
            count += 1
        return count

    def history(self, limit: int = 50) -> list[Phase7ApprovalRequest]:
        return list(self._history[-limit:])
