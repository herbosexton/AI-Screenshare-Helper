"""Phase 7 — Permissions, Approvals, Safety Controls, Audit & Kill Switch tests."""

from __future__ import annotations

import time

import pytest

from src.agent.phase7.envelope import (
    ActionEnvelope,
    ActionFlags,
    RiskLevel,
    classify_risk,
    create_envelope,
)
from src.agent.phase7.context import PermissionContext, ScopedGrant, ApprovalRecord
from src.agent.phase7.policy import PermissionPolicyEngine, PolicyDecision, PolicyResult
from src.agent.phase7.approval import (
    ApprovalManager,
    ApprovalStatus,
    Phase7ApprovalRequest,
)
from src.agent.phase7.redactor import (
    redact_text,
    redact_dict,
    is_sensitive_for_tts,
    redact_for_tts,
)
from src.agent.phase7.audit import AuditEventType, Phase7AuditLog
from src.agent.phase7.guards import DuplicateActionGuard, RateProtection
from src.agent.phase7.gate import ExecutionGate, GateResult


# ===================================================================
# ActionEnvelope + Risk Classification
# ===================================================================


class TestRiskClassification:
    """Risk levels must be deterministic and based on tool+arguments, not LLM."""

    def test_read_page_is_observe(self):
        level, flags = classify_risk("browser.read_page", {})
        assert level == RiskLevel.OBSERVE
        assert not flags.state_changing

    def test_read_file_is_observe(self):
        level, flags = classify_risk("files.read", {"path": "C:\\docs\\report.pdf"})
        assert level == RiskLevel.OBSERVE

    def test_find_recent_is_observe(self):
        level, flags = classify_risk("files.find_recent", {"extensions": ["pdf"]})
        assert level == RiskLevel.OBSERVE

    def test_screen_describe_is_observe(self):
        level, flags = classify_risk("screen.describe", {})
        assert level == RiskLevel.OBSERVE

    def test_open_app_is_local_reversible(self):
        level, flags = classify_risk("computer.open_application", {"name": "Chrome"})
        assert level == RiskLevel.LOCAL_REVERSIBLE
        assert flags.state_changing
        assert flags.reversible

    def test_browser_navigate_is_local_reversible(self):
        level, flags = classify_risk("browser.navigate", {"url": "https://google.com"})
        assert level == RiskLevel.LOCAL_REVERSIBLE

    def test_switch_tab_is_local_reversible(self):
        level, flags = classify_risk("browser.switchTab", {"query": "Google"})
        assert level == RiskLevel.LOCAL_REVERSIBLE

    def test_browser_fill_is_local_reversible(self):
        level, flags = classify_risk("browser.fill", {"selector": "#name", "value": "Herbert"})
        assert level == RiskLevel.LOCAL_REVERSIBLE
        assert flags.reversible

    def test_file_write_is_local_persistent(self):
        level, flags = classify_risk("files.write", {"path": "C:\\docs\\note.txt", "content": "hi"})
        assert level == RiskLevel.LOCAL_PERSISTENT
        assert flags.state_changing

    def test_file_move_is_local_persistent(self):
        level, flags = classify_risk("files.move", {"source": "a.txt", "destination": "b.txt"})
        assert level == RiskLevel.LOCAL_PERSISTENT

    def test_file_delete_is_destructive(self):
        level, flags = classify_risk("files.delete", {"path": "C:\\docs\\old.txt"})
        assert level == RiskLevel.DESTRUCTIVE_EXTERNAL
        assert flags.destructive

    def test_email_send_is_destructive_external(self):
        level, flags = classify_risk("email.send", {"to": "sarah@example.com"})
        assert level == RiskLevel.DESTRUCTIVE_EXTERNAL
        assert flags.communication
        assert flags.external_effect
        assert not flags.reversible

    def test_purchase_is_high_impact(self):
        level, flags = classify_risk("purchase.checkout", {"amount": "49.99"})
        assert level == RiskLevel.HIGH_IMPACT
        assert flags.financial

    def test_auth_change_is_high_impact(self):
        level, flags = classify_risk("auth.change_password", {})
        assert level == RiskLevel.HIGH_IMPACT
        assert flags.authentication_related

    def test_browser_click_submit_is_destructive(self):
        level, flags = classify_risk("browser.click", {"text": "Submit Application"})
        assert level == RiskLevel.DESTRUCTIVE_EXTERNAL
        assert flags.external_effect

    def test_browser_click_regular_is_reversible(self):
        level, flags = classify_risk("browser.click", {"text": "Next"})
        assert level == RiskLevel.LOCAL_REVERSIBLE

    def test_computer_click_purchase_is_destructive(self):
        level, flags = classify_risk("computer.click", {"name": "Place Order"})
        assert level == RiskLevel.DESTRUCTIVE_EXTERNAL

    def test_computer_click_normal_is_reversible(self):
        level, flags = classify_risk("computer.click", {"name": "OK"})
        assert level == RiskLevel.LOCAL_REVERSIBLE

    def test_file_open_is_local_reversible(self):
        level, flags = classify_risk("files.open", {"path": "resume.pdf"})
        assert level == RiskLevel.LOCAL_REVERSIBLE


