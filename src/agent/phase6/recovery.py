"""Failure classification + bounded recovery. Retry, refresh, fall back, replan, then ask."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from src.agent.phase6.binding import relax_arguments, site_url
from src.agent.phase6.models import AgentStep, FailureCode, TaskContext


RETRY = "RETRY"
RELAX_AND_RETRY = "RELAX_AND_RETRY"
REFRESH_AND_RETRY = "REFRESH_AND_RETRY"
ALTERNATIVE_TOOL = "ALTERNATIVE_TOOL"
REPLAN = "REPLAN"
ASK_USER = "ASK_USER"
WAIT_FOR_USER = "WAIT_FOR_USER"
FAIL = "FAIL"

_PATTERNS: list[tuple[FailureCode, re.Pattern[str]]] = [
    # Argument problems are read first: a pydantic "Field required" message mentions
    # "field" and "missing" and would otherwise look like a missing page element.
    (FailureCode.PLAN_INVALIDATED, re.compile(r"invalid arguments for|validation error for", re.I)),
    # A rejected URL is the plan's fault and identical on every retry: rewrite, never repeat.
    (
        FailureCode.PLAN_INVALIDATED,
        re.compile(r"url (?:must include|is empty|is missing)|blocked url scheme|not in the allowlist", re.I),
    ),
    (FailureCode.CAPTCHA_REQUIRED, re.compile(r"captcha", re.I)),
    (FailureCode.AUTHENTICATION_REQUIRED, re.compile(r"\b(login|log in|sign in|auth\w*|credential)", re.I)),
    (FailureCode.TOOL_TIMEOUT, re.compile(r"\btimed? ?out\b|\btimeout\b", re.I)),
    (FailureCode.NETWORK_ERROR, re.compile(r"\b(network|dns|connection refused|unreachable|offline)\b", re.I)),
    (FailureCode.FILE_NOT_FOUND, re.compile(r"\b(file not found|no such file|no matching file|not found on disk)\b", re.I)),
    (FailureCode.TAB_NOT_FOUND, re.compile(r"\btab\b.*\b(not found|no match)\b|\bno such tab\b", re.I)),
    (FailureCode.WINDOW_NOT_FOUND, re.compile(r"\bwindow\b.*\b(not found|missing)\b", re.I)),
    (FailureCode.ELEMENT_AMBIGUOUS, re.compile(r"\b(ambiguous|more than one match|multiple matches)\b", re.I)),
    (FailureCode.ELEMENT_NOT_FOUND, re.compile(r"\b(element|selector|button|link|field)\b.*\b(not found|missing|no match)\b", re.I)),
    (FailureCode.PERMISSION_DENIED, re.compile(r"\b(denied|not permitted|blocked by policy|not allowed)\b", re.I)),
    (FailureCode.PAGE_CHANGED, re.compile(r"\b(page changed|navigation occurred|detached|stale element)\b", re.I)),
    (FailureCode.SCREEN_STATE_STALE, re.compile(r"\bstale (screen|state)\b", re.I)),
    (FailureCode.TOOL_UNAVAILABLE, re.compile(r"\bunknown tool\b|\btool unavailable\b|\bnot available\b", re.I)),
    (FailureCode.USER_INPUT_REQUIRED, re.compile(r"\b(user input required|please provide|需要)\b", re.I)),
]

# Non-retryable: retrying cannot change the outcome.
_TERMINAL = {
    FailureCode.PERMISSION_DENIED,
    FailureCode.TOOL_UNAVAILABLE,
    FailureCode.OPENED_ARTIFACT_UNEXPECTEDLY_CLOSED,
    FailureCode.MISSING_REQUIRED_TASK_OUTPUT,
}

# Genuine substitutes only. Diagnostic reads belong in _REFRESH_TOOLS, not here:
# an alternative must be able to accomplish the step, not just describe why it failed.
# browser.click already falls back to Phase 3 visual clicking internally, which resolves
# coordinates first. computer.click does not: it fires wherever the pointer sits, so it is
# not a substitute for a click whose target could not be found.
_ALTERNATIVES: dict[str, list[str]] = {
    "computer.focus_window": ["computer.open_application"],
    "files.open": ["computer.open_file"],
    "files.find_by_name": ["files.search", "files.find_recent"],
    "files.search": ["files.find_by_name", "files.find_recent"],
}

# A website is not a desktop application. Told to "switch back to ChatGPT", a planner reaches
# for focus_window, no such window exists, and looking harder on the desktop will never find
# one — the page is a tab. Try the browser before anything tries to launch a program.
_DESKTOP_TOOLS = ("computer.focus_window", "computer.open_application")
_SITE_ALTERNATIVES = ["browser.switchTab", "browser.navigate"]


def site_target(step: AgentStep) -> str:
    """The address of a well-known site the step was aiming at, or "" if it named none."""
    for value in (step.tool_arguments or {}).values():
        found = site_url(value) if isinstance(value, str) else ""
        if found:
            return found
    return ""

_REFRESH_TOOLS: dict[FailureCode, str] = {
    FailureCode.ELEMENT_NOT_FOUND: "browser.getPageState",
    FailureCode.PAGE_CHANGED: "browser.getPageState",
    FailureCode.SCREEN_STATE_STALE: "computer.get_screen_state",
    FailureCode.TAB_NOT_FOUND: "browser.listTabs",
    FailureCode.WINDOW_NOT_FOUND: "computer.list_windows",
}


def classify_failure(error: Any, data: Any = None) -> FailureCode:
    """Normalize any tool failure into a FailureCode."""
    code = _code_from_payload(data)
    if code is not None:
        return code
    text = str(error or "")
    if not text:
        return FailureCode.UNKNOWN
    for failure, rx in _PATTERNS:
        if rx.search(text):
            return failure
    return FailureCode.UNKNOWN


def _code_from_payload(data: Any) -> Optional[FailureCode]:
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    raw = err.get("code") if isinstance(err, dict) else data.get("code")
    if not raw:
        return None
    name = str(raw).upper()
    try:
        return FailureCode(name)
    except ValueError:
        return None


@dataclass
class RecoveryAction:
    action: str = FAIL
    reason: str = ""
    failure_code: FailureCode = FailureCode.UNKNOWN
    refresh_tool: str = ""
    alternative_tool: str = ""
    user_message: str = ""


class FailureRecoveryEngine:
    """Bounded ladder: refresh → retry → alternative → replan → ask the user."""

    def __init__(self, registry, *, max_attempts: int = 2):
        self.registry = registry
        self.max_attempts = max_attempts

    def decide(
        self,
        step: AgentStep,
        failure_code: FailureCode,
        context: TaskContext,
        *,
        replans_left: int = 0,
        tried_tools: Optional[set[str]] = None,
        goal: str = "",
    ) -> RecoveryAction:
        if failure_code == FailureCode.CAPTCHA_REQUIRED:
            return RecoveryAction(
                action=WAIT_FOR_USER,
                reason="captcha",
                failure_code=failure_code,
                user_message=(
                    "A CAPTCHA is showing in the browser. Please complete it, then say continue. "
                    "I will not bypass it."
                ),
            )
        if failure_code == FailureCode.AUTHENTICATION_REQUIRED:
            return RecoveryAction(
                action=WAIT_FOR_USER,
                reason="authentication",
                failure_code=failure_code,
                user_message=(
                    "A sign-in page is showing. Please log in, then say continue and I will "
                    "pick up where I left off."
                ),
            )
        if failure_code == FailureCode.USER_INPUT_REQUIRED:
            return RecoveryAction(
                action=ASK_USER,
                reason="user_input_required",
                failure_code=failure_code,
                user_message="I need one more detail before I can continue.",
            )
        if failure_code in _TERMINAL:
            return RecoveryAction(action=FAIL, reason="not_retryable", failure_code=failure_code)

        exhausted = step.attempt_count >= max(1, min(step.max_attempts, self.max_attempts))

        if not exhausted:
            # A search that matched nothing is worth one more try without the filters the
            # planner chose for itself. Cheaper than a replan and it usually is the fault.
            if failure_code == FailureCode.UNEXPECTED_STATE:
                relaxed = relax_arguments(step.preferred_tool or "", step.tool_arguments, goal)
                if relaxed != step.tool_arguments:
                    return RecoveryAction(
                        action=RELAX_AND_RETRY,
                        reason="search_matched_nothing",
                        failure_code=failure_code,
                    )
            refresh = _REFRESH_TOOLS.get(failure_code, "")
            if refresh and self.registry.get(refresh) is not None:
                return RecoveryAction(
                    action=REFRESH_AND_RETRY,
                    reason=f"refresh_state_for_{failure_code.value.lower()}",
                    failure_code=failure_code,
                    refresh_tool=refresh,
                )
            if failure_code in {FailureCode.TOOL_TIMEOUT, FailureCode.NETWORK_ERROR, FailureCode.UNKNOWN}:
                return RecoveryAction(
                    action=RETRY, reason="safe_retry", failure_code=failure_code
                )

        alternative = self._alternative(step, tried_tools or set())
        if alternative:
            return RecoveryAction(
                action=ALTERNATIVE_TOOL,
                reason="alternative_method",
                failure_code=failure_code,
                alternative_tool=alternative,
            )

        if replans_left > 0:
            return RecoveryAction(action=REPLAN, reason="replan_remaining_work", failure_code=failure_code)

        return RecoveryAction(
            action=ASK_USER,
            reason="recovery_exhausted",
            failure_code=failure_code,
            user_message=self._ask_message(step, failure_code),
        )

    def _alternative(self, step: AgentStep, tried: set[str]) -> str:
        tool = step.preferred_tool or ""
        candidates = list(_ALTERNATIVES.get(tool, []))
        if site_target(step) and (tool in _DESKTOP_TOOLS or tool in _SITE_ALTERNATIVES):
            # Also once already inside the browser: no window, then no tab, leaves opening
            # the page as the only way left to be looking at the site the user named.
            candidates = [c for c in _SITE_ALTERNATIVES if c != tool] + candidates
        for candidate in candidates:
            if candidate in tried:
                continue
            if self.registry.get(candidate) is not None:
                return candidate
        return ""

    @staticmethod
    def _ask_message(step: AgentStep, code: FailureCode) -> str:
        if code == FailureCode.ELEMENT_NOT_FOUND:
            return "I could not find that on the page. What should I click?"
        if code == FailureCode.ELEMENT_AMBIGUOUS:
            return "I see more than one match. Which one do you mean?"
        if code == FailureCode.FILE_NOT_FOUND:
            return "I could not find that file. Which file should I use?"
        if code == FailureCode.TAB_NOT_FOUND:
            return "I could not find that tab. Which page do you mean?"
        if code == FailureCode.WINDOW_NOT_FOUND:
            return "I could not find that window. Should I open the application first?"
        return f"I could not finish {step.description.lower()}. How would you like me to proceed?"
