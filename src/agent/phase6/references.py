"""Task-scoped reference resolution. "The second one" never reaches the planner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from src.agent.phase6.models import TaskContext


_ORDINALS = {
    "first": 0, "1st": 0, "one": 0,
    "second": 1, "2nd": 1, "two": 1, "other": 1,
    "third": 2, "3rd": 2, "three": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
    "last": -1, "newest": 0, "latest": 0, "most recent": 0,
}
_ORDINAL_RE = re.compile(
    r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|last|newest|latest|most recent|other)\b",
    re.I,
)
_FILE_RE = re.compile(r"\b(file|files|pdf|document|resume|cv|doc|spreadsheet|one|ones)\b", re.I)
_TAB_RE = re.compile(r"\b(tab|tabs|page|window|site)\b", re.I)
_SECOND_NEWEST = re.compile(r"\bsecond (?:newest|latest|most recent)\b", re.I)


@dataclass
class ReferenceResolution:
    resolved: bool = False
    kind: str = ""
    value: str = ""
    label: str = ""
    reason: str = ""


def resolve_reference(text: str, context: TaskContext) -> ReferenceResolution:
    """Map an ordinal phrase onto candidates already held in TaskContext."""
    if not text:
        return ReferenceResolution(reason="empty")

    if _SECOND_NEWEST.search(text):
        index: Optional[int] = 1
    else:
        match = _ORDINAL_RE.search(text)
        if not match:
            return ReferenceResolution(reason="no_ordinal")
        index = _ORDINALS.get(match.group(1).lower())
    if index is None:
        return ReferenceResolution(reason="unknown_ordinal")

    prefer_tab = bool(_TAB_RE.search(text)) and not _FILE_RE.search(text)
    candidates: list[dict[str, Any]]
    kind: str
    if prefer_tab and context.candidate_tabs:
        candidates, kind = context.candidate_tabs, "tab"
    elif context.candidate_files:
        candidates, kind = context.candidate_files, "file"
    elif context.candidate_tabs:
        candidates, kind = context.candidate_tabs, "tab"
    else:
        return ReferenceResolution(reason="no_candidates")

    if index == -1:
        index = len(candidates) - 1
    if index >= len(candidates):
        return ReferenceResolution(reason="out_of_range")

    chosen = candidates[index]
    if kind == "file":
        value = str(chosen.get("path") or chosen.get("name") or "")
        label = str(chosen.get("name") or value)
        context.selected_file = value
        context.current_document = label
    else:
        value = str(chosen.get("url") or chosen.get("title") or "")
        label = str(chosen.get("title") or value)
        context.current_url = value
        context.current_page = label

    context.resolved_references[text.strip().lower()[:60]] = value
    return ReferenceResolution(resolved=True, kind=kind, value=value, label=label, reason="ordinal")