class TestActionEnvelope:
    def test_envelope_creation(self):
        env = create_envelope("files.read", {"path": "C:\\docs\\report.pdf"}, task_id="t1")
        assert env.tool == "files.read"
        assert env.risk_level == RiskLevel.OBSERVE
        assert env.target == "C:\\docs\\report.pdf"
        assert env.arguments_hash  # computed

    def test_arguments_hash_changes_on_different_args(self):
        e1 = create_envelope("files.delete", {"path": "a.txt"})
        e2 = create_envelope("files.delete", {"path": "b.txt"})
        assert e1.arguments_hash != e2.arguments_hash

    def test_matches_approval(self):
        env = create_envelope("files.delete", {"path": "a.txt"})
        assert env.matches_approval(env.arguments_hash)
        assert not env.matches_approval("different_hash")


# ===================================================================
# PermissionPolicyEngine
# ===================================================================


class TestPolicyEngine:
    @pytest.fixture
    def ctx(self):
        return PermissionContext(session_id="test")

    @pytest.fixture
    def engine(self, ctx):
        return PermissionPolicyEngine(ctx, autonomy_mode="assist")

    def test_observe_always_allowed(self, engine):
        env = create_envelope("browser.read_page", {})
        result = engine.evaluate(env)
        assert result.allowed
        assert result.policy_rule == "DEFAULT_OBSERVE"

    def test_local_reversible_allowed_in_assist(self, engine):
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        result = engine.evaluate(env)
        assert result.allowed
        assert result.policy_rule == "DEFAULT_LOCAL_REVERSIBLE"

    def test_local_persistent_requires_approval(self, engine):
        env = create_envelope("files.write", {"path": "test.txt", "content": "hi"})
        result = engine.evaluate(env)
        assert result.needs_approval
        assert result.policy_rule == "DEFAULT_LOCAL_PERSISTENT"

    def test_destructive_requires_approval(self, engine):
        env = create_envelope("files.delete", {"path": "old.txt"})
        result = engine.evaluate(env)
        assert result.needs_approval

    def test_high_impact_always_requires_approval(self, engine):
        env = create_envelope("purchase.checkout", {"amount": "99.99"})
        result = engine.evaluate(env)
        assert result.needs_approval
        assert result.policy_rule == "DEFAULT_HIGH_IMPACT"

    def test_emergency_stop_blocks_everything(self, engine):
        env = create_envelope("browser.read_page", {})
        result = engine.evaluate(env, emergency_engaged=True)
        assert not result.allowed
        assert result.policy_rule == "EMERGENCY_STOP"

    def test_observe_mode_blocks_actions(self, ctx):
        engine = PermissionPolicyEngine(ctx, autonomy_mode="observe")
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        result = engine.evaluate(env)
        assert not result.allowed
        assert result.policy_rule == "OBSERVE_MODE"

    def test_observe_mode_allows_reads(self, ctx):
        engine = PermissionPolicyEngine(ctx, autonomy_mode="observe")
        env = create_envelope("browser.read_page", {})
        result = engine.evaluate(env)
        assert result.allowed

    def test_scoped_grant_allows_persistent(self, engine, ctx):
        ctx.add_grant(ScopedGrant(
            id="g1", task_id="t1", actions=frozenset({"write"}),
            target_pattern="C:\\docs\\", reason="User approved file writes"
        ))
        env = create_envelope("files.write", {"path": "C:\\docs\\note.txt"}, task_id="t1")
        result = engine.evaluate(env)
        assert result.allowed
        assert result.policy_rule == "SCOPED_GRANT"
        assert result.scope_used == "g1"

    def test_scoped_grant_wrong_task_denied(self, engine, ctx):
        ctx.add_grant(ScopedGrant(
            id="g1", task_id="t1", actions=frozenset({"write"}),
        ))
        env = create_envelope("files.write", {"path": "test.txt"}, task_id="t2")
        result = engine.evaluate(env)
        assert result.needs_approval  # Grant doesn't cover task t2

    def test_explicit_instruction_authorizes_persistent(self, engine):
        env = create_envelope("files.write", {"path": "C:\\docs\\report.pdf", "content": "x"})
        result = engine.evaluate(env, user_instruction="write report.pdf with the summary")
        assert result.allowed
        assert result.policy_rule == "EXPLICIT_INSTRUCTION"

    def test_explicit_instruction_insufficient_for_destructive(self, engine):
        env = create_envelope("files.delete", {"path": "C:\\docs\\report.pdf"})
        result = engine.evaluate(env, user_instruction="delete report.pdf")
        # Even with explicit instruction, destructive still needs approval
        assert result.needs_approval
        assert result.policy_rule == "EXPLICIT_INSTRUCTION_INSUFFICIENT"

    def test_dry_run_blocks_state_changing(self, ctx):
        engine = PermissionPolicyEngine(ctx, autonomy_mode="assist", dry_run=True)
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        result = engine.evaluate(env)
        assert result.decision == PolicyDecision.SANDBOX_ONLY

    def test_dry_run_allows_reads(self, ctx):
        engine = PermissionPolicyEngine(ctx, autonomy_mode="assist", dry_run=True)
        env = create_envelope("browser.read_page", {})
        result = engine.evaluate(env)
        assert result.allowed

    def test_computer_control_disabled(self, ctx):
        engine = PermissionPolicyEngine(ctx, computer_control_enabled=False)
        env = create_envelope("computer.open_application", {"name": "Chrome"})
        result = engine.evaluate(env)
        assert not result.allowed
        assert result.policy_rule == "COMPUTER_CONTROL_DISABLED"

    def test_policy_evaluation_is_fast(self, engine):
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        result = engine.evaluate(env)
        assert result.evaluation_ms < 5.0  # Should be sub-millisecond


