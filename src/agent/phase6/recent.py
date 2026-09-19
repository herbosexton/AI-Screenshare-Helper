"""Short-lived result of the last completed Phase 6 task, for local corrections."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.agent.phase6.models import AgentTask


RECENT_TASK_TTL_S = 15 * 60


@dataclass
class RecentTaskResultContext:
    task_id: str = ""
    original_request: str = ""
    selected_resume: str = ""
    selected_resume_path: str = ""
    candidate_files: list[dict[str, Any]] = field(default_factory=list)
    opened_artifact: dict[str, Any] = field(default_factory=dict)
    final_browser_page: str = ""
    final_browser_url: str = ""
    completed_steps: list[str] = field(default_factory=list)
    claimed_outcomes: dict[str, bool] = field(default_factory=dict)
    verified_outcomes: dict[str, bool] = field(default_factory=dict)
    persistence_state: str = ""
    completed_at: float = 0.0
    filename: str = ""
    hwnd: int = 0
    process_id: int = 0
    process_name: str = ""
    window_title: str = ""
    opened_in_browser: bool = False
    resume_tab_title: str = ""
    prior_chrome_hwnd: int = 0
    prior_chrome_title: str = ""
    task_type: str = ""
    comparison_result: dict[str, Any] = field(default_factory=dict)
    job_page_url: str = ""
    job_page_title: str = ""
    job_page_model_id: str = ""


_LAST: Optional[RecentTaskResultContext] = None


def clear_recent() -> None:
    global _LAST
    _LAST = None


def recent_task() -> Optional[RecentTaskResultContext]:
    if _LAST is None:
        return None
    if _LAST.completed_at and (time.time() - _LAST.completed_at) > RECENT_TASK_TTL_S:
        return None
    return _LAST


def remember_recent_task(task: AgentTask, *, claimed: Optional[dict[str, bool]] = None) -> RecentTaskResultContext:
    global _LAST
    facts = task.context.facts or {}
    arts = list(facts.get("opened_artifacts") or [])
    last_art = arts[-1] if arts and isinstance(arts[-1], dict) else {}
    comparison = facts.get("comparison_result") if isinstance(facts.get("comparison_result"), dict) else {}
    ctx = RecentTaskResultContext(
        task_id=task.id,
        original_request=task.original_request or task.normalized_goal,
        selected_resume=str(facts.get("selected_resume") or task.context.current_document or ""),
        selected_resume_path=str(facts.get("selected_resume_path") or task.context.selected_file or ""),
        candidate_files=list(task.context.candidate_files or []),
        opened_artifact=dict(last_art),
        final_browser_page=str(facts.get("site_name") or task.context.current_page or ""),
        final_browser_url=str(task.context.current_url or facts.get("current_url") or ""),
        completed_steps=list(task.completed_descriptions()),
        claimed_outcomes=dict(claimed or {}),
        verified_outcomes={
            "resume_persisted": bool(facts.get("resume_exists_at_finalization")),
            "chrome_focused": bool(facts.get("chrome_focus_verified")),
            "current_page_resolved": bool(task.context.current_url or facts.get("site_name")),
        },
        persistence_state=str(facts.get("persistence_state") or ""),
        completed_at=time.time(),
        filename=str(last_art.get("filename") or "")
        or str(facts.get("selected_resume_path") or "").replace("\\", "/").split("/")[-1],
        hwnd=int(facts.get("resume_hwnd") or 0) or int(last_art.get("hwnd") or 0),
        process_id=int(facts.get("resume_pid") or 0),
        process_name=str(facts.get("resume_handler") or ""),
        window_title=str(facts.get("resume_window_title") or ""),
        opened_in_browser=bool(facts.get("opened_in_existing_browser")),
        resume_tab_title=str(facts.get("resume_tab_title") or last_art.get("window_title") or ""),
        prior_chrome_hwnd=int(facts.get("prior_chrome_hwnd") or 0),
        prior_chrome_title=str(facts.get("prior_chrome_title") or facts.get("prior_browser_title") or ""),
        task_type=str(facts.get("task_type") or ""),
        comparison_result=dict(comparison),
        job_page_url=str((comparison or {}).get("job_page_url") or task.context.current_url or facts.get("current_url") or ""),
        job_page_title=str((comparison or {}).get("job_page_title") or task.context.current_page or ""),
        job_page_model_id=str(facts.get("job_page_model_id") or (comparison or {}).get("job_page_model_id") or ""),
    )
    _LAST = ctx
    print(
        f"[RecentTask] task_id={ctx.task_id} selected_resume={ctx.selected_resume_path or '-'} "
        f"persistence={ctx.persistence_state or '-'} page={ctx.final_browser_page or '-'}"
    )
    return ctx
