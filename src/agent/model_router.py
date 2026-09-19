"""Model tiers. Direct commands never touch PLANNER_MODEL."""

from __future__ import annotations

from typing import Optional


NO_MODEL = "NO_MODEL"
FAST_MODEL = "FAST_MODEL"
CONVERSATION_MODEL = "CONVERSATION_MODEL"
PLANNER_MODEL = "PLANNER_MODEL"
VISION_MODEL = "VISION_MODEL"


class ModelRouter:
    """Choose the cheapest model tier that can handle the request."""

    def choose(
        self,
        *,
        fast_intent=None,
        control: bool = False,
        hud: bool = False,
        needs_vision: bool = False,
        needs_light_nlu: bool = False,
        conversation: bool = False,
        planner_admitted: bool = False,
    ) -> str:
        if control or hud or fast_intent is not None:
            return NO_MODEL
        if needs_vision:
            return VISION_MODEL
        if needs_light_nlu:
            return FAST_MODEL
        if conversation and not planner_admitted:
            return CONVERSATION_MODEL
        if planner_admitted:
            return PLANNER_MODEL
        return CONVERSATION_MODEL