# ===================================================================
# Approval Lifecycle
# ===================================================================


class TestApprovalLifecycle:
    def test_create_and_approve(self):
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="email.send",
            target="sarah@example.com", title="Send email to Sarah"
        )
        assert approval.is_pending
        resolved = mgr.resolve_voice(approved=True)
        assert resolved is not None
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.resolved_by == "voice"

    def test_create_and_deny(self):
        mgr = ApprovalManager()
        mgr.create(task_id="t1", tool="email.send", target="x@y.com")
        resolved = mgr.resolve_voice(approved=False)
        assert resolved.status == ApprovalStatus.DENIED

    def test_stale_yes_returns_none(self):
        """No pending approval — 'yes' must not approve anything."""
        mgr = ApprovalManager()
        result = mgr.resolve_voice(approved=True)
        assert result is None  # Stale yes protection

    def test_expired_approval(self):
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="email.send", ttl_seconds=0.01
        )
        time.sleep(0.02)
        assert approval.is_expired
        # Trying to resolve an expired approval
        result = mgr.resolve_voice(approved=True)
        assert result is None  # Expired, treated as no pending

    def test_invalidation_on_change(self):
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="email.send",
            arguments_hash="hash1"
        )
        invalidated = mgr.invalidate_current("arguments changed")
        assert invalidated.status == ApprovalStatus.INVALIDATED

    def test_toctou_hash_mismatch(self):
        approval = Phase7ApprovalRequest(
            task_id="t1", tool="email.send",
            arguments_hash="original_hash"
        )
        assert approval.matches_envelope("original_hash")
        assert not approval.matches_envelope("different_hash")

    def test_cancel_all_on_emergency(self):
        mgr = ApprovalManager()
        mgr.create(task_id="t1", tool="email.send")
        count = mgr.cancel_all()
        assert count == 1
        assert mgr.active is None

    def test_new_approval_cancels_previous(self):
        mgr = ApprovalManager()
        first = mgr.create(task_id="t1", tool="email.send")
        second = mgr.create(task_id="t1", tool="files.delete")
        assert first.status == ApprovalStatus.CANCELLED
        assert second.is_pending

    def test_hud_approval(self):
        mgr = ApprovalManager()
        approval = mgr.create(task_id="t1", tool="email.send")
        resolved = mgr.resolve_hud(approval.id, approved=True)
        assert resolved is not None
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.resolved_by == "hud"

    def test_hud_wrong_id_returns_none(self):
        mgr = ApprovalManager()
        mgr.create(task_id="t1", tool="email.send")
        result = mgr.resolve_hud("wrong-id", approved=True)
        assert result is None

    def test_human_summary(self):
        approval = Phase7ApprovalRequest(
            title="Send email to Sarah",
            target="sarah@example.com",
            consequence="This will send the quarterly report",
            reversible=False,
        )
        summary = approval.human_summary()
        assert "Sarah" in summary
        assert "cannot be undone" in summary


