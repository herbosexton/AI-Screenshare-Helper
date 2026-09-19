from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class Step(BaseModel):
    id: str = Field(default_factory=_new_id)
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    tool: Optional[str] = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    attempts: int = 0
    dependencies: list[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=_new_id)
    task_id: str
    action: str
    purpose: str = ""
    target: str = ""
    permission_level: int = 2
    details: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"  # pending | approved | rejected
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = None


class Task(BaseModel):
    id: str = Field(default_factory=_new_id)
    title: str
    user_request: str
    status: TaskStatus = TaskStatus.PENDING
    current_step: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    steps: list[Step] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    results: list[Any] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    summary: str = ""

    def touch(self) -> None:
        self.updated_at = _utcnow()

    def get_step(self, step_id: str) -> Optional[Step]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def add_step(self, description: str, tool: Optional[str] = None, **kwargs: Any) -> Step:
        step = Step(description=description, tool=tool, **kwargs)
        self.steps.append(step)
        self.current_step = step.id
        self.touch()
        return step
