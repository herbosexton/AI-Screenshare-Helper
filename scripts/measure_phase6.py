"""Live Phase 6 measurements against the real local model. Not part of the pytest suite.

Runs the real routing stack (LocalIntentResolver -> UtteranceClassifier ->
PlannerAdmissionController -> AgentTaskRunner) with fake, side-effect-free tools so the
planner, tool filtering, execution, verification and telemetry are all exercised for real.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.agent.audit import AuditLog
from src.agent.emergency import EmergencyStop
from src.agent.local_intent import reset_nlu_context
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine
from src.agent.providers.ollama import LocalOllamaProvider
from src.agent.task_store import TaskStore
from src.agent.tools.base import ToolRegistry


FAST_UTTERANCES = [
    "That's fine.",
    "I see.",
    "Okay then.",
    "Maybe later.",
    "That's an added task.",
]
COMPLEX_GOALS = [
    "Let's find my latest resume and compare it to the job page I have open.",
    "Find my most recent PDF and open it, then switch back to Chrome.",
    "Open Chrome, go to the job site, read the page and then find my newest resume.",
]
QUESTION = "Why is the sky blue?"


def _build(tmp: Path):
    import tests.test_phase6_orchestrator as harness

    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    agent_cfg = config.get("agent") or {}
    provider = LocalOllamaProvider(
        base_url=agent_cfg.get("ollama_base_url", "http://127.0.0.1:11434/v1"),
        model=agent_cfg.get("model", "qwen2.5:7b"),
        vision_model=agent_cfg.get("vision_model", "llava"),
        timeout_s=float(agent_cfg.get("timeout_s", 25)),
        auto_start=True,
    )
    permissions = PermissionEngine(
        autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True
    )
    registry = ToolRegistry(permissions, AuditLog(), EmergencyStop())
    env = harness.env.__wrapped__(registry)
    reset_nlu_context()
    orch = AgentOrchestrator(
        provider,
        registry,
        TaskStore(tmp / "tasks.db"),
        permissions,
        emergency_stop=EmergencyStop(),
    )
    orch.browser = env["browser"]
    orch.computer = env["computer"]
    env["browser"].agent.url = "https://jobs.example.com/1"
    return orch, env, provider


def _marks(result) -> dict:
    return ((result.get("perf") or {}).get("marks")) or {}


ALL_TOOL_NAMES = [
    # The real registry as wired by src/agent/factory.py.
    "agent.list_tools", "agent.get_status", "speech.speak",
    "computer.get_screenshot", "computer.get_clipboard", "computer.set_clipboard",
    "computer.get_screen_state", "computer.verify_window", "computer.verify_element",
    "computer.list_windows", "computer.get_active_window", "computer.find_window",
    "computer.focus_window", "computer.minimize_window", "computer.maximize_window",
    "computer.close_window", "computer.open_application", "computer.open_file",
    "computer.get_running_applications", "computer.move_mouse", "computer.click",
    "computer.double_click", "computer.right_click", "computer.drag", "computer.scroll",
    "computer.type_text", "computer.press_key", "computer.hotkey",
    "computer.get_screen_size", "computer.wait",
    "files.list", "files.find_by_name", "files.find_recent", "files.search",
    "files.get_metadata", "files.read", "files.extract_text", "files.open", "files.copy",
    "files.move", "files.rename", "files.create_directory", "files.index", "files.delete",
    "files.overwrite",
    "browser.status", "browser.snapshot", "browser.get_text", "browser.screenshot",
    "browser.open", "browser.goto", "browser.navigate", "browser.back", "browser.forward",
    "browser.reload", "browser.listTabs", "browser.getActiveTab", "browser.switchTab",
    "browser.findTab", "browser.getCurrentUrl", "browser.getTitle", "browser.getPageState",
    "browser.getVisibleText", "browser.getLinks", "browser.getForms", "browser.findElement",
    "browser.click", "browser.type", "browser.fill", "browser.clear", "browser.select",
    "browser.check", "browser.uncheck", "browser.scroll", "browser.scrollToElement",
    "browser.uploadFile", "browser.press", "browser.wait", "browser.waitForElement",
    "browser.waitForText", "browser.waitForNavigation", "browser.getDialogs",
    "browser.dismissDialog", "browser.acceptDialog", "browser.new_tab", "browser.close_tab",
    "browser.close",
]


def _projected_filtering() -> dict:
    """Category reduction against the full production tool set."""
    from src.agent.phase6.tool_catalog import ToolCategoryResolver, category_for

    from src.agent.phase6.tool_catalog import _PLANNER_CORE

    resolver = ToolCategoryResolver()
    out = {"tools_total": len(ALL_TOOL_NAMES)}
    for label, goal in {
        "file_goal": "Find my newest resume",
        "browser_goal": "Find the Apply button on this page and click it",
        "screen_goal": "Look at my screen and tell me which dialog is showing",
        "cross_tool_goal": "Find my newest resume and compare it with the job page I have open",
    }.items():
        cats = set(resolver.resolve_categories(goal))
        lowered = goal.lower()
        kept = [
            n
            for n in ALL_TOOL_NAMES
            if category_for(n) in cats
            and (
                n in _PLANNER_CORE.get(category_for(n), set())
                or n.split(".")[-1].lower() in lowered
            )
        ]
        out[label] = {"categories": sorted(cats), "tools_after_filter": len(kept)}
    return out


def _control_run(orch, env) -> dict:
    """Pause / resume / skip / cancel / correction against a real planner-produced plan."""
    runner = orch.agent_runner
    original = runner.step_executor.run_step
    out: dict = {}

    original_reason = runner._reason_step

    def pause_after_each(step, ctx, **kw):
        result = original(step, ctx, **kw)
        runner.pause()
        return result

    def pause_after_reasoning(task, step, **kw):
        result = original_reason(task, step, **kw)
        runner.pause()
        return result

    runner.step_executor.run_step = pause_after_each
    runner._reason_step = pause_after_reasoning
    goal = "Let's find my latest resume and open it next to the job page I have open."
    for attempt in range(3):
        started = orch.handle_user_message(goal)
        if started.get("steps_total"):
            break
        print(f"  control plan attempt {attempt + 1} rejected: {started.get('plan_rejected')}")
    out["plan_attempts"] = attempt + 1
    out["plan_steps"] = started.get("steps_total")
    out["paused_status"] = started.get("task_status")
    out["steps_completed_at_pause"] = started.get("steps_completed")
    out["tools_called_at_pause"] = sum(len(t.calls) for t in env["tools"].values())

    # Advance one step at a time so each control verb is observed in isolation.
    while runner.active and not runner.active.context.candidate_files:
        if all(s.is_done for s in runner.active.steps):
            break
        orch.handle_user_message("Continue.")
    out["resumed_steps_completed"] = len(runner.active.completed_descriptions())
    out["candidate_files"] = len(runner.active.context.candidate_files)

    correction = orch.handle_user_message("Actually use the second newest one.")
    out["correction_resolved"] = correction.get("reference_resolved")
    out["correction_kind"] = correction.get("reference_kind")
    out["selected_after_correction"] = runner.active.context.selected_file if runner.active else ""
    out["correction_restarted"] = correction.get("restarted")

    # Skip and cancel need a task with work left, so start a fresh one.
    for tool in env["tools"].values():
        tool.calls.clear()
    fresh = orch.handle_user_message(
        "Find my most recent PDF and open it, then switch back to Chrome."
    )
    out["fresh_plan_steps"] = fresh.get("steps_total")
    out["skip"] = orch.handle_user_message("Skip this.").get("skipped_step") is not None
    runner.pause()
    tools_before_cancel = sum(len(t.calls) for t in env["tools"].values())
    out["cancel_status"] = orch.handle_user_message("Cancel.").get("task_status")
    out["cancelled_steps"] = sum(
        1 for s in (runner.active.steps if runner.active else []) if s.status.value == "CANCELLED"
    )
    out["tools_called_after_cancel"] = (
        sum(len(t.calls) for t in env["tools"].values()) - tools_before_cancel
    )
    runner.step_executor.run_step = original
    runner._reason_step = original_reason
    return out


def main() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-p6-"))
    orch, env, provider = _build(tmp)
    report: dict = {"model": provider.model, "results": {}}

    print("== warming the model ==")
    t0 = time.perf_counter()
    provider.chat([{"role": "user", "content": "ping"}], tools=None)
    report["results"]["model_warm_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    print("== fast / local routing (planner must be 0) ==")
    fast_ms: list[float] = []
    classify_ms: list[float] = []
    fast_planner = 0
    for text in FAST_UTTERANCES:
        t0 = time.perf_counter()
        r = orch.handle_user_message(text)
        fast_ms.append((time.perf_counter() - t0) * 1000)
        classify_ms.append(_marks(r).get("Classifier", 0.0))
        if r.get("planner_admitted"):
            fast_planner += 1
        print(f"  {text!r:34} {fast_ms[-1]:7.1f} ms  admitted={r.get('planner_admitted')}")
    report["results"]["fast_route_ms_avg"] = round(statistics.mean(fast_ms), 1)
    report["results"]["fast_route_ms_max"] = round(max(fast_ms), 1)
    report["results"]["classifier_ms_max"] = round(max(classify_ms), 2)
    report["results"]["fast_planner_admissions"] = fast_planner

    print("== AI conversation (AgentPlanner must be 0) ==")
    t0 = time.perf_counter()
    r = orch.handle_user_message(QUESTION)
    report["results"]["ai_conversation_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    report["results"]["ai_conversation_route"] = r.get("conceptual_route")
    report["results"]["ai_conversation_phase6"] = bool(r.get("phase6"))
    print(f"  {QUESTION!r} {report['results']['ai_conversation_ms']:.0f} ms "
          f"route={r.get('conceptual_route')} phase6={bool(r.get('phase6'))}")

    print("== complex goals (Phase 6) ==")
    tasks: list[dict] = []
    for goal in COMPLEX_GOALS:
        # Reset the fake tools between goals.
        for tool in env["tools"].values():
            tool.calls.clear()
        t0 = time.perf_counter()
        r = orch.handle_user_message(goal)
        total = (time.perf_counter() - t0) * 1000
        perf = r.get("perf") or {}
        marks = _marks(r)
        entry = {
            "goal": goal,
            "ok": r.get("ok"),
            "planner_admitted": r.get("planner_admitted"),
            "admission_reason": r.get("planner_admission_reason"),
            "utterance_type": r.get("utterance_type"),
            "task_status": r.get("task_status"),
            "planner_calls_per_task": r.get("planner_calls_per_task"),
            "replan_count": r.get("replan_count"),
            "steps_total": r.get("steps_total"),
            "steps_completed": r.get("steps_completed"),
            "plan_rejected": r.get("plan_rejected"),
            "planner_llm_ms": round(marks.get("Planner LLM", 0.0), 1),
            "reasoning_ms": round(marks.get("Reasoning step", 0.0), 1),
            "tools_before_filter": perf.get("tools_before_filter"),
            "tools_after_filter": perf.get("tools_after_filter"),
            "tool_schema_tokens_before": perf.get("tool_schema_tokens_before"),
            "tool_schema_tokens_after": perf.get("tool_schema_tokens_after"),
            "planner_input_tokens": perf.get("planner_input_tokens"),
            "planner_output_tokens": perf.get("planner_output_tokens"),
            "task_context_tokens": r.get("task_context_tokens"),
            "tool_ms": {
                k.replace("Tool ", ""): round(v, 1)
                for k, v in marks.items()
                if k.startswith("Tool ")
            },
            "total_ms": round(total, 1),
            "message": (r.get("message") or "")[:160],
        }
        tasks.append(entry)
        print(json.dumps(entry, indent=2, default=str))
    report["results"]["tasks"] = tasks

    report["results"]["completed_tasks"] = len([t for t in tasks if t["task_status"] == "COMPLETED"])
    report["results"]["tool_filtering_full_registry"] = _projected_filtering()

    print("== pause / resume / cancel / skip / modify (live plan) ==")
    report["results"]["control"] = _control_run(orch, env)
    print(json.dumps(report["results"]["control"], indent=2))

    out = Path(__file__).resolve().parents[1] / "docs" / "PHASE_6_PROFILE.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
