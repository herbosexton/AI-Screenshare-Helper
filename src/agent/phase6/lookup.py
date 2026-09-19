"""Resolve a follow-up utterance to one stored comparison requirement."""

from __future__ import annotations

import re
from typing import Optional

from src.agent.phase6.compare_types import ComparisonResult, RequirementItem
from src.agent.phase6.requirements import normalize_text

_TECH_HINTS = {
    "python": ("python",),
    "agentic": ("agentic", "orchestration", "langgraph", "langchain", "crewai", "autogen", "framework"),
    "cloud": ("cloud", "azure", "aws", "gcp", "bedrock", "vertex"),
    "degree": ("degree", "bachelor", "master", "education", "field"),
    "travel": ("travel",),
    "consulting": ("consult", "client-facing", "client facing"),
    "certification": ("certif",),
    "leadership": ("lead", "workstream", "engagement"),
    "clearance": ("clearance", "ts/sci", "secret"),
}
_DEICTIC = re.compile(
    r"\b(why (did you (say|mark) )?(that|this|it)( one)?|that one|this one)\b",
    re.I,
)
_SPECIFIC = re.compile(
    r"\b(python|azure|aws|gcp|cloud|degree|bachelor|master|travel|agentic|"
    r"langgraph|langchain|consult|certif|clearance|sponsorship|workstream|leadership)\b",
    re.I,
)
CLARIFY_FOLLOWUP = (
    "Which requirement do you mean? I can look up a stored item such as Python, "
    "the degree, the cloud stack, or another requirement from that comparison."
)


def _has_specific_needle(text: str) -> bool:
    return bool(_SPECIFIC.search(text or ""))


def _score_item(text: str, item: RequirementItem) -> int:
    score = 0
    hay = " ".join(
        [
            normalize_text(item.requirement),
            normalize_text(item.canonical_text),
            normalize_text(item.category),
            " ".join(item.technologies or []),
            " ".join(item.subcomponents or []),
            " ".join(item.alternatives or []),
            normalize_text(item.matched_alternative),
            normalize_text(item.job_evidence),
        ]
    )
    for tok in text.split():
        if len(tok) < 3:
            continue
        if tok in hay:
            score += 3 if tok in {"python", "azure", "aws", "gcp", "degree", "langgraph"} else 1
    for hint, needles in _TECH_HINTS.items():
        if any(n in text for n in needles) and (
            hint in hay
            or any(n in hay for n in needles)
            or hint == item.category
            or (hint == "agentic" and ("agentic" in hay or "orchestration" in hay))
            or (hint == "degree" and item.category == "degree")
            or (hint == "cloud" and item.category == "cloud")
        ):
            score += 4
    return score


class RequirementLookup:
    def resolve(self, utterance: str, result: ComparisonResult) -> Optional[RequirementItem]:
        item, _clarify = self._resolve(utterance, result)
        return item

    def answer(self, utterance: str, result: ComparisonResult) -> str:
        item, clarify = self._resolve(utterance, result)
        if clarify:
            return clarify
        if item is None:
            return "I do not have a stored comparison item that matches that follow-up."
        return format_requirement_answer(item)

    def _resolve(self, utterance: str, result: ComparisonResult) -> tuple[Optional[RequirementItem], str]:
        text = normalize_text(utterance or "")
        if not text or result is None:
            return None, ""
        items = list(result.items)
        if not items:
            return None, ""
        scored = [( _score_item(text, item), item) for item in items]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        deictic = bool(_DEICTIC.search(utterance or "")) or bool(re.search(r"\b(that|this|it)\b", text))
        specific = _has_specific_needle(utterance or "")

        if scored and specific:
            top, second = scored[0][0], scored[1][0] if len(scored) > 1 else 0
            if top >= 4 and top - second >= 2:
                return scored[0][1], ""
            return None, CLARIFY_FOLLOWUP

        if len(items) == 1:
            return items[0], ""

        if deictic and not specific:
            return None, CLARIFY_FOLLOWUP

        if scored:
            top, second = scored[0][0], scored[1][0] if len(scored) > 1 else 0
            if top >= 6 and top - second >= 3:
                return scored[0][1], ""
        if deictic:
            return None, CLARIFY_FOLLOWUP
        return None, ""


def format_requirement_answer(item: RequirementItem) -> str:
    status = item.status.replace("_", " ")
    evidence = item.resume_evidence or (item.resume_evidence_texts[0] if item.resume_evidence_texts else "")
    missing = item.missing_components or []
    lines = [
        f"Status: {status}",
        f"Job requirement: {item.requirement}",
    ]
    if item.alternatives:
        lines.append("Alternatives: " + " or ".join(item.alternatives))
    if item.matched_alternative:
        lines.append(f"Matched branch: {item.matched_alternative}")
    if evidence:
        lines.append(f"Resume evidence: {evidence}")
    else:
        lines.append("Resume evidence: none")
    if item.reason:
        lines.append(f"Reason: {item.reason}")
    if missing:
        lines.append("Missing proof: " + "; ".join(missing[:6]))
    return "\n".join(lines)
