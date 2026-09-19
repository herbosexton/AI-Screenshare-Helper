"""ActionVerifier — tool success is not goal success. Cheapest reliable tier first."""

from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

from src.agent.phase6.models import AgentStep, TaskContext, Verification


# Long enough for a document viewer to put up a window, short enough that a launch
# that genuinely failed does not stall the task.
WINDOW_APPEAR_TIMEOUT_S = 4.0
WINDOW_POLL_INTERVAL_S = 0.25

_NAV_TOOLS = {"browser.goto", "browser.navigate", "browser.open_and_goto"}
_TAB_TOOLS = {"browser.switchTab", "browser.findTab", "browser.new_tab"}
_APP_TOOLS = {"computer.open_application", "computer.focus_window", "computer.open_file"}
_FILE_OPEN_TOOLS = {"files.open"}
_UI_ACTION_TOOLS = {
    "browser.click",
    "computer.click",
    "computer.double_click",
    "computer.press_key",
    "computer.type_text",
    "browser.fill",
    "browser.type",
}
# These act on whatever is under the pointer or has focus. They cannot fail on a missing
# target, so they need observable evidence that something actually happened.
_BLIND_UI_TOOLS = {
    "computer.click",
    "computer.double_click",
    "computer.right_click",
    "computer.press_key",
    "computer.type_text",
}
# Reads are self-verifying: the payload is the result.
_READ_TOOLS_PREFIX = ("files.find", "files.search", "files.read", "files.extract_text",
                      "browser.get", "browser.snapshot", "browser.listTabs", "browser.status",
                      "computer.get", "computer.list_windows", "files.list", "files.get_metadata")
# Reads whose whole point is to return candidates.
_SEARCH_TOOLS_PREFIX = ("files.find", "files.search", "browser.findElement", "browser.findTab")


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").replace("www.", "").lower()
    except ValueError:
        return ""


