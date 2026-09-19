"""Fast-path router and performance architecture tests — no Ollama required."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from src.agent.audit import AuditLog
from src.agent.emergency import EmergencyStop
from src.agent.model_router import (
    CONVERSATION_MODEL,
    NO_MODEL,
    PLANNER_MODEL,
    ModelRouter,
)
from src.agent.observe import cheapest_web_state, should_use_uia
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine, PermissionLevel
from src.agent.providers.base import ProviderResponse, ToolCallRequest
from src.agent.respond import format_page
from src.agent.router import FastCommandRouter
from src.agent.task_store import TaskStore
from src.agent.tools import EmptyParams
from src.agent.tools.base import BaseTool, ToolRegistry, ToolResult
from tests.test_agent_phase1 import MockProvider


@pytest.fixture
def tmp_store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


@pytest.fixture
def permissions() -> PermissionEngine:
    return PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True)


@pytest.fixture
def registry(permissions: PermissionEngine) -> ToolRegistry:
    return ToolRegistry(permissions, AuditLog(), EmergencyStop())


def _orch(provider, registry, tmp_store, **kwargs) -> AgentOrchestrator:
    from src.agent.local_intent import reset_nlu_context

    reset_nlu_context()
    return AgentOrchestrator(
        provider,
        registry,
        tmp_store,
        registry.permissions,
        emergency_stop=EmergencyStop(),
        **kwargs,
    )


class FakeBrowserAgent:
    def __init__(self):
        self._page = object()
        self.url = "https://loldispensary.com/"
        self.title = "LOL Dispensary | Shop"

    def get_session_state(self):
        return {
            "open": True,
            "sessionId": "sess-test",
            "url": self.url,
            "title": self.title,
            "tabs": 1,
            "browserType": "chrome",
            "connectionStatus": "active",
            "activeTabId": "tab-1",
            "lastNavigation": "2026-08-13T01:00:00",
            "pageFingerprint": "abc123",
        }

    def back(self):
        self.url = "https://example.com/"
        self.title = "Example"
        return {"success": True, "action": "back", "url": self.url, "title": self.title, "verified": True}


class FakeSession:
    def __init__(self):
        self.agent = FakeBrowserAgent()


class NavParams(BaseModel):
    url: str = Field("")


class NavigateTool(BaseTool):
    name = "browser.navigate"
    description = "nav"
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = NavParams
    max_retries = 0

    def __init__(self, agent: FakeBrowserAgent):
        self.agent = agent

    def execute(self, url: str = "", **kwargs) -> ToolResult:
        self.agent.url = url
        self.agent.title = "LOL Dispensary | Shop"
        return ToolResult(
            success=True,
            data={"success": True, "action": "navigate", "url": url, "title": self.agent.title, "verified": True},
        )


class BackTool(BaseTool):
    name = "browser.back"
    description = "back"
    permission_level = PermissionLevel.LOCAL_ACTION
    parameters_model = EmptyParams
    max_retries = 0

    def __init__(self, agent: FakeBrowserAgent):
        self.agent = agent

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data=self.agent.back())


def test_router_page_question():
    r = FastCommandRouter()
    intent = r.route("What page am I on right now?")
    assert intent is not None
    assert intent.action == "browser.current_page"


def test_router_open_browser_url():
    r = FastCommandRouter()
    intent = r.route("Open a browser and go to https://loldispensary.com")
    assert intent is not None
    assert intent.action == "browser.open_and_goto"
    assert "loldispensary.com" in intent.args["url"]


def test_router_go_back():
    r = FastCommandRouter()
    assert r.route("Go back.").action == "browser.back"


def test_router_open_chrome():
    r = FastCommandRouter()
    assert r.route("open chrome").action == "browser.open"


def test_router_scroll_down():
    r = FastCommandRouter()
    intent = r.route("scroll down")
    assert intent is not None
    assert intent.action == "browser.scroll"


def test_router_complex_goes_to_planner():
    r = FastCommandRouter()
    assert r.route("Find the job I was looking at earlier and start the application.") is None


def test_model_router_tiers():
    mr = ModelRouter()
    assert mr.choose(fast_intent=object()) == NO_MODEL
    assert mr.choose(control=True) == NO_MODEL
    assert mr.choose(conversation=True) == CONVERSATION_MODEL
    assert mr.choose(planner_admitted=True) == PLANNER_MODEL
    assert mr.choose() == CONVERSATION_MODEL


def test_observation_skips_uia_for_page_questions():
    assert should_use_uia("What page am I on?", browser_open=True) is False
    state = cheapest_web_state(FakeBrowserAgent())
    assert "loldispensary.com" in (state.get("url") or "")


def test_what_page_bypasses_llm(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, registry, tmp_store)
    orch.browser = FakeSession()
    t0 = time.perf_counter()
    result = orch.handle_user_message("What page am I on right now?")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result["ok"] is True
    assert result.get("fast") is True
    assert "LOL Dispensary" in result["message"]
    assert provider.calls == []
    assert elapsed_ms < 500
    assert "Planner LLM" not in (result.get("perf") or {}).get("marks", {})
    print(f"[MEASURE] what-page fast-path {elapsed_ms:.1f} ms")


def test_go_back_bypasses_llm(tmp_store, registry):
    session = FakeSession()
    registry.register(BackTool(session.agent))
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, registry, tmp_store)
    orch.browser = session
    result = orch.handle_user_message("Go back.")
    assert result.get("fast") is True
    assert provider.calls == []
    assert result["ok"] is True
    assert "back" in result["message"].lower() or "example" in result["message"].lower()


def test_open_and_goto_bypasses_llm(tmp_store, registry):
    session = FakeSession()
    registry.register(NavigateTool(session.agent))
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, registry, tmp_store)
    orch.browser = session
    result = orch.handle_user_message("Open a browser and go to https://loldispensary.com")
    assert result.get("fast") is True
    assert provider.calls == []
    assert result["ok"] is True
    assert "loldispensary.com" in (session.agent.url or "")


def test_complex_command_uses_planner(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="I would need to look up that job first.")])
    orch = _orch(provider, registry, tmp_store, max_steps=3)
    result = orch.handle_user_message("Find the job I was looking at earlier and start the application.")
    assert result.get("fast") is False
    assert result.get("planner_admitted") is True
    assert result.get("conceptual_route") == "AGENT_PLAN"
    assert provider.calls


def test_format_page_no_llm():
    assert "LOL Dispensary" in format_page("https://loldispensary.com", "LOL Dispensary | Shop")


def test_new_command_invalidates_old_generation(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="old answer")])
    orch = _orch(provider, registry, tmp_store, max_steps=5)
    gen_before = orch._generation
    orch.cancel_stale()
    assert orch._generation > gen_before


def test_page_query_preempts_slow_planner(tmp_store, registry):
    started = threading.Event()

    class SlowProvider(MockProvider):
        def chat(self, *args, **kwargs):
            started.set()
            time.sleep(0.45)
            return super().chat(*args, **kwargs)

    provider = SlowProvider([ProviderResponse(content="stale planner answer")])
    orch = _orch(provider, registry, tmp_store, max_steps=3)
    orch.browser = FakeSession()
    holder: dict = {}

    def run():
        holder["r"] = orch.handle_user_message(
            "Find the job I was looking at earlier and start the application."
        )

    t = threading.Thread(target=run)
    t.start()
    assert started.wait(timeout=2)
    page = orch.handle_user_message("What page am I on right now?")
    t.join(timeout=3)
    assert page.get("fast") is True
    assert "LOL Dispensary" in page["message"]
    assert holder["r"].get("stale") or holder["r"].get("ok") is False


def test_stop_is_fast_and_engages_local_emergency(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, registry, tmp_store)
    result = orch.handle_user_message("Stop.")
    assert result.get("fast") is True
    assert orch.emergency.is_engaged
    assert provider.calls == []
    orch.emergency.clear()


class FakeCapture:
    def __init__(self):
        self.capture_calls = 0
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        return {"available": True, "backend": "fake", "monitor_count": 2}

    def get_monitors(self):
        return [{"width": 1920, "height": 1080}, {"width": 1920, "height": 1080}]

    def capture_all(self):
        self.capture_calls += 1
        return [{"monitor_index": 0, "size": (64, 64), "base64": "aaa"}]


class FakeScreenService:
    def __init__(self, capture: FakeCapture):
        self._screen = capture
        self._computer = None

    def capture_status(self):
        return self._screen.status()

    def get_state(self, include_screenshot: bool = False, include_uia: bool = False):
        from src.agent.screen.understanding import ScreenState

        assert include_screenshot is False
        return ScreenState(
            active_application="chrome.exe",
            active_window="Example - Chrome",
            screen_width=1920,
            screen_height=1080,
            visible_text=["Example - Chrome"],
        )

    def find_click_target(self, name: str):
        return {"name": name, "kind": "button", "x": 40, "y": 80, "bounds": {"left": 10, "top": 60, "right": 70, "bottom": 100}}


class FakeComputer:
    def __init__(self):
        self.clicks = []

    def click(self, x, y, button="left"):
        self.clicks.append((x, y, button))
        return {"x": x, "y": y, "button": button}


def test_router_screen_status():
    r = FastCommandRouter()
    assert r.route("Can you see my screen right now?").action == "screen.capture_status"
    assert r.route("do you have screen access").action == "screen.capture_status"


def test_router_screen_describe():
    r = FastCommandRouter()
    assert r.route("What do you see on my screen?").action == "screen.describe"
    assert r.route("what's on my screen").action == "screen.describe"


def test_router_click_login_is_screen_action():
    r = FastCommandRouter()
    intent = r.route("click the login button")
    assert intent is not None
    assert intent.action == "screen.click"
    assert "login" in intent.args["name"]


def test_screen_status_skips_planner_and_vision(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, registry, tmp_store)
    capture = FakeCapture()
    orch.screen_service = FakeScreenService(capture)
    orch.screen_capture = capture
    t0 = time.perf_counter()
    result = orch.handle_user_message("Can you see my screen right now?")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result.get("fast") is True
    assert result["ok"] is True
    assert provider.calls == []
    assert capture.capture_calls == 0
    assert "yes" in result["message"].lower()
    assert "Planner LLM" not in (result.get("perf") or {}).get("marks", {})
    assert "Vision" not in (result.get("perf") or {}).get("marks", {})
    assert elapsed_ms < 100
    print(f"[MEASURE] screen-status {elapsed_ms:.1f} ms")


def test_screen_describe_skips_planner(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="Chrome is showing a document.")])
    orch = _orch(provider, registry, tmp_store)
    capture = FakeCapture()
    orch.screen_service = FakeScreenService(capture)
    orch.screen_capture = capture
    result = orch.handle_user_message("What do you see on my screen?")
    assert result.get("fast") is True
    assert result["ok"] is True
    assert "Planner LLM" not in (result.get("perf") or {}).get("marks", {})
    assert capture.capture_calls == 1
    assert provider.calls
    assert not provider.calls[0].get("tools")
    assert provider.calls[0].get("images_base64")
    assert "chrome" in result["message"].lower() or "document" in result["message"].lower()


def test_screen_click_uses_computer_not_planner(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")])
    orch = _orch(provider, registry, tmp_store)
    capture = FakeCapture()
    orch.screen_service = FakeScreenService(capture)
    orch.computer = FakeComputer()
    result = orch.handle_user_message("click the login button")
    assert result.get("fast") is True
    assert provider.calls == []
    assert orch.computer.clicks
    assert result["ok"] is True


def test_multistep_apply_still_uses_planner(tmp_store, registry):
    provider = MockProvider([ProviderResponse(content="I would need to inspect the page first.")])
    orch = _orch(provider, registry, tmp_store, max_steps=3)
    result = orch.handle_user_message(
        "Find the Apply button and click it, then continue the application."
    )
    assert result.get("fast") is False
    assert provider.calls


def test_planner_does_not_receive_all_tools(tmp_store, registry):
    def _extra(n: str) -> BaseTool:
        class Extra(BaseTool):
            name = n
            description = "unused extra tool"
            permission_level = PermissionLevel.OBSERVE
            parameters_model = EmptyParams
            max_retries = 0

            def execute(self, **kwargs) -> ToolResult:
                return ToolResult(success=True)

        Extra.name = n
        return Extra()

    for i in range(24):
        registry.register(_extra(f"extra.tool{i}"))

    class BrowserClick(BaseTool):
        name = "browser.click"
        description = "click"
        permission_level = PermissionLevel.LOCAL_ACTION
        parameters_model = EmptyParams
        max_retries = 0

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True)

    registry.register(BrowserClick())
    provider = MockProvider([ProviderResponse(content="Need to inspect the form.")])
    orch = _orch(provider, registry, tmp_store, max_steps=3)
    result = orch.handle_user_message(
        "Find the Apply button and click it, then continue the application."
    )
    assert result.get("fast") is False
    # The Phase 6 planner receives the filtered tool list inside the prompt, not as
    # tool schemas, so the model can never call an unfiltered tool.
    assert not (provider.calls[0].get("tools") or [])
    prompt = json.dumps(provider.calls[0].get("messages") or [])
    assert "browser.click" in prompt
    assert "extra.tool" not in prompt
    perf = result.get("perf") or {}
    assert perf.get("tools_before_filter") >= 24
    assert perf.get("tools_after_filter") < perf.get("tools_before_filter")
    assert perf.get("tools_after_filter") >= 1
    print(
        f"[MEASURE] tools {perf.get('tools_before_filter')} -> {perf.get('tools_after_filter')} "
        f"schema tokens {perf.get('tool_schema_tokens_before')} -> "
        f"{perf.get('tool_schema_tokens_after')}"
    )
