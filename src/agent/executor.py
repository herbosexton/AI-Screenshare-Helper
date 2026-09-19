"""Deterministic tool sequence runner. No LLM between successful steps."""

from __future__ import annotations

from typing import Any, Optional

from src.agent.models.task import StepStatus, Task
from src.agent.providers.base import ToolCallRequest
from src.agent.tools.base import ToolRegistry


class DeterministicTaskExecutor:
    def __init__(self, registry: ToolRegistry, emergency=None):
        self.registry = registry
        self.emergency = emergency

    def run(
        self,
        steps: list[tuple[str, dict[str, Any]]],
        *,
        task: Optional[Task] = None,
        task_id: str = "",
        purpose: str = "",
    ) -> dict[str, Any]:
        results: list[Any] = []
        for name, arguments in steps:
            if self.emergency is not None:
                self.emergency.check()
            result = self.registry.execute(
                name,
                arguments or {},
                task_id=task_id,
                purpose=purpose,
            )
            payload = result.data if result.success else {
                "success": False,
                "error": {"code": "TOOL_FAILED", "message": result.error or "failed"},
            }
            if not isinstance(payload, dict):
                payload = {"success": result.success, "data": payload}
            payload.setdefault("success", result.success)
            results.append(payload)
            if task is not None:
                step = task.add_step(description=f"Call {name}", tool=name, arguments=arguments or {})
                step.status = StepStatus.COMPLETED if result.success else StepStatus.FAILED
                step.result = payload if result.success else None
                step.error = None if result.success else result.error
            if not result.success:
                return {"ok": False, "results": results, "error": result.error, "failed_tool": name}
        return {"ok": True, "results": results}
