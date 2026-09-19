"""Canonical job requirements: filter, split, and deduplicate before matching."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

COMPENSATION_CONTEXT = "COMPENSATION_CONTEXT"
LEGAL_CONTEXT = "LEGAL_CONTEXT"
BENEFITS_CONTEXT = "BENEFITS_CONTEXT"
NON_REQUIREMENT = "NON_REQUIREMENT"
FRAGMENT = "FRAGMENT"
VALID = "VALID"

CATEGORY_SKILL = "skill"
CATEGORY_EXPERIENCE = "experience"
CATEGORY_CLOUD = "cloud"
CATEGORY_DEGREE = "degree"
CATEGORY_CERTIFICATION = "certification"
CATEGORY_CONSULTING = "consulting"
CATEGORY_PRESENTATION = "presentation"
CATEGORY_TRAVEL = "travel"
CATEGORY_SPONSORSHIP = "sponsorship"
CATEGORY_AUTHORIZATION = "authorization"
CATEGORY_CLEARANCE = "clearance"
CATEGORY_LEADERSHIP = "leadership"

_FRAGMENT_EXACT = frozenset({
    "and/or", "or", "and", "to include", "including", "etc", "etc.",
    "e.g.", "e.g", "i.e.", "i.e", "such as", "/", "-", "—",
})
_FRAGMENT_ONLY = re.compile(
    r"^(and/or|and|or|to include|including|etc\.?|e\.g\.?|i\.e\.?|such as)[\s./-]*$",
    re.I,
)
_NOUNISH = re.compile(
    r"\b([A-Z][a-zA-Z0-9+#./]{2,}|python|java|azure|aws|gcp|sql|llm|rag|"
    r"degree|certif|clearance|travel|sponsor|bachelor|master|phd|"
    r"agentic|orchestration|framework|kubernetes|docker)\b",
    re.I,
)

_COMPENSATION = re.compile(
    r"\b(wage range|salary|compensation decisions|annual incentive|"
    r"discretionary annual|pay range|base pay|\$\s?\d{2,3},\d{3})\b",
    re.I,
)
_LEGAL = re.compile(
    r"\b(equal opportunity|eeo|affirmative action|reasonable accommodation|"
    r"protected veteran|disability status|not typical for an individual to be hired)\b",
    re.I,
)
_BENEFITS = re.compile(
    r"\b(health insurance|401\s?\(?k\)?|paid time off|wellness program|"
    r"dental|vision plan|parental leave)\b",
    re.I,
)
_NAV = re.compile(
    r"\b(apply now|submit application|click here|back to jobs|sign in to apply)\b",
    re.I,
)

_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", re.I)
_TRAVEL = re.compile(r"travel(?:ling)?(?:\s+up\s+to)?\s+(\d+)\s*%", re.I)
_DEGREE_LEVEL = re.compile(
    r"\b(ph\.?d|doctorate|master'?s?|m\.?s\.?|m\.?a\.?|mba|bachelor'?s?|b\.?s\.?|b\.?a\.?|associate)\b",
    re.I,
)
_IN_FIELD = re.compile(
    r"(?:in|of)\s+([A-Za-z][A-Za-z0-9 &/,+-]+?)(?:\s+or\s+related|\s*$|,)",
    re.I,
)

_TECH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python", re.compile(r"\bpython\b", re.I)),
    ("java", re.compile(r"\bjava\b", re.I)),
    ("javascript", re.compile(r"\bjavascript\b|\bjs\b", re.I)),
    ("typescript", re.compile(r"\btypescript\b|\bts\b", re.I)),
    ("sql", re.compile(r"\bsql\b", re.I)),
    ("azure", re.compile(r"\bazure\b|ai foundry", re.I)),
    ("aws", re.compile(r"\baws\b|amazon bedrock|\bbedrock\b", re.I)),
    ("gcp", re.compile(r"\bgcp\b|vertex ai|\bgemini\b|bigquery|google ai", re.I)),
    ("langchain", re.compile(r"\blangchain\b", re.I)),
    ("langgraph", re.compile(r"\blanggraph\b", re.I)),
    ("autogen", re.compile(r"\bautogen\b", re.I)),
    ("crewai", re.compile(r"\bcrewai\b|crew ai", re.I)),
    ("semantic kernel", re.compile(r"semantic kernel", re.I)),
    ("kubernetes", re.compile(r"\bkubernetes\b|\bgke\b", re.I)),
    ("docker", re.compile(r"\bdocker\b", re.I)),
    ("mlops", re.compile(r"\bmlops\b|\baiops\b", re.I)),
    ("rag", re.compile(r"\brag\b|retrieval augmented", re.I)),
    ("llm", re.compile(r"\bllms?\b|large language", re.I)),
    ("microservices", re.compile(r"\bmicroservices\b", re.I)),
    ("pytorch", re.compile(r"\bpytorch\b", re.I)),
    ("tensorflow", re.compile(r"\btensorflow\b", re.I)),
]

_CLOUD_TECH = frozenset({"azure", "aws", "gcp"})
_AGENT_TECH = frozenset({"langchain", "langgraph", "autogen", "crewai", "semantic kernel"})
_CLOUD_BRANCH_CORE = {
    "Azure": ("ai foundry", "foundry"),
    "AWS": ("bedrock", "amazon bedrock"),
    "GCP": ("vertex", "vertex ai", "gemini"),
}
_CERT_NAMES = re.compile(
    r"(azure ai engineer associate|azure solutions architect expert|"
    r"aws certified[\w\s-]*|google professional[\w\s-]*|pmp|comptia[\w\s-]*)",
    re.I,
)

_KNOWN_SKILLS = frozenset({
    "python", "java", "javascript", "typescript", "sql", "aws", "azure", "gcp",
    "langgraph", "langchain", "autogen", "crewai", "pytorch", "tensorflow",
    "kubernetes", "docker", "rag", "llm", "llms",
})


def normalize_text(text: str) -> str:
    t = (text or "").replace("\xa0", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\s+", " ", t).strip().lower()
    t = re.sub(r"[^a-z0-9+#./' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def identity_key(text: str) -> str:
    return normalize_text(text)


class RequirementFragmentValidator:
    def reject(self, text: str) -> bool:
        raw = (text or "").strip().replace("\xa0", " ")
        if not raw:
            return True
        compact = re.sub(r"\s+", " ", raw).strip().lower().rstrip(".")
        if compact in _FRAGMENT_EXACT or _FRAGMENT_ONLY.match(compact):
            return True
        if len(compact) < 3:
            return True
        if len(compact) < 12 and not _NOUNISH.search(raw) and compact not in _KNOWN_SKILLS:
            return True
        tokens = [t for t in re.findall(r"[a-z0-9+#./]+", compact) if t]
        if tokens and all(t in {"and", "or", "to", "include", "including", "etc", "e", "g", "i"} for t in tokens):
            return True
        if not _NOUNISH.search(raw) and compact not in _KNOWN_SKILLS and len(compact.split()) <= 2:
            return True
        return False


class RequirementContentClassifier:
    def classify(self, text: str) -> str:
        raw = (text or "").strip()
        if RequirementFragmentValidator().reject(raw):
            return FRAGMENT
        if _COMPENSATION.search(raw):
            return COMPENSATION_CONTEXT
        if _LEGAL.search(raw):
            return LEGAL_CONTEXT
        if _BENEFITS.search(raw):
            return BENEFITS_CONTEXT
        if _NAV.search(raw):
            return NON_REQUIREMENT
        return VALID


@dataclass
class CanonicalRequirement:
    id: str
    canonical_text: str
    category: str
    required_or_preferred: str = "required"
    min_years: Optional[float] = None
    technologies: list[str] = field(default_factory=list)
    degree_level: str = ""
    degree_fields: list[str] = field(default_factory=list)
    travel_percent: Optional[int] = None
    certification_names: list[str] = field(default_factory=list)
    subcomponents: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    alternative_concepts: dict[str, list[str]] = field(default_factory=dict)
    source_text: str = ""
    source_texts: list[str] = field(default_factory=list)
    source_section: str = ""
    source_scope: str = "WEB_DOCUMENT"
    source_method: str = ""
    confidence: float = 0.8
    aliases: list[str] = field(default_factory=list)
    preferred: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalizationStats:
    raw_requirement_count: int = 0
    canonical_requirement_count: int = 0
    duplicates_removed: int = 0
    duplicate_groups: int = 0
    non_requirements_removed: int = 0
    fragments_removed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def cloud_branch_core_concepts(text: str, platform: str) -> list[str]:
    blob = normalize_text(text)
    found: list[str] = []
    for marker in _CLOUD_BRANCH_CORE.get(platform, ()):
        if marker in blob and marker not in found:
            found.append(marker)
    return found


def extract_technologies(text: str) -> list[str]:
    found: list[str] = []
    for name, pat in _TECH_PATTERNS:
        if pat.search(text or "") and name not in found:
            found.append(name)
    return found


def extract_years(text: str) -> Optional[float]:
    m = _YEARS.search(text or "")
    return float(m.group(1)) if m else None


def extract_degree(text: str) -> tuple[str, list[str]]:
    raw = text or ""
    level = ""
    m = _DEGREE_LEVEL.search(raw)
    if m:
        token = m.group(1).lower().replace("'", "").replace(".", "")
        if token.startswith("ph") or token == "doctorate":
            level = "phd"
        elif token.startswith("master") or token in {"ms", "ma", "mba"}:
            level = "master"
        elif token.startswith("bachelor") or token in {"bs", "ba"}:
            level = "bachelor"
        else:
            level = "associate"
    fields: list[str] = []
    low = raw.lower()
    for field in (
        "computer science", "engineering", "data science", "artificial intelligence",
        "machine learning", "software", "information systems", "statistics",
        "mathematics", "physics", "finance", "economics",
    ):
        if field in low:
            fields.append(field)
    if " ai" in f" {low}" or low.startswith("ai") or ", ai" in low:
        if "artificial intelligence" not in fields:
            fields.append("ai")
    return level, fields


def infer_category(text: str, source_section: str = "", technologies: Optional[list[str]] = None) -> str:
    low = (text or "").lower()
    section = (source_section or "").lower()
    techs = technologies or extract_technologies(text)
    if _TRAVEL.search(low) or "travel" in low:
        return CATEGORY_TRAVEL
    if "sponsor" in low or "immigration" in low or "work authorization" in low or "visa" in low:
        return CATEGORY_SPONSORSHIP if "sponsor" in low or "immigration" in low else CATEGORY_AUTHORIZATION
    if "clearance" in low or "ts/sci" in low or "public trust" in low:
        return CATEGORY_CLEARANCE
    if "certif" in low or section == "certification":
        return CATEGORY_CERTIFICATION
    if _DEGREE_LEVEL.search(low) or section == "education" or "degree" in low:
        return CATEGORY_DEGREE
    if any(t in _CLOUD_TECH for t in techs) or re.search(r"\b(azure|aws|gcp|cloud platform)\b", low):
        return CATEGORY_CLOUD
    if "consult" in low or "client-facing" in low or "client facing" in low:
        return CATEGORY_CONSULTING
    if re.search(r"\b(presentation|workshop|public speaking|audience)\b", low):
        return CATEGORY_PRESENTATION
    if re.search(r"\b(lead(?:ing)?|workstream|engagements?)\b", low):
        return CATEGORY_LEADERSHIP
    if extract_years(low) or "experience" in low or section in {"experience", "required"}:
        return CATEGORY_EXPERIENCE
    return CATEGORY_SKILL


def _subcomponents(text: str) -> list[str]:
    parts: list[str] = []
    paren = re.search(r"\(([^)]{8,})\)", text or "")
    if paren:
        inner = paren.group(1)
        for chunk in re.split(r",|/", inner):
            item = chunk.strip(" .;")
            if len(item) >= 3 and not RequirementFragmentValidator().reject(item):
                parts.append(item)
    inc = re.search(r"including\s+(.+?)(?:\(|$)", text or "", re.I)
    if inc:
        body = inc.group(1)
        for chunk in re.split(r",| and ", body):
            item = chunk.strip(" .;")
            if len(item) >= 8 and not RequirementFragmentValidator().reject(item):
                parts.append(item)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = normalize_text(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _canonical_label(text: str, category: str, technologies: list[str], min_years: Optional[float]) -> str:
    raw = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip().rstrip(".")
    if category == CATEGORY_DEGREE:
        level, fields = extract_degree(raw)
        field_txt = "/".join(
            f.title() if f != "ai" else "AI"
            for f in (fields or ["CS", "Engineering", "Data Science", "AI"])
            if f not in {"finance", "economics"}
        ) or "CS/Engineering/Data Science/AI"
        if level:
            return f"Technical degree field ({field_txt} or related)"
        return raw
    if category == CATEGORY_CLOUD:
        names = []
        if "azure" in technologies:
            names.append("Azure")
        if "aws" in technologies:
            names.append("AWS")
        if "gcp" in technologies:
            names.append("GCP")
        if not names:
            names = ["Azure", "AWS", "GCP"]
        return "Cloud agentic AI stack (" + " or ".join(names) + ")"
    if category == CATEGORY_TRAVEL:
        m = _TRAVEL.search(raw)
        pct = m.group(1) if m else ""
        return f"Ability to travel{f' up to {pct}%' if pct else ''}".strip()
    if category == CATEGORY_SPONSORSHIP:
        return "Immigration sponsorship / work authorization"
    if len(raw) > 220:
        return raw[:217].rstrip() + "..."
    return raw


def _aliases(req: CanonicalRequirement) -> list[str]:
    aliases = [normalize_text(req.canonical_text), normalize_text(req.source_text)]
    aliases.extend(req.technologies)
    aliases.extend(normalize_text(s) for s in req.subcomponents)
    if req.category == CATEGORY_DEGREE:
        aliases.extend(["degree", "bachelor", "master", "education"])
    if req.category == CATEGORY_CLOUD:
        aliases.extend(["cloud", "azure", "aws", "gcp"])
    if req.category == CATEGORY_TRAVEL:
        aliases.append("travel")
    if "python" in req.technologies:
        aliases.append("python")
    if req.category == CATEGORY_EXPERIENCE and any(t in _AGENT_TECH or t == "langchain" for t in req.technologies):
        aliases.extend(["agentic", "agentic frameworks", "frameworks", "orchestration"])
    if "agentic" in normalize_text(req.source_text):
        aliases.extend(["agentic", "agentic frameworks", "orchestration"])
    return [a for a in dict.fromkeys(aliases) if a]


def _semantic_identity(req: CanonicalRequirement) -> str:
    if req.category in {
        CATEGORY_DEGREE, CATEGORY_TRAVEL, CATEGORY_SPONSORSHIP, CATEGORY_AUTHORIZATION,
    }:
        return req.category
    if req.category == CATEGORY_CLOUD:
        return "cloud:" + ",".join(sorted(set(req.technologies) & _CLOUD_TECH) or req.technologies)
    if req.min_years and req.technologies:
        return f"years:{req.min_years}|tech:{','.join(sorted(req.technologies[:4]))}|{req.category}"
    if req.min_years:
        return f"years:{req.min_years}|{req.category}|{normalize_text(req.canonical_text)[:48]}"
    if req.technologies:
        return f"tech:{','.join(sorted(req.technologies))}|{req.category}"
    return normalize_text(req.canonical_text)[:80]


def _is_subset_duplicate(a: CanonicalRequirement, b: CanonicalRequirement) -> bool:
    if a.category != b.category and not (
        {a.category, b.category} <= {CATEGORY_SKILL, CATEGORY_EXPERIENCE, CATEGORY_DEGREE}
    ):
        short, long = sorted((a, b), key=lambda r: len(normalize_text(r.source_text or r.canonical_text)))
        if len(normalize_text(short.source_text or short.canonical_text).split()) <= 4:
            hay = normalize_text(long.source_text + " " + long.canonical_text)
            if normalize_text(short.source_text or short.canonical_text) in hay:
                return True
        return False
    ta, tb = set(a.technologies), set(b.technologies)
    if ta and tb and (ta <= tb or tb <= ta):
        if a.min_years and b.min_years and a.min_years != b.min_years:
            return False
        return True
    na, nb = normalize_text(a.source_text), normalize_text(b.source_text)
    if na and nb and (na in nb or nb in na) and min(len(na), len(nb)) >= 12:
        return True
    return False


def _from_raw(raw: dict[str, Any], index: int) -> CanonicalRequirement:
    text = str(raw.get("requirement") or raw.get("text") or "").strip()
    section = str(raw.get("job_source_section") or raw.get("category") or "")
    preferred = bool(raw.get("preferred")) or section.lower() == "preferred"
    techs = extract_technologies(text)
    years = extract_years(text)
    category = infer_category(text, section, techs)
    level, fields = extract_degree(text)
    travel = None
    tm = _TRAVEL.search(text)
    if tm:
        travel = int(tm.group(1))
    certs = [m.group(0).strip() for m in _CERT_NAMES.finditer(text)]
    req = CanonicalRequirement(
        id=f"req_{index:02d}",
        canonical_text=_canonical_label(text, category, techs, years),
        category=category,
        required_or_preferred="preferred" if preferred else "required",
        min_years=years,
        technologies=techs,
        degree_level=level,
        degree_fields=fields,
        travel_percent=travel,
        certification_names=certs,
        subcomponents=_subcomponents(text),
        source_text=text,
        source_texts=[text] if text else [],
        source_section=section,
        source_scope=str(raw.get("source_scope") or "WEB_DOCUMENT"),
        source_method=str(raw.get("source_method") or ""),
        confidence=float(raw.get("confidence") or 0.8),
        preferred=preferred,
    )
    if category == CATEGORY_EXPERIENCE and "agentic" in normalize_text(text) and not req.subcomponents:
        req.subcomponents = [t for t in techs if t in _AGENT_TECH]
    if category == CATEGORY_CLOUD and not req.alternatives:
        req.alternatives = [
            label for tech, label in (("azure", "Azure"), ("aws", "AWS"), ("gcp", "GCP")) if tech in techs
        ]
        req.alternative_concepts = {
            label: cloud_branch_core_concepts(text, label)
            for tech, label in (("azure", "Azure"), ("aws", "AWS"), ("gcp", "GCP"))
            if tech in techs
        }
    req.aliases = _aliases(req)
    return req


def _merge_cloud_group(items: list[dict[str, Any]], start_index: int) -> CanonicalRequirement:
    texts = [str(i.get("requirement") or "") for i in items]
    combined = " / ".join(t for t in texts if t)
    techs: list[str] = []
    for t in texts:
        for tech in extract_technologies(t):
            if tech not in techs:
                techs.append(tech)
    preferred = any(bool(i.get("preferred")) for i in items)
    alts = [label for tech, label in (("azure", "Azure"), ("aws", "AWS"), ("gcp", "GCP")) if tech in techs]
    req = CanonicalRequirement(
        id=f"req_{start_index:02d}",
        canonical_text=_canonical_label(combined, CATEGORY_CLOUD, techs, None),
        category=CATEGORY_CLOUD,
        required_or_preferred="preferred" if preferred else "required",
        technologies=techs,
        subcomponents=texts,
        alternatives=alts or ["Azure", "AWS", "GCP"],
        source_text=combined,
        source_texts=[t for t in texts if t],
        source_section=str(items[0].get("job_source_section") or "required"),
        source_scope=str(items[0].get("source_scope") or "WEB_DOCUMENT"),
        source_method=str(items[0].get("source_method") or ""),
        preferred=preferred,
    )
    branch_concepts: dict[str, list[str]] = {}
    for text in texts:
        for tech, label in (("azure", "Azure"), ("aws", "AWS"), ("gcp", "GCP")):
            if tech in extract_technologies(text) or re.search(rf"\b{tech}\b", text or "", re.I):
                branch_concepts[label] = cloud_branch_core_concepts(text, label)
    req.alternative_concepts = branch_concepts
    req.aliases = _aliases(req)
    return req


def _cloud_family(text: str) -> bool:
    return bool(set(extract_technologies(text)) & _CLOUD_TECH) or bool(
        re.search(r"\b(azure|aws|gcp|bedrock|vertex)\b", text or "", re.I)
    )


def canonicalize_requirements(raw_items: list[dict[str, Any]]) -> tuple[list[CanonicalRequirement], CanonicalizationStats]:
    stats = CanonicalizationStats(raw_requirement_count=len(raw_items or []))
    classifier = RequirementContentClassifier()
    kept: list[CanonicalRequirement] = []
    i = 0
    seq = 1
    items = list(raw_items or [])
    while i < len(items):
        text = str(items[i].get("requirement") or items[i].get("text") or "")
        kind = classifier.classify(text)
        if kind == FRAGMENT:
            stats.fragments_removed += 1
            i += 1
            continue
        if kind != VALID:
            stats.non_requirements_removed += 1
            i += 1
            continue
        group = [items[i]]
        j = i + 1
        if _cloud_family(text):
            while j < len(items):
                nxt_text = str(items[j].get("requirement") or "")
                nxt_kind = classifier.classify(nxt_text)
                if nxt_kind == FRAGMENT and j + 1 < len(items):
                    after = str(items[j + 1].get("requirement") or "")
                    if classifier.classify(after) == VALID and _cloud_family(after):
                        stats.fragments_removed += 1
                        group.append(items[j + 1])
                        j += 2
                        continue
                if nxt_kind == VALID and _cloud_family(nxt_text):
                    group.append(items[j])
                    j += 1
                    continue
                break
        if len(group) > 1 and all(_cloud_family(str(g.get("requirement") or "")) for g in group):
            kept.append(_merge_cloud_group(group, seq))
            seq += 1
            i = j
            continue
        kept.append(_from_raw(items[i], seq))
        seq += 1
        i += 1

    merged: list[CanonicalRequirement] = []
    used = [False] * len(kept)
    for a_i, a in enumerate(kept):
        if used[a_i]:
            continue
        group = [a]
        used[a_i] = True
        key_a = _semantic_identity(a)
        for b_i in range(a_i + 1, len(kept)):
            if used[b_i]:
                continue
            b = kept[b_i]
            if key_a == _semantic_identity(b) or _is_subset_duplicate(a, b):
                group.append(b)
                used[b_i] = True
        if len(group) > 1:
            stats.duplicate_groups += 1
            stats.duplicates_removed += len(group) - 1
            winner = max(group, key=lambda r: (len(r.source_text), len(r.canonical_text), len(r.technologies)))
            proven: list[str] = []
            for extra in group:
                for src in list(extra.source_texts or []) + ([extra.source_text] if extra.source_text else []):
                    if src and src not in proven:
                        proven.append(src)
                if extra is winner:
                    continue
                for tech in extra.technologies:
                    if tech not in winner.technologies:
                        winner.technologies.append(tech)
                for sub in extra.subcomponents:
                    if sub not in winner.subcomponents:
                        winner.subcomponents.append(sub)
                for alt in extra.alternatives:
                    if alt not in winner.alternatives:
                        winner.alternatives.append(alt)
                for plat, cores in (extra.alternative_concepts or {}).items():
                    existing = winner.alternative_concepts.setdefault(plat, [])
                    for concept in cores:
                        if concept not in existing:
                            existing.append(concept)
                for field_name in extra.degree_fields:
                    if field_name not in winner.degree_fields:
                        winner.degree_fields.append(field_name)
                if extra.degree_level and (
                    not winner.degree_level
                    or len(extra.source_text) > len(winner.source_text)
                ):
                    if not winner.degree_level:
                        winner.degree_level = extra.degree_level
                if extra.source_text and extra.source_text not in winner.aliases:
                    winner.aliases.append(normalize_text(extra.source_text))
            winner.source_texts = proven
            winner.aliases = _aliases(winner)
            merged.append(winner)
        else:
            merged.append(a)

    for idx, req in enumerate(merged, start=1):
        req.id = f"req_{idx:02d}"
    stats.canonical_requirement_count = len(merged)
    print(
        f"[RequirementCanon] raw_requirement_count={stats.raw_requirement_count} "
        f"canonical_requirement_count={stats.canonical_requirement_count} "
        f"duplicate_groups={stats.duplicate_groups} "
        f"duplicates_removed={stats.duplicates_removed} "
        f"non_requirements_removed={stats.non_requirements_removed} "
        f"fragments_removed={stats.fragments_removed}"
    )
    return merged, stats
