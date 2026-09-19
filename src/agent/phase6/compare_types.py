"""Shared comparison dataclasses. Imported by the engine, retriever, and lookup."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

COVERED = "COVERED"
PARTIAL = "PARTIAL"
NOT_FOUND = "NOT_FOUND"
NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"

TASK_TYPE_JOB_RESUME_COMPARISON = "JOB_RESUME_COMPARISON"


@dataclass
class RequirementItem:
    requirement: str
    status: str
    resume_evidence: str = ""
    job_evidence: str = ""
    confidence: float = 0.0
    category: str = ""
    job_source_section: str = ""
    job_source_text: str = ""
    resume_source_section: str = ""
    source_scope: str = "WEB_DOCUMENT"
    source_method: str = ""
    requirement_id: str = ""
    canonical_text: str = ""
    required_or_preferred: str = "required"
    min_years: Optional[float] = None
    technologies: list[str] = field(default_factory=list)
    subcomponents: list[str] = field(default_factory=list)
    resume_evidence_ids: list[str] = field(default_factory=list)
    resume_evidence_texts: list[str] = field(default_factory=list)
    reason: str = ""
    missing_components: list[str] = field(default_factory=list)
    semantic_similarity: float = 0.0
    category_compatible: bool = False
    duration_verified: bool = False
    alternatives: list[str] = field(default_factory=list)
    matched_alternative: str = ""
    source_texts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _item_from_dict(raw: dict[str, Any]) -> RequirementItem:
    allowed = set(RequirementItem.__dataclass_fields__)
    return RequirementItem(**{k: raw[k] for k in raw if k in allowed})


@dataclass
class ComparisonResult:
    task_id: str = ""
    job_title: str = ""
    company: str = ""
    covered_requirements: list[RequirementItem] = field(default_factory=list)
    partial_requirements: list[RequirementItem] = field(default_factory=list)
    missing_requirements: list[RequirementItem] = field(default_factory=list)
    needs_confirmation: list[RequirementItem] = field(default_factory=list)
    notable_strengths: list[str] = field(default_factory=list)
    source_evidence: list[dict[str, Any]] = field(default_factory=list)
    generated_at: float = 0.0
    job_page_url: str = ""
    job_page_title: str = ""
    resume_path: str = ""
    job_page_model_id: str = ""
    aborted: bool = False
    abort_reason: str = ""
    raw_requirement_count: int = 0
    canonical_requirement_count: int = 0
    duplicates_removed: int = 0
    duplicate_groups: int = 0
    non_requirements_removed: int = 0
    fragments_removed: int = 0
    strong_match_threshold_note: str = ""

    @property
    def items(self) -> list[RequirementItem]:
        return (
            list(self.covered_requirements)
            + list(self.partial_requirements)
            + list(self.missing_requirements)
            + list(self.needs_confirmation)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "job_title": self.job_title,
            "company": self.company,
            "covered_requirements": [i.as_dict() for i in self.covered_requirements],
            "partial_requirements": [i.as_dict() for i in self.partial_requirements],
            "missing_requirements": [i.as_dict() for i in self.missing_requirements],
            "needs_confirmation": [i.as_dict() for i in self.needs_confirmation],
            "notable_strengths": list(self.notable_strengths),
            "source_evidence": list(self.source_evidence),
            "generated_at": self.generated_at,
            "job_page_url": self.job_page_url,
            "job_page_title": self.job_page_title,
            "resume_path": self.resume_path,
            "job_page_model_id": self.job_page_model_id,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "raw_requirement_count": self.raw_requirement_count,
            "canonical_requirement_count": self.canonical_requirement_count,
            "duplicates_removed": self.duplicates_removed,
            "duplicate_groups": self.duplicate_groups,
            "non_requirements_removed": self.non_requirements_removed,
            "fragments_removed": self.fragments_removed,
            "strong_match_threshold_note": self.strong_match_threshold_note,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional["ComparisonResult"]:
        if not isinstance(data, dict) or not data:
            return None

        def _items(key: str) -> list[RequirementItem]:
            out = []
            for raw in data.get(key) or []:
                if isinstance(raw, dict) and raw.get("requirement"):
                    out.append(_item_from_dict(raw))
            return out

        return cls(
            task_id=str(data.get("task_id") or ""),
            job_title=str(data.get("job_title") or ""),
            company=str(data.get("company") or ""),
            covered_requirements=_items("covered_requirements"),
            partial_requirements=_items("partial_requirements"),
            missing_requirements=_items("missing_requirements"),
            needs_confirmation=_items("needs_confirmation"),
            notable_strengths=[str(s) for s in (data.get("notable_strengths") or [])],
            source_evidence=list(data.get("source_evidence") or []),
            generated_at=float(data.get("generated_at") or 0.0),
            job_page_url=str(data.get("job_page_url") or ""),
            job_page_title=str(data.get("job_page_title") or ""),
            resume_path=str(data.get("resume_path") or ""),
            job_page_model_id=str(data.get("job_page_model_id") or ""),
            aborted=bool(data.get("aborted")),
            abort_reason=str(data.get("abort_reason") or ""),
            raw_requirement_count=int(data.get("raw_requirement_count") or 0),
            canonical_requirement_count=int(data.get("canonical_requirement_count") or 0),
            duplicates_removed=int(data.get("duplicates_removed") or 0),
            duplicate_groups=int(data.get("duplicate_groups") or 0),
            non_requirements_removed=int(data.get("non_requirements_removed") or 0),
            fragments_removed=int(data.get("fragments_removed") or 0),
            strong_match_threshold_note=str(data.get("strong_match_threshold_note") or ""),
        )