# ===================================================================
# PermissionContext
# ===================================================================


class TestPermissionContext:
    def test_scoped_grant_covers_action(self):
        ctx = PermissionContext()
        ctx.add_grant(ScopedGrant(
            id="g1", task_id="t1",
            actions=frozenset({"move", "rename"}),
            target_pattern="C:\\Users\\herbi\\Documents\\",
            reason="Organize files"
        ))
        grant = ctx.has_grant("move", "C:\\Users\\herbi\\Documents\\report.pdf", "t1")
        assert grant is not None
        assert grant.id == "g1"

    def test_scoped_grant_wrong_action(self):
        ctx = PermissionContext()
        ctx.add_grant(ScopedGrant(
            id="g1", task_id="t1", actions=frozenset({"move"}),
        ))
        assert ctx.has_grant("delete", "", "t1") is None

    def test_scoped_grant_expiration(self):
        ctx = PermissionContext()
        ctx.add_grant(ScopedGrant(
            id="g1", task_id="t1", actions=frozenset({"move"}),
            expires_at=time.time() - 1,  # Already expired
        ))
        assert ctx.has_grant("move", "", "t1") is None

    def test_revoke_grant(self):
        ctx = PermissionContext()
        ctx.add_grant(ScopedGrant(id="g1", task_id="t1", actions=frozenset({"*"})))
        assert ctx.has_grant("move", "", "t1") is not None
        ctx.revoke_grant("g1")
        assert ctx.has_grant("move", "", "t1") is None

    def test_revoke_task_grants(self):
        ctx = PermissionContext()
        ctx.add_grant(ScopedGrant(id="g1", task_id="t1", actions=frozenset({"*"})))
        ctx.add_grant(ScopedGrant(id="g2", task_id="t2", actions=frozenset({"*"})))
        count = ctx.revoke_task_grants("t1")
        assert count == 1
        assert ctx.has_grant("move", "", "t1") is None
        assert ctx.has_grant("move", "", "t2") is not None

    def test_revoke_all(self):
        ctx = PermissionContext()
        ctx.add_grant(ScopedGrant(id="g1", task_id="t1", actions=frozenset({"*"})))
        ctx.add_grant(ScopedGrant(id="g2", task_id="t2", actions=frozenset({"*"})))
        count = ctx.revoke_all()
        assert count == 2
        assert len(ctx.active_grants()) == 0

    def test_denial_tracking(self):
        ctx = PermissionContext()
        ctx.record_approval(ApprovalRecord(
            approval_id="a1", action_id="act1", task_id="t1",
            tool="files.delete", target="x.txt",
            arguments_hash="hash1", approved=False
        ))
        assert ctx.was_denied("act1")
        assert not ctx.was_denied("act2")


# ===================================================================
# SecretRedactor
# ===================================================================


class TestSecretRedactor:
    def test_api_key_redacted(self):
        text = "The key is sk-proj-EXAMPLETESTKEYNOTREAL000000000000"
        result = redact_text(text)
        assert "sk-proj" not in result
        assert "REDACTED" in result

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123.xyz789"
        result = redact_text(text)
        assert "eyJ" not in result

    def test_password_in_dict_redacted(self):
        data = {"username": "admin", "password": "secret123", "email": "a@b.com"}
        result = redact_dict(data)
        assert result["username"] == "admin"
        assert result["password"] == "[REDACTED]"
        assert result["email"] == "a@b.com"

    def test_nested_dict_redacted(self):
        data = {"config": {"api_key": "sk-12345", "host": "localhost"}}
        result = redact_dict(data)
        assert result["config"]["api_key"] == "[REDACTED]"
        assert result["config"]["host"] == "localhost"

    def test_ssn_redacted(self):
        text = "SSN: 123-45-6789"
        result = redact_text(text)
        assert "123-45-6789" not in result

    def test_normal_text_unchanged(self):
        text = "Open Chrome and go to Google"
        assert redact_text(text) == text

    def test_tts_sensitivity_detection(self):
        assert is_sensitive_for_tts("The key is sk-proj-abc123def456ghi")
        assert not is_sensitive_for_tts("Hello, how are you?")

    def test_tts_redaction_is_speakable(self):
        text = "Your key is sk-proj-abc123def456ghijklmno"
        result = redact_for_tts(text)
        assert "sk-" not in result
        assert "[" not in result  # No brackets for TTS


