"""JobPostingModel from structured page data, never browser chrome."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from src.agent.browser.page_model import (
    BROWSER_CHROME,
    BROWSER_EXTENSION,
    JOB_LISTING,
    OS_UI,
    WEB_DOCUMENT,
    is_browser_noise,
)

VALID_REQUIREMENT = "VALID_REQUIREMENT"
RESPONSIBILITY = "RESPONSIBILITY"
PREFERRED_REQUIREMENT = "PREFERRED_REQUIREMENT"
NAVIGATION = "NAVIGATION"
BROWSER_UI = "BROWSER_UI"
EXTENSION_UI = "EXTENSION_UI"
NOISE = "NOISE"
UNKNOWN = "UNKNOWN"
COMPENSATION_CONTEXT = "COMPENSATION_CONTEXT"
LEGAL_CONTEXT = "LEGAL_CONTEXT"
BENEFITS_CONTEXT = "BENEFITS_CONTEXT"
NON_REQUIREMENT = "NON_REQUIREMENT"
FRAGMENT = "FRAGMENT"

_KNOWN_SKILLS = frozenset({
    "python", "java", "javascript", "typescript", "sql", "aws", "azure", "gcp",
    "langgraph", "langchain", "autogen", "crewai", "pytorch", "tensorflow",
    "kubernetes", "docker", "rag", "llm", "llms",
})

JOB_CONTENT_CONTAMINATION_DETECTED = "JOB_CONTENT_CONTAMINATION_DETECTED"

_SECTION_REQUIRED = (
    "qualifications",
    "required qualifications",
    "required",
    "requirements",
    "basic qualifications",
    "minimum qualifications",
    "what you'll need",
    "what you will need",
    "we're looking for",
)
_SECTION_PREFERRED = (
    "preferred",
    "preferred qualifications",
    "nice to have",
    "desired",
    "bonus",
)
_SECTION_RESP = (
    "responsibilities",
    "work you'll do",
    "work you will do",
    "what you'll do",
    "what you will do",
    "the team",
)
_SOFT = re.compile(
    r"\b(travel|sponsorship|immigration|authorized to work|citizenship|"
    r"relocation|certifications?:|hybrid|remote|on-?site)\b",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")


@dataclass
class Evidence:
    text: str
    section: str = ""
    source_scope: str = WEB_DOCUMENT
    source_method: str = ""
    confidence: float = 0.8

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobPostingModel:
    title: str = ""
    company: str = ""
    job_id: str = ""
    location: str = ""
    employment_type: str = ""
    salary: str = ""
    responsibilities: list[Evidence] = field(default_factory=list)
    required_qualifications: list[Evidence] = field(default_factory=list)
    preferred_qualifications: list[Evidence] = field(default_factory=list)
    required_skills: list[Evidence] = field(default_factory=list)
    preferred_skills: list[Evidence] = field(default_factory=list)
    experience_requirements: list[Evidence] = field(default_factory=list)
    education_requirements: list[Evidence] = field(default_factory=list)
    certifications: list[Evidence] = field(default_factory=list)
    travel_requirements: list[Evidence] = field(default_factory=list)
    sponsorship_notes: list[Evidence] = field(default_factory=list)
    clearance_requirements: list[Evidence] = field(default_factory=list)
    tools_platforms: list[Evidence] = field(default_factory=list)
    source_sections: list[str] = field(default_factory=list)
    source_url: str = ""
    confidence: float = 0.0
    structured_jobposting_found: bool = False
    structured_fields_used: list[str] = field(default_factory=list)
    page_type: str = ""

    def validated_requirements(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev, category, preferred in (
            *((e, "required", False) for e in self.required_qualifications),
            *((e, "experience", False) for e in self.experience_requirements),
            *((e, "education", False) for e in self.education_requirements),
            *((e, "skills", False) for e in self.required_skills),
            *((e, "tools", False) for e in self.tools_platforms),
            *((e, "clearance", False) for e in self.clearance_requirements),
            *((e, "preferred", True) for e in self.preferred_qualifications),
            *((e, "preferred", True) for e in self.preferred_skills),
            *((e, "travel", True) for e in self.travel_requirements),
            *((e, "sponsorship", True) for e in self.sponsorship_notes),
            *((e, "certification", True) for e in self.certifications),
        ):
            kind = JobRequirementValidator().classify(ev.text, ev.section, ev.source_scope)
            if kind not in {VALID_REQUIREMENT, PREFERRED_REQUIREMENT}:
                continue
            if ev.source_scope != WEB_DOCUMENT:
                continue
            out.append(
                {
                    "requirement": ev.text,
                    "category": category,
                    "preferred": preferred or kind == PREFERRED_REQUIREMENT,
                    "job_evidence": ev.text,
                    "job_source_section": ev.section or category,
                    "job_source_text": ev.text,
                    "source_scope": ev.source_scope,
                    "source_method": ev.source_method,
                    "confidence": ev.confidence,
                }
            )
        return out

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


class JobRequirementValidator:
    def classify(self, candidate: str, source_section: str = "", source_scope: str = WEB_DOCUMENT) -> str:
        from src.agent.phase6.requirements import RequirementContentClassifier, RequirementFragmentValidator

        text = (candidate or "").strip()
        if not text:
            return NOISE
        if source_scope in {BROWSER_CHROME, OS_UI}:
            return BROWSER_UI
        if source_scope == BROWSER_EXTENSION or is_browser_noise(text):
            if re.search(r"install |for chrome|extension", text, re.I):
                return EXTENSION_UI
            return BROWSER_UI
        if re.search(r"^https?://|chrome://", text, re.I):
            return NAVIGATION
        if RequirementFragmentValidator().reject(text):
            return FRAGMENT
        kind = RequirementContentClassifier().classify(text)
        if kind == FRAGMENT:
            return FRAGMENT
        if kind == COMPENSATION_CONTEXT:
            return COMPENSATION_CONTEXT
        if kind == LEGAL_CONTEXT:
            return LEGAL_CONTEXT
        if kind == BENEFITS_CONTEXT:
            return BENEFITS_CONTEXT
        if kind == NON_REQUIREMENT:
            return NON_REQUIREMENT
        section = (source_section or "").lower()
        if len(text) < 8 and not re.search(r"\d", text):
            key = re.sub(r"[^a-z0-9+#./]+", "", text.lower())
            if section in {"skills", "required", "tools", "title"} and key in _KNOWN_SKILLS:
                return VALID_REQUIREMENT
            return NOISE
        if any(s in section for s in _SECTION_RESP):
            return RESPONSIBILITY
        if any(s in section for s in _SECTION_PREFERRED) or section == "preferred":
            return PREFERRED_REQUIREMENT
        if any(s in section for s in _SECTION_REQUIRED + ("experience", "education", "skills", "tools")):
            return VALID_REQUIREMENT
        if _SOFT.search(text):
            return PREFERRED_REQUIREMENT
        if len(text.split()) >= 6:
            return VALID_REQUIREMENT
        return UNKNOWN


def _company(posting: dict[str, Any], url: str) -> str:
    org = posting.get("hiringOrganization")
    if isinstance(org, dict) and org.get("name"):
        return str(org["name"])
    if isinstance(org, str):
        return org
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] in {"apply", "jobs", "careers", "boards"}:
        return parts[1].title()
    return parts[0].title() if parts and parts[0] else ""


def _job_id(posting: dict[str, Any], url: str) -> str:
    ident = posting.get("identifier")
    if isinstance(ident, dict):
        return str(ident.get("value") or ident.get("name") or "")
    if ident:
        return str(ident)
    m = re.search(r"(?:jobId|job[_-]?id|requisition)=([A-Za-z0-9-]+)", url or "", re.I)
    return m.group(1) if m else ""


def _html_text(value: Any) -> str:
    from src.agent.browser.document import html_to_text

    if value is None:
        return ""
    if isinstance(value, dict):
        return _html_text(value.get("value") or value.get("name") or "")
    if isinstance(value, list):
        return "\n".join(_html_text(v) for v in value if v)
    return html_to_text(str(value))


def _split_items(text: str) -> list[str]:
    items: list[str] = []
    for raw in (text or "").splitlines():
        line = _BULLET.sub("", raw).strip(" -•\t")
        if len(line) >= 8:
            items.append(line)
    if not items and text and len(text.strip()) >= 12:
        items.append(text.strip())
    return items


def _section_name(line: str) -> str:
    low = line.lower().rstrip(":")
    for name, aliases in (
        ("required", _SECTION_REQUIRED),
        ("preferred", _SECTION_PREFERRED),
        ("responsibilities", _SECTION_RESP),
    ):
        if any(low == a or low.startswith(a) for a in aliases):
            return name
    if low in {"education", "experience", "skills"}:
        return low
    return ""


def extract_sections(document_text: str, source_method: str = "page_reader") -> dict[str, list[Evidence]]:
    buckets: dict[str, list[Evidence]] = {
        "required": [],
        "preferred": [],
        "responsibilities": [],
        "education": [],
        "experience": [],
        "skills": [],
    }
    section = ""
    for raw in (document_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        named = _section_name(_BULLET.sub("", line))
        if named and len(line) <= 48:
            section = named
            continue
        if not section:
            continue
        if is_browser_noise(line):
            continue
        item = _BULLET.sub("", line).strip()
        if len(item) < 3:
            continue
        from src.agent.phase6.requirements import RequirementContentClassifier

        if RequirementContentClassifier().classify(item) != "VALID":
            continue
        if len(item) < 8 and section not in {"required", "skills", "tools"}:
            continue
        buckets.setdefault(section, []).append(
            Evidence(
                text=item,
                section=section,
                source_scope=WEB_DOCUMENT,
                source_method=source_method,
                confidence=0.85,
            )
        )
    return buckets


def build_job_posting(
    *,
    page_model: Any = None,
    posting: Optional[dict[str, Any]] = None,
    document_text: str = "",
    url: str = "",
    title: str = "",
) -> JobPostingModel:
    posting = posting or getattr(page_model, "_jobposting", None) or {}
    url = url or getattr(page_model, "document_url", "") or ""
    title = title or str(posting.get("title") or getattr(page_model, "tab_ref", "") or "")
    text = document_text or getattr(page_model, "main_content", "") or ""
    method = getattr(page_model, "document_source_method", "") or ("json_ld" if posting else "page_reader")
    model = JobPostingModel(
        title=str(posting.get("title") or title or ""),
        company=_company(posting, url),
        job_id=_job_id(posting, url),
        employment_type=str(posting.get("employmentType") or ""),
        source_url=url,
        structured_jobposting_found=bool(posting),
        page_type=getattr(page_model, "page_type", "") or "",
        confidence=0.9 if posting else 0.65,
    )
    loc = posting.get("jobLocation")
    if isinstance(loc, dict):
        addr = loc.get("address") or {}
        if isinstance(addr, dict):
            model.location = str(addr.get("addressLocality") or addr.get("name") or "")
    used: list[str] = []
    if posting.get("title"):
        used.append("title")
    if posting.get("hiringOrganization"):
        used.append("hiringOrganization")
    desc = _html_text(posting.get("description") or "")
    quals = _html_text(posting.get("qualifications") or "")
    section_source = quals if len(quals) >= len(desc) else (quals + "\n" + desc if quals else desc)
    if not section_source:
        section_source = text
    buckets = extract_sections(section_source, source_method=method)
    if not any(buckets.values()):
        buckets = extract_sections(text, source_method=method)
    model.required_qualifications = buckets.get("required") or []
    model.preferred_qualifications = buckets.get("preferred") or []
    model.responsibilities = buckets.get("responsibilities") or []
    model.education_requirements = buckets.get("education") or []
    model.experience_requirements = [
        ev for ev in model.required_qualifications if re.search(r"\d+\+?\s*years?", ev.text, re.I)
    ]
    edu = _html_text(posting.get("educationRequirements") or "")
    if edu and len(edu) < 160:
        model.education_requirements.append(
            Evidence(text=edu, section="education", source_method=method, source_scope=WEB_DOCUMENT)
        )
        used.append("educationRequirements")
    for ev in list(model.required_qualifications) + list(model.preferred_qualifications):
        low = ev.text.lower()
        if "travel" in low:
            model.travel_requirements.append(ev)
        if "sponsor" in low or "immigration" in low:
            model.sponsorship_notes.append(ev)
        if "certif" in low:
            model.certifications.append(ev)
    model.source_sections = [k for k, v in buckets.items() if v]
    if posting.get("qualifications"):
        used.append("qualifications")
    if posting.get("description"):
        used.append("description")
    if posting.get("identifier"):
        used.append("identifier")
    model.structured_fields_used = used
    print(
        f"[JobPosting] structured_jobposting_found={str(model.structured_jobposting_found).lower()} "
        f"structured_fields_used={model.structured_fields_used} "
        f"title={model.title!r} company={model.company!r} job_id={model.job_id or '-'} "
        f"required={len(model.required_qualifications)} preferred={len(model.preferred_qualifications)}"
    )
    return model


def assert_web_document_requirements(requirements: list[dict[str, Any]]) -> Optional[str]:
    for req in requirements:
        scope = str(req.get("source_scope") or "")
        text = str(req.get("requirement") or "")
        if scope in {BROWSER_CHROME, BROWSER_EXTENSION, OS_UI} or is_browser_noise(text):
            print(
                f"[Comparison] comparison_aborted_reason={JOB_CONTENT_CONTAMINATION_DETECTED} "
                f"source_scope={scope or '-'} text={text[:80]!r}"
            )
            return JOB_CONTENT_CONTAMINATION_DETECTED
    return None
