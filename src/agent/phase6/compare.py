"""Deterministic job/resume comparison. Never invents resume experience."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from src.agent.phase6.compare_types import (
    COVERED,
    NEEDS_CONFIRMATION,
    NOT_FOUND,
    PARTIAL,
    TASK_TYPE_JOB_RESUME_COMPARISON,
    ComparisonResult,
    RequirementItem,
)
from src.agent.phase6.evidence import (
    STRONG_THRESHOLD,
    ResumeEvidenceRetriever,
    classify_requirement,
    gap_priority,
)
from src.agent.phase6.lookup import RequirementLookup
from src.agent.phase6.requirements import (
    canonicalize_requirements,
    normalize_text,
)

_COMPARE_GOAL = re.compile(
    r"\b(compare|gap|missing|requirements?|versus|vs\.?)\b",
    re.I,
)
_RESUME = re.compile(r"\b(resume|r[eé]sum[eé]|cv)\b", re.I)
_JOB = re.compile(r"\b(job|listing|role|position|page|requirements?)\b", re.I)
_COMPARE_STEP = re.compile(r"\b(compare|gap|missing|match)\b", re.I)
_STEP_LABEL = re.compile(r"^\s*completed:\s+", re.I)

_STOP = frozenset(
    """a an the and or of to for in on at with by from as is are was were be been
    this that these those it its your you we they their our my me i about into
    over under than then also will can may must should including using such
    other more most any all both each few some only own same so than too very
    just not no nor but if because until while during before after above below
    between through out off up down again further once here there when where
    why how both few more most other some such no nor not only own same so than
    too very can will just don should now role job page resume cv work team
    ability able strong excellent well new""".split()
)

_SKILLS = (
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
    "rust", "sql", "nosql", "mongodb", "postgresql", "mysql", "redis",
    "aws", "azure", "gcp", "kubernetes", "docker", "terraform", "ansible",
    "linux", "git", "ci/cd", "machine learning", "deep learning", "nlp",
    "llm", "llms", "genai", "generative ai", "rag", "langgraph", "langchain",
    "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy",
    "agentic", "multi-agent", "autogen", "crewai",
    "rest", "graphql", "microservices", "distributed systems",
    "spark", "hadoop", "kafka", "airflow", "dbt",
    "react", "node.js", "fastapi", "django", "flask",
    "security clearance", "secret clearance", "ts/sci", "top secret",
    "public trust", "comptia", "pmp", "aws certified",
    "bachelor", "master", "phd", "computer science",
)

_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")


def is_comparison_goal(goal: str) -> bool:
    text = goal or ""
    return bool(_COMPARE_GOAL.search(text) and _RESUME.search(text) and _JOB.search(text))


def is_comparison_step(description: str, goal: str = "") -> bool:
    if _COMPARE_STEP.search(description or ""):
        return True
    return is_comparison_goal(goal) and not (description or "").strip()


def is_step_label(text: str) -> bool:
    return bool(_STEP_LABEL.match(text or ""))


def job_page_model_id(url: str = "", title: str = "") -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}|{(title or '').strip().lower()}"


def _norm(text: str) -> str:
    return normalize_text(text)


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t and t not in _STOP and len(t) > 1}


def _lines(text: str) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _BULLET.sub("", line).strip()
        if line:
            out.append(line)
    return out


def _company_from_url(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] in {"apply", "jobs", "careers", "boards"}:
        return parts[1].title()
    if parts:
        return parts[0].title()
    return ""


def extract_job_requirements(page_text: str, *, title: str = "", url: str = "") -> dict[str, Any]:
    from src.agent.browser.page_model import WEB_DOCUMENT, is_browser_noise
    from src.agent.phase6.job_posting import (
        PREFERRED_REQUIREMENT,
        VALID_REQUIREMENT,
        JobRequirementValidator,
        extract_sections,
    )

    text = "\n".join(ln for ln in _lines(page_text or "") if not is_browser_noise(ln))
    job_title = title.strip()
    company = _company_from_url(url)
    buckets = extract_sections(text, source_method="page_reader")
    validator = JobRequirementValidator()
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(req: str, category: str, section: str = "", preferred: bool = False) -> None:
        key = _norm(req)
        if not key or key in seen or len(key) < 3 or is_browser_noise(req):
            return
        kind = validator.classify(req, section or category, WEB_DOCUMENT)
        if kind not in {VALID_REQUIREMENT, PREFERRED_REQUIREMENT}:
            return
        seen.add(key)
        requirements.append({
            "requirement": req.strip().rstrip("."),
            "category": category,
            "preferred": preferred or kind == PREFERRED_REQUIREMENT,
            "job_evidence": req.strip(),
            "job_source_section": section or category,
            "job_source_text": req.strip(),
            "source_scope": WEB_DOCUMENT,
            "source_method": "page_reader",
        })

    for ev in buckets.get("required") or []:
        _add(ev.text, "required", "required")
    for ev in buckets.get("preferred") or []:
        _add(ev.text, "preferred", "preferred", preferred=True)
    for ev in buckets.get("education") or []:
        _add(ev.text, "education", "education")
    if not requirements:
        blob = _norm(text)
        for skill in sorted(_SKILLS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(skill.lower())}\b", blob):
                _add(skill, "skills", "skills")
    if not requirements and job_title and not is_browser_noise(job_title):
        for tok in _tokens(job_title):
            if tok not in _STOP:
                _add(tok, "skills", "title")
    return {
        "job_title": job_title,
        "company": company,
        "requirements": requirements,
        "extracted": bool(requirements),
    }


def extract_resume_evidence(resume_text: str) -> dict[str, Any]:
    text = (resume_text or "").strip()
    lines = _lines(text)
    snippets = [ln for ln in lines if len(ln) >= 8][:80]
    skills_found = [s for s in _SKILLS if s.lower() in _norm(text)]
    return {
        "text": text,
        "lines": snippets,
        "skills": skills_found,
        "extracted": bool(text),
    }


class JobResumeComparisonEngine:
    """Compare extracted job requirements to resume evidence only."""

    def compare(
        self,
        *,
        page_text: str,
        resume_text: str,
        job_title: str = "",
        job_url: str = "",
        resume_path: str = "",
        task_id: str = "",
        hwnd: int = 0,
        page_model: Any = None,
    ) -> ComparisonResult:
        import time

        from src.agent.browser.page_model import JOB_LISTING, is_browser_noise
        from src.agent.phase6.job_posting import (
            JOB_CONTENT_CONTAMINATION_DETECTED,
            assert_web_document_requirements,
            build_job_posting,
        )

        model = page_model
        if model is None and job_url:
            try:
                from src.agent.browser.document import build_page_model

                built = build_page_model(url=job_url, title=job_title, hwnd=hwnd)
                if built.structured_jobposting_found or (
                    built.page_type == JOB_LISTING and built.page_type_confidence >= 0.5
                ):
                    model = built
                    page_text = built.main_content or page_text
                    job_url = built.document_url or job_url
                    job_title = built.tab_ref or job_title
            except Exception as e:
                print(f"[Comparison] page model build failed: {e}")
        posting = None
        reqs: list[dict[str, Any]] = []
        if model is not None:
            posting = build_job_posting(page_model=model, document_text=page_text, url=job_url, title=job_title)
            reqs = posting.validated_requirements()
            job_title = posting.title or job_title
            company = posting.company
            job_url = posting.source_url or job_url
        else:
            company = ""
        if not reqs:
            job = extract_job_requirements(page_text, title=job_title, url=job_url)
            reqs = list(job.get("requirements") or [])
            job_title = job.get("job_title") or job_title
            company = company or str(job.get("company") or "")
        contaminated = assert_web_document_requirements(reqs)
        chrome_in_text = any(is_browser_noise(ln) for ln in (page_text or "").splitlines()[:40])
        if contaminated or (chrome_in_text and not (posting and posting.structured_jobposting_found) and not reqs):
            print(
                f"[Comparison] comparison_aborted_reason={JOB_CONTENT_CONTAMINATION_DETECTED} "
                f"structured={bool(posting and posting.structured_jobposting_found)}"
            )
            return ComparisonResult(
                task_id=task_id,
                job_title=job_title,
                company=company,
                generated_at=time.time(),
                job_page_url=job_url,
                job_page_title=job_title,
                resume_path=resume_path,
                aborted=True,
                abort_reason=JOB_CONTENT_CONTAMINATION_DETECTED,
            )
        canonical, stats = canonicalize_requirements(reqs)
        retriever = ResumeEvidenceRetriever(resume_text)
        result = ComparisonResult(
            task_id=task_id,
            job_title=job_title,
            company=company,
            generated_at=time.time(),
            job_page_url=job_url,
            job_page_title=job_title,
            resume_path=resume_path,
            job_page_model_id=job_page_model_id(job_url, job_title),
            raw_requirement_count=stats.raw_requirement_count,
            canonical_requirement_count=stats.canonical_requirement_count,
            duplicates_removed=stats.duplicates_removed,
            duplicate_groups=stats.duplicate_groups,
            non_requirements_removed=stats.non_requirements_removed,
            fragments_removed=stats.fragments_removed,
        )
        if not result.covered_requirements:
            result.strong_match_threshold_note = (
                f"A strong match needs direct, requirement-specific resume evidence "
                f"at or above {STRONG_THRESHOLD:.0%} confidence. Generic word overlap is not enough."
            )
        for req in canonical:
            if is_browser_noise(req.source_text or req.canonical_text):
                continue
            item = classify_requirement(req, retriever)
            print(
                f"[Requirement] requirement_id={item.requirement_id or '-'} "
                f"category={item.category or '-'} "
                f"required_or_preferred={item.required_or_preferred or 'required'} "
                f"canonical_text={item.requirement[:80]!r} "
                f"resume_evidence_count={len(item.resume_evidence_texts or ([item.resume_evidence] if item.resume_evidence else []))} "
                f"semantic_similarity={item.semantic_similarity:.3f} "
                f"category_compatible={str(item.category_compatible).lower()} "
                f"duration_verified={str(item.duration_verified).lower()} "
                f"status={item.status} "
                f"reason={(item.reason or '-')[:80]!r} "
                f"missing_components={item.missing_components} "
                f"confidence={item.confidence:.2f} "
                f"source_scope={item.source_scope} source_method={item.source_method or '-'}"
            )
            if item.status == COVERED:
                result.covered_requirements.append(item)
                if item.resume_evidence:
                    result.notable_strengths.append(item.requirement)
                    result.source_evidence.append(
                        {
                            "requirement_id": item.requirement_id,
                            "requirement": item.requirement,
                            "resume_evidence": item.resume_evidence,
                            "resume_evidence_ids": list(item.resume_evidence_ids),
                            "job_source_section": item.job_source_section,
                            "job_source_text": item.job_source_text,
                            "source_scope": item.source_scope,
                        }
                    )
            elif item.status == PARTIAL:
                result.partial_requirements.append(item)
            elif item.status == NEEDS_CONFIRMATION:
                result.needs_confirmation.append(item)
            else:
                result.missing_requirements.append(item)
        if result.covered_requirements:
            result.strong_match_threshold_note = ""
        print(
            f"[Comparison] engine items={len(result.items)} covered={len(result.covered_requirements)} "
            f"partial={len(result.partial_requirements)} missing={len(result.missing_requirements)} "
            f"raw_requirement_count={result.raw_requirement_count} "
            f"canonical_requirement_count={result.canonical_requirement_count} "
            f"duplicates_removed={result.duplicates_removed} "
            f"non_requirements_removed={result.non_requirements_removed} "
            f"fragments_removed={result.fragments_removed}"
        )
        return result


def _clean_items(items: list[RequirementItem]) -> list[RequirementItem]:
    from src.agent.browser.page_model import is_browser_noise
    from src.agent.phase6.requirements import RequirementFragmentValidator

    frag = RequirementFragmentValidator()
    out = []
    for item in items:
        if is_browser_noise(item.requirement) or frag.reject(item.requirement):
            continue
        out.append(item)
    return out


def _biggest_gaps(result: ComparisonResult, limit: int = 3) -> list[RequirementItem]:
    gaps = [
        i
        for i in (result.missing_requirements + result.partial_requirements + result.needs_confirmation)
        if i.status != COVERED
    ]
    gaps = _clean_items(gaps)
    gaps.sort(key=gap_priority)
    seen: set[str] = set()
    ranked: list[RequirementItem] = []
    for item in gaps:
        key = normalize_text(item.requirement)
        if key in seen:
            continue
        seen.add(key)
        ranked.append(item)
        if len(ranked) >= limit:
            break
    return ranked


def format_detailed(result: ComparisonResult) -> str:
    if result.aborted:
        return (
            "I could not compare your resume to that listing because the page text "
            "was mixed with browser controls. I did not use toolbar or extension labels as job requirements."
        )
    title = result.job_title or "this job"
    company = f" at {result.company}" if result.company else ""
    parts = [f"Here's how your resume compares to {title}{company}."]

    covered = _clean_items(result.covered_requirements)
    partials = _clean_items(result.partial_requirements)
    missing = _clean_items(result.missing_requirements)
    confirm = _clean_items(result.needs_confirmation)

    if covered:
        lines = ["STRONG MATCHES"]
        for item in covered:
            lines.append(f"• {item.requirement}")
            if item.resume_evidence:
                lines.append(f"  Evidence: {item.resume_evidence}")
        parts.append("\n".join(lines))
    else:
        note = result.strong_match_threshold_note or (
            "A strong match needs direct, requirement-specific resume evidence. "
            "Generic word overlap is not enough."
        )
        parts.append("STRONG MATCHES\n• none\n  " + note)

    if partials:
        lines = ["PARTIAL MATCHES"]
        for item in partials:
            lines.append(f"• {item.requirement}")
            if item.resume_evidence:
                lines.append(f"  Evidence: {item.resume_evidence}")
            if item.missing_components:
                lines.append("  Missing: " + "; ".join(item.missing_components[:5]))
        parts.append("\n".join(lines))

    if missing:
        lines = ["NOT SHOWN"]
        for item in missing:
            lines.append(f"• {item.requirement}")
        parts.append("\n".join(lines))

    if confirm:
        lines = ["NEEDS CONFIRMATION"]
        for item in confirm:
            lines.append(f"• {item.requirement}")
            if item.reason:
                lines.append(f"  Why: {item.reason}")
        parts.append("\n".join(lines))

    gaps = _biggest_gaps(result)
    if gaps:
        lines = ["BIGGEST GAPS"]
        for i, item in enumerate(gaps, start=1):
            lines.append(f"{i}. {item.requirement}")
        parts.append("\n".join(lines))
    elif covered:
        parts.append("BIGGEST GAPS\nI did not find clear high-value gaps from the listing text I could read.")
    return "\n\n".join(parts)


def format_spoken(result: ComparisonResult) -> str:
    if result.aborted:
        return "I could not read the job listing cleanly, so I did not compare it to your resume."
    n = len(_clean_items(result.covered_requirements))
    p = len(_clean_items(result.partial_requirements))
    m = len(_clean_items(result.missing_requirements))
    return (
        f"I found {n} strong match{'' if n == 1 else 'es'}, "
        f"{p} partial match{'' if p == 1 else 'es'}, and "
        f"{m} requirement{'' if m == 1 else 's'} that are not clearly shown on your resume. "
        f"I put the details on screen."
    )


def format_gaps(result: ComparisonResult) -> str:
    missing = _clean_items(result.missing_requirements)
    partials = _clean_items(result.partial_requirements)
    confirm = _clean_items(result.needs_confirmation)
    if not (missing or partials or confirm):
        return "I did not find requirements that are missing or only partly shown on your resume."
    lines = ["Here's what is missing or only partly shown:"]
    for item in missing:
        lines.append(f"• {item.requirement} — not shown on your resume")
    for item in partials:
        extra = f" → {item.resume_evidence}" if item.resume_evidence else ""
        lines.append(f"• {item.requirement} — partial{extra}")
    for item in confirm:
        lines.append(f"• {item.requirement} — needs confirmation")
    gaps = _biggest_gaps(result)
    if gaps:
        lines.append("Biggest gaps: " + "; ".join(i.requirement for i in gaps) + ".")
    return "\n".join(lines)


def format_matches(result: ComparisonResult) -> str:
    covered = _clean_items(result.covered_requirements)
    if not covered:
        note = result.strong_match_threshold_note or (
            "A strong match needs direct, requirement-specific resume evidence "
            f"at or above {STRONG_THRESHOLD:.0%} confidence."
        )
        return "I did not mark any requirements as strong matches. " + note
    lines = ["STRONG MATCHES"]
    for item in covered:
        lines.append(f"• {item.requirement}")
        if item.resume_evidence:
            lines.append(f"  Evidence: {item.resume_evidence}")
    return "\n".join(lines)


def format_partials(result: ComparisonResult) -> str:
    items = _clean_items(result.partial_requirements)
    if not items:
        return "I did not mark any requirements as partial matches."
    lines = ["PARTIAL MATCHES"]
    for item in items:
        lines.append(f"• {item.requirement}")
        if item.resume_evidence:
            lines.append(f"  Evidence: {item.resume_evidence}")
        if item.missing_components:
            lines.append("  Missing: " + "; ".join(item.missing_components[:5]))
    return "\n".join(lines)


def format_confirmation(result: ComparisonResult) -> str:
    items = _clean_items(result.needs_confirmation)
    if not items:
        return "Nothing needed confirmation from the resume alone."
    lines = ["These I could not determine from the resume alone:"]
    for item in items:
        lines.append(f"• {item.requirement}")
        if item.reason:
            lines.append(f"  Why: {item.reason}")
    return "\n".join(lines)


def format_evidence(result: ComparisonResult, needle: str = "") -> str:
    return RequirementLookup().answer(needle, result)


def contains_comparison(text: str, result: Optional[ComparisonResult] = None) -> bool:
    t = (text or "").strip()
    if not t or is_step_label(t):
        return False
    if re.search(
        r"\b(strong matches|partial matches|not shown|needs confirmation|biggest gaps)\b",
        t,
        re.I,
    ):
        return True
    if result is None:
        return False
    sample = (result.missing_requirements or result.covered_requirements or result.partial_requirements)
    if sample and sample[0].requirement.lower() in t.lower():
        return True
    return bool(result.items) and ("match" in t.lower() or "resume" in t.lower())


def apply_comparison_to_task(task: Any, result: ComparisonResult) -> str:
    """Store ComparisonResult on the task and return the user-facing detailed answer."""
    detailed = format_detailed(result)
    spoken = format_spoken(result)
    facts = task.context.facts
    facts["task_type"] = TASK_TYPE_JOB_RESUME_COMPARISON
    facts["comparison_result"] = result.as_dict()
    facts["comparison_spoken"] = spoken
    facts["comparison_aborted_reason"] = result.abort_reason
    facts["job_page_read"] = bool(facts.get("page_text") or facts.get("page_excerpt") or result.job_page_title)
    facts["resume_read"] = bool(facts.get("resume_text") or facts.get("document_excerpt") or result.resume_path)
    facts["job_requirements_extracted"] = bool(result.items) and not result.aborted
    facts["resume_evidence_extracted"] = bool(
        result.source_evidence or result.covered_requirements or result.partial_requirements or facts.get("resume_text")
    )
    facts["comparison_result_created"] = not result.aborted
    facts["gaps_created"] = not result.aborted
    facts["final_answer_contains_comparison"] = (not result.aborted) and contains_comparison(detailed, result)
    facts["job_page_model_id"] = result.job_page_model_id
    facts["job_requirements_count"] = result.canonical_requirement_count or len(result.items)
    facts["raw_requirement_count"] = result.raw_requirement_count
    facts["canonical_requirement_count"] = result.canonical_requirement_count
    facts["duplicates_removed"] = result.duplicates_removed
    facts["duplicate_groups"] = result.duplicate_groups
    facts["non_requirements_removed"] = result.non_requirements_removed
    facts["fragments_removed"] = result.fragments_removed
    facts["resume_evidence_count"] = len(result.source_evidence)
    facts["comparison_items_count"] = len(result.items)
    facts["covered_count"] = len(result.covered_requirements)
    facts["partial_count"] = len(result.partial_requirements)
    facts["missing_count"] = len(result.missing_requirements)
    facts["needs_confirmation_count"] = len(result.needs_confirmation)
    print(
        f"[Comparison] task_type={TASK_TYPE_JOB_RESUME_COMPARISON} "
        f"job_page_model_id={result.job_page_model_id or '-'} "
        f"resume_path={result.resume_path or '-'} "
        f"raw_requirement_count={result.raw_requirement_count} "
        f"canonical_requirement_count={result.canonical_requirement_count} "
        f"duplicates_removed={result.duplicates_removed} "
        f"duplicate_groups={result.duplicate_groups} "
        f"non_requirements_removed={result.non_requirements_removed} "
        f"fragments_removed={result.fragments_removed} "
        f"job_requirements_count={len(result.items)} "
        f"resume_evidence_count={len(result.source_evidence)} "
        f"comparison_items_count={len(result.items)} "
        f"covered_count={len(result.covered_requirements)} "
        f"partial_count={len(result.partial_requirements)} "
        f"missing_count={len(result.missing_requirements)} "
        f"needs_confirmation_count={len(result.needs_confirmation)} "
        f"comparison_result_saved=true "
        f"final_answer_contains_requested_output={str(facts['final_answer_contains_comparison']).lower()}"
    )
    return detailed