class ActionVerifier:
    """Verification tiers: tool result → app/browser state → structured UI → Phase 3 vision."""

    def __init__(self, *, browser_getter=None, computer_getter=None, screen_getter=None):
        # Getters, because the browser/computer/screen services attach after construction.
        self._browser_getter = browser_getter
        self._computer_getter = computer_getter
        self._screen_getter = screen_getter

    @property
    def _computer(self):
        return self._computer_getter() if callable(self._computer_getter) else None

    @property
    def _screen(self):
        return self._screen_getter() if callable(self._screen_getter) else None

    def verify(
        self,
        step: AgentStep,
        result_data: Any,
        context: TaskContext,
        *,
        allow_vision: bool = False,
    ) -> Verification:
        tool = step.preferred_tool or ""
        if not tool:
            return Verification(verified=True, method="no_tool", detail="Reasoning step.")

        if tool.startswith(_READ_TOOLS_PREFIX):
            return self._verify_read(tool, result_data)
        if tool in _NAV_TOOLS:
            return self._verify_navigation(step, result_data, context)
        if tool in _TAB_TOOLS:
            return self._verify_tab(step, result_data, context)
        if tool in _APP_TOOLS or tool in _FILE_OPEN_TOOLS:
            return self._verify_window(step, result_data, context, allow_vision=allow_vision)
        if tool in _UI_ACTION_TOOLS:
            return self._verify_ui_action(step, result_data, context, allow_vision=allow_vision)

        return Verification(
            verified=True,
            method="tool_result",
            detail=f"{tool} reported success.",
        )

    # --- tiers -------------------------------------------------------------

    def _verify_read(self, tool: str, data: Any) -> Verification:
        empty = data is None or (isinstance(data, (list, dict, str)) and not data)
        if not empty and tool.startswith(_SEARCH_TOOLS_PREFIX):
            # A search that matched nothing is a successful call and a failed step: the
            # steps that follow it were written expecting something to be found.
            empty = not any(
                isinstance(data.get(key), list) and data[key]
                for key in ("matches", "files", "results", "tabs", "elements", "windows")
            ) if isinstance(data, dict) else not data
        if empty:
            return Verification(
                verified=False,
                method="tool_result",
                detail=f"{tool} returned nothing.",
            )
        return Verification(verified=True, method="tool_result", detail=f"{tool} returned data.")

    def _verify_navigation(self, step: AgentStep, data: Any, context: TaskContext) -> Verification:
        expected = str(step.tool_arguments.get("url") or "")
        actual = ""
        agent = self._browser_agent()
        if agent is not None:
            actual = str(getattr(agent, "url", "") or "")
        if not actual and isinstance(data, dict):
            actual = str(data.get("url") or "")
        if actual:
            context.current_url = actual
            context.current_page = str(
                (getattr(agent, "title", "") if agent is not None else "")
                or (data.get("title") if isinstance(data, dict) else "")
                or ""
            )
        if not expected:
            return Verification(
                verified=bool(actual),
                method="browser_state",
                detail="Navigation completed." if actual else "No active URL after navigation.",
                actual=actual,
            )
        ok = bool(actual) and (_host(expected) == _host(actual) or expected.rstrip("/") in actual)
        return Verification(
            verified=ok,
            method="browser_state",
            detail="URL matches request." if ok else "Active URL does not match request.",
            expected=expected,
            actual=actual,
        )

    def _verify_tab(self, step: AgentStep, data: Any, context: TaskContext) -> Verification:
        agent = self._browser_agent()
        actual = str(getattr(agent, "url", "") or "") if agent is not None else ""
        if not actual and isinstance(data, dict):
            actual = str(data.get("url") or "")
        query = str(step.tool_arguments.get("query") or step.tool_arguments.get("title") or "")
        if actual:
            context.current_url = actual
        if not query:
            return Verification(
                verified=bool(actual) or data is not None,
                method="browser_state",
                detail="Tab operation completed.",
                actual=actual,
            )
        title = str(getattr(agent, "title", "") or "") if agent is not None else ""
        haystack = f"{actual} {title}".lower()
        ok = query.lower() in haystack or _host(query) in haystack
        return Verification(
            verified=ok,
            method="browser_state",
            detail="Requested tab is active." if ok else "Requested tab is not active.",
            expected=query,
            actual=f"{title} {actual}".strip(),
        )

    def _verify_window(
        self,
        step: AgentStep,
        data: Any,
        context: TaskContext,
        *,
        allow_vision: bool,
    ) -> Verification:
        needle = str(
            step.tool_arguments.get("name")
            or step.tool_arguments.get("title_contains")
            or step.tool_arguments.get("path")
            or ""
        )
        needle = needle.replace("\\", "/").split("/")[-1]
        active = self._await_window(needle)
        if active:
            context.active_window = active
            context.active_application = active
        if not needle:
            return Verification(
                verified=bool(active) or bool(data),
                method="window_state",
                detail="Application state read.",
                actual=active,
            )
        stem = re.split(r"\.[A-Za-z0-9]{1,5}$", needle)[0].lower()
        ok = bool(active) and (stem in active.lower() or needle.lower() in active.lower())
        if not ok and allow_vision and self._screen is not None:
            try:
                hit = self._screen.verify_window_appeared(needle)
                if isinstance(hit, dict) and hit.get("verified"):
                    return Verification(
                        verified=True,
                        method="phase3_visual",
                        detail="Window confirmed visually.",
                        expected=needle,
                        actual=str(hit.get("title") or active),
                    )
            except Exception as e:
                return Verification(
                    verified=False,
                    method="phase3_visual",
                    detail=f"Visual verification failed: {e}",
                    expected=needle,
                    actual=active,
                )
        return Verification(
            verified=ok,
            method="window_state",
            detail="Target window is active." if ok else "Target window is not active.",
            expected=needle,
            actual=active,
        )

    def _verify_ui_action(
        self,
        step: AgentStep,
        data: Any,
        context: TaskContext,
        *,
        allow_vision: bool,
    ) -> Verification:
        before = context.expected_next_state
        agent = self._browser_agent()
        actual = str(getattr(agent, "url", "") or "") if agent is not None else ""
        changed = bool(actual) and actual != before
        if actual:
            context.current_url = actual
        if isinstance(data, dict) and data.get("changed") is True:
            changed = True
        if changed:
            return Verification(
                verified=True,
                method="browser_state",
                detail="Page state changed after the action.",
                expected=before,
                actual=actual,
            )
        if allow_vision and self._screen is not None:
            try:
                state = self._screen.get_state(include_screenshot=False, include_uia=False)
                return Verification(
                    verified=True,
                    method="phase3_visual",
                    detail=state.summary(),
                )
            except Exception as e:
                return Verification(verified=False, method="phase3_visual", detail=str(e))
        if step.preferred_tool in _BLIND_UI_TOOLS:
            # A coordinate click reports success wherever the pointer happens to be, so
            # "the tool did not error" says nothing about whether the action landed.
            return Verification(
                verified=False,
                method="tool_result",
                detail=f"{step.preferred_tool} reported success but nothing changed.",
                expected=before,
                actual=actual,
            )
        # Targeted actions resolve a named element first and fail when it is absent, so
        # success means a real control was operated even if the page looks the same.
        return Verification(
            verified=True,
            method="tool_result",
            detail="Target was resolved and the action reported success.",
            expected=before,
            actual=actual,
        )

    # --- helpers -----------------------------------------------------------

    def _browser_agent(self):
        session = self._browser_getter() if callable(self._browser_getter) else None
        return getattr(session, "agent", None) if session is not None else None

    def _await_window(self, needle: str) -> str:
        """Launching an app is asynchronous; give its window a moment to come up.

        Reading the foreground title the instant the launch call returns reports the
        window that was already there, which reads as a failure for a launch that is
        simply still in progress.
        """
        active = self._active_window_title()
        if not needle:
            return active
        stem = re.split(r"\.[A-Za-z0-9]{1,5}$", needle)[0].lower()
        deadline = time.monotonic() + WINDOW_APPEAR_TIMEOUT_S
        while time.monotonic() < deadline:
            low = active.lower()
            if stem in low or needle.lower() in low:
                return active
            time.sleep(WINDOW_POLL_INTERVAL_S)
            active = self._active_window_title()
        return active

    def _active_window_title(self) -> str:
        if self._computer is None:
            return ""
        try:
            win = self._computer.get_active_window()
        except Exception:
            return ""
        if isinstance(win, dict):
            return str(win.get("title") or "")
        return str(getattr(win, "title", "") or "")
