"""Semantic resume evidence retrieval and requirement classification."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from src.agent.phase6.compare_types import (
    COVERED,
    NEEDS_CONFIRMATION,
    NOT_FOUND,
    PARTIAL,
    RequirementItem,
)
from src.agent.phase6.requirements import (
    CATEGORY_AUTHORIZATION,
    CATEGORY_CERTIFICATION,
    CATEGORY_CLEARANCE,
    CATEGORY_CLOUD,
    CATEGORY_CONSULTING,
    CATEGORY_DEGREE,
    CATEGORY_EXPERIENCE,
    CATEGORY_LEADERSHIP,
    CATEGORY_PRESENTATION,
    CATEGORY_SKILL,
    CATEGORY_SPONSORSHIP,
    CATEGORY_TRAVEL,
    CanonicalRequirement,
    extract_degree,
    extract_technologies,
    extract_years,
    normalize_text,
)

STRONG_THRESHOLD = 0.75
PARTIAL_THRESHOLD = 0.45

_GENERIC = frozenset(
    """
    experience development engineer engineering solutions business high growth
    herbert sexton role work team skills training including using building years
    year ability strong excellent well new advanced provided developed executed
    support supporting across all teams data and the for with from that this
    into customer product operational directly influencing strategies systems
    iv iii ii senior junior analyst consultant manager associate intern
    """.split()
)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DATE_RANGE = re.compile(
    r"(?P<a>(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{4})"
    r"\s*(?:[-–—]|to)\s*"
    r"(?P<b>present|current|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{4})",
    re.I,
)
_SECTION_HEAD = re.compile(
    r"^(education|academic|experience|work experience|professional experience|"
    r"employment|skills|technical skills|technologies|certifications?|"
    r"certificates|licenses|projects|summary|objective)\s*:?\s*$",
    re.I,
)
_DEGREE_LINE = re.compile(
    r"\b(bachelor(?:'s)?(?:\s+of\s+(?:science|arts|business))?(?:\s+in\s+.+)?|"
    r"master(?:'s)?(?:\s+of\s+.+)?|m\.?b\.?a\.?|ph\.?d\.?|b\.?s\.?|b\.?a\.?|m\.?s\.?)\b",
    re.I,
)

_TECHNICAL_FIELDS = frozenset({
    "computer science", "engineering", "data science", "artificial intelligence",
    "ai", "machine learning", "software", "information systems", "computer engineering",
    "software engineering",
})
_RELATED_FIELDS = frozenset({
    "mathematics", "statistics", "physics", "information technology",
})

_CONCEPT_WEIGHTS = {
    "python": 1.0,
    "java": 1.0,
    "azure": 1.0,
    "aws": 1.0,
    "gcp": 1.0,
    "langchain": 1.15,
    "langgraph": 1.15,
    "autogen": 1.15,
    "crewai": 1.15,
    "semantic kernel": 1.15,
    "kubernetes": 1.0,
    "mlops": 1.0,
    "rag": 1.0,
    "llm": 0.85,
    "pytorch": 1.0,
    "tensorflow": 1.0,
    "microservices": 0.9,
    "sql": 0.7,
    "agentic": 1.1,
    "multi-agent": 1.1,
    "orchestration": 1.0,
    "consulting": 1.0,
    "client-facing": 0.9,
    "workshop": 0.9,
    "presentation": 0.85,
    "machine learning": 0.8,
    "deep learning": 0.8,
    "generative ai": 0.9,
    "ai/ml": 0.7,
    "leadership": 1.0,
    "workstream": 1.0,
    "engagement": 0.9,
    "clearance": 1.15,
    "certification": 1.15,
    "ai foundry": 1.15,
    "foundry": 1.15,
    "bedrock": 1.15,
    "vertex": 1.15,
    "gemini": 1.1,
}

_CONCEPT_ALIASES = {
    "python": ("python",),
    "azure": ("azure", "ai foundry"),
    "aws": ("aws", "bedrock", "amazon bedrock"),
    "gcp": ("gcp", "vertex", "gemini", "bigquery", "google ai"),
    "langchain": ("langchain",),
    "langgraph": ("langgraph",),
    "autogen": ("autogen",),
    "crewai": ("crewai", "crew ai"),
    "semantic kernel": ("semantic kernel",),
    "kubernetes": ("kubernetes", "gke"),
    "mlops": ("mlops", "aiops"),
    "rag": ("rag", "retrieval augmented"),
    "llm": ("llm", "llms", "large language"),
    "pytorch": ("pytorch",),
    "tensorflow": ("tensorflow",),
    "microservices": ("microservices",),
    "sql": ("sql",),
    "agentic": ("agentic", "multi-agent", "multi agent"),
    "orchestration": ("orchestration", "agent orchestration"),
    "consulting": ("consulting", "consultant", "advisory"),
    "client-facing": ("client-facing", "client facing", "1:1 guidance", "customer"),
    "workshop": ("workshop", "workshops", "training session"),
    "presentation": ("presentation", "public speaking", "audience"),
    "machine learning": ("machine learning", "ml models", "ml model", "statistical modeling"),
    "deep learning": ("deep learning", "neural network"),
    "generative ai": ("generative ai", "genai", "llm"),
    "ai/ml": ("ai/ml", "machine learning", "artificial intelligence"),
    "leadership": ("leadership", "leading", "led"),
    "workstream": ("workstream", "workstreams"),
    "engagement": ("engagements", "client engagement"),
    "clearance": (
        "ts/sci", "ts sci", "top secret", "secret clearance",
        "public trust", "security clearance",
    ),
    "certification": ("certified", "certification", "certificate"),
    "ai foundry": ("ai foundry", "foundry"),
    "foundry": ("ai foundry", "foundry"),
    "bedrock": ("bedrock", "amazon bedrock"),
    "vertex": ("vertex", "vertex ai"),
    "gemini": ("gemini",),
}

_CATEGORY_SECTIONS = {
    CATEGORY_SKILL: {"skills", "experience", "projects", "technical", "summary", ""},
    CATEGORY_EXPERIENCE: {"experience", "projects", "work", "summary", ""},
    CATEGORY_CLOUD: {"skills", "experience", "projects", "technical", ""},
    CATEGORY_DEGREE: {"education"},
    CATEGORY_CERTIFICATION: {"certifications", "training", "education", "skills"},
    CATEGORY_CONSULTING: {"experience", "work", "summary", ""},
    CATEGORY_PRESENTATION: {"experience", "work", "summary", ""},
    CATEGORY_LEADERSHIP: {"experience", "work", "summary", ""},
    CATEGORY_TRAVEL: {"experience", "summary", "other", ""},
    CATEGORY_SPONSORSHIP: {"education", "summary", "other", ""},
    CATEGORY_AUTHORIZATION: {"education", "summary", "other", ""},
    CATEGORY_CLEARANCE: {"experience", "summary", "other", "skills", ""},
}

_SALESY = re.compile(
    r"\b(go-to-market|investor outreach|branding|digital marketing|"
    r"sales|customer acquisition|pipeline of vendors)\b",
    re.I,
)
_BUSINESS_GROWTH = re.compile(
    r"\b(consulting|consultant|advisory|investor outreach|go-to-market|"
    r"branding|digital marketing|business planning|business development|"
    r"sales|customer acquisition|high-growth)\b",
    re.I,
)
_HARD_TECH = frozenset({
    "python", "azure", "aws", "gcp", "langchain", "langgraph", "autogen",
    "crewai", "semantic kernel",
})


def _parse_date_token(token: str) -> Optional[date]:
    t = (token or "").strip().lower().rstrip(".")
    if t in {"present", "current"}:
        return date.today()
    m = re.match(r"([a-z]+)\s+(\d{4})", t)
    if m:
        month = _MONTHS.get(m.group(1)[:3]) or _MONTHS.get(m.group(1))
        if month:
            return date(int(m.group(2)), month, 1)
    if re.fullmatch(r"\d{4}", t):
        return date(int(t), 1, 1)
    return None


def _range_years(start: Optional[date], end: Optional[date]) -> Optional[float]:
    if start is None:
        return None
    finish = end or date.today()
    days = (finish - start).days
    if days <= 0:
        return 0.0
    return days / 365.25


@dataclass
class ResumeSnippet:
    id: str
    text: str
    section: str
    start: Optional[date] = None
    end: Optional[date] = None

    @property
    def years(self) -> Optional[float]:
        return _range_years(self.start, self.end)


@dataclass
class ResumeProfile:
    text: str
    snippets: list[ResumeSnippet] = field(default_factory=list)
    degree_level: str = ""
    degree_fields: list[str] = field(default_factory=list)
    degree_evidence: str = ""


@dataclass
class EvidenceHit:
    snippet_id: str
    text: str
    section: str
    score: float
    matched_concepts: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    hits: list[EvidenceHit] = field(default_factory=list)
    best_score: float = 0.0
    category_compatible: bool = False
    duration_years: Optional[float] = None
    duration_verified: bool = False
    matched_concepts: list[str] = field(default_factory=list)
    missing_concepts: list[str] = field(default_factory=list)


_ROLE_VERBS = frozenset({
    "built", "build", "developed", "deployed", "designed", "provided",
    "created", "implemented", "maintained", "automated", "trained",
    "supported", "executed", "prototyped", "wrote", "shipped",
})


def _is_role_boundary(line: str, section: str) -> bool:
    if (section or "") not in {"experience", "work", "professional experience", ""}:
        return False
    raw = (line or "").strip()
    if not raw or len(raw) > 90 or raw.endswith((".", "!", "?")):
        return False
    words = raw.replace("|", " ").split()
    if not (1 <= len(words) <= 10):
        return False
    if any(w.lower().strip(",.") in _ROLE_VERBS for w in words):
        return False
    return bool(re.match(r"^[A-Z0-9]", raw))


def parse_resume(resume_text: str) -> ResumeProfile:
    text = (resume_text or "").replace("\xa0", " ")
    profile = ResumeProfile(text=text)
    section = ""
    current_dates: tuple[Optional[date], Optional[date]] = (None, None)
    idx = 1
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = _SECTION_HEAD.match(re.sub(r"^[\-•*]+\s*", "", line))
        if head and len(line) <= 48:
            section = head.group(1).lower()
            if "educat" in section or section == "academic":
                section = "education"
            elif "certif" in section or "license" in section:
                section = "certifications"
            elif "skill" in section or "technolog" in section:
                section = "skills"
            elif "project" in section:
                section = "projects"
            elif "experience" in section or "employment" in section:
                section = "experience"
            current_dates = (None, None)
            continue
        dm = _DATE_RANGE.search(line)
        if dm:
            current_dates = (_parse_date_token(dm.group("a")), _parse_date_token(dm.group("b")))
        elif _is_role_boundary(line, section):
            current_dates = (None, None)
        if section == "education" or _DEGREE_LINE.search(line):
            level, fields = extract_degree(line)
            if level and not profile.degree_level:
                profile.degree_level = level
                profile.degree_evidence = line
            if fields:
                for f in fields:
                    if f not in profile.degree_fields:
                        profile.degree_fields.append(f)
                if not profile.degree_evidence:
                    profile.degree_evidence = line
                if not profile.degree_level:
                    profile.degree_level = level
        if len(line) < 8:
            continue
        profile.snippets.append(
            ResumeSnippet(
                id=f"ev_{idx:02d}",
                text=line,
                section=section,
                start=current_dates[0],
                end=current_dates[1],
            )
        )
        idx += 1
    if not profile.degree_level:
        level, fields = extract_degree(text)
        profile.degree_level = level
        profile.degree_fields = fields
        if level:
            for sn in profile.snippets:
                if extract_degree(sn.text)[0]:
                    profile.degree_evidence = sn.text
                    break
    return profile


def requirement_concepts(req: CanonicalRequirement) -> list[str]:
    if req.category == CATEGORY_CERTIFICATION:
        names = [normalize_text(n) for n in (req.certification_names or []) if n]
        return names or ["certification"]
    if req.category == CATEGORY_CLEARANCE:
        return ["clearance"]
    concepts: list[str] = []
    for tech in req.technologies:
        if tech not in concepts:
            concepts.append(tech)
    blob = normalize_text(req.source_text + " " + req.canonical_text)
    for name in _CONCEPT_ALIASES:
        if name in concepts:
            continue
        if any(alias in blob for alias in _CONCEPT_ALIASES[name]):
            concepts.append(name)
    if req.category == CATEGORY_CONSULTING and "consulting" not in concepts:
        concepts.append("consulting")
    if req.category == CATEGORY_PRESENTATION and "presentation" not in concepts:
        concepts.append("presentation")
    if req.category == CATEGORY_LEADERSHIP:
        if "leadership" not in concepts:
            concepts.append("leadership")
        if "workstream" in blob and "workstream" not in concepts:
            concepts.append("workstream")
        if "engagement" in blob and "engagement" not in concepts:
            concepts.append("engagement")
    for cores in (req.alternative_concepts or {}).values():
        for concept in cores:
            if concept not in concepts:
                concepts.append(concept)
    return concepts


def _contains_concept(text: str, concept: str) -> bool:
    blob = normalize_text(text)
    for alias in _CONCEPT_ALIASES.get(concept, (concept,)):
        if not alias:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", blob):
            return True
    return False


def _generic_only_overlap(requirement: str, snippet: str) -> bool:
    req_toks = {t for t in normalize_text(requirement).split() if len(t) > 2}
    sn_toks = {t for t in normalize_text(snippet).split() if len(t) > 2}
    overlap = req_toks & sn_toks
    if not overlap:
        return True
    return all(t in _GENERIC for t in overlap)


def _section_compatible(category: str, section: str) -> bool:
    allowed = _CATEGORY_SECTIONS.get(category)
    if allowed is None:
        return True
    key = (section or "").lower()
    if key in allowed:
        return True
    if not key and "" in allowed:
        return True
    return False


class ResumeEvidenceRetriever:
    def __init__(self, resume_text: str):
        self.profile = parse_resume(resume_text)

    def retrieve(self, req: CanonicalRequirement) -> RetrievalResult:
        concepts = requirement_concepts(req)
        hits: list[EvidenceHit] = []
        for sn in self.profile.snippets:
            compatible = _section_compatible(req.category, sn.section)
            if req.category in {CATEGORY_DEGREE, CATEGORY_CERTIFICATION} and not compatible:
                continue
            if _SALESY.search(sn.text) and req.category in {
                CATEGORY_CLOUD, CATEGORY_DEGREE, CATEGORY_CERTIFICATION,
            }:
                continue
            if _SALESY.search(sn.text) and req.category in {CATEGORY_SKILL, CATEGORY_EXPERIENCE}:
                needed = set(req.technologies or []) | set(c.lower() for c in (req.alternatives or []))
                if needed & {"python", "azure", "aws", "gcp", "langchain", "langgraph", "autogen", "crewai"}:
                    if not extract_technologies(sn.text) and "python" not in normalize_text(sn.text):
                        continue
            matched = [c for c in concepts if _contains_concept(sn.text, c)]
            if not matched:
                if _generic_only_overlap(req.source_text or req.canonical_text, sn.text):
                    continue
                continue
            guarded = (
                req.category in {CATEGORY_CLOUD, CATEGORY_DEGREE, CATEGORY_CERTIFICATION}
                or bool(set(req.technologies or []) & _HARD_TECH)
                or "agentic" in normalize_text(req.source_text + " " + req.canonical_text)
            )
            if guarded and _BUSINESS_GROWTH.search(sn.text) and not (set(matched) & _HARD_TECH):
                continue
            weight = sum(_CONCEPT_WEIGHTS.get(c, 0.6) for c in matched)
            denom = max(1.0, sum(_CONCEPT_WEIGHTS.get(c, 0.6) for c in concepts) or 1.0)
            score = min(1.0, weight / denom)
            if req.category == CATEGORY_CLOUD:
                platforms = set(matched) & {"azure", "aws", "gcp"}
                if platforms:
                    core_hit = _cloud_core_hit(req, matched, sn.text)
                    if core_hit:
                        score = max(score, 0.86)
                    else:
                        score = min(max(score, PARTIAL_THRESHOLD), STRONG_THRESHOLD - 0.01)
                else:
                    score *= 0.25
            if not compatible:
                if req.category in {CATEGORY_DEGREE, CATEGORY_CERTIFICATION}:
                    continue
                score *= 0.25
            if score < 0.2:
                continue
            hits.append(
                EvidenceHit(
                    snippet_id=sn.id,
                    text=sn.text,
                    section=sn.section,
                    score=score,
                    matched_concepts=matched,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:3]
        matched = []
        for h in top:
            for c in h.matched_concepts:
                if c not in matched:
                    matched.append(c)
        missing = [c for c in concepts if c not in matched]
        duration_years, duration_verified = self._duration(req, top)
        best = top[0].score if top else 0.0
        compatible = bool(top) and (
            _section_compatible(req.category, top[0].section) or req.category not in {CATEGORY_DEGREE, CATEGORY_CERTIFICATION}
        )
        if req.category == CATEGORY_DEGREE:
            compatible = bool(self.profile.degree_level or self.profile.degree_evidence)
        return RetrievalResult(
            hits=top,
            best_score=best,
            category_compatible=compatible,
            duration_years=duration_years,
            duration_verified=duration_verified,
            matched_concepts=matched,
            missing_concepts=missing,
        )

    def _duration(self, req: CanonicalRequirement, hits: list[EvidenceHit]) -> tuple[Optional[float], bool]:
        if not req.min_years:
            return None, False
        ids = {h.snippet_id for h in hits}
        intervals: list[tuple[date, date]] = []
        for sn in self.profile.snippets:
            if sn.id not in ids or sn.start is None:
                continue
            intervals.append((sn.start, sn.end or date.today()))
        if not intervals:
            return None, False
        intervals.sort()
        merged: list[tuple[date, date]] = [intervals[0]]
        for start, end in intervals[1:]:
            prev_s, prev_e = merged[-1]
            if start <= prev_e:
                merged[-1] = (prev_s, max(prev_e, end))
            else:
                merged.append((start, end))
        months = sum(_calendar_months(s, e) for s, e in merged)
        return months / 12.0, True


def _calendar_months(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _cloud_core_hit(req: CanonicalRequirement, matched: list[str], snippet_text: str) -> bool:
    matched_l = {c.lower() for c in matched}
    for plat, cores in (req.alternative_concepts or {}).items():
        if plat.lower() not in matched_l:
            continue
        if cores and any(c in matched_l or _contains_concept(snippet_text, c) for c in cores):
            return True
    return False


def _certification_claimed(req: CanonicalRequirement, retriever: "ResumeEvidenceRetriever") -> tuple[bool, str]:
    hay = normalize_text(retriever.profile.text)
    names = [normalize_text(n) for n in (req.certification_names or []) if n]
    if names:
        for name in names:
            if name and name in hay:
                for sn in retriever.profile.snippets:
                    if name in normalize_text(sn.text):
                        return True, sn.text
                return True, next((sn.text for sn in retriever.profile.snippets if name in normalize_text(sn.text)), name)
        return False, ""
    if re.search(r"\b(certified|certification|certificate)\b", retriever.profile.text or "", re.I):
        ev = next(
            (sn.text for sn in retriever.profile.snippets if re.search(r"\b(certified|certification|certificate)\b", sn.text, re.I)),
            "",
        )
        return True, ev
    return False, ""


def duration_meets(min_years: float, years: Optional[float]) -> bool:
    if years is None:
        return False
    needed_months = int(round(float(min_years) * 12))
    have_months = int(round(float(years) * 12))
    return have_months >= needed_months


def _degree_status(req: CanonicalRequirement, profile: ResumeProfile) -> tuple[str, str, list[str], float]:
    if not profile.degree_level and not profile.degree_evidence:
        return (
            NOT_FOUND,
            "No degree is shown on the resume.",
            ["degree level", "technical field"],
            0.85,
        )
    wanted = [f for f in (req.degree_fields or []) if f not in {"finance", "economics"}] or [
        "computer science", "engineering", "data science", "ai",
    ]
    have = [normalize_text(f) for f in profile.degree_fields]
    technical_hit = any(f in _TECHNICAL_FIELDS for f in have) or any(
        any(w in h or h in w for h in have) for w in wanted if w in _TECHNICAL_FIELDS or w == "ai"
    )
    related_hit = any(f in _RELATED_FIELDS for f in have)
    field_txt = "/".join(
        ("AI" if f == "ai" else f.title()) for f in wanted if f not in {"finance", "economics"}
    )
    shown = profile.degree_evidence or ", ".join(profile.degree_fields) or profile.degree_level
    if technical_hit:
        return (
            COVERED,
            f"{profile.degree_level.title() if profile.degree_level else 'Degree'} is shown in a technical field.",
            [],
            0.86,
        )
    missing = [f"technical field ({field_txt} or related)"]
    reason = (
        f"{(profile.degree_level or 'Degree').title()}'s degree is shown, but the listed field is "
        f"{shown} rather than {field_txt}; whether the employer treats it as a related field is unclear."
    )
    if related_hit or profile.degree_level:
        return (NEEDS_CONFIRMATION if related_hit else PARTIAL, reason, missing, 0.62)
    return (PARTIAL, reason, missing, 0.58)


def classify_requirement(req: CanonicalRequirement, retriever: ResumeEvidenceRetriever) -> RequirementItem:
    retrieval = retriever.retrieve(req)
    extra = {
        "requirement_id": req.id,
        "canonical_text": req.canonical_text,
        "required_or_preferred": req.required_or_preferred,
        "min_years": req.min_years,
        "technologies": list(req.technologies),
        "subcomponents": list(req.subcomponents),
        "job_source_section": req.source_section or req.category,
        "job_source_text": req.source_text,
        "source_scope": req.source_scope or "WEB_DOCUMENT",
        "source_method": req.source_method,
        "semantic_similarity": round(retrieval.best_score, 3),
        "category_compatible": retrieval.category_compatible,
        "duration_verified": retrieval.duration_verified,
        "alternatives": list(req.alternatives),
        "source_texts": list(req.source_texts or ([req.source_text] if req.source_text else [])),
    }
    evidence_text = retrieval.hits[0].text if retrieval.hits else ""
    evidence_ids = [h.snippet_id for h in retrieval.hits]
    evidence_texts = [h.text for h in retrieval.hits]

    if req.category in {CATEGORY_TRAVEL, CATEGORY_SPONSORSHIP, CATEGORY_AUTHORIZATION}:
        explicit = bool(retrieval.hits) and retrieval.best_score >= PARTIAL_THRESHOLD
        if explicit:
            status = COVERED
            reason = "The resume states this constraint explicitly."
            missing: list[str] = []
        else:
            status = NEEDS_CONFIRMATION
            reason = "The resume does not state this constraint; it usually needs confirmation."
            missing = [req.canonical_text]
        return RequirementItem(
            requirement=req.canonical_text,
            status=status,
            resume_evidence=evidence_text,
            job_evidence=req.source_text,
            confidence=0.7 if explicit else 0.48,
            category=req.category,
            resume_evidence_ids=evidence_ids,
            resume_evidence_texts=evidence_texts,
            reason=reason,
            missing_components=missing,
            **extra,
        )

    if req.category == CATEGORY_DEGREE:
        status, reason, missing, conf = _degree_status(req, retriever.profile)
        ev = retriever.profile.degree_evidence or evidence_text
        return RequirementItem(
            requirement=req.canonical_text,
            status=status,
            resume_evidence=ev,
            job_evidence=req.source_text,
            confidence=conf,
            category=req.category,
            resume_evidence_ids=evidence_ids,
            resume_evidence_texts=[ev] if ev else evidence_texts,
            reason=reason,
            missing_components=missing,
            **extra,
        )

    if req.category == CATEGORY_CERTIFICATION:
        claimed, ev = _certification_claimed(req, retriever)
        if not claimed:
            return RequirementItem(
                requirement=req.canonical_text,
                status=NOT_FOUND,
                resume_evidence="",
                job_evidence=req.source_text,
                confidence=0.86,
                category=req.category,
                reason="The resume does not show this certification by name.",
                missing_components=req.certification_names or [req.canonical_text],
                **extra,
            )
        return RequirementItem(
            requirement=req.canonical_text,
            status=COVERED,
            resume_evidence=ev,
            job_evidence=req.source_text,
            confidence=0.9,
            category=req.category,
            resume_evidence_ids=evidence_ids,
            resume_evidence_texts=[ev] if ev else evidence_texts,
            reason="The named certification is shown on the resume.",
            missing_components=[],
            **extra,
        )

    missing = list(retrieval.missing_concepts)
    for sub in req.subcomponents:
        if req.category == CATEGORY_CLOUD:
            continue
        if normalize_text(sub) not in normalize_text(evidence_text) and not any(
            normalize_text(sub) in normalize_text(h.text) for h in retrieval.hits
        ):
            if sub not in missing and normalize_text(sub) not in {normalize_text(t) for t in req.technologies}:
                missing.append(sub)

    duration_ok = True
    if req.min_years:
        if not retrieval.duration_verified:
            duration_ok = False
            missing.append(f"{req.min_years:g}+ years verified from the resume timeline")
        elif not duration_meets(req.min_years, retrieval.duration_years):
            duration_ok = False
            missing.append(
                f"{req.min_years:g}+ years (timeline shows about {retrieval.duration_years:.1f} years)"
            )

    matched_alternative = ""
    if req.category == CATEGORY_CLOUD:
        for alt in req.alternatives or ["Azure", "AWS", "GCP"]:
            if alt.lower() in {c.lower() for c in retrieval.matched_concepts}:
                matched_alternative = alt
                break
        extra["matched_alternative"] = matched_alternative
        if matched_alternative:
            sibling = {"azure", "aws", "gcp"} - {matched_alternative.lower()}
            missing = [
                m for m in missing
                if normalize_text(m) not in sibling and normalize_text(m) != matched_alternative.lower()
            ]
        cores = (req.alternative_concepts or {}).get(matched_alternative, []) if matched_alternative else []
        branch_complete = bool(matched_alternative) and (
            not cores or _cloud_core_hit(req, retrieval.matched_concepts, evidence_text)
        )
        if matched_alternative and cores and not branch_complete:
            for concept in cores:
                if concept not in missing:
                    missing.append(concept)
    else:
        branch_complete = True

    score = retrieval.best_score
    category_ok = bool(retrieval.category_compatible)
    required_ok = True
    if req.category == CATEGORY_CLOUD:
        required_ok = bool(matched_alternative) or bool(
            set(retrieval.matched_concepts) & {"azure", "aws", "gcp"}
        )
    elif req.technologies:
        required_ok = any(t in retrieval.matched_concepts for t in req.technologies[:3]) or bool(
            retrieval.matched_concepts
        )

    if not retrieval.hits or not category_ok or not required_ok:
        return RequirementItem(
            requirement=req.canonical_text,
            status=NOT_FOUND,
            resume_evidence="",
            job_evidence=req.source_text,
            confidence=0.82,
            category=req.category,
            reason="The resume contains no credible, requirement-specific evidence.",
            missing_components=requirement_concepts(req) or [req.canonical_text],
            **extra,
        )

    strong = (
        category_ok
        and required_ok
        and duration_ok
        and branch_complete
        and score >= STRONG_THRESHOLD
        and not (
            req.category != CATEGORY_CLOUD
            and req.subcomponents
            and len(missing) >= max(2, math.ceil(len(req.subcomponents) * 0.5))
        )
    )
    if strong:
        reason = "Direct, requirement-specific resume evidence meets the strong-match threshold."
        if matched_alternative:
            reason = f"Matched the {matched_alternative} branch of the cloud agent stack."
        return RequirementItem(
            requirement=req.canonical_text,
            status=COVERED,
            resume_evidence=evidence_text,
            job_evidence=req.source_text,
            confidence=min(0.95, 0.7 + score / 4),
            category=req.category,
            resume_evidence_ids=evidence_ids,
            resume_evidence_texts=evidence_texts,
            reason=reason,
            missing_components=[],
            **extra,
        )

    if score >= PARTIAL_THRESHOLD:
        reason = "Related evidence exists, but one or more required elements are missing."
        if req.min_years and not duration_ok:
            reason = (
                "Related work is shown, but the resume timeline does not clearly support "
                f"the {req.min_years:g}+ year minimum."
            )
        return RequirementItem(
            requirement=req.canonical_text,
            status=PARTIAL,
            resume_evidence=evidence_text,
            job_evidence=req.source_text,
            confidence=max(0.5, min(0.7, score)),
            category=req.category,
            resume_evidence_ids=evidence_ids,
            resume_evidence_texts=evidence_texts,
            reason=reason,
            missing_components=missing,
            **extra,
        )

    return RequirementItem(
        requirement=req.canonical_text,
        status=NOT_FOUND,
        resume_evidence="",
        job_evidence=req.source_text,
        confidence=0.8,
        category=req.category,
        reason="Matched tokens were too generic to count as evidence.",
        missing_components=requirement_concepts(req) or [req.canonical_text],
        **extra,
    )


def gap_priority(item: RequirementItem) -> tuple[int, int, int]:
    required = 0 if (item.required_or_preferred or "required") == "required" else 1
    status_rank = {NOT_FOUND: 0, PARTIAL: 1, NEEDS_CONFIRMATION: 2, COVERED: 3}.get(item.status, 4)
    impact = 0
    if item.category in {CATEGORY_EXPERIENCE, CATEGORY_CLOUD, CATEGORY_SKILL, CATEGORY_DEGREE}:
        impact -= 2
    if item.min_years:
        impact -= 1
    if item.category == CATEGORY_CERTIFICATION:
        impact -= 1
    return (required, status_rank, impact)
