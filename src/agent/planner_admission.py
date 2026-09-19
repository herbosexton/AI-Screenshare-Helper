"""Admit AgentPlanner only when there is evidence of a real agent task."""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.utterance import (
    AGENT_PLAN,
    COMPLEX_GOAL,
    DIRECT_COMMAND,
    QUESTION,
    UtteranceClass,
)


MULTI_STEP_GOAL = "MULTI_STEP_GOAL"
TOOL_REASONING_REQUIRED = "TOOL_REASONING_REQUIRED"
CROSS_APPLICATION_TASK = "CROSS_APPLICATION_TASK"
UNKNOWN_ACTION_REQUIRING_PLANNING = "UNKNOWN_ACTION_REQUIRING_PLANNING"
RECOVERY_REPLAN_REQUIRED = "RECOVERY_REPLAN_REQUIRED"
MULTI_STEP_CROSS_TOOL_GOAL = "MULTI_STEP_CROSS_TOOL_GOAL"
ENVIRONMENT_CHANGED = "ENVIRONMENT_CHANGED"
PLAN_INVALIDATED = "PLAN_INVALIDATED"
NOT_ADMITTED = "NOT_ADMITTED"
NO_LOCAL_INTENT = "NO_LOCAL_INTENT"


@dataclass
class PlannerAdmission:
    admitted: bool = False
    reason: str = NOT_ADMITTED
    conceptual_route: str = ""


def admit_planner(
    classified: UtteranceClass,
    *,
    recovery_replan: bool = False,
    local_intent: str = "",
    environment_changed: bool = False,
    plan_invalidated: bool = False,
) -> PlannerAdmission:
    feats = classified.features or {}
    if recovery_replan:
        return PlannerAdmission(True, RECOVERY_REPLAN_REQUIRED, AGENT_PLAN)
    if plan_invalidated:
        return PlannerAdmission(True, PLAN_INVALIDATED, AGENT_PLAN)
    if environment_changed:
        return PlannerAdmission(True, ENVIRONMENT_CHANGED, AGENT_PLAN)

    if classified.utterance_type == COMPLEX_GOAL or classified.conceptual_route == AGENT_PLAN:
        if feats.get("file_cue") and feats.get("surface_cue"):
            return PlannerAdmission(True, MULTI_STEP_CROSS_TOOL_GOAL, AGENT_PLAN)
        if feats.get("multi_step") and int(feats.get("tool_verbs") or 0) >= 1:
            return PlannerAdmission(True, MULTI_STEP_GOAL, AGENT_PLAN)
        if int(feats.get("tool_verbs") or 0) >= 2:
            return PlannerAdmission(True, TOOL_REASONING_REQUIRED, AGENT_PLAN)
        return PlannerAdmission(True, CROSS_APPLICATION_TASK, AGENT_PLAN)

    if classified.utterance_type == DIRECT_COMMAND and not local_intent:
        if int(feats.get("tool_verbs") or 0) >= 2 or feats.get("multi_step"):
            return PlannerAdmission(True, UNKNOWN_ACTION_REQUIRING_PLANNING, AGENT_PLAN)
        return PlannerAdmission(False, NOT_ADMITTED, classified.conceptual_route)

    if classified.utterance_type == QUESTION:
        return PlannerAdmission(False, NOT_ADMITTED, classified.conceptual_route)

    return PlannerAdmission(False, NOT_ADMITTED, classified.conceptual_route)
