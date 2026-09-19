"""Semantic job/resume matching: canonical requirements, evidence, lookup."""

from __future__ import annotations

from src.agent.corrections import SHOW_EVIDENCE, classify_analysis_intent
from src.agent.phase6.compare import (
    COVERED,
    NEEDS_CONFIRMATION,
    NOT_FOUND,
    PARTIAL,
    JobResumeComparisonEngine,
    format_detailed,
    format_evidence,
    format_matches,
)
from src.agent.phase6.lookup import RequirementLookup
from src.agent.phase6.requirements import (
    COMPENSATION_CONTEXT,
    FRAGMENT,
    RequirementContentClassifier,
    RequirementFragmentValidator,
    canonicalize_requirements,
)

DELOITTE_JOB = """
Qualifications
Required:
- 4+ years of experience delivering AI/ML solutions, with at least 1 year focused on Generative AI, Agentic AI or multi-agent systems
- 2+ years of hands-on experience building AI/ML solutions using Python
- 1+ years of direct experience developing agentic AI systems, including agent orchestration, tool integration, and autonomous decision-making workflows (LangChain, Semantic Kernel, AutoGen, Strands, CrewAI, LangGraph)
- Azure: AI Foundry (design, deployment, orchestration of AI/agentic applications)
and/or
- AWS: Amazon Bedrock (foundation models, model evaluation, and agent orchestration)
and/or
- GCP: Vertex AI (e.g., Model Garden, Agent Builder, custom training); Gemini API
- 1+ years experience leading project workstreams/engagements, translating business needs
- Bachelor's or Master's degree in Computer Science, Engineering, Data Science, AI, or related field
- Ability to travel up to 50% on average, based on the work you do and the clients
- Limited immigration sponsorship may be available
- 4+ years of experience delivering AI/ML solutions, with at least 1 year focused on Generative AI, Agentic AI or multi-agent systems
- 2+ years of hands-on experience building AI/ML solutions using Python
Education:
- Bachelor's Degree
Preferred:
- Prior consulting experience in client-facing delivery roles
- Presentation skills with a high degree of comfort with both large and small audiences
- The wage range for this role takes into account the wide range of factors that are considered in making compensation decisions including but not limited to skill sets; experience and training; licensure and certifications. A reasonable estimate of the current range is $122,000 to $240,500.
- You may also be eligible to participate in a discretionary annual incentive program
"""

RESUME = """
Herbert Sexton IV
high-growth business

Education
Bachelor of Science in Finance & Economics
State University, 2022

Experience
Data Analyst | Acme Corp
Designed and maintained scalable data pipelines and automated ETL workflows using Python and SQL, enabling real-time ingestion of sales, customer, and supply chain datasets.
Developed and deployed machine learning models (regression, classification, clustering) to support demand forecasting.

SBDC Business Consultant
Provided strategic consulting and 1:1 guidance to early-stage vendors on business planning, branding, digital marketing, and financial forecasting.
Developed and executed growth strategies and investor outreach.
"""


def _compare(job: str = DELOITTE_JOB, resume: str = RESUME):
    return JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text=resume,
        job_title="US E-Consulting Services-Agentic AI, AI & Data Science Engineer III (359035)",
        job_url="https://apply.deloitte.com/job/359035",
        resume_path="C:/docs/AI Engineer Resume 2026.pdf",
    )


def test_fragments_and_compensation_are_classified():
    validator = RequirementFragmentValidator()
    assert validator.reject("and/or")
    assert validator.reject("or")
    assert validator.reject("including")
    assert not validator.reject("2+ years of hands-on experience building AI/ML solutions using Python")
    clf = RequirementContentClassifier()
    assert clf.classify("and/or") == FRAGMENT
    assert clf.classify(
        "The wage range for this role takes into account compensation decisions. $122,000 to $240,500."
    ) == COMPENSATION_CONTEXT


def test_canonicalize_dedupes_and_drops_noise():
    raw = [
        {"requirement": "4+ years of experience delivering AI/ML solutions, with at least 1 year focused on Generative AI", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "2+ years of hands-on experience building AI/ML solutions using Python", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "and/or", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "Azure: AI Foundry (design, deployment, orchestration of AI/agentic applications)", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "and/or", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "AWS: Amazon Bedrock (foundation models, model evaluation, and agent orchestration)", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "4+ years of experience delivering AI/ML solutions, with at least 1 year focused on Generative AI", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "2+ years of hands-on experience building AI/ML solutions using Python", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "Bachelor's Degree", "category": "education", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "Bachelor's or Master's degree in Computer Science, Engineering, Data Science, AI, or related field", "category": "required", "source_scope": "WEB_DOCUMENT"},
        {"requirement": "The wage range for this role takes into account compensation decisions. $122,000 to $240,500.", "category": "preferred", "preferred": True, "source_scope": "WEB_DOCUMENT"},
    ]
    canon, stats = canonicalize_requirements(raw)
    blob = " | ".join(r.canonical_text.lower() for r in canon)
    assert stats.fragments_removed >= 2
    assert stats.non_requirements_removed >= 1
    assert stats.duplicates_removed >= 2
    assert "and/or" not in blob
    assert "wage" not in blob
    assert "122,000" not in blob
    assert sum(1 for r in canon if r.category == "degree") == 1
    assert sum(1 for r in canon if r.category == "cloud") == 1
    assert stats.canonical_requirement_count < stats.raw_requirement_count


