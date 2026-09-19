"""PermissionPolicyEngine — deterministic policy evaluation for every action.

This is the security boundary. It sits between Phase 6's decision to perform
an action and the actual execution.  The LLM has no say here.

Policy precedence (highest wins):
  1. EMERGENCY_STOP
  2. EXPLICIT_DENY (user denied this specific action)
  3. SYSTEM_SECURITY (secret paths, blocked dirs, dangerous schemes)
  4. EXPIRED/INVALID approval
  5. USER_SCOPED_GRANT (task/session-level authorization)
  6. EXPLICIT_USER_INSTRUCTION (the user's own words authorize this action)
  7. DEFAULT_ACTION_POLICY (risk-level-based defaults)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.agent.phase7.envelope import ActionEnvelope, RiskLevel
from src.agent.phase7.context import PermissionContext, ScopedGrant


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"
    WAIT_FOR_AUTHENTICATION = "WAIT_FOR_AUTHENTICATION"
    SANDBOX_ONLY = "SANDBOX_ONLY"
    REQUIRE_RECONFIRMATION = "REQUIRE_RECONFIRMATION"
    BLOCK_UNSUPPORTED = "BLOCK_UNSUPPORTED"


@dataclass
class PolicyResult:
    """Rich result from a policy evaluation."""

    decision: PolicyDecision
    reason: str
    policy_rule: str = ""       # which rule matched
    scope_used: str = ""        # grant/scope that authorized
    expires_at: float = 0.0     # when this decision expires
    risk_level: RiskLevel = RiskLevel.OBSERVE
    evaluation_ms: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision in (
            PolicyDecision.REQUIRE_APPROVAL,
            PolicyDecision.REQUIRE_RECONFIRMATION,
        )


class PermissionPolicyEngine:
    """Evaluates ActionEnvelopes against policy rules.

    Deterministic, fast, outside the LLM.
    """

    def __init__(
        self,
        context: PermissionContext,
        *,
        autonomy_mode: str = "assist",
        computer_control_enabled: bool = True,
        dry_run: bool = False,
    ):
        self.context = context
        self.autonomy_mode = autonomy_mode
        self.computer_control_enabled = computer_control_enabled
        self.dry_run = dry_run

    def evaluate(
        self,
        envelope: ActionEnvelope,
        *,
        emergency_engaged: bool = False,
        user_instruction: str = "",
    ) -> PolicyResult:
        """Evaluate an action envelope against the full policy stack.

        Returns a PolicyResult with the decision and reasoning.
        """
        t0 = time.perf_counter()

        # --- 1. EMERGENCY STOP ---
        if emergency_engaged:
            return self._result(
                PolicyDecision.DENY,
                "Emergency stop is engaged — all actions blocked",
                "EMERGENCY_STOP",
                envelope,
                t0,
            )

        # --- 2. DRY RUN ---
        if self.dry_run and envelope.risk_level >= RiskLevel.LOCAL_REVERSIBLE:
            if envelope.flags.state_changing:
                return self._result(
                    PolicyDecision.SANDBOX_ONLY,
                    "Dry-run mode — state-changing actions are blocked",
                    "DRY_RUN",
                    envelope,
                    t0,
                )

        # --- 3. OBSERVE MODE ---
        if self.autonomy_mode == "observe" and envelope.risk_level > RiskLevel.OBSERVE:
            return self._result(
                PolicyDecision.DENY,
                "Autonomy mode is OBSERVE — only read actions allowed",
                "OBSERVE_MODE",
                envelope,
                t0,
            )

        # --- 4. COMPUTER CONTROL DISABLED ---
        if not self.computer_control_enabled and envelope.tool.startswith("computer."):
            if envelope.risk_level > RiskLevel.OBSERVE:
                return self._result(
                    PolicyDecision.DENY,
                    "Computer control is disabled",
                    "COMPUTER_CONTROL_DISABLED",
                    envelope,
                    t0,
                )

        # --- 5. EXPLICIT DENY (user previously denied this action) ---
        if self.context.was_denied(envelope.id):
            return self._result(
                PolicyDecision.DENY,
                "User previously denied this action",
                "EXPLICIT_DENY",
                envelope,
                t0,
            )

        # --- 6. RISK-BASED POLICY ---

        # L0 OBSERVE — always allow
        if envelope.risk_level == RiskLevel.OBSERVE:
            return self._result(
                PolicyDecision.ALLOW,
                "Read-only action — no approval needed",
                "DEFAULT_OBSERVE",
                envelope,
                t0,
            )

        # L1 LOCAL_REVERSIBLE — allow in assist/autonomous
        if envelope.risk_level == RiskLevel.LOCAL_REVERSIBLE:
            if self.autonomy_mode in ("assist", "autonomous"):
                return self._result(
                    PolicyDecision.ALLOW,
                    "Local reversible action — auto-allowed",
                    "DEFAULT_LOCAL_REVERSIBLE",
                    envelope,
                    t0,
                )

        # L2 LOCAL_PERSISTENT — check for scoped grant or explicit instruction
        if envelope.risk_level == RiskLevel.LOCAL_PERSISTENT:
            grant = self.context.has_grant(
                envelope.action, envelope.target, envelope.task_id
            )
            if grant:
                return self._result(
                    PolicyDecision.ALLOW,
                    f"Covered by scoped grant: {grant.reason or grant.id}",
                    "SCOPED_GRANT",
                    envelope,
                    t0,
                    scope_used=grant.id,
                )
            if self._explicit_instruction_authorizes(envelope, user_instruction):
                return self._result(
                    PolicyDecision.ALLOW,
                    "User's explicit instruction authorizes this action",
                    "EXPLICIT_INSTRUCTION",
                    envelope,
                    t0,
                )
            if self.autonomy_mode == "autonomous":
                return self._result(
                    PolicyDecision.ALLOW,
                    "Autonomous mode — local persistent auto-allowed",
                    "AUTONOMOUS_MODE",
                    envelope,
                    t0,
                )
            return self._result(
                PolicyDecision.REQUIRE_APPROVAL,
                "Local persistent action requires approval",
                "DEFAULT_LOCAL_PERSISTENT",
                envelope,
                t0,
            )

        # L3 DESTRUCTIVE_EXTERNAL — require approval unless scoped grant
        if envelope.risk_level == RiskLevel.DESTRUCTIVE_EXTERNAL:
            grant = self.context.has_grant(
                envelope.action, envelope.target, envelope.task_id
            )
            if grant:
                return self._result(
                    PolicyDecision.ALLOW,
                    f"Covered by scoped grant: {grant.reason or grant.id}",
                    "SCOPED_GRANT",
                    envelope,
                    t0,
                    scope_used=grant.id,
                )
            if self._explicit_instruction_authorizes(envelope, user_instruction):
                return self._result(
                    PolicyDecision.REQUIRE_APPROVAL,
                    "Destructive/external action — approval required despite instruction",
                    "EXPLICIT_INSTRUCTION_INSUFFICIENT",
                    envelope,
                    t0,
                )
            return self._result(
                PolicyDecision.REQUIRE_APPROVAL,
                "Destructive or external action requires user approval",
                "DEFAULT_DESTRUCTIVE_EXTERNAL",
                envelope,
                t0,
            )

        # L4 HIGH_IMPACT — always require explicit near-action confirmation
        if envelope.risk_level == RiskLevel.HIGH_IMPACT:
            return self._result(
                PolicyDecision.REQUIRE_APPROVAL,
                "High-impact action always requires explicit confirmation",
                "DEFAULT_HIGH_IMPACT",
                envelope,
                t0,
            )

        # Fallback
        return self._result(
            PolicyDecision.REQUIRE_APPROVAL,
            "Unknown risk level — defaulting to approval required",
            "FALLBACK",
            envelope,
            t0,
        )

    def _explicit_instruction_authorizes(
        self, envelope: ActionEnvelope, user_instruction: str
    ) -> bool:
        """Check if the user's explicit words authorize this specific action.

        'Delete report.pdf' authorizes deleting report.pdf.
        'Clean up my computer' does NOT authorize arbitrary deletions.
        """
        if not user_instruction:
            return False

        instruction_lower = user_instruction.lower()
        action_lower = envelope.action.lower()
        target_lower = envelope.target.lower()

        # The instruction must mention both the action verb and the target
        action_words = {"delete", "remove", "send", "submit", "purchase", "buy",
                        "move", "rename", "create", "write", "overwrite"}

        if action_lower not in action_words:
            return False

        # Check if the action word appears in the instruction
        if action_lower not in instruction_lower:
            return False

        # Check if the target (or a recognizable part) appears in the instruction
        if target_lower:
            # Extract filename or last component
            target_name = target_lower.replace("\\", "/").rsplit("/", 1)[-1]
            if target_name and target_name in instruction_lower:
                return True

        return False

    def _result(
        self,
        decision: PolicyDecision,
        reason: str,
        rule: str,
        envelope: ActionEnvelope,
        t0: float,
        scope_used: str = "",
    ) -> PolicyResult:
        elapsed = (time.perf_counter() - t0) * 1000
        return PolicyResult(
            decision=decision,
            reason=reason,
            policy_rule=rule,
            scope_used=scope_used,
            risk_level=envelope.risk_level,
            evaluation_ms=elapsed,
        )
