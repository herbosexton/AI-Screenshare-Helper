"""Phase 7 AuditLog — persistent, typed, redacted action history.

Extends the existing AuditLog with:
- Event types beyond tool execution
- Persistent storage (SQLite)
- User-facing activity history
- Permission/approval events
- Emergency-stop events
- Secret redaction on all outputs
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from enum import Enum
from typing import Any, Optional

from src.agent.phase7.redactor import redact_dict, redact_text


class AuditEventType(str, Enum):
    ACTION_REQUESTED = "ACTION_REQUESTED"
    POLICY_DECISION = "POLICY_DECISION"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_CANCELLED = "ACTION_CANCELLED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_REVOKED = "PERMISSION_REVOKED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"


class Phase7AuditLog:
    """Persistent, typed audit log with secret redaction."""

    def __init__(self, db_path: str = "", max_memory: int = 5000):
        self._lock = threading.Lock()
        self._memory: list[dict[str, Any]] = []
        self._max_memory = max_memory
        self._db_path = db_path
        self._db: Optional[sqlite3.Connection] = None

        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        try:
            self._db = sqlite3.connect(self._db_path, check_same_thread=False)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    task_id TEXT DEFAULT '',
                    step_id TEXT DEFAULT '',
                    action_id TEXT DEFAULT '',
                    tool TEXT DEFAULT '',
                    action TEXT DEFAULT '',
                    target TEXT DEFAULT '',
                    risk_level INTEGER DEFAULT 0,
                    policy_decision TEXT DEFAULT '',
                    policy_rule TEXT DEFAULT '',
                    approval_id TEXT DEFAULT '',
                    approval_status TEXT DEFAULT '',
                    executed INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    result TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    duration_ms REAL DEFAULT 0,
                    details TEXT DEFAULT '{}'
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_task
                ON audit_log (task_id)
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_time
                ON audit_log (timestamp)
            """)
            self._db.commit()
        except Exception as e:
            print(f"[Audit] DB init failed: {e}")
            self._db = None

    def record(
        self,
        event_type: AuditEventType,
        *,
        session_id: str = "",
        task_id: str = "",
        step_id: str = "",
        action_id: str = "",
        tool: str = "",
        action: str = "",
        target: str = "",
        risk_level: int = 0,
        policy_decision: str = "",
        policy_rule: str = "",
        approval_id: str = "",
        approval_status: str = "",
        executed: bool = False,
        success: bool = True,
        result: Any = "",
        error: str = "",
        duration_ms: float = 0.0,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Record an audit event. All values are redacted before storage."""
        import uuid

        entry_id = str(uuid.uuid4())
        now = time.time()

        # Redact everything
        safe_target = redact_text(str(target)[:500]) if target else ""
        safe_result = redact_text(str(result)[:2000]) if result else ""
        safe_error = redact_text(str(error)[:1000]) if error else ""
        safe_details = redact_dict(details) if details else {}

        entry = {
            "id": entry_id,
            "timestamp": now,
            "event_type": event_type.value,
            "session_id": session_id,
            "task_id": task_id,
            "step_id": step_id,
            "action_id": action_id,
            "tool": tool,
            "action": action,
            "target": safe_target,
            "risk_level": risk_level,
            "policy_decision": policy_decision,
            "policy_rule": policy_rule,
            "approval_id": approval_id,
            "approval_status": approval_status,
            "executed": executed,
            "success": success,
            "result": safe_result,
            "error": safe_error,
            "duration_ms": duration_ms,
            "details": safe_details,
        }

        with self._lock:
            self._memory.append(entry)
            if len(self._memory) > self._max_memory:
                self._memory = self._memory[-self._max_memory:]

        if self._db is not None:
            try:
                self._db.execute(
                    """INSERT INTO audit_log
                    (id, timestamp, event_type, session_id, task_id, step_id,
                     action_id, tool, action, target, risk_level, policy_decision,
                     policy_rule, approval_id, approval_status, executed, success,
                     result, error, duration_ms, details)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entry_id, now, event_type.value, session_id, task_id,
                        step_id, action_id, tool, action, safe_target,
                        risk_level, policy_decision, policy_rule,
                        approval_id, approval_status, int(executed),
                        int(success), safe_result, safe_error, duration_ms,
                        json.dumps(safe_details, default=str),
                    ),
                )
                self._db.commit()
            except Exception as e:
                print(f"[Audit] DB write failed: {e}")

        return entry

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._memory[-limit:])

    def for_task(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._memory if e.get("task_id") == task_id][-limit:]

    def activity_history(self, limit: int = 30) -> list[dict[str, str]]:
        """User-facing activity timeline.

        Returns simple entries like:
            {"time": "1:42 PM", "description": "Opened Chrome"}
        """
        entries = []
        with self._lock:
            for e in self._memory[-limit:]:
                ts = time.localtime(e["timestamp"])
                try:
                    time_str = time.strftime("%I:%M %p", ts).lstrip("0")
                except Exception:
                    time_str = ""

                event_type = e.get("event_type", "")
                tool = e.get("tool", "")
                action = e.get("action", "")
                target = e.get("target", "")

                desc = self._describe_event(event_type, tool, action, target)
                if desc:
                    entries.append({"time": time_str, "description": desc})

        return entries

    @staticmethod
    def _describe_event(event_type: str, tool: str, action: str, target: str) -> str:
        """Turn an audit event into a human-readable sentence."""
        if event_type == "ACTION_EXECUTED":
            if tool.startswith("browser."):
                if action in ("navigate", "goto"):
                    return f"Navigated to {target}" if target else "Navigated"
                if action == "open":
                    return "Opened Chrome"
                if action == "new_tab":
                    return "Opened a new tab"
                if action == "switchTab":
                    return f"Switched to {target}" if target else "Switched tabs"
                if action in ("click",):
                    return f"Clicked {target}" if target else "Clicked"
                if action in ("fill", "type"):
                    return f"Typed into {target}" if target else "Typed"
                if action == "read_page":
                    return "Read the page"
            if tool.startswith("files."):
                if action == "read":
                    return f"Read {target}" if target else "Read a file"
                if action in ("find_recent", "search"):
                    return f"Searched for files" + (f" matching {target}" if target else "")
                if action == "open":
                    return f"Opened {target}" if target else "Opened a file"
                if action in ("write", "overwrite"):
                    return f"Wrote {target}" if target else "Wrote a file"
                if action == "delete":
                    return f"Deleted {target}" if target else "Deleted a file"
            if tool.startswith("computer."):
                if action == "open_application":
                    return f"Opened {target}" if target else "Opened an application"
                if action == "focus_window":
                    return f"Switched to {target}" if target else "Switched windows"
                if action == "close_window":
                    return "Closed a window"
                if action == "type_text":
                    return "Typed text"
            if tool.startswith("screen."):
                if action == "describe":
                    return "Described what's on screen"
                if action == "click_element":
                    return f"Clicked {target}" if target else "Clicked an element"
            return f"{action} ({tool})" if tool else action

        if event_type == "APPROVAL_REQUESTED":
            return f"Waiting for approval: {target}" if target else "Waiting for approval"
        if event_type == "APPROVAL_GRANTED":
            return f"Approved: {target}" if target else "Approved"
        if event_type == "APPROVAL_DENIED":
            return f"Denied: {target}" if target else "Denied"
        if event_type == "EMERGENCY_STOP":
            return "Emergency stop engaged"
        if event_type == "TASK_STARTED":
            return f"Started task: {target}" if target else "Started a task"
        if event_type == "TASK_COMPLETED":
            return f"Completed: {target}" if target else "Task completed"

        return ""
