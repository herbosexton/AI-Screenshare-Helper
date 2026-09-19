"""Path allow/deny/readonly resolver for local file tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.agent.permissions import SECRET_PATH_PATTERNS, PermissionDecision, PermissionLevel


class PathAccessError(Exception):
    def __init__(self, message: str, decision: Optional[PermissionDecision] = None):
        super().__init__(message)
        self.decision = decision or PermissionDecision(
            allowed=False, reason=message, level=PermissionLevel.HIGH_RISK
        )


def expand_path(raw: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(raw).strip()))
    return Path(expanded)


def normalize_path(raw: str, *, must_exist: bool = False) -> Path:
    path = expand_path(raw)
    if ".." in path.as_posix().split("/"):
        # Still resolve, but catch escapes after resolve
        pass
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as e:
        raise PathAccessError(f"Path not found: {path}") from e
    except OSError as e:
        raise PathAccessError(f"Invalid path: {path} ({e})") from e
    return resolved


def _norm_str(path: Path) -> str:
    return str(path).replace("/", "\\").lower().rstrip("\\")


def is_secret_path(path: Path | str) -> bool:
    normalized = str(path).replace("/", "\\").lower()
    for pattern in SECRET_PATH_PATTERNS:
        if pattern.lower() in normalized:
            return True
    return False


class FilePathPolicy:
    """Enforces allowlist, blocklist, read-only roots, and secret patterns."""

    def __init__(
        self,
        allowed_directories: Optional[list[str]] = None,
        blocked_directories: Optional[list[str]] = None,
        read_only_directories: Optional[list[str]] = None,
    ):
        self.allowed_roots = self._resolve_roots(allowed_directories or [])
        self.blocked_roots = self._resolve_roots(blocked_directories or [], missing_ok=True)
        self.readonly_roots = self._resolve_roots(read_only_directories or [], missing_ok=True)

    @staticmethod
    def _resolve_roots(raw_list: list[str], missing_ok: bool = True) -> list[Path]:
        roots: list[Path] = []
        for raw in raw_list:
            try:
                path = expand_path(raw)
                resolved = path.resolve(strict=False)
                if resolved.exists() or missing_ok:
                    roots.append(resolved)
            except OSError:
                continue
        return roots

    def _under_any(self, path: Path, roots: list[Path]) -> bool:
        target = _norm_str(path)
        for root in roots:
            prefix = _norm_str(root)
            if target == prefix or target.startswith(prefix + "\\"):
                return True
        return False

    def is_allowed_root(self, path: Path) -> bool:
        if not self.allowed_roots:
            return False
        return self._under_any(path, self.allowed_roots)

    def is_blocked(self, path: Path) -> bool:
        if is_secret_path(path):
            return True
        return self._under_any(path, self.blocked_roots)

    def is_readonly(self, path: Path) -> bool:
        return self._under_any(path, self.readonly_roots)

    def resolve(self, raw: str, *, must_exist: bool = False) -> Path:
        path = normalize_path(raw, must_exist=must_exist)
        # Reject symlink/junction escape: resolved path must still be under allowlist
        if ".." in Path(raw).as_posix().split("/"):
            if not self.is_allowed_root(path):
                raise PathAccessError(
                    "Directory traversal is not allowed",
                    PermissionDecision(
                        allowed=False,
                        reason="Directory traversal is not allowed",
                        level=PermissionLevel.HIGH_RISK,
                    ),
                )
        return path

    def check(self, raw: str, *, write: bool = False, must_exist: bool = False) -> Path:
        path = self.resolve(raw, must_exist=must_exist)
        if is_secret_path(path):
            raise PathAccessError(
                f"Blocked sensitive path: {path}",
                PermissionDecision(
                    allowed=False,
                    reason=f"Blocked sensitive path: {path}",
                    level=PermissionLevel.HIGH_RISK,
                ),
            )
        if self.is_blocked(path):
            raise PathAccessError(
                f"Path is in a blocked directory: {path}",
                PermissionDecision(
                    allowed=False,
                    reason=f"Path is in a blocked directory: {path}",
                    level=PermissionLevel.HIGH_RISK,
                ),
            )
        if not self.allowed_roots:
            raise PathAccessError(
                "No allowed directories configured for file tools",
                PermissionDecision(
                    allowed=False,
                    reason="No allowed directories configured for file tools",
                    level=PermissionLevel.LOCAL_ACTION,
                ),
            )
        if not self.is_allowed_root(path):
            raise PathAccessError(
                f"Path is outside allowed directories: {path}",
                PermissionDecision(
                    allowed=False,
                    reason=f"Path is outside allowed directories: {path}",
                    level=PermissionLevel.LOCAL_ACTION,
                ),
            )
        if write and self.is_readonly(path):
            raise PathAccessError(
                f"Path is in a read-only directory: {path}",
                PermissionDecision(
                    allowed=False,
                    reason=f"Path is in a read-only directory: {path}",
                    level=PermissionLevel.HIGH_RISK,
                ),
            )
        return path
