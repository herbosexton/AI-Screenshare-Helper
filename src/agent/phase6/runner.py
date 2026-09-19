"""AgentTaskRunner — plan once, execute deterministically, verify, recover, replan rarely."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from src.agent.phase6.models import (
    AgentStep,
    AgentStepStatus,
    AgentTask,
    AgentTaskStatus,
    FailureCode,
)
from src.agent.phase6.planner import AgentPlanner, PlanValidationError
from src.agent.phase6.recovery import (
    ALTERNATIVE_TOOL,
    ASK_USER,
    FAIL,
    REFRESH_AND_RETRY,
    RELAX_AND_RETRY,
    REPLAN,
    RETRY,
    WAIT_FOR_USER,
    FailureRecoveryEngine,
)
from src.agent.phase6.references import resolve_reference
from src.agent.phase6.binding import (
    DEFERRED_ARGUMENTS,
    is_placeholder,
    relax_arguments,
    retarget_arguments,
)
from src.agent.phase6.step_executor import PlanStepExecutor, summarize_tool_data, update_context
from src.agent.phase6.tool_catalog import ToolCategoryResolver
from src.agent.phase6.verify import ActionVerifier


REASONING_SYSTEM = (
    "You are Jarvis, reporting on work that has already run. "
    "State the finding or comparison in two or three spoken sentences, past or present tense. "
    "Only describe actions listed under 'done'. Anything under 'not_done' did not happen: "
    "say plainly that you could not do it. Never invent an action or a value that is not in "
    "the supplied state. Never say what you are about to do. "
    "No JSON, no tool names, no step lists, no reasoning."
)

# What a plan with no reasoning step has to say for itself once the actions have run.
_NOTHING_TO_REPORT = "Done."

# Vision is the most expensive verification tier; only use it for these actions.
_VISION_TOOLS = {"computer.open_application", "computer.open_file", "files.open"}


class AgentTaskRunner:
    """Owns one active Phase 6 task at a time."""

    def __init__(
        self,
        provider,
        registry,
        *,
        emergency=None,
        browser_getter: Optional[Callable[[], Any]] = None,
        computer_getter: Optional[Callable[[], Any]] = None,
        screen_getter: Optional[Callable[[], Any]] = None,
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        max_replans: int = 2,
        max_steps: int = 30,
        planner_timeout_s: float = 90.0,
        artifact_settle_s: float = 0.6,
    ):
        self.provider = provider
        self.registry = registry
        self.emergency = emergency
        self.tool_resolver = ToolCategoryResolver()
        self.planner = AgentPlanner(provider, registry, timeout_s=planner_timeout_s)
        self.verifier = ActionVerifier(
            browser_getter=browser_getter,
            computer_getter=computer_getter,
            screen_getter=screen_getter,
        )
        self.step_executor = PlanStepExecutor(registry, self.verifier, emergency=emergency)
        self.recovery = FailureRecoveryEngine(registry)
        self.on_progress = on_progress
        self.on_status = on_status
        self.max_replans = max_replans
        self.max_steps = max_steps
        self.active: Optional[AgentTask] = None
        self._cancelled = False
        self._paused = False
        self._tried: dict[str, set[str]] = {}
        self.artifact_settle_s = artifact_settle_s

    # --- lifecycle ---------------------------------------------------------

    def start(
        self,
        goal: str,
        *,
        admission_reason: str = "",
        perf: Optional[Any] = None,
    ) -> dict[str, Any]:
        task = AgentTask(
            original_request=goal,
            normalized_goal=goal,
            title=goal[:60],
            admission_reason=admission_reason,
            max_replans=self.max_replans,
        )
        task.started_at = task.created_at
        task.context.facts["goal"] = goal
        try:
            from src.agent.phase6.recent import recent_task

            prior = recent_task()
            if prior is not None:
                if prior.selected_resume_path:
                    task.context.facts["prior_resume_path"] = prior.selected_resume_path
                if prior.final_browser_page:
                    task.context.facts["prior_page"] = prior.final_browser_page
        except Exception:
            pass
        self.active = task
        self._cancelled = False
        self._paused = False
        self._tried = {}

        task.status = AgentTaskStatus.PLANNING
        self._publish(task)
        selection = self.tool_resolver.select(
            self.registry,
            goal,
            browser_open=self._browser_open(),
            perf=perf,
        )
        try:
            steps = self.planner.plan(task, tool_schemas=selection.schemas, perf=perf)
        except PlanValidationError as e:
            task.status = AgentTaskStatus.FAILED
            task.errors.append(f"{e.code}: {e.message}")
            self._publish(task)
            return self._result(
                task,
                ok=False,
                message="I could not work out a reliable plan for that. Can you say it more simply?",
                plan_rejected=e.code,
            )
        except Exception as e:
            timed_out = self._is_timeout(e)
            task.status = AgentTaskStatus.FAILED
            task.errors.append("PLANNER_TIMEOUT" if timed_out else str(e))
            self._publish(task)
            return self._result(
                task,
                ok=False,
                message=self._planner_failure_message(e),
                planner_error="PLANNER_TIMEOUT" if timed_out else str(e)[:200],
                planner_timeout=timed_out,
            )

        task.steps = steps
        task.status = AgentTaskStatus.RUNNING
        self._publish(task)
        print(f"[Phase6] plan accepted: {len(steps)} steps, planner_calls={task.planner_calls}")
        for step in steps:
            print(
                f"[Phase6]   {step.order + 1}. {step.description} "
                f"[{step.preferred_tool or 'reason'}] {step.tool_arguments or ''}"
            )
        return self._run(task, perf=perf)

    def resume(self, *, perf: Optional[Any] = None) -> dict[str, Any]:
        task = self.active
        if task is None:
            return {"ok": False, "message": "There is no task to continue.", "no_active_task": True}
        if task.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.CANCELLED}:
            return {"ok": True, "message": "That task is already finished.", "task_id": task.id}
        self._paused = False
        self._cancelled = False
        task.status = AgentTaskStatus.RUNNING
        self._publish(task)
        return self._run(task, perf=perf)

    def pause(self) -> dict[str, Any]:
        task = self.active
        self._paused = True
        if task is None:
            return {"ok": True, "message": "Nothing is running."}
        if task.status not in {AgentTaskStatus.COMPLETED, AgentTaskStatus.CANCELLED}:
            task.status = AgentTaskStatus.PAUSED
            task.touch()
            self._publish(task)
        return {
            "ok": True,
            "message": "Paused.",
            "task_id": task.id,
            "task_status": task.status.value,
        }

    def cancel(self) -> dict[str, Any]:
        task = self.active
        self._cancelled = True
        self._paused = False
        if task is None:
            return {"ok": True, "message": "Nothing to cancel."}
        for step in task.steps:
            if not step.is_done:
                step.status = AgentStepStatus.CANCELLED
        task.status = AgentTaskStatus.CANCELLED
        task.completed_at = task.updated_at
        task.touch()
        self._publish(task)
        return {
            "ok": True,
            "message": "Cancelled.",
            "task_id": task.id,
            "task_status": task.status.value,
        }

    def skip(self) -> dict[str, Any]:
        task = self.active
        if task is None:
            return {"ok": False, "message": "There is no task running."}
        current = task.get_step(task.current_step_id or "")
        # Skip means "don't do the next thing", so a finished current step is not the target.
        step = current if current is not None and not current.is_done else task.next_step()
        if step is None:
            return {"ok": False, "message": "There is no step to skip."}
        step.status = AgentStepStatus.SKIPPED
        task.touch()
        self._publish(task)
        return {
            "ok": True,
            "message": f"Skipping {step.description.lower()}.",
            "task_id": task.id,
            "task_status": task.status.value,
            "skipped_step": step.id,
        }

    def modify(self, instruction: str) -> dict[str, Any]:
        """User correction mid-task. Update context, invalidate dependents, keep the goal."""
        task = self.active
        if task is None:
            return {"ok": False, "message": "There is no task running.", "no_active_task": True}

        resolution = resolve_reference(instruction, task.context)
        task.context.user_corrections.append(instruction[:120])
        task.touch()
        if not resolution.resolved:
            return {
                "ok": False,
                "message": "I am not sure which one you mean.",
                "task_id": task.id,
                "reference_resolved": False,
                "reason": resolution.reason,
            }

        invalidated = self._invalidate_dependents(task, resolution.kind)
        self._publish(task)
        return {
            "ok": True,
            "message": f"Using {resolution.label} instead.",
            "task_id": task.id,
            "reference_resolved": True,
            "reference_kind": resolution.kind,
            "selected": resolution.value,
            "invalidated_steps": invalidated,
            "restarted": False,
        }

    # --- execution ---------------------------------------------------------

    def _run(self, task: AgentTask, *, perf: Optional[Any] = None) -> dict[str, Any]:
        executed = 0
        # The plan's own steps plus an allowance for recovery, recomputed because a replan
        # can lengthen the plan. A flat cap spends the plan's budget on retries and stops a
        # task mid-way: five steps and a five-rung ladder is nine attempts, not five.
        while executed <= len(task.steps) + self.max_steps:
            if self._cancelled:
                return self._result(task, ok=False, message="Cancelled.")
            if self._paused:
                task.status = AgentTaskStatus.PAUSED
                self._publish(task)
                return self._result(task, ok=True, message="Paused.")
            if self.emergency is not None:
                try:
                    self.emergency.check()
                except RuntimeError as e:
                    task.status = AgentTaskStatus.PAUSED
                    self._publish(task)
                    return self._result(task, ok=False, message=str(e))

            step = task.next_step()
            if step is None:
                return self._complete(task, perf=perf)

            task.current_step_id = step.id
            self._publish(task)
            self._say_progress(step)
            executed += 1

            if step.preferred_tool is None:
                outcome_text = self._reason_step(task, step, perf=perf)
                from src.agent.phase6.compare import is_comparison_goal, is_comparison_step, is_step_label

                compare = is_comparison_step(step.description, task.original_request or task.normalized_goal)
                if compare and not task.context.facts.get("comparison_result_created"):
                    step.status = AgentStepStatus.FAILED
                    step.failure_code = FailureCode.MISSING_REQUIRED_TASK_OUTPUT
                    step.error = "MISSING_REQUIRED_TASK_OUTPUT"
                    step.result = outcome_text
                    task.status = AgentTaskStatus.FAILED
                    task.result = outcome_text or "I could not finish the comparison from the page and resume I read."
                    self._publish(task)
                    print("[GoalCheck] FAILED missing=comparison_result MISSING_REQUIRED_TASK_OUTPUT")
                    self._remember_recent(task, {"comparison_result_created": False})
                    return self._result(task, ok=False, message=task.result)
                if is_step_label(outcome_text or "") and is_comparison_goal(task.original_request or ""):
                    step.status = AgentStepStatus.FAILED
                    step.failure_code = FailureCode.MISSING_REQUIRED_TASK_OUTPUT
                    step.error = "MISSING_REQUIRED_TASK_OUTPUT"
                    task.status = AgentTaskStatus.FAILED
                    task.result = "I could not finish the comparison from the page and resume I read."
                    self._publish(task)
                    return self._result(task, ok=False, message=task.result)
                step.status = AgentStepStatus.COMPLETED
                step.result = outcome_text
                task.touch()
                continue

            outcome = self.step_executor.run_step(
                step,
                task.context,
                task_id=task.id,
                purpose=task.original_request,
                allow_vision=step.preferred_tool in _VISION_TOOLS,
            )
            if perf is not None:
                perf.add(f"Tool {step.preferred_tool}", outcome.duration_ms)

            if outcome.requires_approval:
                task.status = AgentTaskStatus.WAITING_FOR_APPROVAL
                self._publish(task)
                return self._result(
                    task,
                    ok=True,
                    message=outcome.error or "That step needs your approval.",
                    waiting_for_approval=True,
                    approval=outcome.approval,
                )

            if outcome.ok:
                task.touch()
                continue

            handled = self._recover(task, step, outcome, perf=perf)
            if handled is not None:
                return handled

        if task.next_step() is None:
            # Running out of budget on the attempt that finished the work is not a failure.
            return self._complete(task, perf=perf)

        task.status = AgentTaskStatus.FAILED
        task.errors.append(f"Stopped after {executed} step attempts.")
        self._publish(task)
        return self._result(task, ok=False, message="That took more steps than expected, so I stopped.")

    def _recover(
        self,
        task: AgentTask,
        step: AgentStep,
        outcome,
        *,
        perf: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        replans_left = max(0, task.max_replans - task.replan_count)
        tried = self._tried.setdefault(step.id, set())
        if step.preferred_tool:
            tried.add(step.preferred_tool)
        action = self.recovery.decide(
            step,
            outcome.failure_code,
            task.context,
            replans_left=replans_left,
            tried_tools=tried,
            goal=task.normalized_goal or task.original_request,
        )
        # The message matters more than the code: UNKNOWN means nothing on its own, and
        # without it every diagnosis of a failed step starts by guessing.
        detail = str(outcome.error or "").strip().replace("\n", " ")[:200]
        print(
            f"[Phase6] step '{step.description}' failed ({action.failure_code.value}) "
            f"-> {action.action} ({action.reason})"
            + (f"\n[Phase6]   because: {detail}" if detail else "")
        )
        if perf is not None:
            perf.set("last_failure_code", action.failure_code.value)
            perf.set("last_recovery_action", action.action)

        if action.action in {WAIT_FOR_USER, ASK_USER}:
            task.status = (
                AgentTaskStatus.WAITING_FOR_USER
                if action.action == WAIT_FOR_USER
                else AgentTaskStatus.BLOCKED
            )
            step.status = AgentStepStatus.BLOCKED
            self._publish(task)
            return self._result(
                task,
                ok=True,
                message=action.user_message,
                waiting_for_user=True,
                failure_code=action.failure_code.value,
            )

        if action.action == FAIL:
            task.status = AgentTaskStatus.FAILED
            task.errors.append(outcome.error)
            self._publish(task)
            return self._result(
                task,
                ok=False,
                message=f"I could not {step.description.lower()}.",
                failure_code=action.failure_code.value,
            )

        if action.action == REFRESH_AND_RETRY and action.refresh_tool:
            try:
                refreshed = self.registry.execute(
                    action.refresh_tool, {}, task_id=task.id, purpose="refresh state"
                )
                if refreshed.success:
                    update_context(task.context, action.refresh_tool, {}, refreshed.data)
            except Exception:
                pass
            # Drop only what nothing deliberately chose, so freshly read state can supply it.
            # A concrete value is the step's target: clearing "ChatGPT" turns "switch to
            # ChatGPT" into "switch to whatever is in front of me", which then succeeds on
            # the tab already open and reports the step done.
            for field in DEFERRED_ARGUMENTS.get(step.preferred_tool or "", set()):
                if is_placeholder(step.tool_arguments.get(field)):
                    step.tool_arguments.pop(field, None)
            step.status = AgentStepStatus.PENDING
            step.error = None
            return None

        if action.action == RELAX_AND_RETRY:
            step.tool_arguments = relax_arguments(
                step.preferred_tool or "",
                step.tool_arguments,
                task.normalized_goal or task.original_request,
            )
            step.status = AgentStepStatus.PENDING
            step.error = None
            return None

        if action.action == RETRY:
            step.status = AgentStepStatus.PENDING
            step.error = None
            return None

        if action.action == ALTERNATIVE_TOOL and action.alternative_tool:
            tried.add(action.alternative_tool)
            step.tool_arguments = retarget_arguments(
                self.registry, action.alternative_tool, step.tool_arguments
            )
            step.preferred_tool = action.alternative_tool
            step.status = AgentStepStatus.PENDING
            step.attempt_count = 0
            step.error = None
            return None

        if action.action == REPLAN:
            return self._replan(task, step, outcome, perf=perf)

        task.status = AgentTaskStatus.FAILED
        self._publish(task)
        return self._result(task, ok=False, message="I hit a problem I could not work around.")

    def _replan(
        self,
        task: AgentTask,
        step: AgentStep,
        outcome,
        *,
        perf: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        task.replan_count += 1
        note = f"Step '{step.description}' failed: {outcome.error} ({outcome.failure_code.value})"
        selection = self.tool_resolver.select(
            self.registry,
            task.normalized_goal or task.original_request,
            browser_open=self._browser_open(),
            perf=perf,
        )
        try:
            new_steps = self.planner.plan(
                task,
                tool_schemas=selection.schemas,
                replan=True,
                failure_note=note,
                perf=perf,
            )
        except Exception as e:
            # The bottom rung of the ladder is asking, not giving up silently: the task
            # state is intact and the user can redirect it.
            task.status = AgentTaskStatus.BLOCKED
            task.errors.append(f"replan failed: {e}")
            step.status = AgentStepStatus.BLOCKED
            self._publish(task)
            return self._result(
                task,
                ok=True,
                message=(
                    f"I could not work out another way to {step.description.lower()}. "
                    "How would you like me to do it?"
                ),
                waiting_for_user=True,
                failure_code=outcome.failure_code.value,
            )

        # The failed step stays in the record. Dropping it would let the final report
        # describe a task where nothing went wrong, which is how a replanned-around
        # failure turns into a false claim of success.
        step.status = AgentStepStatus.FAILED
        history = [s for s in task.steps if s.is_done or s.status == AgentStepStatus.FAILED]
        offset = len(history)
        for i, new in enumerate(new_steps):
            new.order = offset + i
        task.steps = history + new_steps
        task.status = AgentTaskStatus.RUNNING
        self._publish(task)
        print(f"[Phase6] replanned: {len(new_steps)} remaining steps (replan_count={task.replan_count})")
        return None

    def _reason_step(self, task: AgentTask, step: AgentStep, *, perf: Optional[Any] = None) -> str:
        """A tool-free step: answer from compact task state, no tool schemas."""
        from src.agent.phase6.compare import is_comparison_step

        if is_comparison_step(step.description, task.original_request or task.normalized_goal):
            return self._compare_step(task, step, perf=perf)
        payload = {
            "goal": task.normalized_goal or task.original_request,
            "step": step.description,
            "done": task.completed_descriptions()[-6:],
            "not_done": task.abandoned_descriptions()[:3],
            "state": task.context.compact(),
            "facts": {k: v for k, v in task.context.facts.items() if k not in {"page_text", "resume_text", "comparison_result"}},
        }
        messages = [
            {"role": "system", "content": REASONING_SYSTEM},
            {"role": "user", "content": json.dumps(payload, default=str)[:4000]},
        ]
        t0 = time.perf_counter()
        try:
            response = self.provider.chat(messages, tools=None)
            text = (response.content or "").strip()
        except Exception as e:
            text = ""
            print(f"[Phase6] reasoning step failed: {e}")
        if perf is not None:
            perf.add("Reasoning step", (time.perf_counter() - t0) * 1000)
        return text

    def _compare_step(self, task: AgentTask, step: AgentStep, *, perf: Optional[Any] = None) -> str:
        from src.agent.phase6.compare import JobResumeComparisonEngine, apply_comparison_to_task

        t0 = time.perf_counter()
        facts = task.context.facts
        page_text = str(facts.get("page_text") or facts.get("page_excerpt") or "")
        resume_text = str(facts.get("resume_text") or facts.get("document_excerpt") or "")
        if len(page_text) < 80:
            page_text = self._refresh_page_text(task) or page_text
        if not page_text and not resume_text:
            print("[Comparison] missing both sources")
            if perf is not None:
                perf.add("Compare step", (time.perf_counter() - t0) * 1000)
            return ""
        engine = JobResumeComparisonEngine()
        result = engine.compare(
            page_text=page_text,
            resume_text=resume_text,
            job_title=str(facts.get("current_page") or task.context.current_page or ""),
            job_url=str(task.context.current_url or facts.get("current_url") or ""),
            resume_path=str(facts.get("selected_resume_path") or task.context.selected_file or ""),
            task_id=task.id,
            hwnd=int(facts.get("chrome_hwnd") or 0),
        )
        detailed = apply_comparison_to_task(task, result)
        if result.aborted:
            facts["comparison_result_created"] = False
            facts["gaps_created"] = False
            print(f"[Comparison] comparison_aborted_reason={result.abort_reason}")
        if perf is not None:
            perf.add("Compare step", (time.perf_counter() - t0) * 1000)
        print(
            f"[Comparison] engine items={len(result.items)} covered={len(result.covered_requirements)} "
            f"partial={len(result.partial_requirements)} missing={len(result.missing_requirements)}"
        )
        return detailed

    def _refresh_page_text(self, task: AgentTask) -> str:
        getter = getattr(self.verifier, "_browser_getter", None)
        session = getter() if callable(getter) else None
        adapter = None
        if session is not None:
            adapter = getattr(session, "existing_adapter", None) or getattr(session, "_existing_adapter", None)
            agent = getattr(session, "agent", None)
            if adapter is None and agent is not None:
                adapter = getattr(agent, "existing_adapter", None)
        if adapter is None or not hasattr(adapter, "get_page_text"):
            return ""
        try:
            page = adapter.get_page_text(max_chars=8000)
        except Exception as e:
            print(f"[PageModel] refresh failed: {e}")
            return ""
        text = str((page or {}).get("text") or "")
        if text:
            task.context.facts["page_text"] = text[:8000]
            task.context.facts["page_excerpt"] = text[:8000]
            task.context.facts["job_page_read"] = True
            if page.get("url"):
                task.context.current_url = str(page["url"])
                task.context.facts["current_url"] = task.context.current_url
            title = str(page.get("title") or page.get("active_tab_title") or "")
            if title:
                task.context.current_page = title
                task.context.facts["current_page"] = title
        return text

    # --- completion --------------------------------------------------------

    def _complete(self, task: AgentTask, *, perf: Optional[Any] = None) -> dict[str, Any]:
        unverified = [
            s
            for s in task.steps
            if s.status == AgentStepStatus.COMPLETED
            and s.preferred_tool
            and s.verification is not None
            and not s.verification.verified
        ]
        if unverified:
            task.status = AgentTaskStatus.FAILED
            self._publish(task)
            return self._result(
                task,
                ok=False,
                message=f"I could not confirm {unverified[0].description.lower()}.",
            )

        # Running every planned step is not the same as reaching the goal. If an action
        # never happened and nothing later did it instead, the task stops short and the
        # user gets the choice, rather than a clean COMPLETED over missing work.
        abandoned = task.abandoned_descriptions()
        self._verify_artifacts_at_completion(task)
        message = self._final_message(task)
        ok_outcomes, checks = self._goal_outcomes(task)
        print(
            f"[GoalCheck] required_outcomes={checks} satisfied={ok_outcomes} "
            f"task_id={task.id} selected_resume={task.context.facts.get('selected_resume_path') or '-'} "
            f"resume_hwnd={task.context.facts.get('resume_hwnd') or '-'} "
            f"resume_pid={task.context.facts.get('resume_pid') or '-'} "
            f"resume_open_verified={task.context.facts.get('resume_open_verified')} "
            f"resume_exists_after_focus_change={task.context.facts.get('resume_exists_after_switch_to_chrome')} "
            f"chrome_hwnd={task.context.facts.get('chrome_hwnd') or '-'} "
            f"chrome_focus_verified={task.context.facts.get('chrome_focus_verified')} "
            f"current_page={task.context.facts.get('site_name') or task.context.current_page or '-'} "
            f"final_response={message!r} task_status_pending=COMPLETED"
        )
        if not ok_outcomes and not abandoned:
            missing = [k for k, v in checks.items() if not v]
            task.status = AgentTaskStatus.FAILED
            task.result = message or "I could not finish every part of that request."
            self._publish(task)
            print(f"[GoalCheck] FAILED missing={missing}")
            self._remember_recent(task, checks)
            return self._result(task, ok=False, message=task.result, required_outcomes=checks)
        if abandoned:
            shortfall = f"I could not {abandoned[0][0].lower() + abandoned[0][1:]}."
            # Without a reasoning step there is no narrative to carry the bad news, and
            # "Done." over unfinished work is the exact failure this branch exists for.
            message = shortfall if message == _NOTHING_TO_REPORT else f"{message} {shortfall}"
            task.status = AgentTaskStatus.BLOCKED
            task.result = message
            self._publish(task)
            print(f"[Phase6] task stopped short: could not {abandoned[0]!r}")
            return self._result(
                task,
                ok=True,
                message=message,
                waiting_for_user=True,
            )

        task.status = AgentTaskStatus.COMPLETED
        task.completed_at = task.updated_at
        task.result = message
        self._remember_recent(task, checks)
        self._publish(task)
        print(
            f"[Phase6] task complete: planner_calls={task.planner_calls} "
            f"replans={task.replan_count} steps={len(task.steps)}"
        )
        return self._result(task, ok=True, message=message)

    def _verify_artifacts_at_completion(self, task: AgentTask) -> None:
        from src.agent.phase6.artifacts import (
            OPEN_STATES,
            PERSIST_AFTER_TASK,
            registry,
            verify_after_task,
        )

        persist = [a for a in registry() if a.persistence == PERSIST_AFTER_TASK]
        if not persist:
            return
        computer = getattr(self.verifier, "_computer", None)
        delay = max(0.0, float(self.artifact_settle_s))
        last_state = ""
        ok = True
        for art in persist:
            report = verify_after_task(computer, art, delay_s=delay, when="finalization")
            delay = 0.0
            last_state = report.state
            if report.state not in OPEN_STATES:
                ok = False
        task.context.facts["persistence_state"] = last_state
        task.context.facts["resume_exists_at_finalization"] = ok
        task.context.facts["artifact_state_after_delay"] = last_state
        print(
            f"[Artifact] exists_at_finalization={ok} persistence_state={last_state or '-'} "
            f"task_id={task.id}"
        )

    def _remember_recent(self, task: AgentTask, checks: dict) -> None:
        try:
            from src.agent.phase6.recent import remember_recent_task

            remember_recent_task(task, claimed=checks)
        except Exception as e:
            print(f"[RecentTask] remember failed: {e}")

    def _final_message(self, task: AgentTask) -> str:
        from src.agent.phase6.results import synthesize_task_result

        synthesized = synthesize_task_result(task)
        if synthesized:
            return synthesized
        for step in sorted(task.steps, key=lambda s: s.order, reverse=True):
            if step.preferred_tool is None and isinstance(step.result, str) and step.result:
                return step.result
        return _NOTHING_TO_REPORT

    @staticmethod
    def _goal_outcomes(task: AgentTask) -> tuple[bool, dict]:
        from src.agent.phase6.results import outcomes_satisfied

        return outcomes_satisfied(task)

    # --- helpers -----------------------------------------------------------

    def _invalidate_dependents(self, task: AgentTask, kind: str) -> list[str]:
        """A changed selection invalidates later steps, not completed history."""
        invalidated: list[str] = []
        for step in sorted(task.steps, key=lambda s: s.order):
            if step.is_done:
                continue
            if not self._step_uses(step, kind):
                continue
            step.status = AgentStepStatus.PENDING
            step.attempt_count = 0
            step.error = None
            step.verification = None
            invalidated.append(step.id)
        return invalidated

    @staticmethod
    def _step_uses(step: AgentStep, kind: str) -> bool:
        tool = step.preferred_tool or ""
        if kind == "file":
            return tool.startswith("files.") or tool in {"computer.open_file"} or tool == ""
        return tool.startswith("browser.") or tool == ""

    def _browser_open(self) -> bool:
        getter = getattr(self.verifier, "_browser_getter", None)
        session = getter() if callable(getter) else None
        return getattr(getattr(session, "agent", None), "_page", None) is not None

    def _say_progress(self, step: AgentStep) -> None:
        if callable(self.on_status):
            try:
                self.on_status(step.description)
            except Exception:
                pass

    def _publish(self, task: AgentTask) -> None:
        if callable(self.on_progress):
            try:
                self.on_progress(task.progress())
            except Exception:
                pass

    @staticmethod
    def _is_timeout(error: Exception) -> bool:
        return "timeout" in str(error).lower() or "timed out" in str(error).lower()

    @classmethod
    def _planner_failure_message(cls, error: Exception) -> str:
        if cls._is_timeout(error):
            return "Planning that took too long, so I stopped. Try breaking it into two steps."
        return "I could not plan that one."

    def _result(self, task: AgentTask, *, ok: bool, message: str, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": ok,
            "message": message,
            "task_id": task.id,
            "task_status": task.status.value,
            "phase6": True,
            "planner_calls": task.planner_calls,
            "planner_calls_per_task": task.planner_calls,
            "replan_count": task.replan_count,
            "steps_total": len(task.steps),
            "steps_completed": len(task.completed_descriptions()),
            "task_context_tokens": max(1, len(json.dumps(task.compact_state(), default=str)) // 4),
            "progress": task.progress(),
        }
        spoken = str(task.context.facts.get("comparison_spoken") or "")
        if spoken:
            payload.setdefault("spoken", spoken)
        payload.update(extra)
        return payload
