"""Tests for Browser Target Resolution — Phase 8 fix.

Validates that JARVIS distinguishes the user's existing desktop Chrome
from a JARVIS-managed Playwright session, and never silently launches
an automation browser for contextual commands.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.agent.browser.existing import ExistingBrowserAdapter, page_matches_site
from src.agent.browser.resolver import (
    BrowserTargetResolver,
    BrowserWindowInfo,
    _extract_tab_title,
)
from src.agent.computer.platform import WindowInfo
from src.agent.observe import cheapest_web_state


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_window(
    *,
    handle: int = 100,
    title: str = "ChatGPT - Google Chrome",
    pid: int = 1234,
    process_name: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    rect: tuple[int, int, int, int] = (100, 100, 1200, 800),
    is_visible: bool = True,
    is_minimized: bool = False,
    monitor_index: int = 0,
) -> WindowInfo:
    return WindowInfo(
        handle=handle,
        title=title,
        process_id=pid,
        process_name=process_name,
        rect=rect,
        is_visible=is_visible,
        is_minimized=is_minimized,
        monitor_index=monitor_index,
    )


class FakeComputer:
    """Minimal computer mock for resolver tests."""

    def __init__(
        self,
        windows: list[WindowInfo] | None = None,
        fg_hwnd: int = 0,
    ):
        self._windows = windows or []
        self._fg_hwnd = fg_hwnd
        self.focus_calls: list[int] = []
        self._clipboard = ""
        self.keys_pressed: list[str] = []
        self.hotkeys_pressed: list[tuple] = []
        self.text_typed: list[str] = []

    def list_windows(self) -> list[WindowInfo]:
        return list(self._windows)

    def get_active_window(self) -> Optional[WindowInfo]:
        for w in self._windows:
            if w.handle == self._fg_hwnd:
                return w
        return None

    def focus_window(self, handle: int) -> bool:
        self.focus_calls.append(handle)
        return True

    def get_clipboard(self) -> str:
        return self._clipboard

    def set_clipboard(self, text: str) -> None:
        self._clipboard = text

    def press_key(self, key: str) -> None:
        self.keys_pressed.append(key)

    def hotkey(self, keys, *more):
        from src.agent.computer.controller import _normalize_hotkeys

        seq = tuple(_normalize_hotkeys(keys, *more))
        self.hotkeys_pressed.append(seq)
        return {"keys": list(seq)}

    def type_text(self, text: str, interval: float = 0.02) -> None:
        self.text_typed.append(text)


# ─── BrowserTargetResolver ───────────────────────────────────────────


class TestBrowserTargetResolver:
    def test_finds_chrome_window(self):
        w = _make_window()
        computer = FakeComputer([w], fg_hwnd=w.handle)
        resolver = BrowserTargetResolver(computer=computer)
        browsers = resolver.find_all()
        assert len(browsers) == 1
        assert browsers[0].hwnd == w.handle
        assert browsers[0].source == "EXISTING_DESKTOP"
        assert not browsers[0].playwright_owned

    def test_finds_foreground_chrome(self):
        w = _make_window(handle=100)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)
        fg = resolver.find_foreground_browser()
        assert fg is not None
        assert fg.hwnd == 100
        assert fg.is_foreground

    def test_ignores_managed_pid(self):
        w = _make_window(handle=100, pid=5555)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer, managed_pids={5555})
        fg = resolver.find_foreground_browser()
        assert fg is None  # managed PID is excluded

    def test_mark_managed(self):
        w = _make_window(handle=100, pid=7777)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)
        assert resolver.find_foreground_browser() is not None
        resolver.mark_managed(7777)
        assert resolver.find_foreground_browser() is None

    def test_multiple_windows_picks_foreground(self):
        """Two Chrome windows, should pick the foreground one."""
        w1 = _make_window(handle=100, title="Gmail - Google Chrome", pid=1000, monitor_index=0)
        w2 = _make_window(handle=200, title="ChatGPT - Google Chrome", pid=2000, monitor_index=1)
        computer = FakeComputer([w1, w2], fg_hwnd=200)
        resolver = BrowserTargetResolver(computer=computer)
        fg = resolver.find_foreground_browser()
        assert fg is not None
        assert fg.hwnd == 200  # ChatGPT window is foreground

    def test_switch_focus_follows_window(self):
        """Focus changes → target changes."""
        w1 = _make_window(handle=100, title="Gmail - Google Chrome", pid=1000, monitor_index=0)
        w2 = _make_window(handle=200, title="ChatGPT - Google Chrome", pid=2000, monitor_index=1)
        computer = FakeComputer([w1, w2], fg_hwnd=200)
        resolver = BrowserTargetResolver(computer=computer)

        fg = resolver.find_foreground_browser()
        assert fg.hwnd == 200

        # Switch foreground
        computer._fg_hwnd = 100
        fg2 = resolver.find_foreground_browser()
        assert fg2.hwnd == 100

    def test_negative_coordinates_valid(self):
        """Left-side monitor has negative x coordinates."""
        w = _make_window(
            handle=300,
            rect=(-1920, 0, 0, 1080),
            monitor_index=1,
        )
        computer = FakeComputer([w], fg_hwnd=300)
        resolver = BrowserTargetResolver(computer=computer)
        browsers = resolver.find_all()
        assert len(browsers) == 1
        assert browsers[0].bounds[0] == -1920  # negative x is valid

    def test_non_browser_windows_excluded(self):
        notepad = _make_window(
            handle=400,
            title="Untitled - Notepad",
            process_name=r"C:\Windows\system32\notepad.exe",
        )
        chrome = _make_window(handle=500)
        computer = FakeComputer([notepad, chrome], fg_hwnd=400)
        resolver = BrowserTargetResolver(computer=computer)
        browsers = resolver.find_all()
        assert len(browsers) == 1
        assert browsers[0].hwnd == 500

    def test_should_launch_new_when_no_chrome(self):
        computer = FakeComputer([], fg_hwnd=0)
        resolver = BrowserTargetResolver(computer=computer)
        assert resolver.should_launch_new("") is True

    def test_should_not_launch_when_chrome_exists(self):
        w = _make_window()
        computer = FakeComputer([w], fg_hwnd=w.handle)
        resolver = BrowserTargetResolver(computer=computer)
        assert resolver.should_launch_new("") is False

    def test_should_launch_for_explicit_managed_request(self):
        w = _make_window()
        computer = FakeComputer([w], fg_hwnd=w.handle)
        resolver = BrowserTargetResolver(computer=computer)
        assert resolver.should_launch_new("Open a separate browser") is True
        assert resolver.should_launch_new("Open an automation browser") is True

    def test_minimized_chrome_found(self):
        w = _make_window(is_minimized=True, is_visible=True)
        computer = FakeComputer([w], fg_hwnd=0)
        resolver = BrowserTargetResolver(computer=computer)
        found = resolver.find_existing_chrome()
        assert found is not None
        assert found.hwnd == w.handle


# ─── Tab title extraction ────────────────────────────────────────────


class TestTabTitleExtraction:
    def test_chrome_suffix(self):
        assert _extract_tab_title("ChatGPT - Google Chrome") == "ChatGPT"

    def test_edge_suffix(self):
        assert _extract_tab_title("Outlook - Microsoft Edge") == "Outlook"

    def test_no_suffix(self):
        assert _extract_tab_title("Some Random Window") == "Some Random Window"

    def test_complex_title(self):
        assert _extract_tab_title("New Tab - Google Chrome") == "New Tab"


# ─── cheapest_web_state ─────────────────────────────────────────────


class TestCheapestWebState:
    def test_prefers_existing_desktop_foreground(self):
        w = _make_window(handle=100, title="ChatGPT - Google Chrome", pid=1234)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)
        fake_agent = MagicMock()

        state = cheapest_web_state(fake_agent, resolver=resolver)
        assert state["browser_target_source"] == "EXISTING_DESKTOP"
        assert state["browser_selection_reason"] == "FOREGROUND_CHROME"
        assert state["browser_hwnd"] == 100
        assert state["title"] == "ChatGPT"
        fake_agent.get_session_state.assert_not_called()

    def test_falls_back_to_playwright_when_no_desktop(self):
        computer = FakeComputer([], fg_hwnd=0)
        resolver = BrowserTargetResolver(computer=computer)
        fake_agent = MagicMock()
        fake_agent.get_session_state.return_value = {
            "open": True, "url": "about:blank", "title": ""
        }

        state = cheapest_web_state(fake_agent, resolver=resolver)
        assert state["discovery_status"] == "NO_BROWSER_WINDOW"
        assert state["browser_selection_reason"] == "NO_BROWSER_WINDOW"
        fake_agent.get_session_state.assert_not_called()

    def test_existing_visible_but_not_foreground(self):
        """Chrome is visible on monitor 2 but not in foreground."""
        w = _make_window(handle=200, monitor_index=1)
        notepad = _make_window(
            handle=300,
            title="Untitled - Notepad",
            process_name=r"C:\Windows\system32\notepad.exe",
        )
        computer = FakeComputer([notepad, w], fg_hwnd=300)
        resolver = BrowserTargetResolver(computer=computer)

        state = cheapest_web_state(None, resolver=resolver)
        assert state["browser_target_source"] == "EXISTING_DESKTOP"
        assert state["browser_selection_reason"] in {
            "VISIBLE_CHROME",
            "MOST_RECENTLY_ACTIVE",
            "LAST_USER_ACTIVE_BROWSER",
            "FOREGROUND_CHROME",
        }
        assert state["browser_hwnd"] == 200

    def test_managed_pid_not_preferred(self):
        """A Playwright-owned Chrome should not be returned as EXISTING_DESKTOP."""
        w = _make_window(handle=100, pid=9999)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer, managed_pids={9999})
        fake_agent = MagicMock()
        fake_agent.get_session_state.return_value = {"open": True, "url": "about:blank", "title": ""}

        state = cheapest_web_state(fake_agent, resolver=resolver)
        assert state["discovery_status"] == "NO_BROWSER_WINDOW"
        assert state["browser_target_source"] == "NONE"


# ─── ExistingBrowserAdapter ──────────────────────────────────────────


class TestExistingBrowserAdapter:
    def _make_adapter(self, windows=None, fg_hwnd=0):
        windows = windows or []
        computer = FakeComputer(windows, fg_hwnd=fg_hwnd)
        resolver = BrowserTargetResolver(computer=computer)
        return ExistingBrowserAdapter(computer, resolver), computer

    def test_focus_existing_chrome(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.focus()
        assert result["ok"]
        assert result["hwnd"] == 100
        assert 100 in computer.focus_calls

    def test_focus_no_chrome(self):
        adapter, _ = self._make_adapter([], fg_hwnd=0)
        result = adapter.focus()
        assert not result["ok"]

    def test_navigate_sends_keyboard(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.navigate("https://google.com")
        assert result["ok"]
        assert result["source"] == "EXISTING_DESKTOP"
        assert result["method"] == "keyboard"
        assert ("ctrl", "l") in computer.hotkeys_pressed
        assert "https://google.com" in computer.text_typed
        assert "Return" in computer.keys_pressed

    def test_hotkey_accepts_star_args_and_list(self):
        from src.agent.computer.controller import _normalize_hotkeys

        assert _normalize_hotkeys("ctrl", "t") == ["ctrl", "t"]
        assert _normalize_hotkeys(["ctrl", "t"]) == ["ctrl", "t"]
        assert _normalize_hotkeys("ctrl+t") == ["ctrl", "t"]

    def test_new_tab_ctrl_t(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.new_tab("https://example.com")
        assert result["ok"]
        assert ("ctrl", "t") in computer.hotkeys_pressed
        assert "https://example.com" in computer.text_typed

    def test_switch_tab(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.switch_tab("ChatGPT")
        assert result["ok"]
        assert ("ctrl", "shift", "a") in computer.hotkeys_pressed
        assert "ChatGPT" in computer.text_typed

    def test_go_to_site_prefers_existing_tab_by_domain(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        pages = [
            {"ok": True, "url": "https://www.google.com", "title": "Google", "active_tab_title": "Google"},
            {"ok": True, "url": "https://chatgpt.com", "title": "AI Desktop Agent Build", "active_tab_title": "AI Desktop Agent Build"},
        ]

        def _page(**kwargs):
            return pages.pop(0) if len(pages) > 1 else pages[0]

        with patch.object(adapter, "get_current_page", side_effect=_page), patch.object(
            adapter, "_activate_tab_by_title", return_value=False
        ), patch("src.agent.browser.existing.time.sleep"):
            result = adapter.go_to_site(site="ChatGPT", url="https://chatgpt.com")
        assert result["ok"]
        assert result["existing_tab_found"] is True
        assert result["source"] == "EXISTING_DESKTOP"
        assert result["hwnd"] == 100
        assert result["message"] == "Switched to ChatGPT."
        assert ("ctrl", "shift", "a") in computer.hotkeys_pressed
        assert "chatgpt.com" in computer.text_typed
        assert ("ctrl", "l") not in computer.hotkeys_pressed
        assert ("ctrl", "t") not in computer.hotkeys_pressed

    def test_page_matches_site_uses_url_not_conversation_title(self):
        page = {
            "url": "https://chatgpt.com/c/abc",
            "title": "AI Desktop Agent Build",
            "active_tab_title": "AI Desktop Agent Build",
        }
        assert page_matches_site(page, "ChatGPT", "https://chatgpt.com")
        assert not page_matches_site(
            {"url": "https://www.google.com", "title": "Google"},
            "ChatGPT",
            "https://chatgpt.com",
        )
        assert not page_matches_site(
            {"url": "https://mail.google.com", "title": "Gmail"},
            "Google",
            "https://www.google.com",
        )

    def test_go_to_site_navigates_same_hwnd_when_tab_missing(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)

        def _page(**kwargs):
            typed = " ".join(computer.text_typed)
            if "chatgpt.com" in typed and ("ctrl", "l") in computer.hotkeys_pressed:
                return {
                    "ok": True,
                    "url": "https://chatgpt.com",
                    "title": "ChatGPT",
                    "active_tab_title": "ChatGPT",
                }
            return {
                "ok": True,
                "url": "https://www.google.com",
                "title": "Google",
                "active_tab_title": "Google",
            }

        with patch.object(adapter, "get_current_page", side_effect=_page), patch.object(
            adapter, "_activate_tab_by_title", return_value=False
        ), patch("src.agent.browser.existing.time.sleep"):
            result = adapter.go_to_site(site="ChatGPT", url="https://chatgpt.com")
        assert result["ok"]
        assert result["existing_tab_found"] is False
        assert result["hwnd"] == 100
        assert result["source"] == "EXISTING_DESKTOP"
        assert result["message"] == "Switched to ChatGPT."
        assert ("ctrl", "l") in computer.hotkeys_pressed
        assert "https://chatgpt.com" in computer.text_typed
        assert ("ctrl", "t") not in computer.hotkeys_pressed

    def test_go_back(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.go_back()
        assert result["ok"]
        assert ("alt", "Left") in computer.hotkeys_pressed

    def test_go_forward(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.go_forward()
        assert result["ok"]
        assert ("alt", "Right") in computer.hotkeys_pressed

    def test_reload(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.reload()
        assert result["ok"]
        assert ("ctrl", "r") in computer.hotkeys_pressed

    def test_close_tab(self):
        w = _make_window(handle=100)
        adapter, computer = self._make_adapter([w], fg_hwnd=100)
        result = adapter.close_tab()
        assert result["ok"]
        assert ("ctrl", "w") in computer.hotkeys_pressed


# ─── Open Chrome focuses existing ────────────────────────────────────


class TestOpenChromeFocusesExisting:
    """Validates requirement 3: 'open Chrome' should focus existing, not launch new."""

    def test_focus_existing_chrome_via_computer_controller(self):
        """Simulate open_application('chrome') with existing Chrome running."""
        from src.agent.computer.windows import WindowsComputerPlatform

        w = _make_window(handle=100, title="Gmail - Google Chrome", pid=1234)
        platform = WindowsComputerPlatform.__new__(WindowsComputerPlatform)

        # Mock the methods
        platform.list_windows = MagicMock(return_value=[w])
        platform.get_active_window = MagicMock(return_value=w)
        platform.focus_window = MagicMock(return_value=True)

        result = platform.open_application("chrome")
        assert result["method"] == "focused_existing"
        assert result["new_process"] is False
        assert result["hwnd"] == 100
        platform.focus_window.assert_called_once_with(100)


# ─── Multi-monitor targeting ─────────────────────────────────────────


class TestMultiMonitorTargeting:
    def test_chrome_on_secondary_monitor(self):
        w = _make_window(
            handle=100,
            rect=(1920, 0, 3840, 1080),
            monitor_index=1,
        )
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)
        fg = resolver.find_foreground_browser()
        assert fg is not None
        assert fg.monitor_index == 1

    def test_chrome_on_left_monitor_negative_coords(self):
        w = _make_window(
            handle=100,
            rect=(-1920, 0, 0, 1080),
            monitor_index=1,
        )
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)
        fg = resolver.find_foreground_browser()
        assert fg is not None
        assert fg.bounds[0] == -1920

    def test_two_monitors_focus_follows(self):
        """Chrome on both monitors, focus follows the foreground window."""
        chrome_mon1 = _make_window(handle=100, title="Gmail - Google Chrome", pid=1000, monitor_index=0)
        chrome_mon2 = _make_window(handle=200, title="YouTube - Google Chrome", pid=2000, monitor_index=1)
        computer = FakeComputer([chrome_mon1, chrome_mon2], fg_hwnd=200)
        resolver = BrowserTargetResolver(computer=computer)

        # YouTube is foreground on monitor 2
        state = cheapest_web_state(None, resolver=resolver)
        assert state["browser_hwnd"] == 200
        assert state["title"] == "YouTube"

        # Switch to Gmail on monitor 1
        computer._fg_hwnd = 100
        state2 = cheapest_web_state(None, resolver=resolver)
        assert state2["browser_hwnd"] == 100
        assert state2["title"] == "Gmail"

    def test_window_moved_between_monitors(self):
        """Window moved from monitor 2 to monitor 1."""
        w = _make_window(
            handle=100,
            rect=(1920, 0, 3840, 1080),
            monitor_index=1,
        )
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)

        fg = resolver.find_foreground_browser()
        assert fg.monitor_index == 1

        # Simulate move to monitor 0
        w_moved = _make_window(
            handle=100,
            rect=(0, 0, 1920, 1080),
            monitor_index=0,
        )
        computer._windows = [w_moved]

        fg2 = resolver.find_foreground_browser()
        assert fg2.monitor_index == 0


# ─── Integration: router + orchestrator ──────────────────────────────


class TestRouterBrowserIntent:
    def test_what_page_routes_to_current_page(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("What page am I on?")
        assert intent is not None
        assert intent.action == "browser.current_page"

    def test_on_google_chrome_is_browser_filter(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("What page am I on on google chrome")
        assert intent is not None
        assert intent.action == "browser.current_page"
        assert intent.args.get("preferred_browser") == "chrome"

    def test_how_many_monitors_is_fast_path(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        for q in (
            "How many monitors do I have",
            "how many screens do I have",
            "how many displays do I have?",
        ):
            intent = router.route(q)
            assert intent is not None, q
            assert intent.action == "screen.monitor_count"

    def test_hud_answers_monitor_count(self, tmp_path):
        from src.ui.dashboard_data import DailyTaskStore, hud_spoken_reply

        store = DailyTaskStore(tmp_path / "daily_tasks.json")
        msg = hud_spoken_reply("How many monitors do I have?", store)
        assert msg is not None
        assert "monitor" in msg.lower()
        assert "planner" not in msg.lower()

    def test_open_chrome_routes_to_browser_open(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("Open Chrome")
        assert intent is not None
        assert intent.action == "browser.open"

    def test_go_to_google_routes_to_open_and_goto(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("Go to Google")
        assert intent is not None
        assert intent.action == "browser.open_and_goto"

    def test_switch_back_to_chatgpt(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("Switch back to the ChatGPT tab")
        assert intent is not None
        assert intent.action == "browser.switch_tab"

    def test_open_another_tab(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("Open a new tab")
        assert intent is not None
        assert intent.action == "browser.new_tab"

    def test_close_this_tab_and_next_tab(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        assert router.route("Close this tab").action == "browser.close_tab"
        assert router.route("Switch to the next tab").action == "browser.next_tab"

    def test_open_new_tab_and_go_to_google(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("Open a new tab and go to Google.")
        assert intent is not None
        assert intent.action == "browser.new_tab"
        assert "google.com" in (intent.args.get("url") or "")

    def test_now_go_to_chatgpt_and_stt_variants(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        for spoken, action in (
            ("Now go to ChatGPT.", "browser.open_and_goto"),
            ("now go to chatgbt", "browser.open_and_goto"),
            ("go to chat gbt", "browser.open_and_goto"),
            ("take me to ChatGPT", "browser.open_and_goto"),
            ("show me ChatGPT", "browser.open_and_goto"),
            ("open ChatGPT", "browser.open_and_goto"),
            ("okay now open Gmail", "browser.open_and_goto"),
            ("switch to ChatGPT", "browser.switch_tab"),
            ("Now go back to Google.", "browser.switch_tab"),
            ("then go back to Google", "browser.switch_tab"),
        ):
            intent = router.route(spoken)
            assert intent is not None, spoken
            assert intent.action == action, (spoken, intent.action)
            url = (intent.args.get("url") or "").lower()
            if "gmail" in spoken.lower():
                assert "mail.google.com" in url
            elif "google" in spoken.lower():
                assert "google.com" in url
            else:
                assert "chatgpt.com" in url
                assert "chatgbt.com" not in url

    def test_chatgbt_without_nav_is_not_a_browser_command(self):
        from src.agent.router import FastCommandRouter

        assert FastCommandRouter().route("chatgbt") is None

    def test_now_go_to_chatgbt_is_not_silent(self, tmp_path):
        from src.agent.audit import AuditLog
        from src.agent.emergency import EmergencyStop
        from src.agent.local_intent import reset_nlu_context
        from src.agent.orchestrator import AgentOrchestrator
        from src.agent.permissions import AutonomyMode, PermissionEngine
        from src.agent.task_store import TaskStore
        from src.agent.tools.base import ToolRegistry
        from tests.test_agent_phase1 import MockProvider
        from src.agent.providers.base import ProviderResponse

        reset_nlu_context()

        class _Resolver:
            def should_launch_new(self, _url: str) -> bool:
                return False

        class _Adapter:
            def go_to_site(self, site="", url="", query=""):
                assert "chatgpt.com" in (url or "")
                return {
                    "ok": True,
                    "message": "Switched to ChatGPT.",
                    "existing_tab_found": True,
                    "hwnd": 197160,
                    "source": "EXISTING_DESKTOP",
                    "verification_result": "PASS",
                    "site": "ChatGPT",
                    "selected_tab": "AI Desktop Agent Build",
                    "planner_calls": 0,
                }

        orch = AgentOrchestrator(
            MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")]),
            ToolRegistry(
                PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True),
                AuditLog(),
                EmergencyStop(),
            ),
            TaskStore(tmp_path / "tasks.db"),
            PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True),
            emergency_stop=EmergencyStop(),
            hud_tasks_path=str(tmp_path / "daily_tasks.json"),
        )
        orch.existing_browser = _Adapter()
        orch.browser_resolver = _Resolver()
        result = orch.handle_user_message("now go to chatgbt")
        assert result.get("planner_admitted") is False
        assert (result.get("message") or "").strip() == "Switched to ChatGPT."
        assert result.get("ok") is True


# ─── No silent Playwright launch ─────────────────────────────────────


class TestNoSilentPlaywrightLaunch:
    """Core requirement: JARVIS must never silently launch Playwright for
    contextual commands about the user's current browser."""

    def test_page_query_does_not_touch_playwright(self):
        """'What page am I on?' uses existing Chrome, no Playwright calls."""
        w = _make_window(handle=100, title="ChatGPT - Google Chrome", pid=1234)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)

        mock_agent = MagicMock()
        state = cheapest_web_state(mock_agent, resolver=resolver)
        assert state["browser_target_source"] == "EXISTING_DESKTOP"
        mock_agent.get_session_state.assert_not_called()
        mock_agent.open.assert_not_called()

    def test_about_blank_is_not_returned_when_chrome_exists(self):
        """If a real Chrome is open, we must never return about:blank."""
        w = _make_window(handle=100, title="GitHub - Google Chrome", pid=1234)
        computer = FakeComputer([w], fg_hwnd=100)
        resolver = BrowserTargetResolver(computer=computer)

        mock_agent = MagicMock()
        mock_agent.get_session_state.return_value = {
            "open": True, "url": "about:blank", "title": ""
        }

        state = cheapest_web_state(mock_agent, resolver=resolver)
        assert state.get("url", "") != "about:blank"
        assert state["title"] == "GitHub"