def test_deloitte_style_comparison_rejects_noise_and_weak_evidence():
    result = _compare()
    blob = " ".join(i.requirement.lower() for i in result.items)
    assert "and/or" not in blob
    assert "wage" not in blob
    assert "annual incentive" not in blob
    assert "zoom" not in blob
    assert result.duplicates_removed >= 1
    assert result.canonical_requirement_count <= result.raw_requirement_count
    assert not any(i.requirement.lower().strip() in {"and/or", "or", "including"} for i in result.items)
    assert not any("computer science" == i.requirement.lower() for i in result.items)
    assert not any(i.requirement.lower() == "rag" for i in result.items)

    python = next(i for i in result.items if "python" in i.requirement.lower())
    assert python.status in {PARTIAL, COVERED}
    if python.status == PARTIAL:
        assert python.resume_evidence
        assert "python" in python.resume_evidence.lower()
        assert python.missing_components

    cloud = next(i for i in result.items if i.category == "cloud" or "azure" in i.requirement.lower() or "gcp" in i.requirement.lower())
    assert cloud.status == NOT_FOUND
    assert not cloud.resume_evidence or "investor" not in cloud.resume_evidence.lower()

    degree = next(i for i in result.items if i.category == "degree")
    assert degree.status in {PARTIAL, NEEDS_CONFIRMATION}
    assert "bachelor's degree" != degree.requirement.lower()
    assert "finance" in (degree.reason + degree.resume_evidence).lower() or "economics" in (degree.reason + degree.resume_evidence).lower()

    travel = next(i for i in result.items if i.category == "travel")
    assert travel.status == NEEDS_CONFIRMATION

    detailed = format_detailed(result)
    assert "and/or" not in detailed.lower()
    assert "wage range" not in detailed.lower()
    assert "BIGGEST GAPS" in detailed
    assert "NOT SHOWN" in detailed or "PARTIAL MATCHES" in detailed


def test_cloud_does_not_match_sales_bullets():
    result = _compare()
    cloud_items = [i for i in result.items if i.category == "cloud"]
    assert cloud_items
    for item in cloud_items:
        ev = (item.resume_evidence or "").lower()
        assert "investor outreach" not in ev
        assert "go-to-market" not in ev
        assert item.status == NOT_FOUND


def test_generic_tokens_are_not_evidence():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- Azure AI Foundry, OpenAI Service, Vector DBs, Entra ID, Key Vault\n",
        resume_text="Herbert Sexton IV\nhigh-growth business\nexperience\nDevelopment Engineer\nProvided strategic consulting and investor outreach.",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/cloud",
    )
    items = [i for i in result.items if i.category == "cloud" or "azure" in i.requirement.lower()]
    assert items
    assert all(i.status == NOT_FOUND for i in items)


def test_years_not_inferred_from_one_bullet():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- 4+ years of experience delivering AI/ML solutions using Python\n",
        resume_text="Built one machine learning model in Python last summer.",
        job_title="ML Engineer",
        job_url="https://jobs.example.com/ml",
    )
    item = next(i for i in result.items if "python" in i.requirement.lower() or i.min_years)
    assert item.status in {PARTIAL, NEEDS_CONFIRMATION, NOT_FOUND}
    assert item.status != COVERED


def test_verified_timeline_can_cover_years():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- 2+ years of hands-on experience building AI/ML solutions using Python\n",
        resume_text=(
            "Experience\n"
            "Machine Learning Engineer | Jun 2022 – Present\n"
            "Built production AI/ML solutions using Python, including ranking models and LLM prototypes.\n"
        ),
        job_title="ML Engineer",
        job_url="https://jobs.example.com/ml2",
    )
    item = next(i for i in result.items if "python" in i.requirement.lower())
    assert item.status == COVERED
    assert item.duration_verified
    assert item.resume_evidence
    assert "python" in item.resume_evidence.lower()


def test_zero_strong_matches_explains_threshold():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- LangGraph agent orchestration\n- TS/SCI clearance\n",
        resume_text="Customer success associate. High-growth business development.",
        job_title="Agent Engineer",
        job_url="https://jobs.example.com/agent",
    )
    assert result.covered_requirements == []
    msg = format_matches(result).lower()
    assert "strong match" in msg
    assert "threshold" in msg or "requirement-specific" in msg


def test_python_followup_is_targeted():
    result = _compare()
    spoken = "Why did you mark Python as covered/partial?"
    assert classify_analysis_intent(spoken) == SHOW_EVIDENCE
    item = RequirementLookup().resolve(spoken, result)
    assert item is not None
    assert "python" in item.requirement.lower()
    answer = format_evidence(result, spoken)
    assert "python" in answer.lower()
    assert "status:" in answer.lower()
    assert "job requirement:" in answer.lower()
    assert "langgraph" not in answer.lower()
    assert "wage" not in answer.lower()


def test_agentic_parent_keeps_subcomponents():
    raw = [{
        "requirement": (
            "1+ years of direct experience developing agentic AI systems, including agent orchestration, "
            "tool integration, and autonomous decision-making workflows (LangChain, Semantic Kernel, "
            "AutoGen, Strands, CrewAI, LangGraph)"
        ),
        "category": "required",
        "source_scope": "WEB_DOCUMENT",
    }]
    canon, _ = canonicalize_requirements(raw)
    assert len(canon) == 1
    req = canon[0]
    assert req.subcomponents
    assert any("orchestration" in s.lower() or "langchain" in s.lower() for s in req.subcomponents)
    result = _compare()
    agentic = [i for i in result.items if "agentic" in i.requirement.lower() or "orchestration" in " ".join(i.subcomponents).lower()]
    assert agentic
    assert all(i.status in {PARTIAL, NOT_FOUND} for i in agentic)
    if agentic[0].status == PARTIAL:
        assert agentic[0].missing_components
