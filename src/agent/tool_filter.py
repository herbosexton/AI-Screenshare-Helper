"""Select only the tool categories needed for a planner request."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional


CATEGORIES: dict[str, set[str]] = {
    "core": {"agent.get_status"},
    "browser": {
        "browser.open",
        "browser.navigate",
        "browser.goto",
        "browser.status",
        "browser.snapshot",
        "browser.getVisibleText",
        "browser.get_text",
        "browser.click",
        "browser.fill",
        "browser.type",
        "browser.close",
        "browser.findElement",
        "browser.getPageState",
    },
    "browser_session": {
        "browser.back",
        "browser.forward",
        "browser.reload",
        "browser.listTabs",
        "browser.switchTab",
        "browser.findTab",
        "browser.select",
        "browser.check",
        "browser.new_tab",
    },
    "files": {
        "files.find_recent",
        "files.find_by_name",
        "files.search",
        "files.open",
    },
    "computer": {
        "computer.get_active_window",
        "computer.open_application",
        "computer.focus_window",
        "computer.close_window",
        "computer.click",
        "computer.scroll",
        "computer.type_text",
        "computer.press_key",
        "computer.verify_window",
    },
    "screen": {
        "computer.get_screen_state",
        "computer.verify_element",
    },
}

_BROWSER_RE = re.compile(
    r"\b(browser|chrome|edge|chromium|tab|page|url|website|site|http|www\.|"
    r"navigate|linkedin|gmail|youtube|dom|form|captcha|apply|login|job)\b",
    re.I,
)
_FILE_RE = re.compile(
    r"\b(file|document|folder|resume|pdf|download|docx|txt)\b",
    re.I,
)
_COMPUTER_RE = re.compile(
    r"\b(window|application|notepad|cursor|desktop|click|scroll|type|press|"
    r"button|hotkey|mouse)\b",
    re.I,
)
_SCREEN_RE = re.compile(
    r"\b(screen|screenshot|uia|on.?screen|what do you see)\b",
    re.I,
)


def classify_request(text: str) -> set[str]:
    t = text or ""
    cats: set[str] = {"core"}
    if _BROWSER_RE.search(t):
        cats.add("browser")
    if _FILE_RE.search(t):
        cats.add("files")
    if _COMPUTER_RE.search(t):
        cats.add("computer")
    if _SCREEN_RE.search(t):
        cats.add("screen")
    return cats


def selected_tool_names(
    text: str,
    available: Iterable[str],
    *,
    browser_open: bool = False,
) -> set[str]:
    cats = classify_request(text)
    names: set[str] = set()
    for cat in cats:
        names |= CATEGORIES.get(cat, set())
    if browser_open and "browser" in cats:
        names |= CATEGORIES["browser_session"]
    avail = set(available)
    return names & avail


def schema_token_estimate(tools: list[dict[str, Any]]) -> int:
    if not tools:
        return 0
    return max(1, len(json.dumps(tools)) // 4)


def filter_tools(
    registry,
    text: str,
    *,
    browser_open: bool = False,
    perf: Optional[Any] = None,
) -> list[dict[str, Any]]:
    available = [t.name for t in registry.list_tools()]
    before = len(available)
    names = selected_tool_names(text, available, browser_open=browser_open)
    filtered = registry.openai_tools(names)
    after = len(filtered)
    tokens = schema_token_estimate(filtered)
    cats = sorted(classify_request(text))
    print(
        f"[Perf] tools_before_filter={before} tools_after_filter={after} "
        f"tool_schema~{tokens} tok categories={','.join(cats)}"
    )
    if perf is not None:
        perf.set("tools_before_filter", before)
        perf.set("tools_after_filter", after)
        perf.set("tool_schema_tokens_est", tokens)
        perf.set("tool_categories", cats)
    return filtered