# ─── BrowserWindowInfo dataclass ──────────────────────────────────────


class TestBrowserWindowInfo:
    def test_defaults(self):
        bwi = BrowserWindowInfo()
        assert bwi.hwnd == 0
        assert bwi.source == "EXISTING_DESKTOP"
        assert not bwi.playwright_owned

    def test_managed_source(self):
        bwi = BrowserWindowInfo(source="MANAGED_PLAYWRIGHT", playwright_owned=True)
        assert bwi.source == "MANAGED_PLAYWRIGHT"
        assert bwi.playwright_owned


# ─── WindowInfo monitor_index ────────────────────────────────────────


class TestWindowInfoMonitor:
    def test_monitor_index_defaults_to_negative(self):
        w = WindowInfo(handle=1, title="Test")
        assert w.monitor_index == -1

    def test_monitor_index_set(self):
        w = WindowInfo(handle=1, title="Test", monitor_index=2)
        assert w.monitor_index == 2


class DictComputer:
    """Mirrors ComputerController.list_windows() dict shape — the live bug."""

    def __init__(self, windows, fg_hwnd=0):
        self._windows = windows
        self._fg_hwnd = fg_hwnd

    def list_windows(self):
        return list(self._windows)

    def get_active_window(self):
        for w in self._windows:
            if w.get("handle") == self._fg_hwnd:
                return w
        return {
            "handle": self._fg_hwnd,
            "title": "J.A.R.V.I.S. OS",
            "process_id": 1,
            "process_name": "python.exe",
            "rect": {"left": 0, "top": 0, "right": 800, "bottom": 600},
            "is_visible": True,
            "is_minimized": False,
        }


