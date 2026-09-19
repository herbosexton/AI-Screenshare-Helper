from __future__ import annotations

from enum import IntEnum
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.agent.models.task import ApprovalRequest


class PermissionLevel(IntEnum):
    OBSERVE = 0
    LOCAL_ACTION = 1
    EXTERNAL_ACTION = 2
    HIGH_RISK = 3


class AutonomyMode(str):
    OBSERVE = "observe"
    ASSIST = "assist"
    AUTONOMOUS = "autonomous"


# Paths / filenames that must never be auto-exposed
SECRET_PATH_PATTERNS = (
    ".env",
    ".env.",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    ".pem",
    ".key",
    "credentials.json",
    "token.json",
    "secrets.json",
    "login data",
    "logindata",
    "cookies.sqlite",
    "wallet.dat",
    "masterkey",
    "credential",
    "password",
    "privatekey",
    "private_key",
    "ssh/",
    ".ssh\\",
    ".ssh/",
    "appdata\\microsoft\\credentials",
    "appdata/microsoft/credentials",
    "appdata\\microsoft\\protect",
    "appdata/microsoft/protect",
)


class PermissionDecision(BaseModel):
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
    level: PermissionLevel = PermissionLevel.OBSERVE
    approval: Optional[ApprovalRequest] = None


class PermissionEngine:
    """
    Hard permission gate outside the LLM.
    Autonomy mode cannot raise privileges above what the tool declares;
    it only controls whether L1/L2 auto-run or need approval.
    """

    def __init__(
        self,
        autonomy_mode: str = AutonomyMode.ASSIST,
        computer_control_enabled: bool = False,
        blocked_directories: Optional[list[str]] = None,
        allowed_directories: Optional[list[str]] = None,
    ):
        self.autonomy_mode = autonomy_mode
        self.computer_control_enabled = computer_control_enabled
        self.blocked_directories = [d.lower() for d in (blocked_directories or [])]
        self.allowed_directories = [d.lower() for d in (allowed_directories or [])]

    def is_secret_path(self, path: str) -> bool:
        normalized = path.replace("/", "\\").lower()
        for pattern in SECRET_PATH_PATTERNS:
            if pattern.lower() in normalized:
                return True
        return False

    def check_path_access(self, path: str, write: bool = False) -> PermissionDecision:
        if self.is_secret_path(path):
            return PermissionDecision(
                allowed=False,
                reason=f"Blocked sensitive path: {path}",
                level=PermissionLevel.HIGH_RISK,
            )
        # Directory traversal attempt
        if ".." in path.replace("\\", "/").split("/"):
            return PermissionDecision(
                allowed=False,
                reason="Directory traversal is not allowed",
                level=PermissionLevel.HIGH_RISK,
            )
        lower = path.lower()
        for blocked in self.blocked_directories:
            if lower.startswith(blocked) or blocked in lower:
                return PermissionDecision(
                    allowed=False,
                    reason=f"Path is in a blocked directory: {path}",
                    level=PermissionLevel.HIGH_RISK,
                )
        if self.allowed_directories:
            if not any(lower.startswith(a) for a in self.allowed_directories):
                return PermissionDecision(
                    allowed=False,
                    reason=f"Path is outside allowed directories: {path}",
                    level=PermissionLevel.LOCAL_ACTION,
                )
        if write:
            return PermissionDecision(
                allowed=False,
                requires_approval=True,
                reason="Write/overwrite requires explicit approval",
                level=PermissionLevel.HIGH_RISK,
            )
        return PermissionDecision(allowed=True, level=PermissionLevel.OBSERVE)

    def evaluate(
        self,
        tool_name: str,
        permission_level: int,
        task_id: str,
        arguments: Optional[dict[str, Any]] = None,
        purpose: str = "",
    ) -> PermissionDecision:
        level = PermissionLevel(permission_level)
        arguments = arguments or {}

        # Path arguments get secret/traversal checks
        for key in ("path", "file", "filepath", "directory", "source", "destination"):
            if key in arguments and isinstance(arguments[key], str):
                path_decision = self.check_path_access(
                    arguments[key],
                    write=level >= PermissionLevel.HIGH_RISK
                    or "delete" in tool_name
                    or "overwrite" in tool_name,
                )
                if not path_decision.allowed and not path_decision.requires_approval:
                    return path_decision

        if self.autonomy_mode == AutonomyMode.OBSERVE and level > PermissionLevel.OBSERVE:
            return PermissionDecision(
                allowed=False,
                reason="Autonomy mode is OBSERVE; actions are disabled",
                level=level,
            )

        if level == PermissionLevel.OBSERVE:
            return PermissionDecision(allowed=True, level=level)

        if level == PermissionLevel.LOCAL_ACTION:
            control_tools = (
                "computer.click",
                "computer.double_click",
                "computer.right_click",
                "computer.drag",
                "computer.scroll",
                "computer.move_mouse",
                "computer.type_text",
                "computer.press_key",
                "computer.hotkey",
                "computer.open_application",
                "computer.open_file",
                "computer.close_window",
                "computer.focus_window",
                "computer.minimize_window",
                "computer.maximize_window",
            )
            if not self.computer_control_enabled and tool_name.startswith(control_tools):
                return PermissionDecision(
                    allowed=False,
                    reason="Computer control is disabled in config (agent.computer_control_enabled)",
                    level=level,
                )
            if self.autonomy_mode == AutonomyMode.OBSERVE:
                return PermissionDecision(
                    allowed=False,
                    reason="Observe mode blocks local actions",
                    level=level,
                )
            return PermissionDecision(allowed=True, level=level)

        # L2 and L3 require approval except temporary autonomous for L2 only
        if level == PermissionLevel.EXTERNAL_ACTION:
            if self.autonomy_mode == AutonomyMode.AUTONOMOUS:
                return PermissionDecision(allowed=True, level=level)
            approval = ApprovalRequest(
                task_id=task_id,
                action=tool_name,
                purpose=purpose or f"External action: {tool_name}",
                target=str(arguments.get("target") or arguments.get("to") or ""),
                permission_level=int(level),
                details=arguments,
            )
            return PermissionDecision(
                allowed=False,
                requires_approval=True,
                reason="External action requires user approval",
                level=level,
                approval=approval,
            )

        # HIGH_RISK always needs approval
        approval = ApprovalRequest(
            task_id=task_id,
            action=tool_name,
            purpose=purpose or f"High-risk action: {tool_name}",
            target=str(arguments.get("path") or arguments.get("target") or ""),
            permission_level=int(level),
            details=arguments,
        )
        return PermissionDecision(
            allowed=False,
            requires_approval=True,
            reason="High-risk action always requires explicit approval",
            level=level,
            approval=approval,
        )
