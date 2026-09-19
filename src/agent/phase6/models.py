"""Phase 6 task model. Structured plan state, not a chat transcript."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from src.agent.phase6.tool_catalog import accomplishes_same


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class AgentTaskStatus(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentStepStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK = {
    AgentTaskStatus.COMPLETED,
    AgentTaskStatus.FAILED,
    AgentTaskStatus.CANCELLED,
}


class FailureCode(str, Enum):
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    TAB_NOT_FOUND = "TAB_NOT_FOUND"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    NETWORK_ERROR = "NETWORK_ERROR"
    PAGE_CHANGED = "PAGE_CHANGED"
    SCREEN_STATE_STALE = "SCREEN_STATE_STALE"
    UNEXPECTED_STATE = "UNEXPECTED_STATE"
    USER_INPUT_REQUIRED = "USER_INPUT_REQUIRED"
    PLAN_INVALIDATED = "PLAN_INVALIDATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED = "OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED"
    MISSING_REQUIRED_TASK_OUTPUT = "MISSING_REQUIRED_TASK_OUTPUT"
    UNKNOWN = "UNKNOWN"


WAIT_FOR_USER_FAILURES = {
    FailureCode.AUTHENTICATION_REQUIRED,
    FailureCode.CAPTCHA_REQUIRED,
    FailureCode.USER_INPUT_REQUIRED,
}


class Verification(BaseModel):
    verified: bool = False
    method: str = ""
    detail: str = ""
    expected: str = ""
    actual: str = ""


class AgentStep(BaseModel):
    id: str = Field(default_factory=_new_id)
    order: int = 0
    description: str = ""
    status: AgentStepStatus = AgentStepStatus.PENDING
    preferred_tool: Optional[str] = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    attempt_count: int = 0
    max_attempts: int = 2
    result: Optional[Any] = None
    verification: Optional[Verification] = None
    error: Optional[str] = None
    failure_code: Optional[FailureCode] = None
    state_changing: bool = False
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def is_done(self) -> bool:
        return self.status in {
            AgentStepStatus.COMPLETED,
            AgentStepStatus.SKIPPED,
            AgentStepStatus.CANCELLED,
        }

    def summary_line(self) -> str:
        mark = {
            AgentStepStatus.COMPLETED: "done",
            AgentStepStatus.SKIPPED: "skipped",
            AgentStepStatus.RUNNING: "running",
            AgentStepStatus.VERIFYING: "verifying",
            AgentStepStatus.FAILED: "failed",
            AgentStepStatus.BLOCKED: "blocked",
            AgentStepStatus.CANCELLED: "cancelled",
        }.get(self.status, "pending")
        return f"{self.description} [{mark}]"


class TaskContext(BaseModel):
    """Temporary task memory. Not long-term personal memory."""

    active_application: str = ""
    active_window: str = ""
    browser_session_id: str = ""
    active_tab_id: str = ""
    current_url: str = ""
    candidate_tabs: list[dict[str, Any]] = Field(default_factory=list)
    selected_file: str = ""
    candidate_files: list[dict[str, Any]] = Field(default_factory=list)
    current_document: str = ""
    current_page: str = ""
    recent_tool_results: list[dict[str, Any]] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    resolved_references: dict[str, str] = Field(default_factory=dict)
    user_corrections: list[str] = Field(default_factory=list)
    expected_next_state: str = ""

    def note_tool_result(self, tool: str, summary: str, *, keep: int = 6) -> None:
        entry = {"tool": tool, "summary": summary[:300]}
        self.recent_tool_results.append(entry)
        if len(self.recent_tool_results) > keep:
            self.recent_tool_results = self.recent_tool_results[-keep:]

    def compact(self) -> dict[str, Any]:
        """Small dict for planner re-entry. Never the full payload history."""
        state: dict[str, Any] = {}
        for key in (
            "active_application",
            "active_window",
            "current_url",
            "selected_file",
            "current_document",
            "current_page",
            "active_tab_id",
        ):
            value = getattr(self, key, "")
            if value:
                state[key] = value
        if self.candidate_files:
            state["candidate_files"] = [
                str(f.get("name") or f.get("path") or "")[:120] for f in self.candidate_files[:5]
            ]
        if self.candidate_tabs:
            state["candidate_tabs"] = [
                str(t.get("title") or t.get("url") or "")[:120] for t in self.candidate_tabs[:5]
            ]
        if self.facts:
            state["facts"] = {k: str(v)[:120] for k, v in list(self.facts.items())[:6]}
        if self.user_corrections:
            state["user_corrections"] = self.user_corrections[-2:]
        return state


class AgentTask(BaseModel):
    id: str = Field(default_factory=_new_id)
    original_request: str = ""
    normalized_goal: str = ""
    title: str = ""
    status: AgentTaskStatus = AgentTaskStatus.PENDING
    steps: list[AgentStep] = Field(default_factory=list)
    current_step_id: Optional[str] = None
    context: TaskContext = Field(default_factory=TaskContext)
    result: str = ""
    errors: list[str] = Field(default_factory=list)
    replan_count: int = 0
    planner_calls: int = 0
    max_replans: int = 2
    admission_reason: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None

    def touch(self) -> None:
        self.updated_at = _utcnow()

    def get_step(self, step_id: str) -> Optional[AgentStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def next_step(self) -> Optional[AgentStep]:
        for step in sorted(self.steps, key=lambda s: s.order):
            if step.is_done or step.status == AgentStepStatus.FAILED:
                continue
            if not self._dependencies_met(step):
                continue
            return step
        return None

    def _dependencies_met(self, step: AgentStep) -> bool:
        for dep in step.dependencies:
            hit = self.get_step(dep)
            if hit is None or hit.status != AgentStepStatus.COMPLETED:
                return False
        return True

    def remaining_steps(self) -> list[AgentStep]:
        """Work still ahead. A failed step is history, not something still pending."""
        return [
            s
            for s in sorted(self.steps, key=lambda s: s.order)
            if not s.is_done and s.status != AgentStepStatus.FAILED
        ]

    def completed_descriptions(self) -> list[str]:
        return [
            s.description
            for s in sorted(self.steps, key=lambda s: s.order)
            if s.status == AgentStepStatus.COMPLETED
        ]

    def abandoned_descriptions(self) -> list[str]:
        """Steps that failed and were replanned around, so nothing may claim they happened.

        A step is resolved if a later step completed the same work, by the same tool or a
        substitute for it, which is how a replan retries a search by a different route.
        Anything left is a real shortfall: an action that never ran, or a lookup that
        found nothing and left every step after it with no input.

        State-changing steps come first, because when only one shortfall can be reported
        the action the user asked for matters more than the lookup that fed it.
        """
        ordered = sorted(self.steps, key=lambda s: s.order)
        actions: list[str] = []
        reads: list[str] = []
        for i, step in enumerate(ordered):
            if step.status != AgentStepStatus.FAILED:
                continue
            if any(
                later.status == AgentStepStatus.COMPLETED
                and accomplishes_same(step.preferred_tool or "", later.preferred_tool or "")
                for later in ordered[i + 1 :]
            ):
                continue
            (actions if step.state_changing else reads).append(step.description)
        return actions + reads

    def progress(self) -> dict[str, Any]:
        """Compact HUD progress. No chain of thought."""
        return {
            "task_id": self.id,
            "title": self.title or self.normalized_goal or self.original_request[:60],
            "status": self.status.value,
            "current_step_id": self.current_step_id,
            "steps": [
                {
                    "id": s.id,
                    "order": s.order,
                    "description": (
                        "Resume closed unexpectedly"
                        if s.failure_code == FailureCode.OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED
                        else s.description
                    ),
                    "status": s.status.value,
                    "error": (s.error or "")[:80],
                }
                for s in sorted(self.steps, key=lambda s: s.order)
            ],
        }

    def compact_state(self) -> dict[str, Any]:
        """Planner re-entry payload. Bounded by construction."""
        return {
            "goal": self.normalized_goal or self.original_request,
            "completed": self.completed_descriptions()[-6:],
            "current_state": self.context.compact(),
            "next_goal": (self.remaining_steps()[0].description if self.remaining_steps() else ""),
        }