class TestComputerControllerDictShape:
    def test_dicts_are_not_treated_as_no_browser(self):
        chrome = {
            "handle": 197160,
            "title": "ChatGPT - Google Chrome",
            "process_id": 19348,
            "process_name": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "rect": {"left": -1928, "top": 252, "right": 8, "bottom": 1300},
            "is_visible": True,
            "is_minimized": False,
            "monitor_index": 1,
        }
        computer = DictComputer([chrome], fg_hwnd=999)
        resolver = BrowserTargetResolver(computer=computer)
        state = cheapest_web_state(None, resolver=resolver)
        assert state["open"] is True
        assert state["browser_target_source"] == "EXISTING_DESKTOP"
        assert state["browser_hwnd"] == 197160
        assert state["title"] == "ChatGPT"
        assert state["discovery_status"] != "NO_BROWSER_WINDOW"

    def test_jarvis_foreground_keeps_last_chrome(self):
        chrome = {
            "handle": 200,
            "title": "ChatGPT - Google Chrome",
            "process_id": 10,
            "process_name": "chrome.exe",
            "rect": {"left": -1920, "top": 0, "right": 0, "bottom": 1080},
            "is_visible": True,
            "is_minimized": False,
            "monitor_index": 1,
        }
        jarvis = {
            "handle": 1,
            "title": "J.A.R.V.I.S. OS",
            "process_id": 2,
            "process_name": "python.exe",
            "rect": {"left": 0, "top": 0, "right": 800, "bottom": 600},
            "is_visible": True,
            "is_minimized": False,
            "monitor_index": 0,
        }
        computer = DictComputer([jarvis, chrome], fg_hwnd=1)
        resolver = BrowserTargetResolver(computer=computer)
        target = resolver.resolve_target(log=False)
        assert target is not None
        assert target.hwnd == 200
        assert target.selection_reason in {
            "LAST_USER_ACTIVE_BROWSER",
            "MOST_RECENTLY_ACTIVE",
            "VISIBLE_CHROME",
            "FOREGROUND_CHROME",
        }


