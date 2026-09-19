from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Type

from pydantic import BaseModel, ValidationError

from src.agent.audit import AuditLog, redact_secrets
from src.agent.emergency import EmergencyStop, GLOBAL_EMERGENCY_STOP
from src.agent.permissions import PermissionEngine, PermissionLevel


class ToolResult(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None
    requires_approval: bool = False
    approval_id: Optional[str] = None


class BaseTool(ABC):
    name: str
    description: str
    permission_level: PermissionLevel = PermissionLevel.OBSERVE
    timeout_s: float = 30.0
    max_retries: int = 2
    parameters_model: Type[BaseModel] = BaseModel
    response_model: Type[BaseModel] = ToolResult

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    def openai_schema(self) -> dict[str, Any]:
        schema = self.parameters_model.model_json_schema()
        schema.pop("title", None)
        schema.pop("$defs", None)
        desc = (self.description or "").strip()
        if len(desc) > 160:
            desc = desc[:157].rstrip() + "…"
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": desc,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(
        self,
        permission_engine: PermissionEngine,
        audit_log: Optional[AuditLog] = None,
        emergency_stop: Optional[EmergencyStop] = None,
    ):
        self._tools: dict[str, BaseTool] = {}
        self.permissions = permission_engine
        self.audit = audit_log or AuditLog()
        self.emergency = emergency_stop or GLOBAL_EMERGENCY_STOP
        self.pending_approvals: dict[str, Any] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def openai_tools(self, names: Optional[set[str]] = None) -> list[dict[str, Any]]:
        tools = self._tools.values()
        if names is not None:
            tools = [t for t in tools if t.name in names]
        return [t.openai_schema() for t in tools]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        try:
            model = tool.parameters_model.model_validate(arguments or {})
            return model.model_dump()
        except ValidationError as e:
            raise ValueError(f"Invalid arguments for {name}: {e}") from e

    def execute(
        self,
        name: str,
        arguments: Optional[dict[str, Any]] = None,
        *,
        task_id: str = "",
        purpose: str = "",
    ) -> ToolResult:
        self.emergency.check()

        tool = self._tools.get(name)
        if tool is None:
            self.audit.record(
                tool=name,
                action="reject_unknown",
                task_id=task_id or None,
                success=False,
                error="Unknown tool",
            )
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        try:
            validated = self.validate_arguments(name, arguments or {})
        except (KeyError, ValueError) as e:
            self.audit.record(
                tool=name,
                action="validation_failed",
                task_id=task_id or None,
                success=False,
                error=str(e),
            )
            return ToolResult(success=False, error=str(e))

        decision = self.permissions.evaluate(
            tool_name=name,
            permission_level=int(tool.permission_level),
            task_id=task_id or "none",
            arguments=validated,
            purpose=purpose,
        )

        if decision.requires_approval and decision.approval:
            self.pending_approvals[decision.approval.id] = decision.approval
            self.audit.record(
                tool=name,
                action="awaiting_approval",
                task_id=task_id or None,
                target=str(validated)[:200],
                permission_level=int(tool.permission_level),
                success=True,
                result={"approval_id": decision.approval.id},
            )
            return ToolResult(
                success=False,
                requires_approval=True,
                approval_id=decision.approval.id,
                error=decision.reason,
                data=decision.approval.model_dump(mode="json"),
            )

        if not decision.allowed:
            self.audit.record(
                tool=name,
                action="denied",
                task_id=task_id or None,
                permission_level=int(tool.permission_level),
                success=False,
                error=decision.reason,
            )
            return ToolResult(success=False, error=decision.reason)

        attempts = 0
        last_error: Optional[str] = None
        max_retries = max(0, tool.max_retries)

        while attempts <= max_retries:
            self.emergency.check()
            attempts += 1
            started = time.perf_counter()
            try:
                result = tool.execute(**validated)
                duration = (time.perf_counter() - started) * 1000
                self.audit.record(
                    tool=name,
                    action="execute",
                    task_id=task_id or None,
                    target=str(redact_secrets(validated))[:300],
                    result=redact_secrets(result.data if result.success else result.error),
                    permission_level=int(tool.permission_level),
                    duration_ms=duration,
                    success=result.success,
                    error=result.error,
                )
                if result.success or attempts > max_retries:
                    return result
                last_error = result.error
            except Exception as e:
                duration = (time.perf_counter() - started) * 1000
                last_error = str(e)
                self.audit.record(
                    tool=name,
                    action="crash",
                    task_id=task_id or None,
                    permission_level=int(tool.permission_level),
                    duration_ms=duration,
                    success=False,
                    error=last_error,
                )
                if attempts > max_retries:
                    return ToolResult(success=False, error=last_error)

        return ToolResult(success=False, error=last_error or "Tool failed")