# ===================================================================
# Phase 7 AuditLog
# ===================================================================


class TestPhase7AuditLog:
    def test_record_and_retrieve(self):
        log = Phase7AuditLog()
        log.record(
            AuditEventType.ACTION_EXECUTED,
            tool="browser.navigate",
            action="navigate",
            target="https://google.com",
        )
        entries = log.recent(10)
        assert len(entries) == 1
        assert entries[0]["tool"] == "browser.navigate"

    def test_secrets_redacted_in_log(self):
        log = Phase7AuditLog()
        log.record(
            AuditEventType.ACTION_EXECUTED,
            tool="auth.login",
            target="Bearer eyJhbGciOiJIUzI1NiJ9.abc.xyz",
            details={"password": "secret123"},
        )
        entry = log.recent(1)[0]
        assert "eyJ" not in entry["target"]
        assert entry["details"]["password"] == "[REDACTED]"

    def test_task_filter(self):
        log = Phase7AuditLog()
        log.record(AuditEventType.ACTION_EXECUTED, task_id="t1", tool="a")
        log.record(AuditEventType.ACTION_EXECUTED, task_id="t2", tool="b")
        log.record(AuditEventType.ACTION_EXECUTED, task_id="t1", tool="c")
        assert len(log.for_task("t1")) == 2

    def test_activity_history_readable(self):
        log = Phase7AuditLog()
        log.record(
            AuditEventType.ACTION_EXECUTED,
            tool="browser.navigate", action="navigate",
            target="https://google.com",
        )
        log.record(
            AuditEventType.ACTION_EXECUTED,
            tool="computer.open_application", action="open_application",
            target="Chrome",
        )
        history = log.activity_history()
        assert len(history) == 2
        assert "Navigated" in history[0]["description"]
        assert "Chrome" in history[1]["description"]

    def test_emergency_stop_logged(self):
        log = Phase7AuditLog()
        log.record(AuditEventType.EMERGENCY_STOP, tool="system", action="emergency_stop")
        history = log.activity_history()
        assert any("Emergency" in h["description"] for h in history)


# ===================================================================
# Safety Guards
# ===================================================================


class TestDuplicateActionGuard:
    def test_first_action_allowed(self):
        guard = DuplicateActionGuard()
        env = create_envelope("email.send", {"to": "a@b.com"})
        assert guard.check(env) is None

    def test_duplicate_blocked(self):
        guard = DuplicateActionGuard()
        env = create_envelope("email.send", {"to": "a@b.com"})
        guard.record(env)
        reason = guard.check(env)
        assert reason is not None
        assert "Duplicate" in reason

    def test_different_target_allowed(self):
        guard = DuplicateActionGuard()
        env1 = create_envelope("email.send", {"to": "a@b.com"})
        env2 = create_envelope("email.send", {"to": "c@d.com"})
        guard.record(env1)
        assert guard.check(env2) is None

    def test_low_risk_not_guarded(self):
        guard = DuplicateActionGuard()
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        guard.record(env)
        assert guard.check(env) is None  # L1 — not guarded


class TestRateProtection:
    def test_under_limit_allowed(self):
        rp = RateProtection(max_per_window=3, window_seconds=60)
        env = create_envelope("email.send", {"to": "a@b.com"})
        rp.record(env)
        rp.record(env)
        assert rp.check(env) is None  # 2 < 3

    def test_over_limit_blocked(self):
        rp = RateProtection(max_per_window=2, window_seconds=60)
        env = create_envelope("email.send", {"to": "a@b.com"})
        rp.record(env)
        rp.record(env)
        reason = rp.check(env)
        assert reason is not None
        assert "Rate limit" in reason

    def test_low_risk_not_rate_limited(self):
        rp = RateProtection(max_per_window=1, window_seconds=60)
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        rp.record(env)
        rp.record(env)
        assert rp.check(env) is None  # L1 — not rate-limited


# ===================================================================
# Integration: Full flow
# ===================================================================


