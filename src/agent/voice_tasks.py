"""Deterministic HUD task intents. Planner must not handle 'cross out'."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Optional

from src.agent.router import FastIntent
from src.agent.voice_echo import normalize_speech


_COMPLETE = re.compile(
    r"\b(cross out|check off|check-off|mark done|mark complete|mark completed|"
    r"tick off|cross off)\b|"
    r"\b(complete|finish)\b.+\b(task|todo)\b|"
    r"\b(task|todo).+\b(complete|completed|done|finished)\b",
    re.I,
)
_UNCOMPLETE = re.compile(
    r"\b("
    r"undo(?: that(?: task)?)?|"
    r"uncheck|"
    r"reopen|"
    r"put (?:it |that |the .+ )?back|"
    r"mark (?:that |it |the .+ )?incomplete|"
    r"mark .+ not done|"
    r"not done|"
    r"did(?: not|n'?t) finish"
    r")\b",
    re.I,
)
_ADD = re.compile(r"\b(?:add|put|create)\b (.+?) \b(?:to (?:my )?tasks?|on (?:my )?(?:list|todo))\b", re.I)
_DELETE = re.compile(r"\b(?:delete|remove)\b (.+?) \b(?:from (?:my )?(?:tasks?|list)|task)\b", re.I)
_LIST = re.compile(
    r"\b(what(?:'s| is|s)? on my (?:task )?list|what tasks|read (?:me )?my tasks|"
    r"what do i have to do|show (?:my )?tasks)\b",
    re.I,
)
_ORDINAL = {"first": 0, "second": 1, "third": 2, "fourth": 3, "1st": 0, "2nd": 1, "3rd": 2, "4th": 3}


def _task_query(text: str) -> str:
    t = normalize_speech(text)
    t = re.sub(
        r"\b(cross out|check off|check off the|check it off|cross it out|"
        r"mark|complete|finish|tick off|cross off|undo|uncheck|reopen|"
        r"the|task|tasks|todo|test|tests|as completed|as complete|done|please|"
        r"one|it|that|this|again|back|incomplete|not|didnt|did|i|put)\b",
        " ",
        t,
    )
    return " ".join(t.split())


def fuzzy_task_match(tasks: list[dict[str, Any]], query: str) -> dict[str, Any]:
    q = normalize_speech(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for task in tasks:
        name = normalize_speech(str(task.get("text") or ""))
        if not name:
            continue
        ratio = SequenceMatcher(None, q, name).ratio()
        if q and q in name:
            ratio = max(ratio, 0.86)
        if name and name in q:
            ratio = max(ratio, 0.8)
        q_words = [w for w in q.split() if len(w) > 2]
        if q_words:
            hits = sum(1 for w in q_words if w in name)
            ratio = max(ratio, hits / len(q_words) * 0.9)
        scored.append((ratio, task))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return {"task": None, "ambiguous": False, "candidates": [], "score": 0.0}
    best, second = scored[0][0], scored[1][0] if len(scored) > 1 else 0.0
    if best < 0.45:
        return {"task": None, "ambiguous": False, "candidates": scored[:3], "score": best}
    if second >= 0.78 and abs(best - second) < 0.08:
        return {
            "task": None,
            "ambiguous": True,
            "candidates": [s[1] for s in scored[:3]],
            "score": best,
        }
    return {"task": scored[0][1], "ambiguous": False, "candidates": [scored[0][1]], "score": best}


def route_task_intent(text: str, tasks: Optional[list[dict[str, Any]]] = None) -> Optional[FastIntent]:
    from src.agent.command_validator import ASK_CLARIFICATION, DISCARD_LOW_CONFIDENCE, EXECUTE_LOCAL
    from src.agent.local_intent import resolve_intent

    resolved = resolve_intent(text, tasks=list(tasks or []), hud_active=True)
    if (resolved.intent or "").startswith("task.") and resolved.execution_decision in {
        EXECUTE_LOCAL,
        ASK_CLARIFICATION,
        DISCARD_LOW_CONFIDENCE,
    }:
        return resolved.to_fast_intent()
    return None
