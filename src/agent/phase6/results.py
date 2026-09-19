"""Build the spoken Phase 6 result from completed step facts, never a bare Done."""

from __future__ import annotations

import re
from typing import Any

from src.agent.phase6.artifacts import OpenedArtifact
from src.agent.phase6.models import AgentStepStatus, AgentTask


def _filename(task: AgentTask) -> str:
    facts = task.context.facts or {}
    path = str(facts.get("selected_resume_path") or task.context.selected_file or "")
    if path:
        return path.replace("\\", "/").split("/")[-1]
    arts = list(facts.get("opened_artifacts") or [])
    if arts and isinstance(arts[0], dict):
        return str(arts[0].get("filename") or arts[0].get("file_path") or "").replace("\\", "/").split("/")[-1]
    return ""


def _page_line(task: AgentTask) -> str:
    from src.agent.respond import page_identity

    url = task.context.current_url or str(task.context.facts.get("current_url") or "")
    title = task.context.current_page or str(task.context.facts.get("current_page") or "")
    site = str(task.context.facts.get("site_name") or "") or page_identity(url, title)
    title = (title or "").strip()
    if site and title and site.lower() not in title.lower() and title.lower() not in site.lower():
        return f"You're back on {site} — {title}."
    if site:
        return f"You're back on {site}."
    if title:
        return f"You're back on {title}."
    return ""


def synthesize_task_result(task: AgentTask) -> str:
    """User-facing answer from structured step results. Not 'Done.'"""
    from src.agent.phase6.compare import (
        ComparisonResult,
        contains_comparison,
        format_detailed,
        is_comparison_goal,
        is_step_label,
    )

    facts = task.context.facts or {}
    stored = ComparisonResult.from_dict(facts.get("comparison_result") if isinstance(facts.get("comparison_result"), dict) else None)
    if stored is not None and stored.items:
        detailed = format_detailed(stored)
        if contains_comparison(detailed, stored):
            return detailed
    goal = (task.original_request or task.normalized_goal or "").lower()
    wants_page = bool(re.search(r"\b(what page|what website|what site|page i['’]?m on|page am i)\b", goal))
    wants_open = bool(re.search(r"\bopen(?:\s+(?:it|the|my|a|that|this))\b", goal))
    if is_comparison_goal(task.original_request or task.normalized_goal or ""):
        wants_page = False
        wants_open = False
    name = _filename(task)
    state = str(facts.get("persistence_state") or "")
    verified_final = facts.get("resume_exists_at_finalization")
    opened = bool(facts.get("resume_open_verified") or name)
    parts: list[str] = []
    wants_chrome = bool(re.search(r"\b(chrome|browser)\b", goal))
    if wants_open and name and opened:
        if verified_final is True and state in {"PERSISTING", "BACKGROUND", "MINIMIZED"}:
            if wants_chrome:
                parts.append(f"I opened {name} and left it open in the background.")
            else:
                parts.append(f"I opened {name}.")
        elif state == "CLOSED" or verified_final is False:
            parts.append(
                f"I opened {name}, but it closed unexpectedly when I returned to Chrome."
                if wants_chrome
                else f"I opened {name}, but it closed unexpectedly."
            )
        else:
            parts.append(f"I opened {name}, but I couldn't confirm that it stayed open.")
    elif wants_open and opened and not name:
        if verified_final is True:
            parts.append("I opened the file and left it open.")
        else:
            parts.append("I opened the file, but I couldn't confirm that it stayed open.")
    page = _page_line(task)
    if wants_page:
        parts.append(page or "I could not read the current page.")
    if parts:
        return " ".join(parts)
    for step in sorted(task.steps, key=lambda s: s.order, reverse=True):
        if step.preferred_tool is None and isinstance(step.result, str) and step.result.strip():
            text = step.result.strip()
            if is_step_label(text) or text.lower() in {"done.", "done", "completed.", "ok."}:
                continue
            return text
    return ""


def required_outcomes(task: AgentTask) -> dict[str, bool]:
    goal = (task.original_request or task.normalized_goal or "").lower()
    facts = task.context.facts or {}
    out: dict[str, bool] = {}
    if re.search(r"\b(resume|r[eé]sum[eé]|cv)\b", goal) and re.search(r"\b(find|search|newest|latest)\b", goal):
        out["resume_found"] = bool(task.context.selected_file or facts.get("selected_resume_path"))
    if re.search(r"\bopen(?:\s+(?:it|the|my|a|that|this))\b", goal) and re.search(
        r"\b(resume|it|file|document|cv)\b", goal
    ):
        out["resume_opened"] = bool(facts.get("resume_open_verified") or _filename(task))
        if re.search(r"\b(chrome|browser)\b", goal):
            out["resume_still_open"] = facts.get("resume_exists_at_finalization") is True
    if re.search(r"\b(switch|go back|return|focus).{0,24}\b(chrome|browser)\b", goal):
        active = (task.context.active_window or "").lower()
        out["chrome_focused"] = "chrome" in active or bool(facts.get("chrome_focus_verified"))
    if re.search(r"\b(what page|what website|page i['’]?m on|page am i)\b", goal):
        out["current_page_resolved"] = bool(
            task.context.current_url or task.context.current_page or facts.get("site_name")
        )
        out["final_answer_contains_page"] = bool(_page_line(task))
    from src.agent.phase6.compare import ComparisonResult, contains_comparison, is_comparison_goal

    if is_comparison_goal(task.original_request or task.normalized_goal or ""):
        stored = ComparisonResult.from_dict(
            facts.get("comparison_result") if isinstance(facts.get("comparison_result"), dict) else None
        )
        out["job_page_read"] = bool(facts.get("page_text") or facts.get("page_excerpt") or facts.get("job_page_read"))
        out["resume_read"] = bool(facts.get("resume_text") or facts.get("document_excerpt") or facts.get("resume_read"))
        out["job_requirements_extracted"] = bool(facts.get("job_requirements_extracted") or (stored and stored.items))
        out["resume_evidence_extracted"] = bool(
            facts.get("resume_evidence_extracted") or (stored and (stored.source_evidence or stored.covered_requirements))
        )
        out["comparison_result_created"] = bool(facts.get("comparison_result_created") or stored)
        out["gaps_created"] = bool(facts.get("gaps_created") or stored)
        answer = str(task.result or "")
        if not answer:
            from src.agent.phase6.compare import format_detailed

            answer = format_detailed(stored) if stored else ""
        out["final_answer_contains_comparison"] = bool(
            facts.get("final_answer_contains_comparison") or contains_comparison(answer, stored)
        )
    return out


def outcomes_satisfied(task: AgentTask) -> tuple[bool, dict[str, bool]]:
    checks = required_outcomes(task)
    return (all(checks.values()) if checks else True), checks


def artifact_snapshot(art: OpenedArtifact) -> dict[str, Any]:
    return {
        "artifact_type": art.artifact_type,
        "file_path": art.file_path,
        "filename": art.filename,
        "process_id": art.process_id,
        "hwnd": art.hwnd,
        "window_title": art.window_title,
        "persistence": art.persistence,
        "verified_open": art.verified_open,
    }


def step_was_completed(task: AgentTask, tool_prefix: str) -> bool:
    return any(
        s.status == AgentStepStatus.COMPLETED and (s.preferred_tool or "").startswith(tool_prefix)
        for s in task.steps
    )
