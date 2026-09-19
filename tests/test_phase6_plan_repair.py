"""A malformed step must not become a step that quietly does nothing."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from src.agent.audit import AuditLog
from src.agent.emergency import EmergencyStop
from src.agent.permissions import AutonomyMode, PermissionEngine, PermissionLevel
from src.agent.phase6.planner import (
    Plan,
    PlanValidationError,
    PlanValidator,
    _normalize_steps,
)
from src.agent.tools.base import BaseTool, ToolRegistry, ToolResult


MANGLED = (
    'step_5:{id":"5","description":"Switch back to chatgpt",'
    '"tool":"computer.focus_window","arguments":{"title_contains":"ChatGPT"}}'
)


class TitleParams(BaseModel):
    title_contains: str = Field("")


class _Focus(BaseTool):
    name = "computer.focus_window"
    description = "focus a window"
    parameters_model = TitleParams
    permission_level = PermissionLevel.LOCAL_ACTION

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data={})


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry(
        PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True),
        AuditLog(),
        EmergencyStop(),
    )
    reg.register(_Focus())
    return reg


def test_a_step_the_model_serialized_as_a_string_keeps_its_tool():
    """The blob landed in `description`, the tool went missing, and the step did nothing."""
    (step,) = _normalize_steps([MANGLED])
    assert step["tool"] == "computer.focus_window"
    assert step["description"] == "Switch back to chatgpt"
    assert step["arguments"] == {"title_contains": "ChatGPT"}


def test_a_well_formed_step_is_left_alone():
    steps = _normalize_steps([{"id": "s1", "description": "Go", "tool": "browser.goto"}])
    assert steps == [{"id": "s1", "description": "Go", "tool": "browser.goto"}]


def test_a_plain_string_step_is_still_a_description():
    assert _normalize_steps(["Compare the two"]) == [{"description": "Compare the two"}]


def test_the_recovered_step_validates_into_a_real_action(registry):
    steps = PlanValidator(registry).validate(Plan(goal="switch back", steps=[MANGLED]))
    assert steps[0].preferred_tool == "computer.focus_window"
    assert steps[0].tool_arguments == {"title_contains": "ChatGPT"}


def test_json_left_in_a_description_is_rejected_rather_than_run(registry):
    """If recovery fails the plan must go back for repair, not execute a no-op step."""
    with pytest.raises(PlanValidationError) as caught:
        PlanValidator(registry).validate(
            Plan(goal="x", steps=[{"description": '{"tool":"computer.focus_window"'}])
        )
    assert caught.value.code == "BAD_STEP"


def test_a_step_that_is_really_a_field_name_is_rejected(registry):
    """Four fragments of one step ran nothing each, then reported the work as done."""
    for fragment in ("step_3", "description_open another tab", "tool_browser.new_tab", "arguments_null"):
        with pytest.raises(PlanValidationError) as caught:
            PlanValidator(registry).validate(
                Plan(goal="x", steps=[{"description": fragment}])
            )
        assert caught.value.code == "BAD_STEP", fragment


def test_an_ordinary_description_is_not_mistaken_for_structure(registry):
    for fine in ("Open another tab", "Identify the newest resume", "Compare the two documents"):
        steps = PlanValidator(registry).validate(
            Plan(goal="x", steps=[
                {"description": fine},
                {"description": "Focus it", "tool": "computer.focus_window"},
            ])
        )
        assert steps[0].description == fine


def test_a_plan_that_mostly_does_nothing_is_rejected(registry):
    """Tool-less steps complete by themselves, so such a plan finishes without working."""
    with pytest.raises(PlanValidationError) as caught:
        PlanValidator(registry).validate(
            Plan(goal="x", steps=[
                {"description": "Focus it", "tool": "computer.focus_window"},
                {"description": "Consider the options"},
                {"description": "Think about it"},
                {"description": "Reflect further"},
            ])
        )
    assert caught.value.code == "NO_ACTIONABLE_STEP"


def test_a_plan_with_one_reasoning_step_is_still_fine(registry):
    steps = PlanValidator(registry).validate(
        Plan(goal="x", steps=[
            {"description": "Focus it", "tool": "computer.focus_window"},
            {"description": "Compare the two"},
        ])
    )
    assert len(steps) == 2
