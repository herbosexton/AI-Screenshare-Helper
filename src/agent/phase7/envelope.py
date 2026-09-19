"""ActionEnvelope — normalized metadata for every action before execution.

Every tool call passes through an ActionEnvelope before reaching the permission policy.
The envelope carries enough context to make a deterministic security decision without
consulting the LLM.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------

class RiskLevel(IntEnum):
    """5-tier risk classification.

    Policy decisions key off this, not tool names.  A tool that normally sits
    at OBSERVE can produce a LOCAL_PERSISTENT envelope when its arguments
    include a write target, and DESTRUCTIVE_EXTERNAL when the target is remote.
    """

    OBSERVE = 0            # Read screen / file / page — no change at all
    LOCAL_REVERSIBLE = 1   # Open app, switch tab, scroll, fill unsent field
    LOCAL_PERSISTENT = 2   # Create / modify / rename / move local file
    DESTRUCTIVE_EXTERNAL = 3  # Delete file, send email, submit form, share
    HIGH_IMPACT = 4        # Purchase, financial transfer, legal submission


# ---------------------------------------------------------------------------
# Action classification flags
# ---------------------------------------------------------------------------

class ActionFlags(BaseModel):
    """Semantic booleans describing *what kind of effect* the action has.

    These are derived from the tool + arguments, not from the model's opinion.
    """

    state_changing: bool = False
    external_effect: bool = False
    destructive: bool = False
    reversible: bool = True
    sensitive: bool = False
    financial: bool = False
    authentication_related: bool = False
    communication: bool = False
    data_sharing: bool = False


# ---------------------------------------------------------------------------
# ActionEnvelope
# ---------------------------------------------------------------------------

class ActionEnvelope(BaseModel):
    """Normalized representation of a single action about to execute.

    Created by the tool registry immediately before the permission policy
    evaluates whether to ALLOW, REQUIRE_APPROVAL, or DENY.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    step_id: str = ""
    tool: str
    action: str = ""          # sub-action if the tool has several (e.g. "send" vs "draft")
    target: str = ""          # human-readable: file path, URL, email address, etc.
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_hash: str = ""  # SHA-256 of canonical arguments for TOCTOU binding

    actor: str = "agent"      # "agent" | "user_direct"
    source: str = "phase6"    # "phase6" | "fast_router" | "manual"

    risk_level: RiskLevel = RiskLevel.OBSERVE
    flags: ActionFlags = Field(default_factory=ActionFlags)
    permission_scope: str = ""  # e.g. "task:abc123" or "session"

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def compute_arguments_hash(self) -> str:
        """Canonical hash of the arguments for TOCTOU binding."""
        canonical = json.dumps(self.arguments, sort_keys=True, default=str)
        self.arguments_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return self.arguments_hash

    def matches_approval(self, approval_arguments_hash: str) -> bool:
        """True if this envelope's arguments still match the approved version."""
        if not self.arguments_hash:
            self.compute_arguments_hash()
        return self.arguments_hash == approval_arguments_hash


# ---------------------------------------------------------------------------
# Risk classifier
# ---------------------------------------------------------------------------

# Tools whose final action is inherently destructive or external
_DESTRUCTIVE_TOOLS = frozenset({
    "files.delete", "files.permanent_delete",
})

_EXTERNAL_TOOLS = frozenset({
    "email.send", "email.forward", "email.reply",
    "message.send",
    "form.submit",
})

_FINANCIAL_TOOLS = frozenset({
    "purchase.checkout", "purchase.confirm",
    "payment.send", "payment.transfer",
})

_AUTH_TOOLS = frozenset({
    "auth.login", "auth.change_password", "auth.update_credentials",
})

_COMMUNICATION_TOOLS = frozenset({
    "email.send", "email.forward", "email.reply",
    "message.send", "message.forward",
})

# Browser actions that submit externally
_SUBMIT_ACTIONS = frozenset({
    "submit", "send", "publish", "post", "purchase", "checkout",
    "pay", "transfer", "confirm purchase", "place order",
    "confirm_purchase", "place_order", "buy", "order",
})

# File actions by operation type
_FILE_READ_OPS = frozenset({"read", "find_recent", "search", "list", "head", "stat"})
_FILE_WRITE_OPS = frozenset({"write", "overwrite", "create", "append", "create_directory"})
_FILE_MOVE_OPS = frozenset({"move", "rename", "copy"})
_FILE_DELETE_OPS = frozenset({"delete", "permanent_delete", "remove"})


def _extract_action(tool: str) -> str:
    """The last component of the tool name, e.g. 'files.delete' -> 'delete'."""
    return tool.rsplit(".", 1)[-1] if "." in tool else tool


def _extract_target(tool: str, arguments: dict[str, Any]) -> str:
    """Best human-readable target from the arguments."""
    for key in ("path", "url", "target", "to", "file", "filepath",
                "directory", "email", "recipient", "title_contains"):
        if key in arguments and arguments[key]:
            return str(arguments[key])
    return ""


