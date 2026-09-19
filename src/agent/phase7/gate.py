"""Phase 7 Execution Gate — the single enforcement point between planning and execution.

This module sits between the DeterministicTaskExecutor (Phase 6) and actual tool
execution.  Every tool call passes through here.  The gate:

1. Creates an ActionEnvelope from the tool call
2. Runs DuplicateActionGuard and RateProtection
3. Evaluates the PermissionPolicyEngine
4. If ALLOW → execute + audit
5. If REQUIRE_APPROVAL → create ApprovalRequest, pause task
6. If DENY/other → block + audit

The gate is deterministic code.  The LLM has no say in security decisions.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from src.agent.phase7.envelope import ActionEnvelope, RiskLevel, create_envelope
from src.agent.phase7.policy import PermissionPolicyEngine, PolicyDecision, PolicyResult
from src.agent.phase7.approval import ApprovalManager, Phase7ApprovalRequest
from src.agent.phase7.audit import AuditEventType, Phase7AuditLog
from src.agent.phase7.guards import DuplicateActionGuard, RateProtection
from src.agent.phase7.redactor import redact_dict


class GateResult:
    """Result of a gate evaluation."""

    __slots__ = (
        "allowed", "executed", "approval_required", "blocked_reason",
        "approval", "policy_result", "envelope", "tool_result",
    )

    def __init__(
        self,
        *,
        allowed: bool = False,
        executed: bool = False,
        approval_required: bool = False,
        blocked_reason: str = "",
        approval: Optional[Phase7ApprovalRequest] = None,
        policy_result: Optional[PolicyResult] = None,
        envelope: Optional[ActionEnvelope] = None,
        tool_result: Any = None,
    ):
        self.allowed = allowed
        self.executed = executed
        self.approval_required = approval_required
        self.blocked_reason = blocked_reason
        self.approval = approval
        self.policy_result = policy_result
        self.envelope = envelope
        self.tool_result = tool_result


class ExecutionGate:
    """The Phase 7 enforcement point.  All tool calls flow through here."""

    def __init__(
        self,
        policy_engine: PermissionPolicyEngine,
        approval_manager: ApprovalManager,
        audit_log: Phase7AuditLog,
        duplicate_guard: Optional[DuplicateActionGuard] = None,
        rate_protection: Optional[RateProtection] = None,
        emergency_check: Optional[Callable[[], bool]] = None,
    ):
        self.policy = policy_engine
        self.approvals = approval_manager
        self.audit = audit_log
        self.duplicates = duplicate_guard or DuplicateActionGuard()
        self.rate = rate_protection or RateProtection()
        self._emergency_check = emergency_check or (lambda: False)

    def check(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        task_id: str = "",
        step_id: str = "",
        user_instruction: str = "",
        execute_fn: Optional[Callable[[str, dict[str, Any]], Any]] = None,
    ) -> GateResult:
        """Evaluate a tool call against Phase 7 policy.

        If execute_fn is provided and the action is ALLOWED, it will be
        called to actually run the tool.  Otherwise, just the policy
        decision is returned.
        """
        t0 = time.perf_counter()

        # 1. Create ActionEnvelope
        envelope = create_envelope(
            tool, arguments,
            task_id=task_id, step_id=step_id,
        )

        # 2. Audit: ACTION_REQUESTED
        self.audit.record(
            AuditEventType.ACTION_REQUESTED,
            task_id=task_id,
            step_id=step_id,
            action_id=envelope.id,
            tool=tool,
            action=envelope.action,
            target=envelope.target,
            risk_level=int(envelope.risk_level),
        )

        # 3. Duplicate check (for external/destructive only)
        dup_reason = self.duplicates.check(envelope)
        if dup_reason:
            self.audit.record(
                AuditEventType.ACTION_CANCELLED,
                task_id=task_id, action_id=envelope.id,
                tool=tool, action=envelope.action,
                target=envelope.target,
                risk_level=int(envelope.risk_level),
                policy_decision="BLOCKED_DUPLICATE",
                success=False, error=dup_reason,
            )
            return GateResult(blocked_reason=dup_reason, envelope=envelope)

        # 4. Rate check
        rate_reason = self.rate.check(envelope)
        if rate_reason:
            self.audit.record(
                AuditEventType.ACTION_CANCELLED,
                task_id=task_id, action_id=envelope.id,
                tool=tool, action=envelope.action,
                target=envelope.target,
                risk_level=int(envelope.risk_level),
                policy_decision="BLOCKED_RATE",
                success=False, error=rate_reason,
            )
            return GateResult(blocked_reason=rate_reason, envelope=envelope)

        # 5. Policy evaluation
        policy_result = self.policy.evaluate(
            envelope,
            emergency_engaged=self._emergency_check(),
            user_instruction=user_instruction,
        )

        # 6. Audit: POLICY_DECISION
        self.audit.record(
            AuditEventType.POLICY_DECISION,
            task_id=task_id, action_id=envelope.id,
            tool=tool, action=envelope.action,
            target=envelope.target,
            risk_level=int(envelope.risk_level),
            policy_decision=policy_result.decision.value,
            policy_rule=policy_result.policy_rule,
            duration_ms=policy_result.evaluation_ms,
        )

        # 7. Handle decision
        if policy_result.decision == PolicyDecision.ALLOW:
            return self._execute(
                envelope, policy_result, execute_fn,
                task_id=task_id, step_id=step_id,
            )

        if policy_result.needs_approval:
            return self._request_approval(
                envelope, policy_result,
                task_id=task_id, step_id=step_id,
            )

        if policy_result.decision == PolicyDecision.SANDBOX_ONLY:
            # Dry-run: report what would happen
            return GateResult(
                allowed=False,
                blocked_reason=f"DRY RUN — would {envelope.action} on {envelope.target}",
                policy_result=policy_result,
                envelope=envelope,
            )

        # DENY, BLOCK_UNSUPPORTED, etc.
        return GateResult(
            allowed=False,
            blocked_reason=policy_result.reason,
            policy_result=policy_result,
            envelope=envelope,
        )

    def execute_approved(
        self,
        approval: Phase7ApprovalRequest,
        envelope: ActionEnvelope,
        execute_fn: Callable[[str, dict[str, Any]], Any],
        *,
        task_id: str = "",
        step_id: str = "",
    ) -> GateResult:
        """Execute an action that has been approved by the user.

        TOCTOU protection: re-validates the envelope hash against the approval.
        """
        # TOCTOU check
        if not envelope.arguments_hash:
            envelope.compute_arguments_hash()
        if not approval.matches_envelope(envelope.arguments_hash):
            approval.invalidate("arguments changed since approval")
            self.audit.record(
                AuditEventType.APPROVAL_INVALIDATED,
                task_id=task_id, action_id=envelope.id,
                approval_id=approval.id,
                tool=envelope.tool, action=envelope.action,
                target=envelope.target,
                success=False,
                error="Arguments changed since approval — TOCTOU protection",
            )
            return GateResult(
                blocked_reason="Arguments changed since approval",
                envelope=envelope,
            )

        # Re-check emergency stop
        if self._emergency_check():
            self.audit.record(
                AuditEventType.ACTION_CANCELLED,
                task_id=task_id, action_id=envelope.id,
                tool=envelope.tool, action=envelope.action,
                policy_decision="EMERGENCY_STOP",
                success=False,
            )
            return GateResult(blocked_reason="Emergency stop engaged", envelope=envelope)

        # Execute
        policy_result = PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason="User-approved",
            policy_rule="USER_APPROVED",
            risk_level=envelope.risk_level,
        )
        return self._execute(
            envelope, policy_result, execute_fn,
            task_id=task_id, step_id=step_id,
            approval_id=approval.id,
        )

    def _execute(
        self,
        envelope: ActionEnvelope,
        policy_result: PolicyResult,
        execute_fn: Optional[Callable],
        *,
        task_id: str = "",
        step_id: str = "",
        approval_id: str = "",
    ) -> GateResult:
        """Actually execute the tool and record the result."""
        if execute_fn is None:
            return GateResult(
                allowed=True, executed=False,
                policy_result=policy_result, envelope=envelope,
            )

        t0 = time.perf_counter()
        try:
            result = execute_fn(envelope.tool, envelope.arguments)
            duration = (time.perf_counter() - t0) * 1000

            self.duplicates.record(envelope)
            self.rate.record(envelope)

            self.audit.record(
                AuditEventType.ACTION_EXECUTED,
                task_id=task_id, step_id=step_id,
                action_id=envelope.id,
                tool=envelope.tool, action=envelope.action,
                target=envelope.target,
                risk_level=int(envelope.risk_level),
                policy_decision=policy_result.decision.value,
                approval_id=approval_id,
                executed=True, success=True,
                duration_ms=duration,
                details=redact_dict(envelope.arguments),
            )

            return GateResult(
                allowed=True, executed=True,
                policy_result=policy_result, envelope=envelope,
                tool_result=result,
            )

        except Exception as e:
            duration = (time.perf_counter() - t0) * 1000
            self.audit.record(
                AuditEventType.ACTION_FAILED,
                task_id=task_id, step_id=step_id,
                action_id=envelope.id,
                tool=envelope.tool, action=envelope.action,
                target=envelope.target,
                risk_level=int(envelope.risk_level),
                executed=True, success=False,
                error=str(e), duration_ms=duration,
            )
            return GateResult(
                allowed=True, executed=True,
                policy_result=policy_result, envelope=envelope,
                tool_result=None,
                blocked_reason=str(e),
            )

    def _request_approval(
        self,
        envelope: ActionEnvelope,
        policy_result: PolicyResult,
        *,
        task_id: str = "",
        step_id: str = "",
    ) -> GateResult:
        """Create an ApprovalRequest for a consequential action."""
        # Build human-readable info
        title = self._make_title(envelope)
        consequence = self._make_consequence(envelope)

        approval = self.approvals.create(
            task_id=task_id,
            step_id=step_id,
            action_id=envelope.id,
            arguments_hash=envelope.arguments_hash,
            title=title,
            description=f"{envelope.tool}.{envelope.action} on {envelope.target}",
            tool=envelope.tool,
            target=envelope.target,
            consequence=consequence,
            reversible=envelope.flags.reversible,
            risk_level=int(envelope.risk_level),
            preview=redact_dict(envelope.arguments),
        )

        self.audit.record(
            AuditEventType.APPROVAL_REQUESTED,
            task_id=task_id, step_id=step_id,
            action_id=envelope.id,
            approval_id=approval.id,
            tool=envelope.tool, action=envelope.action,
            target=envelope.target,
            risk_level=int(envelope.risk_level),
            policy_decision=policy_result.decision.value,
            policy_rule=policy_result.policy_rule,
        )

        return GateResult(
            approval_required=True,
            approval=approval,
            policy_result=policy_result,
            envelope=envelope,
        )

    @staticmethod
    def _make_title(envelope: ActionEnvelope) -> str:
        action = envelope.action.replace("_", " ").title()
        if envelope.target:
            # Shorten long paths
            target = envelope.target
            if len(target) > 60:
                target = "..." + target[-57:]
            return f"{action}: {target}"
        return action

    @staticmethod
    def _make_consequence(envelope: ActionEnvelope) -> str:
        if envelope.flags.financial:
            return "This will make a financial transaction."
        if envelope.flags.communication:
            return "This will send a message externally."
        if envelope.flags.destructive:
            if envelope.flags.reversible:
                return "This will delete the item (can be recovered from Recycle Bin)."
            return "This will permanently delete the item."
        if envelope.flags.external_effect:
            return "This will have an external effect that may not be reversible."
        if envelope.flags.state_changing:
            return "This will modify local files."
        return ""
