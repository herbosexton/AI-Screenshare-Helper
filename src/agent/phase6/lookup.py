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
}


class RequirementLookup:
    def resolve(self, utterance: str, result: ComparisonResult) -> Optional[RequirementItem]:
        text = normalize_text(utterance or "")
        if not text or result is None:
            return None
        items = list(result.items)
        if not items:
            return None
        scored: list[tuple[int, RequirementItem]] = []
        for item in items:
            score = 0
            hay = " ".join(
                [
                    normalize_text(item.requirement),
                    normalize_text(item.canonical_text),
                    normalize_text(item.category),
                    " ".join(item.technologies or []),
                    " ".join(item.subcomponents or []),
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
                    hint in hay or any(n in hay for n in needles) or hint == item.category
                    or (hint == "agentic" and ("agentic" in hay or "orchestration" in hay))
                    or (hint == "degree" and item.category == "degree")
                    or (hint == "cloud" and item.category == "cloud")
                ):
                    score += 4
            if score:
                scored.append((score, item))
        if not scored:
            if re.search(r"\b(that|this|it|one)\b", text) and result.covered_requirements:
                return result.covered_requirements[0]
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1]


def format_requirement_answer(item: RequirementItem) -> str:
    status = item.status.replace("_", " ")
    evidence = item.resume_evidence or (item.resume_evidence_texts[0] if item.resume_evidence_texts else "")
    missing = item.missing_components or []
    lines = [
        f"Status: {status}",
        f"Job requirement: {item.requirement}",
    ]
    if evidence:
        lines.append(f"Resume evidence: {evidence}")
    else:
        lines.append("Resume evidence: none")
    if item.reason:
        lines.append(f"Reason: {item.reason}")
    if missing:
        lines.append("Missing proof: " + "; ".join(missing[:6]))
    return "\n".join(lines)
