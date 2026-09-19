"""Full-utterance coverage for the fast router. One fragment must not consume a goal."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


FULL_COVERAGE = "FULL_COVERAGE"
PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
AMBIGUOUS = "AMBIGUOUS"

_COMPOUND_MARKERS = re.compile(
    r"\b("
    r"and then|then|after that|next|before|after|once|when|"
    r"followed by|and tell me|and open|and switch|and find"
    r")\b",
    re.I,
)
_SPLIT = re.compile(
    r",\s*(?:and\s+)?(?:then\s+)?|"
    r"\s+and then\s+|"
    r"\s+then\s+|"
    r"\s+after that\s+|"
    r"\s+and tell me\s+|"
    r"\s+and (?=(?:then )?(?:open|switch|find|go|search|close|tell|show)\b)",
    re.I,
)

_ACTION_VERBS = frozenset(
    """open find search go navigate switch close read compare tell show
    look click type start check get focus scroll press return take me""".split()
)


@dataclass
class CoverageReport:
    original_utterance: str = ""
    normalized_utterance: str = ""
    command_segments: list[str] = field(default_factory=list)
    detected_actions: list[str] = field(default_factory=list)
    detected_domains: list[str] = field(default_factory=list)
    selected_fast_intent: str = ""
    selected_clause: str = ""
    unconsumed_clauses: list[str] = field(default_factory=list)
    coverage: str = FULL_COVERAGE
    fast_route_allowed: bool = True
    compound_markers: list[str] = field(default_factory=list)

    def as_log(self) -> str:
        return (
            f"[FastCoverage] original_utterance={self.original_utterance!r} "
            f"normalized_utterance={self.normalized_utterance!r} "
            f"command_segments={self.command_segments} "
            f"detected_actions={self.detected_actions} "
            f"detected_domains={self.detected_domains} "
            f"selected_fast_intent={self.selected_fast_intent or '-'} "
            f"selected_clause={self.selected_clause or '-'} "
            f"unconsumed_clauses={self.unconsumed_clauses} "
            f"coverage={self.coverage} "
            f"fast_route_allowed={str(self.fast_route_allowed).lower()}"
        )


def segment_compound_command(text: str) -> list[str]:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    parts = [p.strip(" .!?") for p in _SPLIT.split(raw) if p and p.strip(" .!?")]
    return parts or [raw]


def _detect_action(seg: str) -> str:
    t = (seg or "").strip().lower()
    if not t:
        return ""
    if re.search(r"\b(what page|what website|what site|page i['’]?m on|page am i)\b", t):
        return "current_page"
    if re.search(r"\b(find|search)\b", t) and re.search(
        r"\b(resume|r[eé]sum[eé]|cv|file|pdf|document|curriculum)\b", t
    ):
        return "find_file"
    if re.search(r"\bopen\b", t) and re.search(r"\b(it|file|resume|pdf|document|cv)\b", t):
        if not re.search(r"\b(chrome|browser|edge|tab)\b", t):
            return "open_file"
    if re.search(r"\b(switch|go back|return|focus).{0,24}\b(chrome|browser)\b", t):
        return "focus_browser"
    if re.search(r"\b(open|launch|start)\b.{0,16}\b(chrome|browser|edge|chromium)\b", t):
        return "open_browser"
    if re.search(r"\bnew tab\b", t):
        return "new_tab"
    if re.search(r"\b(go to|navigate to|take me to|show me)\b", t):
        return "navigate"
    if re.search(r"\b(switch(?: back)? to|go back to|return to)\b", t):
        return "switch_tab"
    if re.search(r"\bcompare\b", t):
        return "compare"
    if re.search(r"\b(look at|read)\b", t) and re.search(r"\b(page|job|site|tab|listing)\b", t):
        return "read_page"
    if re.search(r"\bsearch\b", t):
        return "search"
    words = t.split()
    for w in words[:4]:
        if w in _ACTION_VERBS:
            return w
    return ""


def _domain_for(action: str) -> str:
    if action in {"find_file", "open_file"}:
        return "files"
    if action in {
        "open_browser",
        "focus_browser",
        "current_page",
        "navigate",
        "new_tab",
        "switch_tab",
        "search",
        "read_page",
    }:
        return "browser"
    if action == "compare":
        return "cross"
    if action:
        return "other"
    return ""


_INTENT_COVERS: dict[str, set[str]] = {
    "browser.open": {"open_browser", "focus_browser", "open"},
    "browser.open_and_goto": {"open_browser", "navigate", "new_tab", "go", "open"},
    "browser.new_tab": {"new_tab", "navigate", "go", "open"},
    "browser.current_page": {"current_page"},
    "browser.current_url": {"current_page"},
    "browser.active_tab": {"current_page"},
    "browser.page_entity": {"current_page"},
    "browser.page_about": {"current_page"},
    "browser.switch_tab": {"switch_tab", "focus_browser", "switch"},
    "browser.back": {"back"},
    "browser.forward": {"forward"},
    "browser.reload": {"reload"},
    "browser.close_tab": {"close"},
    "browser.next_tab": {"switch_tab"},
    "browser.check": {"check"},
    "browser.fill": {"fill"},
    "browser.select": {"select"},
    "task.complete": {"check"},
    "screen.monitor_count": {"tell", "show"},
}


def _intent_covers(action_name: str, args: dict[str, Any]) -> set[str]:
    covered = set(_INTENT_COVERS.get(action_name, ()))
    if action_name == "browser.new_tab" and not (args or {}).get("url"):
        covered.discard("navigate")
        covered.discard("go")
    return covered


def analyze_fast_coverage(
    text: str,
    *,
    intent_action: str = "",
    intent_args: Optional[dict[str, Any]] = None,
    selected_clause: str = "",
) -> CoverageReport:
    from src.agent.voice_echo import normalize_speech

    original = (text or "").strip()
    normalized = normalize_speech(original)
    segments = segment_compound_command(original)
    actions: list[str] = []
    action_segs: list[tuple[str, str]] = []
    for seg in segments:
        act = _detect_action(seg)
        if act:
            actions.append(act)
            action_segs.append((seg, act))
    domains = []
    for act in actions:
        d = _domain_for(act)
        if d and d not in domains:
            domains.append(d)
    markers = [m.group(1).lower() for m in _COMPOUND_MARKERS.finditer(original)]
    report = CoverageReport(
        original_utterance=original,
        normalized_utterance=normalized,
        command_segments=segments,
        detected_actions=actions,
        detected_domains=domains,
        selected_fast_intent=intent_action,
        selected_clause=selected_clause,
        compound_markers=markers,
    )
    if len(action_segs) <= 1:
        report.coverage = FULL_COVERAGE
        report.fast_route_allowed = True
        return report

    if not intent_action:
        report.coverage = PARTIAL_COVERAGE
        report.fast_route_allowed = False
        report.unconsumed_clauses = [s for s, _ in action_segs]
        return report

    covered = _intent_covers(intent_action, intent_args or {})
    leftover = [(seg, act) for seg, act in action_segs if act not in covered]
    report.unconsumed_clauses = [s for s, _ in leftover]
    cross_tool = len([d for d in domains if d in {"files", "browser", "cross"}]) >= 2
    if leftover or cross_tool:
        report.coverage = PARTIAL_COVERAGE
        report.fast_route_allowed = False
        if cross_tool and not report.unconsumed_clauses:
            report.unconsumed_clauses = [s for s, a in action_segs if a not in covered]
        return report
    report.coverage = FULL_COVERAGE
    report.fast_route_allowed = True
    return report


def is_compound_multi_action(text: str) -> bool:
    report = analyze_fast_coverage(text)
    return len(report.detected_actions) >= 2