class TestHwndSelectionPriority:
    def test_last_user_beats_other_visible_chrome(self):
        from src.agent.browser.discovery import BrowserWindowRef, select_browser

        window_a = BrowserWindowRef(
            hwnd=100, pid=1, process_name="chrome.exe", exe_path="chrome.exe",
            title="AI Desktop Agent Build - Google Chrome", visible=True, monitor_index=0,
            last_foreground_at=1.0,
        )
        window_b = BrowserWindowRef(
            hwnd=200, pid=2, process_name="chrome.exe", exe_path="chrome.exe",
            title="ChatGPT - Google Chrome", visible=True, monitor_index=1,
            last_foreground_at=9.0,
        )
        picked, reason = select_browser(
            [window_a, window_b],
            foreground_hwnd=1,
            last_user_hwnd=200,
        )
        assert picked is not None
        assert picked.hwnd == 200
        assert reason == "LAST_USER_ACTIVE_BROWSER"

    def test_most_recent_when_no_last_user(self):
        from src.agent.browser.discovery import BrowserWindowRef, select_browser

        older = BrowserWindowRef(
            hwnd=100, pid=1, process_name="chrome.exe", exe_path="chrome.exe",
            title="AI Desktop Agent Build - Google Chrome", visible=True,
            last_foreground_at=1.0,
        )
        newer = BrowserWindowRef(
            hwnd=200, pid=2, process_name="chrome.exe", exe_path="chrome.exe",
            title="ChatGPT - Google Chrome", visible=True,
            last_foreground_at=9.0,
        )
        picked, reason = select_browser(
            [older, newer],
            foreground_hwnd=1,
            last_user_hwnd=0,
        )
        assert picked is not None
        assert picked.hwnd == 200
        assert reason == "MOST_RECENTLY_ACTIVE"

    def test_google_chrome_is_browser_filter_not_page_search(self):
        from src.agent.browser.discovery import parse_preferred_browser

        assert parse_preferred_browser("What page am I on on google chrome") == "chrome"
        assert parse_preferred_browser("what page am I on in Chrome") == "chrome"

    def test_preferred_chrome_ignores_edge(self):
        from src.agent.browser.discovery import BrowserWindowRef, select_browser

        edge = BrowserWindowRef(
            hwnd=50, pid=3, process_name="msedge.exe", exe_path="msedge.exe",
            title="Bing - Microsoft Edge", visible=True, is_foreground=True,
        )
        chrome = BrowserWindowRef(
            hwnd=200, pid=2, process_name="chrome.exe", exe_path="chrome.exe",
            title="ChatGPT - Google Chrome", visible=True,
        )
        picked, reason = select_browser(
            [edge, chrome],
            foreground_hwnd=50,
            last_user_hwnd=0,
            preferred_browser="chrome",
        )
        assert picked is not None
        assert picked.hwnd == 200


