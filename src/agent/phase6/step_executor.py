"""Executes one validated plan step: run tool → classify → verify → update context."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.agent.phase6.artifacts import capture_opened_file, verify_persisted
from src.agent.phase6.binding import bind_arguments, missing_required
from src.agent.phase6.models import (
    AgentStep,
    AgentStepStatus,
    FailureCode,
    TaskContext,
    Verification,
)
from src.agent.phase6.results import artifact_snapshot
from src.agent.phase6.recovery import classify_failure


# Tools whose payloads can be huge; only compact facts are kept.
_MAX_SUMMARY = 300
_MAX_SOURCE_TEXT = 8000


@dataclass
class StepOutcome:
    ok: bool = False
    data: Any = None
    error: str = ""
    failure_code: FailureCode = FailureCode.UNKNOWN
    verification: Optional[Verification] = None
    requires_approval: bool = False
    approval: Any = None
    duration_ms: float = 0.0


def summarize_tool_data(tool: str, data: Any) -> str:
    """Compact, human-safe one-liner. Never the raw payload."""
    if data is None:
        return f"{tool}: no data"
    if isinstance(data, dict):
        for key in ("summary", "message", "title", "url", "path", "name"):
            if data.get(key):
                return f"{tool}: {str(data[key])[:_MAX_SUMMARY]}"
        for key in ("files", "results", "matches", "tabs", "elements", "windows"):
            items = data.get(key)
            if isinstance(items, list):
                return f"{tool}: {len(items)} result(s)"
        if "text" in data:
            return f"{tool}: {str(data['text'])[:_MAX_SUMMARY]}"
        return f"{tool}: ok"
    if isinstance(data, list):
        return f"{tool}: {len(data)} result(s)"
    return f"{tool}: {str(data)[:_MAX_SUMMARY]}"


def _as_items(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in keys:
            items = data.get(key)
            if isinstance(items, list):
                return [d for d in items if isinstance(d, dict)]
    return []


def update_context(context: TaskContext, tool: str, arguments: dict[str, Any], data: Any) -> None:
    """Pull only task-relevant facts out of a tool payload."""
    if tool.startswith("files.find") or tool == "files.search" or tool == "files.list":
        items = _as_items(data, "files", "results", "matches")
        if items:
            context.candidate_files = [
                {
                    "name": str(i.get("name") or i.get("filename") or ""),
                    "path": str(i.get("path") or i.get("full_path") or ""),
                    "modified": str(i.get("modified") or i.get("modified_at") or ""),
                }
                for i in items[:10]
            ]
            if not context.selected_file and context.candidate_files:
                context.selected_file = context.candidate_files[0]["path"]
            goal = str(context.facts.get("goal") or "")
            if context.selected_file and re.search(r"\b(resume|r[eé]sum[eé]|cv|curriculum)\b", goal, re.I):
                context.facts["selected_resume"] = context.selected_file
                context.facts["selected_resume_path"] = context.selected_file
                context.facts["selected_resume_modified_at"] = str(
                    (context.candidate_files[0] or {}).get("modified") or ""
                )
    elif tool in {"files.open", "computer.open_file"}:
        path = str(arguments.get("path") or arguments.get("file_path") or "")
        if path:
            context.selected_file = path
            context.current_document = path.replace("\\", "/").split("/")[-1]
            if re.search(r"\b(resume|r[eé]sum[eé]|cv|curriculum)\b", str(context.facts.get("goal") or ""), re.I):
                context.facts["selected_resume"] = path
                context.facts["selected_resume_path"] = path
                context.facts["resume_open_verified"] = True
    elif tool in {"files.read", "files.extract_text"}:
        path = str(arguments.get("path") or "")
        if path:
            context.current_document = path.replace("\\", "/").split("/")[-1]
        text = data.get("text") if isinstance(data, dict) else None
        if isinstance(text, str) and text:
            context.facts["resume_text"] = text[:_MAX_SOURCE_TEXT]
            context.facts["document_excerpt"] = text[:600]
            context.facts["resume_read"] = True
    elif tool == "browser.listTabs":
        items = _as_items(data, "tabs", "results")
        if items:
            context.candidate_tabs = [
                {"title": str(i.get("title") or ""), "url": str(i.get("url") or "")}
                for i in items[:10]
            ]
    elif tool.startswith("browser."):
        if isinstance(data, dict):
            if data.get("url"):
                context.current_url = str(data["url"])
                context.facts["current_url"] = context.current_url
            title = str(data.get("title") or data.get("active_tab_title") or "")
            if title:
                context.current_page = title
                context.facts["current_page"] = title
            if context.current_url:
                from src.agent.respond import page_identity

                context.facts["site_name"] = page_identity(context.current_url, context.current_page)
            text = data.get("text") or data.get("visible_text")
            if isinstance(text, str) and text:
                context.facts["page_text"] = text[:_MAX_SOURCE_TEXT]
                context.facts["page_excerpt"] = text[:600]
                context.facts["job_page_read"] = True
                if data.get("page_type"):
                    context.facts["page_type"] = data.get("page_type")
                if data.get("document_source_method"):
                    context.facts["document_source_method"] = data.get("document_source_method")
                if data.get("structured_jobposting_found") is not None:
                    context.facts["structured_jobposting_found"] = data.get("structured_jobposting_found")
    elif tool in {"computer.get_active_window", "computer.focus_window", "computer.open_application"}:
        if isinstance(data, dict):
            win = data.get("window")
            title = ""
            hwnd = 0
            if isinstance(win, dict):
                title = str(win.get("title") or "")
                hwnd = int(win.get("handle") or win.get("hwnd") or 0)
            title = title or str(data.get("title") or "")
            if title:
                context.active_window = title
                context.active_application = title
            if "chrome" in title.lower():
                context.facts["chrome_focus_verified"] = True
                if hwnd:
                    context.facts["chrome_hwnd"] = hwnd


class PlanStepExecutor:
    def __init__(self, registry, verifier, *, emergency=None):
        self.registry = registry
        self.verifier = verifier
        self.emergency = emergency

    def run_step(
        self,
        step: AgentStep,
        context: TaskContext,
        *,
        task_id: str = "",
        purpose: str = "",
        tool_override: str = "",
        allow_vision: bool = False,
    ) -> StepOutcome:
        tool = tool_override or step.preferred_tool
        if not tool:
            step.status = AgentStepStatus.COMPLETED
            return StepOutcome(
                ok=True,
                verification=Verification(verified=True, method="no_tool", detail="Reasoning step."),
            )

        if self.emergency is not None:
            self.emergency.check()

        # Record what the page looked like before, so UI actions can be verified.
        context.expected_next_state = context.current_url

        step.status = AgentStepStatus.RUNNING
        step.attempt_count += 1
        # The plan was written before the earlier steps ran, so values it could not
        # know yet (a discovered file path, the tab to return to) are filled in now.
        arguments = bind_arguments(tool, step.tool_arguments or {}, context)
        step.tool_arguments = arguments
        unbound = missing_required(self.registry, tool, arguments)
        if unbound:
            # Nothing in task context supplied it, so the plan itself is wrong about the
            # order of work. Say that plainly rather than letting a schema error surface
            # as a missing file or element.
            step.status = AgentStepStatus.FAILED
            step.error = f"No value for {', '.join(sorted(unbound))} when running {tool}."
            step.failure_code = FailureCode.PLAN_INVALIDATED
            return StepOutcome(
                ok=False,
                error=step.error,
                failure_code=FailureCode.PLAN_INVALIDATED,
            )
        if tool in {"files.open", "computer.open_file"}:
            self._snapshot_browser(context)

        started = time.perf_counter()
        result = self.registry.execute(
            tool,
            dict(arguments),
            task_id=task_id,
            purpose=purpose,
        )
        duration = (time.perf_counter() - started) * 1000

        if getattr(result, "requires_approval", False):
            step.status = AgentStepStatus.BLOCKED
            return StepOutcome(
                ok=False,
                requires_approval=True,
                approval=result.data,
                error=result.error or "Approval required",
                duration_ms=duration,
            )

        if not result.success:
            code = classify_failure(result.error, result.data)
            step.status = AgentStepStatus.FAILED
            step.error = result.error
            step.failure_code = code
            return StepOutcome(
                ok=False,
                data=result.data,
                error=result.error or "Tool failed",
                failure_code=code,
                duration_ms=duration,
            )

        update_context(context, tool, arguments, result.data)
        context.note_tool_result(tool, summarize_tool_data(tool, result.data))

        step.status = AgentStepStatus.VERIFYING
        verification = self.verifier.verify(step, result.data, context, allow_vision=allow_vision)
        step.verification = verification
        step.result = summarize_tool_data(tool, result.data)

        if not verification.verified:
            step.status = AgentStepStatus.FAILED
            step.error = verification.detail
            step.failure_code = FailureCode.UNEXPECTED_STATE
            return StepOutcome(
                ok=False,
                data=result.data,
                error=verification.detail,
                failure_code=FailureCode.UNEXPECTED_STATE,
                verification=verification,
                duration_ms=duration,
            )

        if tool in {"files.open", "computer.open_file"}:
            self._remember_opened_file(context, arguments, task_id)
            self._keep_pdf_tab_if_chrome(context)
        if tool == "computer.focus_window":
            if not self._confirm_artifacts_survived(context, when="after_chrome_focus"):
                step.status = AgentStepStatus.FAILED
                step.error = "The file I opened closed when I switched windows."
                step.failure_code = FailureCode.OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED
                return StepOutcome(
                    ok=False,
                    data=result.data,
                    error=step.error,
                    failure_code=FailureCode.OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED,
                    verification=verification,
                    duration_ms=duration,
                )
        if tool.startswith("browser.get"):
            self._confirm_artifacts_survived(context, when="after_page_query")

        step.status = AgentStepStatus.COMPLETED
        return StepOutcome(
            ok=True,
            data=result.data,
            verification=verification,
            duration_ms=duration,
        )

    def _remember_opened_file(self, context: TaskContext, arguments: dict[str, Any], task_id: str) -> None:
        path = str(arguments.get("path") or arguments.get("file_path") or context.selected_file or "")
        if not path:
            return
        computer = getattr(self.verifier, "_computer", None)
        art = capture_opened_file(computer, path, task_id=task_id)
        snaps = list(context.facts.get("opened_artifacts") or [])
        snaps.append(artifact_snapshot(art))
        context.facts["opened_artifacts"] = snaps
        if art.verified_open:
            context.facts["resume_open_verified"] = True
            context.facts["selected_resume_path"] = path
            context.facts["resume_hwnd"] = art.hwnd
            context.facts["resume_pid"] = art.process_id
            context.facts["resume_window_title"] = art.window_title
            context.facts["resume_handler"] = art.extra.get("default_handler") or ""
            context.facts["opened_in_existing_browser"] = bool(art.extra.get("opened_in_existing_browser"))
            if art.window_title:
                context.facts["resume_tab_title"] = art.window_title

    def _snapshot_browser(self, context: TaskContext) -> None:
        computer = getattr(self.verifier, "_computer", None)
        if computer is not None:
            try:
                active = computer.get_active_window() or {}
            except Exception:
                active = {}
            if isinstance(active, dict):
                title = str(active.get("title") or "")
                hwnd = int(active.get("handle") or active.get("hwnd") or 0)
                if "chrome" in title.lower():
                    context.facts.setdefault("prior_chrome_title", title)
                    context.facts.setdefault("prior_chrome_hwnd", hwnd)
        session = None
        getter = getattr(self.verifier, "_browser_getter", None)
        if callable(getter):
            try:
                session = getter()
            except Exception:
                session = None
        agent = getattr(session, "agent", None)
        if agent is not None:
            context.facts.setdefault("prior_browser_url", str(getattr(agent, "url", "") or ""))
            context.facts.setdefault("prior_browser_title", str(getattr(agent, "title", "") or ""))

    def _keep_pdf_tab_if_chrome(self, context: TaskContext) -> None:
        """A PDF that opened as a Chrome tab must stay; later steps use the prior tab."""
        from src.agent.phase6.artifacts import registry

        arts = registry()
        if not arts:
            return
        art = arts[-1]
        handler = str(art.extra.get("process_name") or art.extra.get("default_handler") or "").lower()
        if "chrome" not in handler:
            return
        prior_hwnd = int(context.facts.get("prior_chrome_hwnd") or 0)
        prior_title = str(context.facts.get("prior_browser_title") or context.facts.get("prior_chrome_title") or "")
        if not prior_title or not art.hwnd or (prior_hwnd and art.hwnd != prior_hwnd):
            return
        art.extra["resume_tab_title"] = art.window_title
        try:
            from src.agent.browser.discovery import extract_tab_title
            from src.agent.browser.tabs import ActiveTabResolver

            needle = extract_tab_title(prior_title) or prior_title
            ActiveTabResolver().activate_tab_named(art.hwnd, needle)
            print(f"[Artifact] restored prior Chrome tab={needle!r} resume_tab kept")
        except Exception as e:
            print(f"[Artifact] could not restore prior Chrome tab: {e}")

    def _confirm_artifacts_survived(self, context: TaskContext, *, when: str = "after_focus_change") -> bool:
        from src.agent.phase6.artifacts import PERSIST_AFTER_TASK, registry

        computer = getattr(self.verifier, "_computer", None)
        persist = [a for a in registry() if a.persistence == PERSIST_AFTER_TASK]
        if not persist:
            return True
        ok = True
        for art in persist:
            if not verify_persisted(computer, art, when=when):
                ok = False
        if when in {"after_focus_change", "after_chrome_focus"}:
            context.facts["resume_exists_after_switch_to_chrome"] = ok
        context.facts[f"resume_exists_{when}"] = ok
        return ok
