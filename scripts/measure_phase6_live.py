"""Live Phase 6 end-to-end: real Chromium, real files, real local model.

`measure_phase6.py` exercises routing and telemetry with side-effect-free tools.
This script exercises the parts that only mean something against a real environment:
a real browser process with a real DOM, real files on disk, real window state, and
the real planner deciding what to do. Nothing here is mocked except the web server,
which is local so the run is deterministic and offline.

Covers Phase 6 acceptance tests 4, 11, 12, 13, 16, 17 and 19.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml

from src.agent.audit import AuditLog
from src.agent.browser.policy import UrlPolicy
from src.agent.browser.session import BrowserSession
from src.agent.browser.tools import build_browser_tools
from src.agent.computer.controller import ComputerController
from src.agent.computer.tools import build_computer_tools
from src.agent.emergency import EmergencyStop
from src.agent.files.filesystem import FileSystemService
from src.agent.files.permissions import FilePathPolicy
from src.agent.files.tools import build_file_tools
from src.agent.local_intent import reset_nlu_context
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine
from src.agent.providers.ollama import LocalOllamaProvider
from src.agent.response_boundary import sanitize_for_tts
from src.agent.task_store import TaskStore
from src.agent.tools.base import ToolRegistry

from tests.test_browser_phase5 import (
    APPLY_HTML,
    CONTINUE_HTML,
    INDEX_HTML,
    JOB_HTML,
    LOGIN_HTML,
    NEXT_HTML,
    _FixtureHandler,
)

# Clicking Apply here lands on a sign-in wall, which is the situation Phase 6 has to
# notice mid-plan rather than blunder through.
GATED_APPLY_HTML = """<!doctype html>
<html><head><title>Senior Engineer - Apply</title></head>
<body>
<h1>Senior Engineer</h1>
<p>Apply for this role.</p>
<button id="apply" type="button" onclick="location.href='/login.html'">Apply</button>
</body></html>
"""


def _serve(root: Path) -> tuple[str, ThreadingHTTPServer, threading.Thread]:
    root.mkdir(parents=True, exist_ok=True)
    pages = {
        "index.html": INDEX_HTML,
        "next.html": NEXT_HTML,
        "job.html": JOB_HTML,
        "apply.html": APPLY_HTML,
        "continue.html": CONTINUE_HTML,
        "login.html": LOGIN_HTML,
        "gated.html": GATED_APPLY_HTML,
    }
    for name, body in pages.items():
        (root / name).write_text(body, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_FixtureHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return f"http://{host}:{port}", server, thread


def _documents(root: Path) -> list[Path]:
    """Three real PDFs with distinct modification times, newest last."""
    import os

    from reportlab.pdfgen import canvas

    root.mkdir(parents=True, exist_ok=True)
    bodies = {
        "resume_2023.pdf": "Herbie - resume 2023. Python, SQL.",
        "resume_2024.pdf": "Herbie - resume 2024. Python, SQL, distributed systems.",
        "resume_2026.pdf": "Herbie - resume 2026. Python, Rust, agents, browser automation.",
    }
    made: list[Path] = []
    now = time.time()
    for offset, (name, body) in enumerate(bodies.items()):
        path = root / name
        pdf = canvas.Canvas(str(path))
        pdf.drawString(72, 720, body)
        pdf.save()
        stamp = now - (len(bodies) - offset) * 3600
        os.utime(path, (stamp, stamp))
        made.append(path)
    return made


def _build(tmp: Path, docs: Path):
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    agent_cfg = config.get("agent") or {}
    provider = LocalOllamaProvider(
        base_url=agent_cfg.get("ollama_base_url", "http://127.0.0.1:11434/v1"),
        model=agent_cfg.get("model", "qwen2.5:7b"),
        vision_model=agent_cfg.get("vision_model", "llava"),
        timeout_s=float(agent_cfg.get("timeout_s", 25)),
        auto_start=True,
    )
    permissions = PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True)
    registry = ToolRegistry(permissions, AuditLog(), EmergencyStop())

    session = BrowserSession(
        policy=UrlPolicy(),
        profile_dir=tmp / "browser_profile",
        headed=False,
        persist_profile=False,
        channel="chromium",
    )
    fs = FileSystemService(FilePathPolicy(allowed_directories=[str(docs)]))
    controller = ComputerController()
    for tool in build_browser_tools(session):
        registry.register(tool)
    for tool in build_file_tools(fs):
        registry.register(tool)
    for tool in build_computer_tools(controller):
        registry.register(tool)

    reset_nlu_context()
    orch = AgentOrchestrator(
        provider,
        registry,
        TaskStore(tmp / "tasks.db"),
        permissions,
        emergency_stop=EmergencyStop(),
    )
    orch.browser = session
    orch.computer = controller
    return orch, session, provider, registry


def _task_facts(result: dict, runner) -> dict:
    task = runner.active
    return {
        "ok": result.get("ok"),
        "task_status": result.get("task_status"),
        "planner_calls_per_task": result.get("planner_calls_per_task"),
        "replan_count": result.get("replan_count"),
        "steps_total": result.get("steps_total"),
        "steps_completed": result.get("steps_completed"),
        "tools_used": [s.preferred_tool for s in (task.steps if task else []) if s.preferred_tool],
        "step_errors": [s.error for s in (task.steps if task else []) if s.error][:3],
        "message": (result.get("message") or "")[:200],
    }


# --- scenarios -------------------------------------------------------------


def test4_complex_browser(orch, session, site) -> dict:
    """One task, several real browser actions, planner does not wake per step."""
    session.goto(site + "/index.html")
    result = orch.handle_user_message(
        f"Open a new tab on {site}/job.html, then switch back to the first tab."
    )
    facts = _task_facts(result, orch.agent_runner)
    tabs = session.agent.list_tabs()
    facts["real_tabs"] = len(tabs)
    facts["active_url"] = session.agent.get_session_state().get("url")
    facts["planner_woke_per_step"] = (result.get("planner_calls_per_task") or 0) > 1
    return facts


def test11_verify_state_change(orch, session, site) -> dict:
    """Report success only after the page actually changed."""
    session.goto(site + "/index.html")
    before = session.agent.get_session_state().get("title")
    result = orch.handle_user_message(
        "Click the Go next link and then tell me which page is showing."
    )
    facts = _task_facts(result, orch.agent_runner)
    after = session.agent.get_session_state().get("title")
    task = orch.agent_runner.active
    verified = [
        {"step": s.description, "verified": bool(s.verification and s.verification.verified)}
        for s in (task.steps if task else [])
        if s.preferred_tool
    ]
    facts["title_before"] = before
    facts["title_after"] = after
    facts["page_actually_changed"] = before != after
    facts["step_verification"] = verified
    return facts


def test12_failure_recovery(orch, session, site) -> dict:
    """The element genuinely is not on the page; recovery must be bounded."""
    session.goto(site + "/index.html")
    t0 = time.perf_counter()
    result = orch.handle_user_message(
        "Click the Checkout button on this page and then read the page heading."
    )
    facts = _task_facts(result, orch.agent_runner)
    task = orch.agent_runner.active
    facts["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    facts["attempts"] = {s.description[:40]: s.attempt_count for s in (task.steps if task else [])}
    facts["bounded"] = all(s.attempt_count <= s.max_attempts for s in (task.steps if task else []))
    facts["failure_codes"] = [
        s.error for s in (task.steps if task else []) if s.error
    ][:3]
    return facts


def test13_login_interruption(orch, session, site) -> dict:
    """The flow has changed to a sign-in wall: keep the goal, hand control to the user.

    The browser starts on the login page the Apply button leads to, which is the state
    Phase 6 has to notice. Whichever element the plan reaches for, Phase 5's interrupt
    check fires first and the task must stop and ask rather than poke at the form.
    """
    session.goto(site + "/gated.html")
    session.agent.click(name="Apply")
    result = orch.handle_user_message(
        "Continue the job application on this page and fill in the email field with my address."
    )
    task = orch.agent_runner.active
    facts = _task_facts(result, orch.agent_runner)
    facts["waiting_for_user"] = bool(result.get("waiting_for_user"))
    facts["landed_on"] = session.agent.get_session_state().get("url")
    facts["goal_preserved"] = bool(task and task.original_request)
    facts["asked_user"] = "sign in" in (result.get("message") or "").lower() or "log in" in (
        result.get("message") or ""
    ).lower()
    return facts


def test16_context_compression(orch, session, site) -> dict:
    """Context sent to a replan must not grow with every completed step."""
    session.goto(site + "/index.html")
    orch.handle_user_message(
        "Read this page, open a new tab on the job page, and read that one too."
    )
    task = orch.agent_runner.active
    sizes = {
        "compact_state_chars": len(json.dumps(task.compact_state(), default=str)) if task else 0,
        "context_chars": len(json.dumps(task.context.compact(), default=str)) if task else 0,
        "steps_completed": len(task.completed_descriptions()) if task else 0,
        "recent_results_kept": len(task.context.recent_tool_results) if task else 0,
    }
    # A replan re-enters the planner with this state; that is the number that must stay small.
    sizes["replan_input_tokens_estimate"] = sizes["compact_state_chars"] // 4
    sizes["compact"] = sizes["compact_state_chars"] < 2000
    return sizes


def test17_model_output_safety() -> dict:
    """Tool-call JSON from the model must never reach speech."""
    samples = [
        '{"name":"get_status","arguments":{}}',
        '```json\n{"tool":"browser.click","arguments":{"query":"Apply"}}\n```',
        "<tool_call>browser.goto(url)</tool_call>",
        "I found three resumes.",
    ]
    out = []
    for sample in samples:
        classified = sanitize_for_tts(sample)
        out.append(
            {
                "input": sample[:48],
                "output_type": classified.output_type,
                "tts_allowed": classified.tts_allowed,
                "spoken": classified.spoken,
            }
        )
    return {
        "classified": out,
        "raw_json_spoken": sum(1 for o in out if "{" in (o["spoken"] or "")),
        "speech_allowed_for_tool_calls": sum(
            1 for o in out if o["output_type"] != "USER_RESPONSE" and o["tts_allowed"]
        ),
    }


def test19_end_to_end(orch, session, site, docs: Path) -> dict:
    """Browser work and file work inside one goal, against real state."""
    session.goto(site + "/index.html")
    t0 = time.perf_counter()
    result = orch.handle_user_message(
        f"Go to {site}/job.html, read what the job is, then find my newest resume and read it."
    )
    facts = _task_facts(result, orch.agent_runner)
    task = orch.agent_runner.active
    facts["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    facts["final_url"] = session.agent.get_session_state().get("url")
    facts["selected_file"] = task.context.selected_file if task else ""
    facts["selected_is_newest"] = bool(
        task and "resume_2026" in (task.context.selected_file or "")
    )
    facts["candidate_files"] = len(task.context.candidate_files) if task else 0
    return facts


def main() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-p6-live-"))
    docs = tmp / "docs"
    _documents(docs)
    site, server, thread = _serve(tmp / "site")
    orch, session, provider, registry = _build(tmp, docs)

    report: dict = {"model": provider.model, "site": site, "results": {}}
    scenarios = [
        ("test4_complex_browser", lambda: test4_complex_browser(orch, session, site)),
        ("test11_verify_state_change", lambda: test11_verify_state_change(orch, session, site)),
        ("test12_failure_recovery", lambda: test12_failure_recovery(orch, session, site)),
        ("test13_login_interruption", lambda: test13_login_interruption(orch, session, site)),
        ("test16_context_compression", lambda: test16_context_compression(orch, session, site)),
        ("test17_model_output_safety", test17_model_output_safety),
        ("test19_end_to_end", lambda: test19_end_to_end(orch, session, site, docs)),
    ]
    only = {
        part
        for arg in sys.argv[1:]
        if not arg.startswith("-")
        for part in arg.split(",")
        if part
    }
    if only:
        scenarios = [(n, f) for n, f in scenarios if any(o in n for o in only)]
    try:
        for name, fn in scenarios:
            print(f"\n== {name} ==")
            try:
                report["results"][name] = fn()
            except Exception as e:  # a scenario failing must not hide the others
                report["results"][name] = {"error": f"{type(e).__name__}: {e}"}
            print(json.dumps(report["results"][name], indent=2, default=str))
    finally:
        try:
            session.close()
        except Exception:
            pass
        server.shutdown()
        thread.join(timeout=2)

    out = Path(__file__).resolve().parents[1] / "docs" / "PHASE_6_LIVE_PROFILE.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