class TestCurrentPageCopy:
    def test_title_without_url_is_not_no_browser(self):
        from src.agent.respond import format_current_page

        msg = format_current_page({
            "open": True,
            "title": "ChatGPT",
            "url": "",
            "discovery_status": "BROWSER_FOUND_URL_UNAVAILABLE",
        })
        assert msg == "You're on ChatGPT in Chrome."
        assert "no browser" not in msg.lower()

    def test_chatgpt_url_wins_over_conversation_title(self):
        from src.agent.respond import format_current_page

        msg = format_current_page({
            "open": True,
            "title": "AI Desktop Agent Build",
            "url": "chatgpt.com/c/6a7cf04a-0814-83e8-a15f-e8df67b2efc7",
            "discovery_status": "BROWSER_FOUND",
        })
        assert msg == "You're on ChatGPT in Chrome."
        assert "AI Desktop Agent Build" not in msg

    def test_coursera_url_wins_over_course_title(self):
        from src.agent.respond import format_current_page

        msg = format_current_page({
            "open": True,
            "title": "AI Agent Developer | Coursera",
            "url": "coursera.org/specializations/ai-agents",
            "discovery_status": "BROWSER_FOUND",
        })
        assert msg == "You're on Coursera in Chrome."
        assert "AI Agent Developer" not in msg

        msg2 = format_current_page({
            "open": True,
            "title": "IBM RAG and Agentic AI Professional Certificate | Coursera",
            "url": "coursera.org/professional-certificates/ibm-rag-and-agentic-ai#testimonials",
            "discovery_status": "BROWSER_FOUND",
        })
        assert msg2 == "You're on Coursera in Chrome."

    def test_whats_the_website_is_fast_path(self):
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        for q in (
            "whats the website",
            "what's the website?",
            "what is the website",
            "What\u2019s the website",
        ):
            intent = router.route(q)
            assert intent is not None, q
            assert intent.action == "browser.current_page"

    def test_whats_the_page_saying_is_about_not_site(self):
        from src.agent.observe import remember_page_state
        from src.agent.respond import format_page_about
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        for q in ("whats the page saying", "what is the page saying", "what does the page say"):
            intent = router.route(q)
            assert intent is not None, q
            assert intent.action == "browser.page_about"

        assert router.route("What page am I on?").action == "browser.current_page"

        coursera = {
            "open": True,
            "title": "IBM Generative AI Engineering Professional Certificate | Coursera",
            "url": "coursera.org/professional-certificates/ibm-generative-ai-engineering",
            "discovery_status": "BROWSER_FOUND",
        }
        remember_page_state(coursera)
        msg = format_page_about(state=coursera)
        assert msg == "The page is about IBM Generative AI Engineering Professional Certificate."
        assert "ChatGPT" not in msg

    def test_what_repository_is_this_is_fast_path(self):
        from src.agent.respond import format_page_entity
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        intent = router.route("What repository is this?")
        assert intent is not None
        assert intent.action == "browser.page_entity"
        assert intent.args.get("kind") == "repository"

        msg = format_page_entity(
            {
                "open": True,
                "title": "herbosexton/inventory-manager",
                "url": "github.com/herbosexton/inventory-manager",
                "discovery_status": "BROWSER_FOUND",
            },
            kind="repository",
        )
        assert msg == "The repository is herbosexton/inventory-manager."

    def test_what_video_is_this_uses_youtube_title(self):
        from src.agent.respond import format_page_entity
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        for q in ("What video is this?", "what is this video", "what am I watching"):
            intent = router.route(q)
            assert intent is not None, q
            assert intent.action == "browser.page_entity"
            assert intent.args.get("kind") == "video"

        msg = format_page_entity(
            {
                "open": True,
                "title": "FIRST TAKE | NYG gonna win NFC East with HC Harbaugh - Stephen A. Smith on Giants vs Rams Week 2 - YouTube",
                "url": "https://www.youtube.com/watch?v=abc123",
                "discovery_status": "BROWSER_FOUND",
            },
            kind="video",
        )
        assert msg.startswith("The video is FIRST TAKE")
        assert "YouTube" not in msg.replace("The video is ", "")
        assert "planner" not in msg.lower()

    def test_whats_the_course_is_fast_path(self):
        from src.agent.respond import format_page_entity
        from src.agent.router import FastCommandRouter

        router = FastCommandRouter()
        for q in ("whats the course?", "what is the course", "What course is this"):
            intent = router.route(q)
            assert intent is not None, q
            assert intent.action == "browser.page_entity"
            assert intent.args.get("kind") == "course"

        msg = format_page_entity({
            "open": True,
            "title": "IBM Generative AI Engineering Professional Certificate | Coursera",
            "url": "coursera.org/professional-certificates/ibm-generative-ai-engineering",
            "discovery_status": "BROWSER_FOUND",
        })
        assert msg == "The course is IBM Generative AI Engineering Professional Certificate."
        assert "planner" not in msg.lower()

    def test_hud_answers_curly_website(self, tmp_path):
        from src.ui.dashboard_data import DailyTaskStore, hud_spoken_reply

        store = DailyTaskStore(tmp_path / "daily_tasks.json")
        msg = hud_spoken_reply("What\u2019s the website", store)
        assert msg is not None
        assert "planner" not in msg.lower()
        assert "you're on" in msg.lower()

    def test_zero_candidates_says_no_browser(self):
        from src.agent.respond import format_current_page

        msg = format_current_page({
            "open": False,
            "discovery_status": "NO_BROWSER_WINDOW",
        })
        assert msg == "No browser is open."


class TestActiveTabResolver:
    def test_ignores_first_tab_if_not_selected(self):
        from src.agent.browser.tabs import ActiveTabResolver

        class FakePat:
            def __init__(self, selected):
                self.IsSelected = selected

        class FakeTab:
            def __init__(self, name, selected):
                self.ControlTypeName = "TabItem"
                self.Name = name
                self._selected = selected

            def GetSelectionItemPattern(self):
                return FakePat(self._selected)

            def GetChildren(self):
                return []

        class FakeRoot:
            ControlTypeName = "Window"
            Name = "Chrome"

            def GetChildren(self):
                return [
                    FakeTab("AI Desktop Agent Build", False),
                    FakeTab("Google", False),
                    FakeTab("ChatGPT", True),
                ]

        resolver = ActiveTabResolver()
        title = resolver._walk_selected_tab(FakeRoot(), 0, 8, deadline=0.0)
        assert title == "ChatGPT"

    def test_window_title_is_fallback(self):
        from src.agent.browser.tabs import ActiveTabResolver

        page = ActiveTabResolver().resolve(0, window_title="ChatGPT - Google Chrome")
        assert page.active_tab_title == "ChatGPT"
        assert page.title_source == "WINDOW_TITLE_FALLBACK"