def classify_risk(tool: str, arguments: dict[str, Any]) -> tuple[RiskLevel, ActionFlags]:
    """Classify a tool call into a risk level and semantic flags.

    This is deterministic code, not an LLM decision.  It examines the tool
    name and arguments to decide what kind of effect the action has.
    """
    action = _extract_action(tool)
    flags = ActionFlags()

    # --- Financial (always L4) ---
    if tool in _FINANCIAL_TOOLS:
        flags.financial = True
        flags.state_changing = True
        flags.external_effect = True
        flags.reversible = False
        return RiskLevel.HIGH_IMPACT, flags

    # --- Authentication (always L4) ---
    if tool in _AUTH_TOOLS:
        flags.authentication_related = True
        flags.state_changing = True
        flags.sensitive = True
        flags.reversible = False
        return RiskLevel.HIGH_IMPACT, flags

    # --- Destructive file operations (L3) ---
    if tool in _DESTRUCTIVE_TOOLS or action in _FILE_DELETE_OPS:
        flags.destructive = True
        flags.state_changing = True
        flags.reversible = action != "permanent_delete"
        return RiskLevel.DESTRUCTIVE_EXTERNAL, flags

    # --- External communication (L3) ---
    if tool in _EXTERNAL_TOOLS or tool in _COMMUNICATION_TOOLS:
        flags.communication = True
        flags.external_effect = True
        flags.state_changing = True
        flags.reversible = False
        return RiskLevel.DESTRUCTIVE_EXTERNAL, flags

    # --- Browser form submit / purchase button click (L3) ---
    if tool.startswith("browser.") and action in ("click", "submit"):
        # Check if the click target looks like a submit action
        target_text = str(arguments.get("text", "") or arguments.get("name", "")).lower()
        if any(s in target_text for s in _SUBMIT_ACTIONS):
            flags.external_effect = True
            flags.state_changing = True
            flags.reversible = False
            return RiskLevel.DESTRUCTIVE_EXTERNAL, flags

    # --- Computer control clicking a dangerous button (L3) ---
    if tool in ("computer.click", "screen.click_element"):
        target_text = str(
            arguments.get("name", "")
            or arguments.get("text", "")
            or arguments.get("element", "")
            or arguments.get("target", "")
        ).lower()
        if any(s in target_text for s in _SUBMIT_ACTIONS):
            flags.external_effect = True
            flags.state_changing = True
            return RiskLevel.DESTRUCTIVE_EXTERNAL, flags

    # --- File write/create (L2) ---
    if tool.startswith("files.") and action in _FILE_WRITE_OPS:
        flags.state_changing = True
        flags.reversible = True
        return RiskLevel.LOCAL_PERSISTENT, flags

    # --- File move/rename (L2) ---
    if tool.startswith("files.") and action in _FILE_MOVE_OPS:
        flags.state_changing = True
        flags.reversible = True
        return RiskLevel.LOCAL_PERSISTENT, flags

    # --- File read / search / list (L0) ---
    if tool.startswith("files.") and action in _FILE_READ_OPS:
        return RiskLevel.OBSERVE, flags

    # --- Screen read (L0) ---
    if tool.startswith("screen.") and action in ("describe", "read_text", "find_element",
                                                   "get_page_info", "list_elements"):
        return RiskLevel.OBSERVE, flags

    # --- Browser read (L0) ---
    if tool.startswith("browser.") and action in ("read_page", "get_tabs", "get_url",
                                                    "page_info", "list_tabs", "readPage",
                                                    "getUrl", "getTabs"):
        return RiskLevel.OBSERVE, flags

    # --- Browser navigation (L1 — reversible, no external effect) ---
    if tool.startswith("browser.") and action in ("navigate", "goto", "open", "new_tab",
                                                    "switchTab", "close_tab", "back",
                                                    "forward", "reload"):
        flags.state_changing = True
        flags.reversible = True
        return RiskLevel.LOCAL_REVERSIBLE, flags

    # --- Browser form interaction without submit (L1) ---
    if tool.startswith("browser.") and action in ("fill", "type", "select", "check",
                                                    "uncheck", "clear", "click"):
        flags.state_changing = True
        flags.reversible = True
        return RiskLevel.LOCAL_REVERSIBLE, flags

    # --- Computer control (L1 for most) ---
    if tool.startswith("computer."):
        if action in ("get_active_window", "list_windows", "screenshot"):
            return RiskLevel.OBSERVE, flags
        flags.state_changing = True
        flags.reversible = True
        return RiskLevel.LOCAL_REVERSIBLE, flags

    # --- File open (L1) ---
    if tool.startswith("files.") and action in ("open",):
        flags.state_changing = True
        flags.reversible = True
        return RiskLevel.LOCAL_REVERSIBLE, flags

    # --- Default: OBSERVE for reads, LOCAL_REVERSIBLE for unknowns ---
    if action in ("read", "get", "list", "search", "find", "describe", "status",
                   "info", "check", "inspect", "query"):
        return RiskLevel.OBSERVE, flags

    flags.state_changing = True
    return RiskLevel.LOCAL_REVERSIBLE, flags


def create_envelope(
    tool: str,
    arguments: dict[str, Any],
    *,
    task_id: str = "",
    step_id: str = "",
    actor: str = "agent",
    source: str = "phase6",
) -> ActionEnvelope:
    """Build an ActionEnvelope with automatic risk classification."""
    risk_level, flags = classify_risk(tool, arguments)
    target = _extract_target(tool, arguments)

    envelope = ActionEnvelope(
        tool=tool,
        action=_extract_action(tool),
        target=target,
        arguments=arguments,
        task_id=task_id,
        step_id=step_id,
        actor=actor,
        source=source,
        risk_level=risk_level,
        flags=flags,
    )
    envelope.compute_arguments_hash()
    return envelope