class TestFullFlow:
    def test_read_action_flows_through_without_approval(self):
        """Test 1 — READ-ONLY ACTION"""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        env = create_envelope("browser.read_page", {})
        result = engine.evaluate(env)
        assert result.allowed
        assert not result.needs_approval

    def test_open_file_flows_without_approval(self):
        """Test 2 — OPEN FILE"""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        env = create_envelope("files.open", {"path": "C:\\docs\\resume.pdf"})
        result = engine.evaluate(env)
        assert result.allowed

    def test_delete_requires_approval(self):
        """Test 4 — DELETE FILE"""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        env = create_envelope("files.delete", {"path": "C:\\docs\\test.txt"})
        result = engine.evaluate(env)
        assert result.needs_approval

    def test_voice_yes_approves_pending(self):
        """Test 12 — VOICE YES"""
        mgr = ApprovalManager()
        mgr.create(task_id="t1", tool="email.send", target="x@y.com")
        resolved = mgr.resolve_voice(approved=True)
        assert resolved is not None
        assert resolved.status == ApprovalStatus.APPROVED

    def test_voice_no_denies_pending(self):
        """Test 13 — VOICE NO"""
        mgr = ApprovalManager()
        mgr.create(task_id="t1", tool="email.send")
        resolved = mgr.resolve_voice(approved=False)
        assert resolved.status == ApprovalStatus.DENIED

    def test_random_yes_does_nothing(self):
        """Test 14 — RANDOM YES"""
        mgr = ApprovalManager()
        result = mgr.resolve_voice(approved=True)
        assert result is None

    def test_emergency_stop_blocks_all(self):
        """Test 15 — EMERGENCY STOP"""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        env = create_envelope("browser.navigate", {"url": "https://google.com"})
        result = engine.evaluate(env, emergency_engaged=True)
        assert not result.allowed

    def test_stale_approval_not_executed(self):
        """Test 10 — STALE APPROVAL"""
        mgr = ApprovalManager()
        mgr.create(task_id="t1", tool="email.send", ttl_seconds=0.01)
        time.sleep(0.02)
        result = mgr.resolve_voice(approved=True)
        assert result is None  # Expired

    def test_target_changed_invalidates_approval(self):
        """Test 11 — TARGET CHANGED AFTER APPROVAL"""
        env1 = create_envelope("email.send", {"to": "sarah@example.com"})
        approval = Phase7ApprovalRequest(
            task_id="t1", tool="email.send",
            arguments_hash=env1.arguments_hash,
        )
        # Arguments change
        env2 = create_envelope("email.send", {"to": "tony@example.com"})
        assert not approval.matches_envelope(env2.arguments_hash)

    def test_dry_run_blocks_mutations(self):
        """Test 23 — DRY RUN"""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx, dry_run=True)
        env = create_envelope("files.write", {"path": "test.txt", "content": "hi"})
        result = engine.evaluate(env)
        assert result.decision == PolicyDecision.SANDBOX_ONLY

    def test_duplicate_submit_blocked(self):
        """Test 21 — DUPLICATE SUBMIT"""
        guard = DuplicateActionGuard()
        env = create_envelope("form.submit", {"form_id": "job-app"})
        guard.record(env)
        assert guard.check(env) is not None

    def test_loop_protection(self):
        """Test 22 — LOOP PROTECTION"""
        rp = RateProtection(max_per_window=3, window_seconds=60)
        env = create_envelope("email.send", {"to": "a@b.com"})
        for _ in range(3):
            rp.record(env)
        assert rp.check(env) is not None

    def test_phase6_regression_low_risk_not_slowed(self):
        """Test 27 — Phase 6 multi-step should not be slowed by approval for reads."""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        # Typical Phase 6 sequence
        for tool, args in [
            ("browser.open", {}),
            ("browser.navigate", {"url": "https://google.com"}),
            ("browser.new_tab", {}),
            ("browser.navigate", {"url": "https://tradingview.com"}),
            ("browser.switchTab", {"query": "Google"}),
        ]:
            env = create_envelope(tool, args)
            result = engine.evaluate(env)
            assert result.allowed, f"{tool} should be auto-allowed but got {result.decision}"
            assert result.evaluation_ms < 5.0

    def test_fast_path_no_overhead(self):
        """Test 28/29 — Fast path remains fast."""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        env = create_envelope("browser.read_page", {})
        result = engine.evaluate(env)
        assert result.allowed
        assert result.evaluation_ms < 2.0


# ===================================================================
# ExecutionGate — integrated flow
# ===================================================================


