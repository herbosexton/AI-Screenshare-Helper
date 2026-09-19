"""Tool metadata + category resolution. The planner never sees all 87 tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


BROWSER = "browser"
FILES = "files"
COMPUTER = "computer"
SCREEN = "screen"
CORE = "core"
TASK = "task"
OTHER = "other"

_PREFIX_CATEGORY = {
    "browser.": BROWSER,
    "files.": FILES,
    "computer.": COMPUTER,
    "screen.": SCREEN,
    "agent.": CORE,
    "speech.": CORE,
    "task.": TASK,
}

# computer.* tools that are really Phase 3 screen understanding
_SCREEN_TOOLS = {
    "computer.get_screen_state",
    "computer.verify_element",
    "computer.verify_window",
    "computer.get_screenshot",
}

_STATE_CHANGING = re.compile(
    r"\.(open|goto|navigate|click|type|fill|press|hotkey|select|check|uncheck|clear|"
    r"scroll|drag|close|move|rename|copy|delete|overwrite|create_directory|upload|"
    r"new_tab|close_tab|switchTab|back|forward|reload|focus_window|minimize_window|"
    r"maximize_window|close_window|open_application|open_file|set_clipboard|speak|index)",
    re.I,
)
_EXTERNAL_EFFECT = re.compile(
    r"\.(goto|navigate|open|uploadFile|delete|overwrite|move|rename|copy|speak)",
    re.I,
)

_CUES: list[tuple[str, re.Pattern[str]]] = [
    (
        BROWSER,
        re.compile(
            r"\b(browser|chrome|edge|chromium|tab|tabs|page|url|website|site|web|http|www\.|"
            r"navigate|google|linkedin|gmail|youtube|dom|form|captcha|apply|login|job|"
            r"posting|link|search for)\b",
            re.I,
        ),
    ),
    (
        FILES,
        re.compile(
            r"\b(file|files|document|documents|folder|resume|cv|pdf|download|downloads|"
            r"docx|txt|spreadsheet|xlsx|csv|save|saved|newest|latest|recent)\b",
            re.I,
        ),
    ),
    (
        COMPUTER,
        re.compile(
            r"\b(window|windows|application|app|notepad|cursor|explorer|outlook|desktop|"
            r"click|scroll|type|press|button|hotkey|mouse|keyboard|launch|open|switch|"
            r"next to|side by side|minimize|maximize)\b",
            re.I,
        ),
    ),
    (
        SCREEN,
        re.compile(
            r"\b(screen|screenshot|see|look|visible|on.?screen|dialog|error message|uia)\b",
            re.I,
        ),
    ),
]


# The tools worth planning with. Everything else in a category is a refinement the
# executor and recovery ladder reach for; putting all 87 in the prompt makes the plan
# worse, not better. A tool named in the goal is added back in `select`.
_PLANNER_CORE: dict[str, set[str]] = {
    BROWSER: {
        "browser.open", "browser.goto", "browser.navigate", "browser.back", "browser.forward",
        "browser.reload", "browser.getPageState", "browser.getVisibleText", "browser.getCurrentUrl",
        "browser.listTabs", "browser.switchTab", "browser.findTab", "browser.new_tab",
        "browser.findElement", "browser.click", "browser.fill", "browser.scroll",
        "browser.uploadFile", "browser.status",
    },
    FILES: {
        "files.find_recent", "files.find_by_name", "files.search", "files.read",
        "files.extract_text", "files.open", "files.list", "files.get_metadata",
    },
    # computer.click / move_mouse / drag / scroll take raw screen coordinates, which a
    # planner cannot know. They are reached through the Phase 3 visual fallback, which
    # resolves coordinates first. Planning a bare click means clicking wherever the
    # pointer happens to sit, so they are not offered here.
    COMPUTER: {
        "computer.open_application", "computer.open_file", "computer.focus_window",
        "computer.list_windows", "computer.get_active_window",
        "computer.type_text", "computer.press_key", "computer.hotkey",
        "computer.minimize_window", "computer.maximize_window",
    },
    SCREEN: {"computer.get_screen_state", "computer.verify_window", "computer.verify_element"},
    CORE: {"agent.get_status"},
}


@dataclass
class ToolMeta:
    name: str
    category: str
    description: str = ""
    timeout_s: float = 30.0
    max_retries: int = 2
    state_changing: bool = False
    external_effect: bool = False
    future_permission_level: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "timeout": self.timeout_s,
            "retry_policy": {"max_retries": self.max_retries},
            "state_changing": self.state_changing,
            "external_effect": self.external_effect,
            "future_permission_level": self.future_permission_level,
        }


def category_for(name: str) -> str:
    if name in _SCREEN_TOOLS:
        return SCREEN
    for prefix, cat in _PREFIX_CATEGORY.items():
        if name.startswith(prefix):
            return cat
    # Unrecognized namespaces are never offered to the planner unasked.
    return OTHER


def is_state_changing(name: str) -> bool:
    """Whether the tool acts on the world rather than only reading it."""
    return bool(_STATE_CHANGING.search(name or ""))


# Tools that reach the same outcome by another route. This is an equivalence, not the
# recovery ladder's preference order: it answers "did anything actually do this?" when a
# replan retries a failed step with a different tool, so the task record can tell work
# that was rerouted from work that never happened.
INTERCHANGEABLE: dict[str, tuple[str, ...]] = {
    "files.open": ("computer.open_file",),
    "computer.open_file": ("files.open",),
    "computer.focus_window": ("computer.open_application",),
    "files.find_recent": ("files.find_by_name", "files.search"),
    "files.find_by_name": ("files.find_recent", "files.search"),
    "files.search": ("files.find_by_name", "files.find_recent"),
    "browser.goto": ("browser.navigate", "browser.open"),
    "browser.navigate": ("browser.goto", "browser.open"),
}


def accomplishes_same(tool: str, other: str) -> bool:
    """Whether `other` does the work of `tool` — the same tool, or a substitute for it."""
    if not tool or not other:
        return False
    return other == tool or other in INTERCHANGEABLE.get(tool, ())


def describe_tool(tool: Any) -> ToolMeta:
    name = getattr(tool, "name", "") or ""
    return ToolMeta(
        name=name,
        category=category_for(name),
        description=(getattr(tool, "description", "") or "")[:160],
        timeout_s=float(getattr(tool, "timeout_s", 30.0) or 30.0),
        max_retries=int(getattr(tool, "max_retries", 2) or 0),
        state_changing=bool(_STATE_CHANGING.search(name)),
        external_effect=bool(_EXTERNAL_EFFECT.search(name)),
        future_permission_level=int(getattr(tool, "permission_level", 1) or 1),
    )


@dataclass
class ToolSelection:
    categories: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    schemas: list[dict[str, Any]] = field(default_factory=list)
    tools_before_filter: int = 0
    tools_after_filter: int = 0
    schema_tokens_before: int = 0
    schema_tokens_after: int = 0


def schema_token_estimate(schemas: list[dict[str, Any]]) -> int:
    if not schemas:
        return 0
    return max(1, len(json.dumps(schemas)) // 4)


class ToolCategoryResolver:
    """Goal → relevant categories → filtered tool schemas."""

    def resolve_categories(self, goal: str, *, browser_open: bool = False) -> list[str]:
        text = goal or ""
        cats: set[str] = set()
        for cat, rx in _CUES:
            if rx.search(text):
                cats.add(cat)
        if browser_open:
            cats.add(BROWSER)
        if not cats:
            cats = {COMPUTER, SCREEN}
        # Screen is the fallback verification tier for any UI work.
        if BROWSER in cats or COMPUTER in cats:
            cats.add(SCREEN)
        cats.add(CORE)
        return sorted(cats)

    def select(
        self,
        registry,
        goal: str,
        *,
        browser_open: bool = False,
        extra_names: Optional[Iterable[str]] = None,
        perf: Optional[Any] = None,
    ) -> ToolSelection:
        all_tools = list(registry.list_tools())
        cats = set(self.resolve_categories(goal, browser_open=browser_open))
        lowered = (goal or "").lower()
        keep: set[str] = set()
        for tool in all_tools:
            cat = category_for(tool.name)
            if cat not in cats:
                continue
            core = _PLANNER_CORE.get(cat)
            # A goal may name a specific tool ("use browser.uploadFile"), but only the
            # qualified name counts: bare verbs like "click" or "open" appear in almost
            # every goal and would drag the excluded tools straight back in.
            named = tool.name.lower() in lowered
            # Unknown categories (test doubles, plugins) have no curated set; keep them all.
            if core is None or tool.name in core or named:
                keep.add(tool.name)
        keep |= {n for n in (extra_names or []) if registry.get(n) is not None}

        schemas = registry.openai_tools(keep)
        before_tokens = schema_token_estimate(registry.openai_tools())
        after_tokens = schema_token_estimate(schemas)
        sel = ToolSelection(
            categories=sorted(cats),
            names=sorted(keep),
            schemas=schemas,
            tools_before_filter=len(all_tools),
            tools_after_filter=len(schemas),
            schema_tokens_before=before_tokens,
            schema_tokens_after=after_tokens,
        )
        print(
            f"[Phase6] tools_before_filter={sel.tools_before_filter} "
            f"tools_after_filter={sel.tools_after_filter} "
            f"tool_schema_tokens_before={before_tokens} tool_schema_tokens_after={after_tokens} "
            f"categories={','.join(sel.categories)}"
        )
        if perf is not None:
            perf.set("tools_before_filter", sel.tools_before_filter)
            perf.set("tools_after_filter", sel.tools_after_filter)
            perf.set("tool_schema_tokens_before", before_tokens)
            perf.set("tool_schema_tokens_after", after_tokens)
            perf.set("tool_categories", sel.categories)
        return sel

    def catalog(self, registry) -> list[dict[str, Any]]:
        return [describe_tool(t).as_dict() for t in registry.list_tools()]
