"""Phase 6 — multi-step agent orchestrator tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest
from pydantic import BaseModel, Field

from src.agent.audit import AuditLog
from src.agent.emergency import EmergencyStop
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine, PermissionLevel
from src.agent.phase6.models import (
    AgentStep,
    AgentStepStatus,
    AgentTask,
    AgentTaskStatus,
    FailureCode,
    TaskContext,
)
from src.agent.phase6.planner import (
    AgentPlanner,
    Plan,
    PlanValidationError,
    PlanValidator,
    extract_plan_json,
    required_step_floor,
)
from src.agent.phase6.recovery import (
    ALTERNATIVE_TOOL,
    ASK_USER,
    REFRESH_AND_RETRY,
    RELAX_AND_RETRY,
    REPLAN,
    WAIT_FOR_USER,
    FailureRecoveryEngine,
    classify_failure,
)
from src.agent.phase6.binding import (
    canonical_site,
    relax_arguments,
    retarget_arguments,
    site_url,
)
from src.agent.phase6.references import resolve_reference
from src.agent.phase6.runner import AgentTaskRunner
from src.agent.phase6.step_executor import summarize_tool_data, update_context
from src.agent.phase6.tool_catalog import ToolCategoryResolver, describe_tool, is_state_changing
from src.agent.phase6.verify import ActionVerifier
from src.agent.providers.base import ProviderResponse
from src.agent.task_store import TaskStore
from src.agent.tools.base import BaseTool, ToolRegistry, ToolResult
from tests.test_agent_phase1 import MockProvider


# --- fake environment ------------------------------------------------------


class FakeBrowser:
    def __init__(self):
        self._page = object()
        self.url = ""
        self.title = ""


class FakeSession:
    def __init__(self):
        self.agent = FakeBrowser()


class FakeComputer:
    def __init__(self):
        self.active = ""
        self.windows: list[dict[str, Any]] = []

    def get_active_window(self):
        return {"title": self.active}

    def list_windows(self):
        return list(self.windows)

    def focus_window(self, handle=None, title_contains: str = ""):
        needle = (title_contains or "").lower()
        for win in self.windows:
            if handle and int(win.get("handle") or win.get("hwnd") or 0) == int(handle):
                self.active = str(win.get("title") or "")
                return {"success": True, "window": win}
            if needle and needle in str(win.get("title") or "").lower():
                self.active = str(win.get("title") or "")
                return {"success": True, "window": win}
        if needle == "chrome":
            self.active = "AI Desktop Agent Build - Google Chrome"
            return {"success": True, "window": {"handle": 197160, "title": self.active}}
        raise ValueError(f"No window matching {handle or title_contains}")


class PathParams(BaseModel):
    path: str = Field("")


class QueryParams(BaseModel):
    query: str = Field("")


class RequiredPathParams(BaseModel):
    path: str


class RequiredQueryParams(BaseModel):
    query: str


class ClickParams(BaseModel):
    name: str


class BoundedParams(BaseModel):
    max_chars: int = Field(2000, ge=200, le=8000)


class UrlParams(BaseModel):
    url: str = Field("")


class NoParams(BaseModel):
    pass


class TitleOrHandle(BaseModel):
    handle: Optional[int] = None
    title_contains: str = ""


class ScriptedTool(BaseTool):
    """A tool whose outcome the test decides."""

    permission_level = PermissionLevel.LOCAL_ACTION
    max_retries = 0

    def __init__(self, name: str, params, handler):
        self.name = name
        self.description = f"fake {name}"
        self.parameters_model = params
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def execute(self, **kwargs) -> ToolResult:
        self.calls.append(dict(kwargs))
        return self._handler(self, **kwargs)


def _ok(data):
    return lambda tool, **kw: ToolResult(success=True, data=data(**kw) if callable(data) else data)


def _fail(error, data=None):
    return lambda tool, **kw: ToolResult(success=False, error=error, data=data)


@pytest.fixture
def permissions() -> PermissionEngine:
    return PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True)


@pytest.fixture
def registry(permissions: PermissionEngine) -> ToolRegistry:
    return ToolRegistry(permissions, AuditLog(), EmergencyStop())


@pytest.fixture
def env(registry: ToolRegistry):
    """A registry wired with fake file / browser / computer tools."""
    from src.agent.phase6.artifacts import clear_registry
    from src.agent.phase6.recent import clear_recent

    clear_registry()
    clear_recent()
    browser = FakeSession()
    computer = FakeComputer()

    def _goto(**kw):
        browser.agent.url = kw.get("url", "")
        browser.agent.title = "Job Posting"
        return {"url": browser.agent.url, "title": browser.agent.title}

    def _open_app(**kw):
        computer.active = kw.get("path", "") or "Chrome"
        return {"title": computer.active}

    def _focus_chrome(**kw):
        computer.active = "AI Desktop Agent Build - Google Chrome"
        return {
            "success": True,
            "window": {"handle": 197160, "title": computer.active},
        }

    tools = {
        "files.find_recent": ScriptedTool(
            "files.find_recent",
            QueryParams,
            _ok(
                {
                    "files": [
                        {"name": "resume_2026.pdf", "path": "C:/docs/resume_2026.pdf", "modified": "2026-09-01"},
                        {"name": "resume_2024.pdf", "path": "C:/docs/resume_2024.pdf", "modified": "2024-05-02"},
                        {"name": "resume_old.pdf", "path": "C:/docs/resume_old.pdf", "modified": "2022-01-09"},
                    ]
                }
            ),
        ),
        "files.read": ScriptedTool(
            "files.read", PathParams, _ok({"text": "Python, distributed systems, 6 years."})
        ),
        # Mirrors the real tool: a path is required, so the plan must defer it.
        "files.open": ScriptedTool(
            "files.open", RequiredPathParams, lambda tool, **kw: _open_file(computer, **kw)
        ),
        "browser.goto": ScriptedTool("browser.goto", UrlParams, _ok(_goto)),
        "browser.getPageState": ScriptedTool(
            "browser.getPageState",
            NoParams,
            _ok(lambda **kw: {"url": browser.agent.url, "title": browser.agent.title, "text": "Senior Python role."}),
        ),
        "browser.click": ScriptedTool("browser.click", QueryParams, _ok({"clicked": True})),
        "computer.click": ScriptedTool("computer.click", QueryParams, _ok({"clicked": True})),
        "browser.findElement": ScriptedTool("browser.findElement", QueryParams, _ok({"found": True})),
        "browser.listTabs": ScriptedTool(
            "browser.listTabs",
            NoParams,
            _ok({"tabs": [{"title": "Job Posting", "url": "https://jobs.example.com/1"},
                          {"title": "Docs", "url": "https://docs.example.com"}]}),
        ),
        "computer.open_application": ScriptedTool(
            "computer.open_application", PathParams, _ok(_open_app)
        ),
        "computer.open_file": ScriptedTool(
            "computer.open_file", RequiredPathParams, lambda tool, **kw: _open_file(computer, **kw)
        ),
        "computer.get_active_window": ScriptedTool(
            "computer.get_active_window", NoParams, _ok(lambda **kw: {"title": computer.active})
        ),
        "computer.get_screen_state": ScriptedTool(
            "computer.get_screen_state", NoParams, _ok({"summary": "desktop"})
        ),
        "computer.list_windows": ScriptedTool("computer.list_windows", NoParams, _ok({"windows": []})),
        "computer.focus_window": ScriptedTool(
            "computer.focus_window",
            TitleOrHandle,
            _ok(_focus_chrome),
        ),
        "browser.getCurrentUrl": ScriptedTool(
            "browser.getCurrentUrl",
            NoParams,
            _ok(lambda **kw: {
                "url": browser.agent.url or "https://chatgpt.com/c/abc",
                "title": browser.agent.title or "AI Desktop Agent Build",
                "source": "EXISTING_DESKTOP",
            }),
        ),
        "agent.get_status": ScriptedTool("agent.get_status", NoParams, _ok({"summary": "idle"})),
    }
    for tool in tools.values():
        registry.register(tool)
    return {"registry": registry, "tools": tools, "browser": browser, "computer": computer}


def _open_file(computer: FakeComputer, **kw) -> ToolResult:
    path = kw.get("path", "")
    name = Path(path).name if path else ""
    computer.active = name
    computer.windows.append(
        {
            "handle": 99,
            "hwnd": 99,
            "title": name,
            "process_id": 7,
            "process_name": "msedge.exe",
            "is_minimized": False,
        }
    )
    return ToolResult(success=True, data={"path": path, "opened": True, "filename": name})


def _runner(env, provider, **kwargs) -> AgentTaskRunner:
    kwargs.setdefault("artifact_settle_s", 0.0)
    return AgentTaskRunner(
        provider,
        env["registry"],
        browser_getter=lambda: env["browser"],
        computer_getter=lambda: env["computer"],
        **kwargs,
    )


def _plan(*steps) -> ProviderResponse:
    return ProviderResponse(
        content=json.dumps({"goal": "test goal", "steps": list(steps)})
    )


def _step(sid, description, tool=None, arguments=None, depends_on=None):
    return {
        "id": sid,
        "description": description,
        "tool": tool,
        "arguments": arguments or {},
        "depends_on": depends_on or [],
    }


# --- Task / TaskStep / TaskContext models ----------------------------------


def test_task_model_statuses_and_ordering():
    task = AgentTask(original_request="do a thing", normalized_goal="do a thing")
    assert task.status == AgentTaskStatus.PENDING
    task.steps = [
        AgentStep(id="b", order=1, description="second", preferred_tool="files.read", dependencies=["a"]),
        AgentStep(id="a", order=0, description="first", preferred_tool="files.find_recent"),
    ]
    assert task.next_step().id == "a"
    task.get_step("a").status = AgentStepStatus.COMPLETED
    assert task.next_step().id == "b"
    assert task.completed_descriptions() == ["first"]


def test_task_step_dependencies_block_out_of_order_execution():
    task = AgentTask()
    task.steps = [
        AgentStep(id="a", order=0, description="first", preferred_tool="files.read"),
        AgentStep(id="b", order=1, description="second", preferred_tool="files.open", dependencies=["a"]),
    ]
    task.get_step("a").status = AgentStepStatus.FAILED
    # b depends on an unfinished a, and a is failed, so nothing is runnable.
    assert task.next_step() is None


def test_task_context_stays_compact():
    ctx = TaskContext()
    for i in range(30):
        ctx.note_tool_result("files.search", "x" * 500)
        ctx.candidate_files.append({"name": f"f{i}.pdf", "path": f"C:/f{i}.pdf"})
    ctx.selected_file = "C:/f0.pdf"
    assert len(ctx.recent_tool_results) == 6
    compact = ctx.compact()
    assert len(compact["candidate_files"]) == 5
    assert len(json.dumps(compact)) < 1200


def test_task_progress_has_no_reasoning():
    task = AgentTask(normalized_goal="compare resume", title="compare resume")
    task.steps = [AgentStep(id="a", order=0, description="Find resume", preferred_tool="files.find_recent")]
    progress = task.progress()
    assert progress["steps"][0]["description"] == "Find resume"
    assert "reasoning" not in json.dumps(progress).lower()
    assert "prompt" not in json.dumps(progress).lower()


# --- PlanValidator ---------------------------------------------------------


def test_plan_validator_accepts_wellformed_plan(env):
    validator = PlanValidator(env["registry"])
    steps = validator.validate(
        Plan(
            goal="g",
            steps=[
                _step("step_1", "Find resume", "files.find_recent", {"query": "resume"}),
                _step("step_2", "Read it", "files.read", {"path": "C:/x.pdf"}, ["step_1"]),
                _step("step_3", "Compare", None),
            ],
        )
    )
    assert [s.id for s in steps] == ["step_1", "step_2", "step_3"]
    assert steps[2].preferred_tool is None
    assert steps[1].dependencies == ["step_1"]


@pytest.mark.parametrize(
    "plan,code",
    [
        (Plan(steps=[]), "EMPTY_PLAN"),
        (Plan(steps=[_step("s1", "x", "files.nope")]), "UNKNOWN_TOOL"),
        (Plan(steps=[_step("s1", "", "files.read")]), "MISSING_DESCRIPTION"),
        (Plan(steps=[_step("s1", "x", "files.read", {"path": 5})]), "BAD_ARGUMENTS"),
        (Plan(steps=[_step("s1", "x", "files.read"), _step("s1", "y", "files.read")]), "DUPLICATE_STEP_ID"),
        (Plan(steps=[_step("s1", "x", "files.read", None, ["s9"])]), "BAD_DEPENDENCY"),
        (Plan(steps=[_step("s1", "x", "files.read", None, ["s1"])]), "CYCLE"),
        (Plan(steps=[_step("s1", "just think", None)]), "NO_ACTIONABLE_STEP"),
        (Plan(steps=[_step(f"s{i}", "x", "files.read") for i in range(30)]), "PLAN_TOO_LONG"),
    ],
)
def test_plan_validator_rejects_malformed_plans(env, plan, code):
    with pytest.raises(PlanValidationError) as e:
        PlanValidator(env["registry"]).validate(plan)
    assert e.value.code == code


def test_plan_validator_defers_arguments_the_plan_cannot_know_yet(registry):
    """A plan is written before the search runs, so files.open has no path yet."""
    registry.register(ScriptedTool("files.open", RequiredPathParams, _ok({"opened": True})))
    registry.register(ScriptedTool("files.find_recent", QueryParams, _ok({"files": []})))
    steps = PlanValidator(registry).validate(
        Plan(
            steps=[
                _step("step_1", "Find recent resumes", "files.find_recent", {"query": "resume"}),
                _step("step_2", "Open the newest one", "files.open", {}, ["step_1"]),
            ]
        )
    )
    assert steps[1].tool_arguments == {}


def test_plan_validator_drops_placeholder_arguments(registry):
    registry.register(ScriptedTool("files.open", RequiredPathParams, _ok({"opened": True})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Open it", "files.open", {"path": "<path to the resume>"})])
    )
    assert steps[0].tool_arguments == {}


def test_plan_validator_still_rejects_arguments_nothing_can_supply(registry):
    registry.register(ScriptedTool("files.search", RequiredQueryParams, _ok({"files": []})))
    with pytest.raises(PlanValidationError) as e:
        PlanValidator(registry).validate(Plan(steps=[_step("s1", "Search", "files.search", {})]))
    assert e.value.code == "BAD_ARGUMENTS"
    assert "query" in e.value.message


def test_plan_validator_tolerates_arguments_that_are_not_an_object(registry):
    registry.register(ScriptedTool("files.read", PathParams, _ok({"text": "x"})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Read the file", "files.read", "the newest resume")])
    )
    assert steps[0].tool_arguments == {}


def test_executor_binds_selected_file_at_run_time(env):
    """The path discovered by an earlier step reaches files.open without a replan."""
    runner = _runner(env, MockProvider([]))
    context = TaskContext(selected_file="C:/docs/resume_2026.pdf")
    step = AgentStep(id="s1", order=0, description="Open it", preferred_tool="files.open")
    outcome = runner.step_executor.run_step(step, context)
    assert outcome.ok
    assert step.tool_arguments == {"path": "C:/docs/resume_2026.pdf"}
    assert env["computer"].active == "resume_2026.pdf"


def test_window_verification_waits_for_a_launch_still_in_progress(env, monkeypatch):
    """A viewer that takes a beat to appear is a slow launch, not a failed one."""
    monkeypatch.setattr("src.agent.phase6.verify.WINDOW_APPEAR_TIMEOUT_S", 1.0)
    monkeypatch.setattr("src.agent.phase6.verify.WINDOW_POLL_INTERVAL_S", 0.01)
    computer = env["computer"]
    computer.active = "Jarvis Fixture Home - Chromium"
    reads = {"n": 0}

    def slow_launch():
        reads["n"] += 1
        if reads["n"] > 3:
            computer.active = "resume_2026.pdf - Acrobat"
        return {"title": computer.active}

    computer.get_active_window = slow_launch
    verifier = ActionVerifier(browser_getter=lambda: env["browser"], computer_getter=lambda: computer)
    step = AgentStep(
        description="Open the resume",
        preferred_tool="computer.open_file",
        tool_arguments={"path": "C:/docs/resume_2026.pdf"},
    )

    verification = verifier.verify(step, {"opened": True}, TaskContext())
    assert verification.verified is True
    assert verification.method == "window_state"


def test_window_verification_still_fails_when_nothing_ever_appears(env, monkeypatch):
    monkeypatch.setattr("src.agent.phase6.verify.WINDOW_APPEAR_TIMEOUT_S", 0.2)
    monkeypatch.setattr("src.agent.phase6.verify.WINDOW_POLL_INTERVAL_S", 0.01)
    env["computer"].active = "Jarvis Fixture Home - Chromium"
    verifier = ActionVerifier(browser_getter=lambda: env["browser"], computer_getter=lambda: env["computer"])
    step = AgentStep(
        description="Open the resume",
        preferred_tool="computer.open_file",
        tool_arguments={"path": "C:/docs/resume_2026.pdf"},
    )

    assert verifier.verify(step, {"opened": True}, TaskContext()).verified is False


def test_replanned_around_action_is_not_reported_as_though_it_happened(env):
    """A click that never landed must survive in the record for the final report."""
    provider = MockProvider(
        [
            _plan(_step("s1", "Click Checkout", "browser.click", {"query": "Checkout"})),
            _plan(_step("s2", "Read the page", "browser.getPageState")),
        ]
    )
    env["tools"]["browser.click"]._handler = _fail("Element not found")
    runner = _runner(env, provider)
    result = runner.start("Click the Checkout button")

    task = runner.active
    assert task.abandoned_descriptions() == ["Click Checkout"]
    assert "Click Checkout" not in task.completed_descriptions()
    assert result["planner_calls_per_task"] == 2
    assert result["task_status"] == "BLOCKED"
    assert result["message"] == "I could not click Checkout."


def test_the_action_the_user_asked_for_is_disclaimed_before_the_read_that_fed_it():
    """Only the first shortfall is spoken, so the action has to outrank the lookup."""
    task = AgentTask(original_request="Read this page and click Checkout")
    task.steps = [
        AgentStep(order=0, description="Read the page", preferred_tool="browser.getPageState"),
        AgentStep(order=1, description="Click Checkout", preferred_tool="browser.click"),
    ]
    for step in task.steps:
        step.state_changing = is_state_changing(step.preferred_tool)
        step.status = AgentStepStatus.FAILED

    assert task.abandoned_descriptions() == ["Click Checkout", "Read the page"]


def test_a_search_that_found_nothing_is_disclaimed_not_quietly_dropped():
    """The live bug: a lookup returns nothing, later steps run anyway, task says "Done."."""
    task = AgentTask(original_request="Find my newest PDF and open it")
    task.steps = [
        AgentStep(order=0, description="Find newest PDF", preferred_tool="files.find_recent"),
        AgentStep(order=1, description="Focus Chrome", preferred_tool="computer.focus_window"),
    ]
    for step in task.steps:
        step.state_changing = is_state_changing(step.preferred_tool)
    task.steps[0].status = AgentStepStatus.FAILED
    task.steps[1].status = AgentStepStatus.COMPLETED

    assert task.abandoned_descriptions() == ["Find newest PDF"]


def test_a_search_retried_by_another_route_needs_no_disclaimer():
    """A replan that finds the files a different way leaves nothing to apologise for."""
    task = AgentTask(original_request="Find my newest PDF and open it")
    task.steps = [
        AgentStep(order=0, description="Find newest PDF", preferred_tool="files.find_recent"),
        AgentStep(order=1, description="Search for PDFs", preferred_tool="files.search"),
    ]
    for step in task.steps:
        step.state_changing = is_state_changing(step.preferred_tool)
    task.steps[0].status = AgentStepStatus.FAILED
    task.steps[1].status = AgentStepStatus.COMPLETED

    assert task.abandoned_descriptions() == []


def test_plan_validator_moves_jquery_contains_selector_onto_the_name(registry):
    """`a:contains("Go next")` is jQuery, not CSS; the label is what the model meant."""
    registry.register(ScriptedTool("browser.click", ClickParams, _ok({"clicked": True})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Click it", "browser.click", {"selector": 'a:contains("Go next")'})])
    )
    assert steps[0].tool_arguments == {"name": "Go next"}


def test_plan_validator_takes_the_first_of_a_list_of_tools(registry):
    """One step runs one tool; offering a choice is a formatting slip, not a new plan."""
    registry.register(ScriptedTool("files.open", RequiredPathParams, _ok({"opened": True})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Open it", "[files.open, files.extract_text]", {"path": "a.pdf"})])
    )
    assert steps[0].preferred_tool == "files.open"


def test_plan_validator_clamps_numbers_into_the_allowed_range(registry):
    registry.register(ScriptedTool("browser.getVisibleText", BoundedParams, _ok({"text": "x"})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Read page", "browser.getVisibleText", {"max_chars": 100})])
    )
    assert steps[0].tool_arguments == {"max_chars": 200}


def test_plan_validator_renames_arguments_the_model_invented(registry):
    registry.register(ScriptedTool("browser.click", ClickParams, _ok({"clicked": True})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Click Apply", "browser.click", {"query": "Apply"})])
    )
    assert steps[0].tool_arguments == {"name": "Apply"}


def test_plan_validator_rejects_arguments_no_alias_explains(registry):
    registry.register(ScriptedTool("browser.click", ClickParams, _ok({"clicked": True})))
    with pytest.raises(PlanValidationError) as e:
        PlanValidator(registry).validate(
            Plan(steps=[_step("s1", "Click", "browser.click", {"coordinates": [4, 5]})])
        )
    assert e.value.code == "BAD_ARGUMENTS"
    assert "coordinates" in e.value.message


def test_planner_is_never_offered_coordinate_only_mouse_tools(env):
    """computer.click takes raw x/y, so a planned click lands wherever the pointer is."""
    sel = ToolCategoryResolver().select(env["registry"], "Click the Apply button on this job page")
    assert "computer.click" not in sel.names
    assert "browser.click" in sel.names


def test_plan_json_extraction_tolerates_fences():
    plan = extract_plan_json('```json\n{"goal":"g","steps":[{"id":"s1","description":"d"}]}\n```')
    assert plan.goal == "g"
    with pytest.raises(PlanValidationError):
        extract_plan_json("I will first open Chrome and then look around.")


# --- ToolCategoryResolver / dynamic filtering ------------------------------


def test_tool_filtering_selects_only_relevant_categories(env):
    resolver = ToolCategoryResolver()
    sel = resolver.select(env["registry"], "Find my newest resume")
    assert "files.find_recent" in sel.names
    assert "browser.goto" not in sel.names
    assert sel.tools_after_filter < sel.tools_before_filter
    assert sel.schema_tokens_after < sel.schema_tokens_before


def test_tool_filtering_browser_task(env):
    sel = ToolCategoryResolver().select(env["registry"], "Find the Apply button on this page and click it")
    assert "browser.click" in sel.names
    assert "files.read" not in sel.names


def test_tool_filtering_cross_tool_goal_keeps_both(env):
    sel = ToolCategoryResolver().select(
        env["registry"], "Find my newest resume and compare it with the job page I have open"
    )
    assert "files.find_recent" in sel.names
    assert "browser.getPageState" in sel.names


def test_tool_metadata_is_phase7_ready(env):
    meta = describe_tool(env["tools"]["browser.goto"]).as_dict()
    assert meta["category"] == "browser"
    assert meta["state_changing"] is True
    assert meta["external_effect"] is True
    assert "future_permission_level" in meta
    read = describe_tool(env["tools"]["files.read"]).as_dict()
    assert read["state_changing"] is False


# --- ActionVerifier --------------------------------------------------------


def test_verifier_catches_navigation_mismatch(env):
    verifier = ActionVerifier(browser_getter=lambda: env["browser"])
    env["browser"].agent.url = "https://other.example.com/"
    step = AgentStep(preferred_tool="browser.goto", tool_arguments={"url": "https://jobs.example.com/1"})
    v = verifier.verify(step, {"url": "https://other.example.com/"}, TaskContext())
    assert v.verified is False
    assert v.method == "browser_state"


def test_verifier_accepts_matching_navigation(env):
    verifier = ActionVerifier(browser_getter=lambda: env["browser"])
    env["browser"].agent.url = "https://jobs.example.com/1"
    step = AgentStep(preferred_tool="browser.goto", tool_arguments={"url": "https://jobs.example.com/1"})
    assert verifier.verify(step, {}, TaskContext()).verified is True


def test_verifier_checks_window_for_file_open(env):
    env["computer"].active = "Notepad"
    verifier = ActionVerifier(computer_getter=lambda: env["computer"])
    step = AgentStep(preferred_tool="files.open", tool_arguments={"path": "C:/docs/resume_2026.pdf"})
    assert verifier.verify(step, {}, TaskContext()).verified is False
    env["computer"].active = "resume_2026.pdf - Acrobat"
    assert verifier.verify(step, {}, TaskContext()).verified is True


def test_verifier_flags_empty_read(env):
    verifier = ActionVerifier()
    step = AgentStep(preferred_tool="files.find_recent", tool_arguments={})
    assert verifier.verify(step, {}, TaskContext()).verified is False
    assert verifier.verify(step, {"files": [1]}, TaskContext()).verified is True


def test_verifier_does_not_use_vision_by_default(env):
    calls = []

    class Screen:
        def get_state(self, **kw):
            calls.append("state")
            raise AssertionError("vision must not run for a plain click")

        def verify_window_appeared(self, needle):
            calls.append("window")
            return {"verified": True}

    verifier = ActionVerifier(browser_getter=lambda: env["browser"], screen_getter=Screen)
    step = AgentStep(preferred_tool="browser.click", tool_arguments={"query": "Apply"})
    verifier.verify(step, {"clicked": True}, TaskContext(), allow_vision=False)
    assert calls == []


# --- Failure classification / recovery -------------------------------------


@pytest.mark.parametrize(
    "error,expected",
    [
        ("Element not found: #apply", FailureCode.ELEMENT_NOT_FOUND),
        ("Request timed out", FailureCode.TOOL_TIMEOUT),
        ("No such file on disk", FailureCode.FILE_NOT_FOUND),
        ("Please sign in to continue", FailureCode.AUTHENTICATION_REQUIRED),
        ("captcha detected", FailureCode.CAPTCHA_REQUIRED),
        ("network unreachable", FailureCode.NETWORK_ERROR),
        ("Ambiguous match for 'Apply'", FailureCode.ELEMENT_AMBIGUOUS),
        ("Denied by policy", FailureCode.PERMISSION_DENIED),
        ("something odd happened", FailureCode.UNKNOWN),
    ],
)
def test_failure_classification(error, expected):
    assert classify_failure(error) == expected


def test_failure_classification_prefers_structured_code():
    assert classify_failure("boom", {"error": {"code": "CAPTCHA_REQUIRED"}}) == FailureCode.CAPTCHA_REQUIRED


def test_recovery_refreshes_state_before_retrying(env):
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(preferred_tool="browser.click", max_attempts=2)
    step.attempt_count = 1
    action = engine.decide(step, FailureCode.ELEMENT_NOT_FOUND, TaskContext(), replans_left=2)
    assert action.action == REFRESH_AND_RETRY
    assert action.refresh_tool == "browser.getPageState"


def test_recovery_falls_back_to_alternative_then_replans(env):
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(preferred_tool="files.open", max_attempts=1)
    step.attempt_count = 2
    action = engine.decide(step, FailureCode.FILE_NOT_FOUND, TaskContext(), replans_left=2)
    assert action.action == ALTERNATIVE_TOOL
    assert action.alternative_tool == "computer.open_file"

    action2 = engine.decide(
        step,
        FailureCode.FILE_NOT_FOUND,
        TaskContext(),
        replans_left=2,
        tried_tools={"files.open", "computer.open_file"},
    )
    assert action2.action == REPLAN


@pytest.mark.parametrize(
    "goal, floor",
    [
        ("Find my most recent PDF and open it, then switch back to Chrome.", 3),
        ("Let's find my latest resume and compare it to the job page I have open", 2),
        ("Open Chrome, go to google.com, open another tab, then switch back.", 4),
        # A goal naming one action needs one step, and "and" inside a name is not a clause.
        ("Open Chrome.", 1),
        ("What page am I on?", 1),
        ("Why is the sky blue?", 1),
        ("Find the Attnex and Metrc order form and open it", 2),
    ],
)
def test_the_planner_knows_how_many_actions_a_goal_is_asking_for(goal, floor):
    assert required_step_floor(goal) == floor


def test_a_plan_that_covers_one_action_of_three_is_sent_back(registry):
    """The live bug: "find it, open it, switch back" planned as a single search."""
    registry.register(ScriptedTool("files.find_recent", QueryParams, _ok({"matches": []})))
    registry.register(ScriptedTool("files.open", RequiredPathParams, _ok({"opened": True})))
    provider = MockProvider(
        [
            _plan(_step("s1", "Find newest PDF", "files.find_recent")),
            _plan(
                _step("s1", "Find newest PDF", "files.find_recent"),
                _step("s2", "Open it", "files.open"),
                _step("s3", "Back to Chrome", "files.open"),
            ),
        ]
    )
    task = AgentTask(original_request="Find my newest PDF and open it, then switch back to Chrome")
    task.normalized_goal = task.original_request
    steps = AgentPlanner(provider, registry).plan(task, tool_schemas=[])

    assert len(steps) == 3
    assert task.planner_calls == 2


def test_a_short_plan_is_still_better_than_no_plan(registry):
    """Coverage is advisory: if the model insists, run what it gave rather than failing."""
    registry.register(ScriptedTool("files.find_recent", QueryParams, _ok({"matches": []})))
    provider = MockProvider([_plan(_step("s1", "Find newest PDF", "files.find_recent"))] * 2)
    task = AgentTask(original_request="Find my newest PDF and open it, then switch back to Chrome")
    task.normalized_goal = task.original_request
    steps = AgentPlanner(provider, registry).plan(task, tool_schemas=[])

    assert len(steps) == 1


def test_a_guessed_domain_one_letter_off_a_brand_is_snapped_to_the_real_one():
    """The live incident: "chatgbt" became chatgbt.com, a live typosquat, and it loaded."""
    assert canonical_site("https://chatgbt.com/inbox/user_account") == "https://chatgpt.com"
    assert canonical_site("gogle.com") == "https://www.google.com"


def test_snapping_leaves_alone_every_address_that_is_not_a_near_miss():
    """A correct URL, a known host, and an unrelated site must all survive untouched."""
    for url in (
        "https://chatgpt.com/c/abc123",
        "https://www.google.com/search?q=cats",
        "https://example.com/page",
        "http://localhost:8000/fixture",
        "https://loldispensary.com",
    ):
        assert canonical_site(url) == url


def test_a_navigation_step_cannot_carry_a_typosquat_into_the_browser(registry):
    registry.register(ScriptedTool("browser.navigate", UrlParams, _ok({"url": "x"})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Go to chatgbt", "browser.navigate", {"url": "chatgbt.com"})])
    )
    assert steps[0].tool_arguments == {"url": "https://chatgpt.com"}


def test_a_window_argument_offered_to_a_tab_tool_lands_on_the_right_field(registry):
    """switchTab takes query; the model borrows title_contains from focus_window."""
    registry.register(ScriptedTool("browser.switchTab", QueryParams, _ok({"switched": True})))
    steps = PlanValidator(registry).validate(
        Plan(steps=[_step("s1", "Back to Google", "browser.switchTab", {"title_contains": "Google"})])
    )
    assert steps[0].tool_arguments == {"query": "Google"}


def test_a_rejected_url_is_rewritten_rather_than_retried_unchanged(env):
    """Retrying a URL the policy refused just spends the retry budget twice over."""
    assert (
        classify_failure("URL must include a scheme (http or https)")
        == FailureCode.PLAN_INVALIDATED
    )
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(preferred_tool="browser.goto", tool_arguments={"url": "google.com"})
    action = engine.decide(step, FailureCode.PLAN_INVALIDATED, TaskContext(), replans_left=2)
    assert action.action == REPLAN


def test_a_search_that_found_nothing_sheds_the_planners_own_filters_first(env):
    """The live bug: "most recent PDF" planned as name_contains="*.pdf", since_days=0."""
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(
        preferred_tool="files.find_recent",
        tool_arguments={"name_contains": "*.pdf", "extensions": ["pdf"], "since_days": 0},
        max_attempts=2,
    )
    action = engine.decide(step, FailureCode.UNEXPECTED_STATE, TaskContext(), replans_left=2)
    assert action.action == RELAX_AND_RETRY
    # The extension came from the user saying "PDF" and has to survive the relaxation.
    assert relax_arguments(step.preferred_tool, step.tool_arguments) == {"extensions": ["pdf"]}


def test_a_search_with_nothing_left_to_relax_does_not_retry_itself_forever(env):
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(
        preferred_tool="files.find_recent", tool_arguments={"extensions": ["pdf"]}, max_attempts=2
    )
    action = engine.decide(step, FailureCode.UNEXPECTED_STATE, TaskContext(), replans_left=2)
    assert action.action != RELAX_AND_RETRY


def test_recovery_never_substitutes_a_blind_click_for_a_failed_page_click(env):
    """computer.click fires at the pointer, so it cannot stand in for a lost element."""
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(preferred_tool="browser.click", max_attempts=1)
    step.attempt_count = 2
    action = engine.decide(step, FailureCode.ELEMENT_NOT_FOUND, TaskContext(), replans_left=2)
    assert action.action == REPLAN


def test_blind_click_is_not_verified_without_an_observable_change(env):
    verifier = ActionVerifier(browser_getter=lambda: env["browser"], computer_getter=lambda: env["computer"])
    context = TaskContext(expected_next_state="https://jobs.example.com/1")
    env["browser"].agent.url = "https://jobs.example.com/1"
    blind = AgentStep(id="s1", order=0, description="Click", preferred_tool="computer.click")
    assert verifier.verify(blind, {"clicked": True}, context).verified is False
    targeted = AgentStep(id="s2", order=1, description="Click Apply", preferred_tool="browser.click")
    assert verifier.verify(targeted, {"clicked": True}, context).verified is True


def test_search_that_matches_nothing_is_not_a_completed_step(env):
    verifier = ActionVerifier()
    step = AgentStep(id="s1", order=0, description="Find resumes", preferred_tool="files.find_recent")
    assert verifier.verify(step, {"matches": [], "count": 0}, TaskContext()).verified is False
    assert verifier.verify(step, {"matches": [{"path": "a.pdf"}]}, TaskContext()).verified is True


def test_recovery_asks_user_when_exhausted(env):
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(preferred_tool="agent.get_status", description="check status", max_attempts=1)
    step.attempt_count = 3
    action = engine.decide(step, FailureCode.UNEXPECTED_STATE, TaskContext(), replans_left=0)
    assert action.action == ASK_USER
    assert action.user_message


def test_recovery_waits_for_user_on_login(env):
    engine = FailureRecoveryEngine(env["registry"])
    step = AgentStep(preferred_tool="browser.click")
    action = engine.decide(step, FailureCode.AUTHENTICATION_REQUIRED, TaskContext())
    assert action.action == WAIT_FOR_USER
    assert "log in" in action.user_message.lower()


# --- tool result summarization / context compression -----------------------


def test_tool_results_are_summarized_not_dumped():
    huge = {"files": [{"name": f"f{i}.pdf", "path": "x" * 500} for i in range(200)]}
    summary = summarize_tool_data("files.search", huge)
    assert summary == "files.search: 200 result(s)"
    assert len(summary) < 60


def test_context_extracts_only_relevant_facts():
    ctx = TaskContext()
    update_context(
        ctx,
        "files.find_recent",
        {},
        {"files": [{"name": "a.pdf", "path": "C:/a.pdf"}, {"name": "b.pdf", "path": "C:/b.pdf"}]},
    )
    assert ctx.selected_file == "C:/a.pdf"
    assert len(ctx.candidate_files) == 2
    update_context(ctx, "browser.getPageState", {}, {"url": "https://x.com", "title": "X", "text": "y" * 5000})
    assert ctx.current_url == "https://x.com"
    assert len(ctx.facts["page_excerpt"]) <= 600
    assert len(ctx.facts["page_text"]) == 5000


# --- reference resolution --------------------------------------------------


def test_reference_resolution_picks_ordinal_file():
    ctx = TaskContext(
        candidate_files=[
            {"name": "new.pdf", "path": "C:/new.pdf"},
            {"name": "older.pdf", "path": "C:/older.pdf"},
        ]
    )
    res = resolve_reference("Use the second one.", ctx)
    assert res.resolved is True
    assert ctx.selected_file == "C:/older.pdf"


def test_reference_resolution_handles_second_newest():
    ctx = TaskContext(
        candidate_files=[
            {"name": "a.pdf", "path": "C:/a.pdf"},
            {"name": "b.pdf", "path": "C:/b.pdf"},
        ]
    )
    assert resolve_reference("Actually use the second newest one.", ctx).value == "C:/b.pdf"


def test_reference_resolution_picks_tab_when_asked():
    ctx = TaskContext(
        candidate_tabs=[{"title": "One", "url": "https://one"}, {"title": "Two", "url": "https://two"}],
        candidate_files=[{"name": "a.pdf", "path": "C:/a.pdf"}],
    )
    res = resolve_reference("No, the other tab.", ctx)
    assert res.kind == "tab"
    assert ctx.current_url == "https://two"


def test_reference_resolution_declines_without_candidates():
    assert resolve_reference("Use the second one.", TaskContext()).resolved is False


# --- runner: planning + deterministic execution ----------------------------


def test_runner_executes_plan_with_one_planner_call(env):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find recent resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Read the newest resume", "files.read", {"path": "C:/docs/resume_2026.pdf"}, ["s1"]),
                _step("s3", "Read the job page", "browser.getPageState", {}, ["s1"]),
                _step("s4", "Compare them", None, {}, ["s2", "s3"]),
            ),
            ProviderResponse(content="SHOULD NOT BE USED FOR COMPARE"),
        ]
    )
    runner = _runner(env, provider)
    env["browser"].agent.url = "https://jobs.example.com/1"
    result = runner.start("Find my newest resume and compare it with the job page I have open")

    assert result["ok"] is True
    assert result["task_status"] == "COMPLETED"
    assert result["planner_calls_per_task"] == 1
    assert result["replan_count"] == 0
    assert result["steps_completed"] == 4
    assert "Python" in result["message"]
    assert "Completed:" not in (result.get("message") or "")
    assert "not shown on your resume" in (result.get("message") or "").lower() or "strong matches" in (result.get("message") or "").lower()
    # Compare is a local engine, not a second planner/LLM call.
    assert len(provider.calls) == 1
    from src.agent.phase6.recent import recent_task

    stored = recent_task()
    assert stored is not None
    assert stored.comparison_result
    assert result.get("spoken")


def test_planner_is_not_woken_between_successful_steps(env):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Open Chrome", "computer.open_application", {"path": "chrome"}),
                _step("s2", "Go to the site", "browser.goto", {"url": "https://jobs.example.com/1"}, ["s1"]),
                _step("s3", "List the tabs", "browser.listTabs", {}, ["s2"]),
                _step("s4", "Read the page", "browser.getPageState", {}, ["s3"]),
                _step("s5", "Check status", "agent.get_status", {}, ["s4"]),
            )
        ]
    )
    runner = _runner(env, provider)
    result = runner.start("Open Chrome, go to the job site, list tabs, read the page and check status")
    assert result["task_status"] == "COMPLETED"
    assert result["steps_completed"] == 5
    assert result["planner_calls_per_task"] == 1
    assert len(provider.calls) == 1


def test_runner_rejects_unparseable_plan_without_executing(env):
    provider = MockProvider([ProviderResponse(content="Sure, I will open Chrome and look around.")])
    runner = _runner(env, provider)
    result = runner.start("Find my newest resume and compare it with the job page")
    assert result["ok"] is False
    assert result["plan_rejected"] == "UNPARSEABLE_PLAN"
    assert env["tools"]["files.find_recent"].calls == []


def test_runner_rejects_hallucinated_tool(env):
    provider = MockProvider([_plan(_step("s1", "Do magic", "files.telepathy", {}))])
    result = _runner(env, provider).start("Find my newest resume and email it")
    assert result["ok"] is False
    assert result["plan_rejected"] == "UNKNOWN_TOOL"


def test_planner_timeout_keeps_jarvis_responsive(env):
    class TimeoutProvider(MockProvider):
        def chat(self, *a, **kw):
            raise RuntimeError("Planner timed out after 25s.")

    result = _runner(env, TimeoutProvider([])).start("Find my resume and compare it with the job page")
    assert result["ok"] is False
    assert "too long" in result["message"].lower()
    assert result["task_status"] == "FAILED"


# --- runner: verification, recovery, replanning ----------------------------


def test_step_failure_triggers_recovery_not_immediate_failure(env):
    attempts = {"n": 0}

    def flaky(tool, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return ToolResult(success=False, error="Element not found: #apply")
        return ToolResult(success=True, data={"clicked": True, "changed": True})

    env["tools"]["browser.click"]._handler = flaky
    provider = MockProvider([_plan(_step("s1", "Click Apply", "browser.click", {"query": "Apply"}))])
    result = _runner(env, provider).start("Click apply on the job page")
    assert result["ok"] is True
    assert attempts["n"] == 2
    # State was refreshed before the retry, and the planner never woke again.
    assert env["tools"]["browser.getPageState"].calls
    assert result["planner_calls_per_task"] == 1


def test_retries_are_bounded_and_then_replan(env):
    env["tools"]["browser.click"]._handler = _fail("Element not found: #apply")
    env["tools"]["computer.click"]._handler = _fail("Element not found: #apply")
    provider = MockProvider(
        [
            _plan(_step("s1", "Click Apply", "browser.click", {"query": "Apply"})),
            _plan(_step("s2", "Read the page instead", "browser.getPageState", {})),
        ]
    )
    result = _runner(env, provider).start("Click apply on the job page")
    assert result["ok"] is True
    assert result["replan_count"] == 1
    assert result["planner_calls_per_task"] == 2
    assert len(env["tools"]["browser.click"].calls) <= 3


def test_login_interruption_waits_for_user_and_preserves_goal(env):
    env["tools"]["browser.click"]._handler = _fail(
        "blocked", {"error": {"code": "AUTHENTICATION_REQUIRED"}}
    )
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Click Apply", "browser.click", {"query": "Apply"}),
                _step("s2", "Read the form", "browser.getPageState", {}, ["s1"]),
            )
        ]
    )
    runner = _runner(env, provider)
    result = runner.start("Open the job page, click apply and start the application")
    assert result["waiting_for_user"] is True
    assert result["task_status"] == "WAITING_FOR_USER"
    assert "log in" in result["message"].lower()
    # The goal survives: the remaining step is still on the plan.
    assert runner.active.normalized_goal
    assert any(s.id == "s2" and not s.is_done for s in runner.active.steps)


def test_unverified_action_is_not_reported_as_success(env):
    env["tools"]["browser.goto"]._handler = lambda tool, **kw: ToolResult(
        success=True, data={"url": "https://wrong.example.com/"}
    )
    provider = MockProvider(
        [
            _plan(_step("s1", "Go to the job page", "browser.goto", {"url": "https://jobs.example.com/1"})),
            _plan(_step("s2", "Read the page", "browser.getPageState", {})),
        ]
    )
    result = _runner(env, provider).start("Open the job page in the browser and read it")
    # Verification failed, so it recovered rather than claiming success.
    assert result["replan_count"] >= 1 or result["ok"] is False


# --- runner: pause / resume / cancel / skip / modify ------------------------


def test_pause_stops_future_tool_execution(env):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Open the resume", "files.open", {"path": "C:/docs/resume_2026.pdf"}, ["s1"]),
            )
        ]
    )
    runner = _runner(env, provider)
    original = runner.step_executor.run_step

    def pause_after_first(step, ctx, **kw):
        out = original(step, ctx, **kw)
        runner.pause()
        return out

    runner.step_executor.run_step = pause_after_first
    result = runner.start("Find my newest resume and open it")
    assert result["task_status"] == "PAUSED"
    assert env["tools"]["files.open"].calls == []

    runner.step_executor.run_step = original
    resumed = runner.resume()
    assert resumed["task_status"] == "COMPLETED"
    assert len(env["tools"]["files.open"].calls) == 1


def test_cancel_stops_execution_and_marks_task_cancelled(env):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Open the resume", "files.open", {"path": "C:/docs/resume_2026.pdf"}, ["s1"]),
            )
        ]
    )
    runner = _runner(env, provider)
    original = runner.step_executor.run_step

    def cancel_after_first(step, ctx, **kw):
        out = original(step, ctx, **kw)
        runner.cancel()
        return out

    runner.step_executor.run_step = cancel_after_first
    result = runner.start("Find my newest resume and open it")
    assert result["task_status"] == "CANCELLED"
    assert env["tools"]["files.open"].calls == []
    assert all(s.status != AgentStepStatus.PENDING for s in runner.active.steps)


def test_skip_marks_step_skipped_and_continues(env):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Open the resume", "files.open", {"path": "C:/docs/resume_2026.pdf"}),
            )
        ]
    )
    runner = _runner(env, provider)
    original = runner.step_executor.run_step

    def pause_after_first(step, ctx, **kw):
        out = original(step, ctx, **kw)
        runner.pause()
        return out

    runner.step_executor.run_step = pause_after_first
    runner.start("Find my newest resume and open it")
    runner.step_executor.run_step = original
    skipped = runner.skip()
    assert skipped["ok"] is True
    resumed = runner.resume()
    assert resumed["task_status"] == "COMPLETED"
    assert env["tools"]["files.open"].calls == []


def test_task_modification_swaps_file_without_restarting(env):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Open the newest resume", "files.open", {"path": "C:/docs/resume_2026.pdf"}, ["s1"]),
            )
        ]
    )
    runner = _runner(env, provider)
    original = runner.step_executor.run_step

    def pause_after_first(step, ctx, **kw):
        out = original(step, ctx, **kw)
        if step.id == "s1":
            runner.pause()
        return out

    runner.step_executor.run_step = pause_after_first
    runner.start("Find my newest resume and open it")
    assert runner.active.context.selected_file == "C:/docs/resume_2026.pdf"

    modified = runner.modify("Actually use the second newest one.")
    assert modified["reference_resolved"] is True
    assert runner.active.context.selected_file == "C:/docs/resume_2024.pdf"
    assert modified["restarted"] is False
    # The completed search is not repeated.
    assert len(env["tools"]["files.find_recent"].calls) == 1


# --- orchestrator wiring / routing regression -------------------------------


@pytest.fixture
def tmp_store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


def _orch(provider, env, tmp_store, **kwargs) -> AgentOrchestrator:
    from src.agent.local_intent import reset_nlu_context

    reset_nlu_context()
    orch = AgentOrchestrator(
        provider,
        env["registry"],
        tmp_store,
        env["registry"].permissions,
        emergency_stop=EmergencyStop(),
        **kwargs,
    )
    orch.browser = env["browser"]
    orch.computer = env["computer"]
    return orch


def test_complex_goal_enters_phase6(env, tmp_store):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find recent resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Read the job page", "browser.getPageState", {}, ["s1"]),
                _step("s3", "Compare them", None, {}, ["s2"]),
            ),
            ProviderResponse(content="The resume lines up with the role."),
        ]
    )
    orch = _orch(provider, env, tmp_store)
    env["browser"].agent.url = "https://jobs.example.com/1"
    result = orch.handle_user_message(
        "Let's find my latest resume and compare it to the job page I have open."
    )
    assert result["planner_admitted"] is True
    assert result["planner_admission_reason"] == "MULTI_STEP_CROSS_TOOL_GOAL"
    assert result["utterance_type"] == "COMPLEX_GOAL"
    assert result["phase6"] is True
    assert result["planner_calls_per_task"] == 1


@pytest.mark.parametrize(
    "text",
    ["That's fine.", "I see.", "Okay then.", "Maybe later.", "That's an added task."],
)
def test_comments_never_reach_phase6(env, tmp_store, text):
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, env, tmp_store)
    result = orch.handle_user_message(text)
    assert result.get("planner_admitted") is False
    assert result.get("phase6") is not True
    assert provider.calls == []


def test_question_uses_conversation_not_phase6(env, tmp_store):
    provider = MockProvider([ProviderResponse(content="Light scatters in the atmosphere.")])
    orch = _orch(provider, env, tmp_store)
    result = orch.handle_user_message("Why is the sky blue?")
    assert result.get("planner_admitted") is False
    assert result.get("conceptual_route") == "AI_CONVERSATION"
    assert result.get("phase6") is not True
    # AI conversation gets no tool schemas at all.
    assert not (provider.calls[0].get("tools") or [])


def test_control_commands_drive_the_active_phase6_task(env, tmp_store):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Open the resume", "files.open", {"path": "C:/docs/resume_2026.pdf"}, ["s1"]),
            )
        ]
    )
    orch = _orch(provider, env, tmp_store)
    runner = orch.agent_runner
    original = runner.step_executor.run_step

    def pause_after_first(step, ctx, **kw):
        out = original(step, ctx, **kw)
        if step.id == "s1":
            runner.pause()
        return out

    runner.step_executor.run_step = pause_after_first
    orch.handle_user_message("Let's find my latest resume and open it next to the job page I have open.")
    assert runner.active.status == AgentTaskStatus.PAUSED

    runner.step_executor.run_step = original
    resumed = orch.handle_user_message("Continue.")
    assert resumed["task_status"] == "COMPLETED"

    cancelled = orch.handle_user_message("Cancel.")
    assert cancelled["ok"] is True


def test_mid_task_correction_is_local_not_a_new_task(env, tmp_store):
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Open the newest resume", "files.open", {"path": "C:/docs/resume_2026.pdf"}, ["s1"]),
            )
        ]
    )
    orch = _orch(provider, env, tmp_store)
    runner = orch.agent_runner
    original = runner.step_executor.run_step

    def pause_after_first(step, ctx, **kw):
        out = original(step, ctx, **kw)
        if step.id == "s1":
            runner.pause()
        return out

    runner.step_executor.run_step = pause_after_first
    orch.handle_user_message("Let's find my latest resume and open it next to the job page I have open.")
    runner.step_executor.run_step = original

    planner_calls_before = len(provider.calls)
    result = orch.handle_user_message("Actually use the second newest one.")
    assert result.get("reference_resolved") is True
    assert result.get("planner_admitted") is False
    assert len(provider.calls) == planner_calls_before
    assert runner.active.context.selected_file == "C:/docs/resume_2024.pdf"


def test_hud_progress_updates_without_reasoning(env, tmp_store):
    provider = MockProvider(
        [_plan(_step("s1", "Find recent resumes", "files.find_recent", {"query": "resume"}))]
    )
    orch = _orch(provider, env, tmp_store)
    seen: list[dict] = []
    orch.on_task_progress = seen.append
    orch.handle_user_message("Let's find my latest resume and open it next to the job page I have open.")
    assert seen
    last = seen[-1]
    assert last["steps"][0]["description"] == "Find recent resumes"
    blob = json.dumps(seen).lower()
    assert "system" not in blob and "<think>" not in blob


def test_phase6_telemetry_is_recorded(env, tmp_store):
    provider = MockProvider(
        [_plan(_step("s1", "Find recent resumes", "files.find_recent", {"query": "resume"}))]
    )
    orch = _orch(provider, env, tmp_store)
    result = orch.handle_user_message("Let's find my latest resume and open it next to the job page I have open.")
    perf = result.get("perf") or {}
    for key in (
        "tools_before_filter",
        "tools_after_filter",
        "tool_schema_tokens_before",
        "tool_schema_tokens_after",
        "planner_calls_per_task",
        "replan_count",
        "task_context_tokens",
        "planner_admitted",
        "planner_admission_reason",
        "utterance_type",
    ):
        assert key in perf, f"missing telemetry: {key}"
    assert perf["tools_after_filter"] <= perf["tools_before_filter"]


def test_model_output_boundary_blocks_raw_tool_json(env, tmp_store):
    provider = MockProvider([ProviderResponse(content='{"name":"get_status","arguments":{}}')])
    orch = _orch(provider, env, tmp_store)
    result = orch.handle_user_message("Why is the sky blue?")
    assert result["tts_allowed"] is False or "{" not in result["message"]
    assert '"arguments"' not in result["message"]


def test_context_stays_compact_across_replan(env, tmp_store):
    env["tools"]["browser.click"]._handler = _fail("Element not found: #apply")
    env["tools"]["computer.click"]._handler = _fail("Element not found: #apply")
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find resumes", "files.find_recent", {"query": "resume"}),
                _step("s2", "Read the page", "browser.getPageState", {}, ["s1"]),
                _step("s3", "Click Apply", "browser.click", {"query": "Apply"}, ["s2"]),
            ),
            _plan(_step("s4", "Check status", "agent.get_status", {})),
        ]
    )
    runner = _runner(env, provider)
    result = runner.start("Find my resume, read the job page and click apply")
    assert result["replan_count"] == 1
    replan_prompt = provider.calls[-1]["messages"][-1]["content"]
    assert len(replan_prompt) < 4000
    assert "resume_2026.pdf" not in replan_prompt or len(replan_prompt) < 4000
    assert result["task_context_tokens"] < 400


# --- a website is not a desktop window -------------------------------------


class TitleParams(BaseModel):
    title_contains: str = Field("")


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("ChatGPT", "https://chatgpt.com"),
        ("ChatGBT", "https://chatgpt.com"),
        ("chatgbt.com", "https://chatgpt.com"),
        ("YouTube", "https://www.youtube.com"),
        ("Notepad", ""),
        ("resume_2026.pdf", ""),
        ("", ""),
    ],
)
def test_a_site_is_recognized_by_the_name_people_say_it_by(spoken, expected):
    assert site_url(spoken) == expected


def test_a_site_that_has_no_window_is_looked_for_in_the_browser(registry):
    """"Switch back to ChatGPT" hunted the desktop, then tried to launch it as a program."""
    registry.register(ScriptedTool("browser.switchTab", QueryParams, _ok({"switched": True})))
    registry.register(ScriptedTool("computer.open_application", PathParams, _ok({})))
    step = AgentStep(
        order=0,
        description="Switch back to ChatGPT",
        preferred_tool="computer.focus_window",
        tool_arguments={"title_contains": "ChatGBT"},
        attempt_count=2,
    )
    action = FailureRecoveryEngine(registry).decide(
        step, FailureCode.WINDOW_NOT_FOUND, TaskContext()
    )
    assert action.action == ALTERNATIVE_TOOL
    assert action.alternative_tool == "browser.switchTab"


def test_a_window_that_is_not_a_website_still_tries_the_application(registry):
    """The browser detour is for sites only; a real program is still launched."""
    registry.register(ScriptedTool("browser.switchTab", QueryParams, _ok({"switched": True})))
    registry.register(ScriptedTool("computer.open_application", PathParams, _ok({})))
    step = AgentStep(
        order=0,
        description="Focus Notepad",
        preferred_tool="computer.focus_window",
        tool_arguments={"title_contains": "Notepad"},
        attempt_count=2,
    )
    action = FailureRecoveryEngine(registry).decide(
        step, FailureCode.WINDOW_NOT_FOUND, TaskContext()
    )
    assert action.alternative_tool == "computer.open_application"


def test_a_fallback_keeps_hold_of_what_the_step_was_aiming_at(registry):
    """Swapping the tool and dropping the target left switchTab nothing to search for."""
    registry.register(ScriptedTool("browser.switchTab", QueryParams, _ok({})))
    carried = retarget_arguments(registry, "browser.switchTab", {"title_contains": "ChatGPT"})
    assert carried == {"query": "ChatGPT"}


def test_a_fallback_to_navigation_supplies_the_address_the_step_only_named(registry):
    registry.register(ScriptedTool("browser.navigate", UrlParams, _ok({})))
    carried = retarget_arguments(registry, "browser.navigate", {"title_contains": "ChatGBT"})
    assert carried == {"url": "https://chatgpt.com"}


def test_a_site_with_no_window_and_no_tab_is_finally_opened(registry):
    """No window, then no tab: navigating is the only way left to reach the site."""
    registry.register(ScriptedTool("browser.switchTab", QueryParams, _ok({})))
    registry.register(ScriptedTool("browser.navigate", UrlParams, _ok({})))
    step = AgentStep(
        order=0,
        description="Switch back to ChatGPT",
        preferred_tool="browser.switchTab",
        tool_arguments={"query": "ChatGPT"},
        attempt_count=2,
    )
    action = FailureRecoveryEngine(registry).decide(
        step,
        FailureCode.TAB_NOT_FOUND,
        TaskContext(),
        tried_tools={"computer.focus_window", "browser.switchTab"},
    )
    assert action.alternative_tool == "browser.navigate"


def test_refreshing_state_does_not_drop_the_target_the_step_was_given(env):
    """Clearing "ChatGPT" let late binding retarget the step at the tab already open."""
    from src.agent.phase6.step_executor import StepOutcome

    runner = _runner(env, MockProvider([]))
    task = AgentTask(original_request="switch back to chatgpt")
    step = AgentStep(
        order=0,
        description="Switch back to ChatGPT",
        preferred_tool="browser.switchTab",
        tool_arguments={"query": "ChatGPT"},
    )
    task.steps = [step]
    runner._recover(
        task,
        step,
        StepOutcome(ok=False, error="Tab not found", failure_code=FailureCode.TAB_NOT_FOUND),
    )
    assert step.tool_arguments == {"query": "ChatGPT"}


def test_relaxing_a_search_keeps_the_word_the_user_actually_said():
    """Dropping name_contains="resume" retries as "any recent file" and opens the wrong one."""
    goal = "find my most recent resume and open it"
    kept = relax_arguments("files.find_recent", {"name_contains": "resume", "since_days": 0}, goal)
    assert kept == {"name_contains": "resume"}


def test_relaxing_still_sheds_a_filter_the_planner_invented():
    goal = "find my most recent pdf and open it"
    for invented in ("*.pdf", "pdf", "recent.pdf"):
        kept = relax_arguments("files.find_recent", {"name_contains": invented}, goal)
        assert kept == {}, invented


def test_a_word_absent_from_the_goal_is_the_planners_own_idea():
    kept = relax_arguments("files.find_recent", {"name_contains": "invoice"}, "find my newest file")
    assert kept == {}


def test_a_length_rejection_asks_for_the_count_the_goal_needs(registry):
    """"at most 3 steps" for a five-action goal made the model drop tools to comply."""
    planner = AgentPlanner(MockProvider([]), registry)
    prompt = planner._repair_prompt(
        PlanValidationError("PLAN_TOO_LONG", "Planner returned 21 steps."), [], "", 5
    )
    assert "exactly 5 step" in prompt
    assert "at most 3" not in prompt


def test_a_toolless_plan_is_told_that_steps_need_tools(registry):
    planner = AgentPlanner(MockProvider([]), registry)
    prompt = planner._repair_prompt(
        PlanValidationError("NO_ACTIONABLE_STEP", "Plan has no tool-backed step."), [], "", 3
    )
    assert '"tool"' in prompt


def test_repair_addresses_the_latest_rejection_not_the_first(env):
    """Repairing against a stale error asks the model to undo the fix it just made."""
    provider = MockProvider(
        [
            ProviderResponse(content=json.dumps({"goal": "g", "steps": [{"description": "x"}] * 21})),
            ProviderResponse(content=json.dumps({"goal": "g", "steps": [{"description": "think"}]})),
            ProviderResponse(
                content=json.dumps(
                    {"goal": "g", "steps": [_step("s1", "Find files", "files.find_recent", {})]}
                )
            ),
        ]
    )
    planner = AgentPlanner(provider, env["registry"])
    task = AgentTask(original_request="find my files")
    steps = planner.plan(task, tool_schemas=[])
    assert [s.preferred_tool for s in steps] == ["files.find_recent"]
    assert "no tool-backed step" in provider.calls[-1]["messages"][-1]["content"].lower()


def test_a_task_that_finished_on_its_last_attempt_is_not_a_failure(env):
    """Five steps and a five-rung ladder is nine attempts; the budget stopped at eight."""
    provider = MockProvider(
        [
            _plan(
                _step("s1", "Find files", "files.find_recent", {"query": "resume"}),
                _step("s2", "Read the page", "browser.getPageState", {}, ["s1"]),
            ),
            ProviderResponse(content="Found the resume and read the page."),
        ]
    )
    runner = _runner(env, provider, max_steps=0)
    result = runner.start("find my resume and read the job page")
    assert result["ok"] is True
    assert "took more steps" not in (result.get("message") or "")


_RESUME_CHROME_GOAL = (
    "Find my newest resume, open it, then switch back to Chrome and tell me what page I'm on."
)


def _resume_chrome_plan() -> ProviderResponse:
    return _plan(
        _step("s1", "Find newest resume", "files.find_recent", {"query": "resume"}),
        _step("s2", "Open the resume", "files.open", {}, ["s1"]),
        _step("s3", "Focus existing Chrome", "computer.focus_window", {"title_contains": "Chrome"}, ["s2"]),
        _step("s4", "Read the current page", "browser.getCurrentUrl", {}, ["s3"]),
    )


def test_resume_chrome_task_synthesizes_filename_and_page(env):
    """A completed multi-step goal must name the file and the page, never 'Done.'"""
    from src.agent.phase6.artifacts import registry

    result = _runner(env, MockProvider([_resume_chrome_plan()])).start(_RESUME_CHROME_GOAL)
    assert result["ok"] is True
    assert result["task_status"] == "COMPLETED"
    message = result["message"]
    assert message != "Done."
    assert "resume_2026.pdf" in message
    assert "left it open" in message.lower()
    assert "ChatGPT" in message
    assert "AI Desktop Agent Build" in message
    assert env["computer"].windows
    assert any("resume_2026" in str(w.get("title", "")).lower() for w in env["computer"].windows)
    assert registry()
    assert registry()[-1].verified_open is True
    assert registry()[-1].hwnd == 99


def test_resume_chrome_fails_if_page_was_never_read(env):
    """Step success is not goal success when the user asked what page they are on."""
    plan = _plan(
        _step("s1", "Find newest resume", "files.find_recent", {"query": "resume"}),
        _step("s2", "Open the resume", "files.open", {}, ["s1"]),
        _step("s3", "Focus existing Chrome", "computer.focus_window", {"title_contains": "Chrome"}, ["s2"]),
    )
    result = _runner(env, MockProvider([plan])).start(_RESUME_CHROME_GOAL)
    assert result["ok"] is False
    assert result["task_status"] == "FAILED"
    assert result["message"] != "Done."
    assert "could not read the current page" in (result.get("message") or "").lower()


def test_focus_chrome_does_not_close_opened_resume(env):
    from src.agent.phase6.artifacts import window_still_open

    result = _runner(env, MockProvider([_resume_chrome_plan()])).start(_RESUME_CHROME_GOAL)
    assert result["ok"] is True
    assert "chrome" in env["computer"].active.lower()
    resume_windows = [w for w in env["computer"].windows if "resume" in str(w.get("title", "")).lower()]
    assert resume_windows
    from src.agent.phase6.artifacts import registry

    art = registry()[-1]
    assert window_still_open(env["computer"], art) is True


def test_focus_chrome_fails_if_resume_vanishes(env):
    from src.agent.phase6.models import FailureCode

    runner = _runner(env, MockProvider([_resume_chrome_plan()]))
    original = runner.step_executor.run_step

    def drop_resume_after_open(step, ctx, **kw):
        out = original(step, ctx, **kw)
        if step.preferred_tool == "files.open":
            env["computer"].windows.clear()
        return out

    runner.step_executor.run_step = drop_resume_after_open
    result = runner.start(_RESUME_CHROME_GOAL)
    assert result["ok"] is False
    failed = [s for s in runner.active.steps if s.status == AgentStepStatus.FAILED]
    assert failed
    assert failed[0].failure_code == FailureCode.OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED
    progress = runner.active.progress()
    assert any(s.get("description") == "Resume closed unexpectedly" for s in progress["steps"])


def test_open_resume_only_names_file_and_stays_open(env):
    plan = _plan(
        _step("s1", "Find newest resume", "files.find_recent", {"query": "resume"}),
        _step("s2", "Open the resume", "files.open", {}, ["s1"]),
    )
    result = _runner(env, MockProvider([plan])).start("Open my newest resume.")
    assert result["ok"] is True
    assert result["task_status"] == "COMPLETED"
    assert result["message"] == "I opened resume_2026.pdf."
    assert env["computer"].windows


def test_synthesize_task_result_never_says_done():
    from src.agent.phase6.results import required_outcomes, synthesize_task_result

    task = AgentTask(original_request=_RESUME_CHROME_GOAL)
    task.context.selected_file = "C:/docs/Herberton_Sexton_Resume.pdf"
    task.context.facts["resume_open_verified"] = True
    task.context.facts["resume_exists_after_switch_to_chrome"] = True
    task.context.facts["resume_exists_at_finalization"] = True
    task.context.facts["persistence_state"] = "BACKGROUND"
    task.context.facts["chrome_focus_verified"] = True
    task.context.current_url = "https://chatgpt.com/c/abc"
    task.context.current_page = "AI Desktop Agent Build"
    task.context.facts["site_name"] = "ChatGPT"
    task.context.active_window = "AI Desktop Agent Build - Google Chrome"
    message = synthesize_task_result(task)
    assert "Herberton_Sexton_Resume.pdf" in message
    assert "left it open" in message.lower()
    assert "ChatGPT" in message
    assert message.lower() != "done."
    checks = required_outcomes(task)
    assert checks["resume_found"] is True
    assert checks["resume_opened"] is True
    assert checks["resume_still_open"] is True
    assert checks["chrome_focused"] is True
    assert checks["current_page_resolved"] is True
    assert checks["final_answer_contains_page"] is True


def test_synthesize_does_not_claim_left_open_without_final_verify():
    from src.agent.phase6.results import synthesize_task_result

    task = AgentTask(original_request=_RESUME_CHROME_GOAL)
    task.context.selected_file = "C:/docs/AI Engineer Resume 2026.pdf"
    task.context.facts["resume_open_verified"] = True
    task.context.facts["site_name"] = "Google"
    task.context.current_url = "https://www.google.com"
    message = synthesize_task_result(task)
    assert "left it open" not in message.lower()
    assert "couldn't confirm" in message.lower()


def test_synthesize_reports_unexpected_close():
    from src.agent.phase6.results import synthesize_task_result

    task = AgentTask(original_request=_RESUME_CHROME_GOAL)
    task.context.selected_file = "C:/docs/AI Engineer Resume 2026.pdf"
    task.context.facts["resume_open_verified"] = True
    task.context.facts["resume_exists_at_finalization"] = False
    task.context.facts["persistence_state"] = "CLOSED"
    task.context.facts["site_name"] = "Google"
    task.context.current_url = "https://www.google.com"
    message = synthesize_task_result(task)
    assert "left it open" not in message.lower()
    assert "closed unexpectedly" in message.lower()


def test_closed_resume_after_delay_does_not_claim_left_open(env):
    runner = _runner(env, MockProvider([_resume_chrome_plan()]))
    original = runner.step_executor.run_step

    def drop_after_page(step, ctx, **kw):
        out = original(step, ctx, **kw)
        if step.preferred_tool == "browser.getCurrentUrl":
            env["computer"].windows.clear()
        return out

    runner.step_executor.run_step = drop_after_page
    result = runner.start(_RESUME_CHROME_GOAL)
    assert "left it open" not in (result.get("message") or "").lower()
    assert result.get("ok") is False or "closed unexpectedly" in (result.get("message") or "").lower()


def test_comparison_engine_never_invents_resume_experience():
    from src.agent.phase6.compare import (
        COVERED,
        NOT_FOUND,
        JobResumeComparisonEngine,
        format_detailed,
        format_spoken,
    )

    job = (
        "Agentic AI Engineer, Senior\n"
        "Requirements:\n"
        "- Python\n"
        "- LangGraph\n"
        "- TS/SCI clearance\n"
        "- 5+ years of experience\n"
    )
    resume = "Herberton Sexton\nPython developer.\nBuilt internal automation with Python and FastAPI."
    result = JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text=resume,
        job_title="Agentic AI Engineer, Senior",
        job_url="https://jobs.example.com/agentic",
        resume_path="C:/docs/resume.pdf",
    )
    assert any(i.requirement.lower() == "python" and i.status == COVERED for i in result.covered_requirements)
    missing = " ".join(i.requirement.lower() for i in result.missing_requirements)
    assert "ts/sci" in missing or "clearance" in missing
    for item in result.covered_requirements:
        assert item.resume_evidence
        assert "python" in item.resume_evidence.lower()
    detailed = format_detailed(result)
    assert "not shown" in detailed.lower()
    assert "you do not have" not in detailed.lower()
    assert "Completed:" not in detailed
    spoken = format_spoken(result)
    assert "details on screen" in spoken.lower()


def test_compare_without_sources_does_not_complete(env):
    provider = MockProvider(
        [
            _plan(_step("s1", "Compare and list gaps", None, {})),
        ]
    )
    result = _runner(env, provider).start("Compare my resume to the job page I have open")
    assert result["ok"] is False
    assert result["task_status"] == "FAILED"
    assert "Completed:" not in (result.get("message") or "")


def test_remember_recent_task_without_artifacts():
    from src.agent.phase6.recent import remember_recent_task, recent_task

    task = AgentTask(original_request="compare resume to job")
    task.context.facts["comparison_result"] = {
        "job_title": "Engineer",
        "covered_requirements": [],
        "partial_requirements": [],
        "missing_requirements": [{"requirement": "Rust", "status": "NOT_FOUND", "resume_evidence": "", "job_evidence": "Rust", "confidence": 0.8, "category": "skills"}],
        "needs_confirmation": [],
        "job_page_url": "https://jobs.example.com/2",
    }
    task.context.facts["task_type"] = "JOB_RESUME_COMPARISON"
    ctx = remember_recent_task(task)
    assert ctx.comparison_result
    assert recent_task() is not None
    assert ctx.job_page_url.endswith("/2")


def test_chrome_ui_is_not_a_job_requirement():
    from src.agent.browser.page_model import BROWSER_CHROME, classify_text_scope, is_browser_noise
    from src.agent.phase6.compare import JobResumeComparisonEngine
    from src.agent.phase6.job_posting import BROWSER_UI, JobRequirementValidator, extract_sections

    chrome = [
        "Zoom: 110%",
        "Bookmark this tab",
        "Install Reddit",
        "Energy Saver is on",
        "Managed bookmarks",
        "Ask Google",
        "Close side panel",
        "View site information",
    ]
    for label in chrome:
        assert is_browser_noise(label), label
        assert classify_text_scope(label) in {BROWSER_CHROME, "BROWSER_EXTENSION"}
        assert JobRequirementValidator().classify(label, "required", "WEB_DOCUMENT") in {BROWSER_UI, "EXTENSION_UI", "NOISE"}
    text = "Qualifications\nRequired:\n- 2+ years hands-on building AI/ML solutions using Python\n" + "\n".join(chrome)
    buckets = extract_sections(text)
    reqs = [ev.text.lower() for ev in buckets.get("required") or []]
    assert any("python" in r for r in reqs)
    assert not any("zoom" in r or "bookmark" in r or "reddit" in r for r in reqs)
    result = JobResumeComparisonEngine().compare(
        page_text=text,
        resume_text="Python and AI/ML delivery.",
        job_title="Agentic AI Engineer, Senior",
        job_url="https://jobs.example.com/deloitte",
    )
    assert result.aborted is False
    blob = " ".join(i.requirement.lower() for i in result.items)
    assert "python" in blob
    assert "zoom" not in blob
    assert "bookmark" not in blob
    assert "reddit" not in blob
    assert "energy saver" not in blob


def test_job_sections_extract_deloitte_style_requirements():
    from src.agent.phase6.job_posting import build_job_posting, extract_sections

    text = (
        "Qualifications\n"
        "Required:\n"
        "- 4+ years of experience delivering AI/ML solutions, with at least 1 year focused on Generative AI\n"
        "- 2+ years of hands-on experience building AI/ML solutions using Python\n"
        "- frameworks such as LangChain, Semantic Kernel, AutoGen, CrewAI, or LangGraph\n"
        "- Bachelor's or Master's degree in a related technical field\n"
        "- Ability to travel up to 50%\n"
        "Preferred:\n"
        "- Prior consulting experience in client-facing delivery roles\n"
        "- Experience with MLOps / AIOps and CI/CD\n"
    )
    buckets = extract_sections(text)
    assert any("python" in ev.text.lower() for ev in buckets["required"])
    assert any("consulting" in ev.text.lower() for ev in buckets["preferred"])
    model = build_job_posting(document_text=text, url="https://jobs.example.com/x", title="Agentic AI Engineer, Senior")
    reqs = model.validated_requirements()
    assert reqs
    assert all(r["source_scope"] == "WEB_DOCUMENT" for r in reqs)
    assert not any("zoom" in r["requirement"].lower() for r in reqs)


def test_contaminated_chrome_dump_aborts_without_job_model():
    from src.agent.phase6.compare import JobResumeComparisonEngine
    from src.agent.phase6.job_posting import JOB_CONTENT_CONTAMINATION_DETECTED

    result = JobResumeComparisonEngine().compare(
        page_text="Zoom: 110%\nBookmark this tab\nEnergy Saver is on\nInstall Reddit",
        resume_text="Python developer",
        job_title="Chrome",
        job_url="https://jobs.example.com/noise",
    )
    assert result.aborted is True
    assert result.abort_reason == JOB_CONTENT_CONTAMINATION_DETECTED
    assert result.items == []


def test_what_does_this_page_say_is_page_about():
    from src.agent.router import FastCommandRouter

    intent = FastCommandRouter().route("What does this page say?")
    assert intent is not None
    assert intent.action == "browser.page_about"