class TestExecutionGate:
    @pytest.fixture
    def gate(self):
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        approvals = ApprovalManager()
        audit = Phase7AuditLog()
        return ExecutionGate(engine, approvals, audit)

    def test_read_flows_through(self, gate):
        result = gate.check("browser.read_page", {})
        assert result.allowed
        assert not result.approval_required

    def test_navigate_auto_allowed(self, gate):
        def fake_exec(tool, args):
            return {"ok": True}
        result = gate.check("browser.navigate", {"url": "https://google.com"},
                           execute_fn=fake_exec)
        assert result.allowed
        assert result.executed
        assert result.tool_result == {"ok": True}

    def test_delete_requires_approval(self, gate):
        result = gate.check("files.delete", {"path": "C:\\docs\\old.txt"})
        assert result.approval_required
        assert result.approval is not None
        assert result.approval.is_pending

    def test_email_send_requires_approval(self, gate):
        result = gate.check("email.send", {"to": "sarah@example.com"})
        assert result.approval_required
        assert "sarah" in result.approval.target.lower()

    def test_approval_then_execute(self, gate):
        # Step 1: Check returns approval required
        result = gate.check("email.send", {"to": "sarah@example.com"},
                           task_id="t1")
        assert result.approval_required

        # Step 2: User approves via voice
        approved = gate.approvals.resolve_voice(approved=True)
        assert approved is not None

        # Step 3: Execute the approved action
        env = create_envelope("email.send", {"to": "sarah@example.com"}, task_id="t1")
        def fake_send(tool, args):
            return {"sent": True}
        exec_result = gate.execute_approved(
            approved, env, fake_send, task_id="t1"
        )
        assert exec_result.allowed
        assert exec_result.executed

    def test_toctou_blocks_changed_args(self, gate):
        # Step 1: Request approval for email to Sarah
        result = gate.check("email.send", {"to": "sarah@example.com"}, task_id="t1")
        approval = result.approval

        # Step 2: Approve
        gate.approvals.resolve_voice(approved=True)

        # Step 3: Arguments change — now sending to Tony
        env = create_envelope("email.send", {"to": "tony@example.com"}, task_id="t1")
        def fake_send(tool, args):
            return {"sent": True}
        exec_result = gate.execute_approved(
            approval, env, fake_send, task_id="t1"
        )
        assert not exec_result.executed
        assert "changed" in exec_result.blocked_reason.lower()

    def test_emergency_stop_blocks_approved(self, gate):
        result = gate.check("email.send", {"to": "x@y.com"}, task_id="t1")
        approval = result.approval
        gate.approvals.resolve_voice(approved=True)

        # Emergency stop engaged
        gate._emergency_check = lambda: True
        env = create_envelope("email.send", {"to": "x@y.com"}, task_id="t1")
        exec_result = gate.execute_approved(
            approval, env, lambda t, a: None, task_id="t1"
        )
        assert not exec_result.executed

    def test_duplicate_blocked(self, gate):
        def fake_exec(tool, args):
            return {"ok": True}
        env = create_envelope("email.send", {"to": "x@y.com"})
        # First execution works
        r1 = gate.check("email.send", {"to": "x@y.com"}, execute_fn=fake_exec)
        # The first one went to approval, so let's simulate direct duplicate tracking
        gate.duplicates.record(env)
        r2 = gate.check("email.send", {"to": "x@y.com"})
        assert "Duplicate" in (r2.blocked_reason or "")

    def test_audit_records_flow(self, gate):
        gate.check("browser.read_page", {}, task_id="t1")
        entries = gate.audit.recent(10)
        types = {e["event_type"] for e in entries}
        assert "ACTION_REQUESTED" in types
        assert "POLICY_DECISION" in types

    def test_dry_run_blocks_writes(self):
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx, dry_run=True)
        approvals = ApprovalManager()
        audit = Phase7AuditLog()
        gate = ExecutionGate(engine, approvals, audit)

        result = gate.check("files.write", {"path": "test.txt", "content": "hi"})
        assert not result.allowed
        assert "DRY RUN" in (result.blocked_reason or "")

    def test_approval_has_human_readable_title(self, gate):
        result = gate.check("files.delete", {"path": "C:\\Users\\herbi\\Documents\\report.pdf"})
        assert result.approval is not None
        assert "Delete" in result.approval.title
        assert "report.pdf" in result.approval.title

    def test_multiple_tools_fast_path(self, gate):
        """Phase 6 regression: multiple low-risk tools should all auto-allow."""
        tools = [
            ("browser.open", {}),
            ("browser.navigate", {"url": "https://google.com"}),
            ("browser.new_tab", {}),
            ("browser.switchTab", {"query": "Tab 1"}),
            ("screen.describe", {}),
            ("browser.read_page", {}),
        ]
        for tool, args in tools:
            result = gate.check(tool, args)
            assert result.allowed, f"{tool} should be auto-allowed"


