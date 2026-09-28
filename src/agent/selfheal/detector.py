"""Deterministic checks over a finished turn. No model calls: each rule reads recorded decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.agent.selfheal.recorder import TurnRecord

ACTION_ROUTED_TO_CONVERSATION = "ACTION_ROUTED_TO_CONVERSATION"
COMPOUND_COMMAND_DROPPED = "COMPOUND_COMMAND_DROPPED"
PLANNER_TASK_FAILED = "PLANNER_TASK_FAILED"
USER_CORRECTION = "USER_CORRECTION"
USER_REPORTED = "USER_REPORTED"

# Router action labels (src/agent/fast_coverage.py) that require a tool to change something.
STATE_CHANGING_ACTIONS = frozenset({
    "open", "open_file", "open_browser", "focus_browser", "new_tab", "navigate",
    "switch_tab", "switch", "go", "compare", "search", "find", "find_file",
    "close", "click", "type", "press", "scroll", "start", "take",
})
_NO_TOOL_PATHS = frozenset({"ai_conversation"})
_PLANNER_PATHS = frozenset({"planner"})
_BENIGN_FAILURE_CODES = frozenset({
    "CAPTCHA_REQUIRED", "AUTHENTICATION_REQUIRED", "WAITING_FOR_APPROVAL",
    "CANCELLED", "USER_CANCELLED", "EMERGENCY_STOP",
})

_CORRECTION = re.compile(
    r"^\s*(?:no[,.!]?\s+)?(?:jarvis[,.]?\s+)?"
    r"(?:that(?:'s| is| was)\s+(?:wrong|not (?:right|what i (?:asked|said|meant)))|"
    r"you didn'?t (?:do|open|answer) (?:it|that|what i asked)|"
    r"that(?:'s| is) not what i (?:asked|said|meant)|"
    r"you (?:got|did) (?:it|that) wrong)\b",
    re.I,
)


@dataclass
class Finding:
    rule: str
    detail: str
    earliest_wrong_decision: str = ""


def is_user_correction(utterance: str) -> bool:
    return bool(_CORRECTION.search(utterance or ""))


class FailureDetector:
    def check(self, rec: TurnRecord) -> list[Finding]:
        findings: list[Finding] = []
        for rule in (self._action_to_conversation, self._compound_dropped, self._planner_failed):
            hit = rule(rec)
            if hit is not None:
                findings.append(hit)
        return findings

    @staticmethod
    def _action_to_conversation(rec: TurnRecord) -> Optional[Finding]:
        if rec.path not in _NO_TOOL_PATHS:
            return None
        acting = [a for a in rec.detected_actions if a in STATE_CHANGING_ACTIONS]
        if not acting:
            return None
        return Finding(
            rule=ACTION_ROUTED_TO_CONVERSATION,
            detail=(
                f"Router detected state-changing action(s) {acting} but the turn went to the "
                f"no-tools conversation path (planner_admission_reason="
                f"{rec.result.get('planner_admission_reason') or rec.perf.get('planner_admission_reason') or '-'})."
            ),
            earliest_wrong_decision="planner admission / conversational route selection",
        )

    @staticmethod
    def _compound_dropped(rec: TurnRecord) -> Optional[Finding]:
        segments = rec.perf.get("command_segments") or []
        if rec.perf.get("coverage") != "PARTIAL_COVERAGE" or len(segments) < 2:
            return None
        if rec.path in _PLANNER_PATHS or rec.result.get("planner_admitted"):
            return None
        return Finding(
            rule=COMPOUND_COMMAND_DROPPED,
            detail=(
                f"Utterance had {len(segments)} command segments with PARTIAL_COVERAGE, but the "
                f"planner was not admitted (path={rec.path or '-'}). Some segments were never executed."
            ),
            earliest_wrong_decision="planner admission after partial fast-route coverage",
        )

    @staticmethod
    def _planner_failed(rec: TurnRecord) -> Optional[Finding]:
        if rec.path not in _PLANNER_PATHS or rec.result.get("ok", True):
            return None
        code = str(rec.result.get("code") or "")
        if code in _BENIGN_FAILURE_CODES:
            return None
        return Finding(
            rule=PLANNER_TASK_FAILED,
            detail=f"Planner task returned ok=false (code={code or '-'}): {rec.message[:200]!r}",
            earliest_wrong_decision="Phase 6 plan/execute/verify",
        )