# ===================================================================
# Voice / HUD Approval Synchronization
# ===================================================================


class TestVoiceHUDSync:
    """Voice and HUD must reference the SAME ApprovalRequest object."""

    def test_voice_yes_updates_same_object(self):
        """Test C: Voice 'yes' changes the exact same approval the HUD displays."""
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="form.submit",
            target="Example Corp", title="Submit job application",
        )
        original_id = approval.id

        # Voice says "yes" — resolves same object
        resolved = mgr.resolve_voice(approved=True)
        assert resolved is not None
        assert resolved.id == original_id
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.resolved_by == "voice"

    def test_voice_no_updates_same_object(self):
        """Test C repeat: Voice 'no' denies the exact same approval."""
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="form.submit",
            target="Example Corp",
        )
        original_id = approval.id

        resolved = mgr.resolve_voice(approved=False)
        assert resolved is not None
        assert resolved.id == original_id
        assert resolved.status == ApprovalStatus.DENIED

    def test_hud_approve_updates_same_object(self):
        """HUD button click resolves same approval as voice would."""
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="email.send", target="x@y.com",
        )
        resolved = mgr.resolve_hud(approval.id, approved=True)
        assert resolved is not None
        assert resolved.id == approval.id
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.resolved_by == "hud"

    def test_hud_deny_updates_same_object(self):
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="email.send", target="x@y.com",
        )
        resolved = mgr.resolve_hud(approval.id, approved=False)
        assert resolved is not None
        assert resolved.status == ApprovalStatus.DENIED
        assert resolved.resolved_by == "hud"

    def test_stale_hud_click_returns_none(self):
        """Test D: Expired approval — HUD Approve must not execute."""
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="email.send", ttl_seconds=0.01,
        )
        time.sleep(0.02)
        # HUD tries to approve — but it's expired
        resolved = mgr.resolve_hud(approval.id, approved=True)
        assert resolved is None

    def test_changed_target_invalidates(self):
        """Test E: Changed arguments invalidate the old HUD approval."""
        mgr = ApprovalManager()
        original = mgr.create(
            task_id="t1", tool="email.send",
            arguments_hash="hash_v1",
            target="sarah@example.com",
        )
        original_id = original.id

        # Arguments change — invalidate
        invalidated = mgr.invalidate_current("arguments changed")
        assert invalidated is not None
        assert invalidated.id == original_id
        assert invalidated.status == ApprovalStatus.INVALIDATED

        # Old Approve button tries to fire — nothing pending
        resolved = mgr.resolve_hud(original_id, approved=True)
        assert resolved is None

        # New approval created
        new_approval = mgr.create(
            task_id="t1", tool="email.send",
            arguments_hash="hash_v2",
            target="tony@example.com",
        )
        assert new_approval.id != original_id
        assert new_approval.is_pending

    def test_emergency_stop_invalidates_approval(self):
        """Test F: Emergency stop invalidates pending approval."""
        mgr = ApprovalManager()
        approval = mgr.create(
            task_id="t1", tool="form.submit", target="Example Corp",
        )
        assert approval.is_pending

        count = mgr.cancel_all()
        assert count == 1
        assert mgr.active is None

        # HUD tries to approve — nothing pending
        resolved = mgr.resolve_hud(approval.id, approved=True)
        assert resolved is None

    def test_only_one_voice_addressable(self):
        """Only one active approval at a time — second cancels first."""
        mgr = ApprovalManager()
        first = mgr.create(task_id="t1", tool="a")
        second = mgr.create(task_id="t1", tool="b")
        assert first.status == ApprovalStatus.CANCELLED
        assert second.is_pending
        assert mgr.active.id == second.id

    def test_gate_creates_approval_with_hud_fields(self):
        """Gate produces approval requests with human-readable fields for HUD."""
        ctx = PermissionContext()
        engine = PermissionPolicyEngine(ctx)
        mgr = ApprovalManager()
        audit = Phase7AuditLog()
        gate = ExecutionGate(engine, mgr, audit)

        result = gate.check("files.delete", {"path": "C:\\docs\\report.pdf"})
        assert result.approval_required
        approval = result.approval
        assert approval is not None
        assert approval.title  # non-empty
        assert approval.target  # non-empty
        assert approval.consequence  # non-empty
        assert approval.risk_level > 0
        assert "report.pdf" in approval.target or "report.pdf" in approval.title
