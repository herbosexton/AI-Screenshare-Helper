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
from src.agent.phase6.evidence import parse_resume
from src.agent.phase6.lookup import RequirementLookup
from src.agent.phase6.requirements import (
    COMPENSATION_CONTEXT,
    FRAGMENT,
    RequirementContentClassifier,
    RequirementFragmentValidator,
    canonicalize_requirements,
    normalize_text,
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

SANITIZED_RESUME = """
Candidate A

Education
B.A. in Finance & Economics
State University, 2022

Experience
Data Analyst | Acme Corp
Designed and maintained scalable data pipelines and automated ETL workflows using Python and SQL.
Built data-mining workflows, statistical modeling, and feature-engineering steps for forecasting.
Developed and deployed machine learning models (regression, classification, clustering).
Prototyped a deep-learning ranking model for demand signals.

Business Consultant
Provided strategic consulting and advisory guidance on business planning, branding, digital marketing, and investor outreach.
Led workstream planning and leadership workshops for client teams.
"""

RESUME = SANITIZED_RESUME


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
        resume_text="Candidate A\nhigh-growth business\nexperience\nDevelopment Engineer\nProvided strategic consulting and investor outreach.",
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


def test_cloud_alternative_group_one_branch_satisfies():
    raw = [
        {"requirement": "Azure: AI Foundry (design, deployment, orchestration of AI/agentic applications)", "category": "required"},
        {"requirement": "and/or", "category": "required"},
        {"requirement": "AWS: Amazon Bedrock (foundation models, model evaluation, and agent orchestration)", "category": "required"},
        {"requirement": "and/or", "category": "required"},
        {"requirement": "GCP: Vertex AI (e.g., Model Garden, Agent Builder, custom training); Gemini API", "category": "required"},
    ]
    canon, _ = canonicalize_requirements(raw)
    assert len(canon) == 1
    req = canon[0]
    assert req.category == "cloud"
    assert set(req.alternatives) >= {"Azure", "AWS", "GCP"}
    assert len(req.source_texts) >= 3
    result = JobResumeComparisonEngine().compare(
        page_text=(
            "Requirements:\n"
            "- Azure: AI Foundry\n"
            "and/or\n"
            "- AWS: Amazon Bedrock\n"
            "and/or\n"
            "- GCP: Vertex AI\n"
        ),
        resume_text=(
            "Experience\n"
            "ML Engineer | Jan 2023 – Jan 2025\n"
            "Deployed agentic applications on Azure AI Foundry and Azure OpenAI.\n"
        ),
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/cloud-or",
    )
    cloud = next(i for i in result.items if i.category == "cloud")
    assert cloud.status == COVERED
    assert cloud.alternatives
    assert cloud.matched_alternative == "Azure"
    answer = format_evidence(result, "Why did you mark the cloud stack as covered?")
    assert "azure" in answer.lower()
    assert "matched branch" in answer.lower()


def test_category_mismatch_consulting_never_supports_technical():
    result = JobResumeComparisonEngine().compare(
        page_text=(
            "Requirements:\n"
            "- 2+ years of hands-on experience building AI/ML solutions using Python\n"
            "- Azure or AWS or GCP agentic stack\n"
            "- Bachelor's or Master's degree in Computer Science, Engineering, Data Science, AI, or related field\n"
            "- AWS Certified Solutions Architect\n"
            "- LangChain, AutoGen, CrewAI, LangGraph agent orchestration\n"
        ),
        resume_text=(
            "Experience\n"
            "Business Consultant\n"
            "Provided strategic consulting and advisory guidance on business planning, "
            "branding, digital marketing, and investor outreach.\n"
            "Led sales workshops and high-growth business development for client teams.\n"
        ),
        job_title="Agentic Engineer",
        job_url="https://jobs.example.com/mismatch",
    )
    forbidden_ev = ("consulting", "advisory", "investor", "branding", "sales", "high-growth")
    for item in result.items:
        if item.category in {"cloud", "degree", "certification"} or "python" in item.requirement.lower() or "lang" in item.requirement.lower() or "agentic" in item.requirement.lower():
            blob = " ".join(
                [item.resume_evidence or "", " ".join(item.resume_evidence_texts or [])]
            ).lower()
            assert not any(tok in blob for tok in forbidden_ev), item.requirement
            if item.category in {"cloud", "certification"} or "python" in item.requirement.lower():
                assert item.status in {NOT_FOUND, PARTIAL, NEEDS_CONFIRMATION}
                if item.category == "cloud":
                    assert item.status == NOT_FOUND


def test_ambiguous_pronoun_asks_for_clarification():
    result = _compare()
    spoken = "Why that one?"
    item = RequirementLookup().resolve(spoken, result)
    assert item is None
    answer = format_evidence(result, spoken)
    assert "which requirement" in answer.lower()
    assert answer.count("Status:") == 0


def test_timeline_boundary_months_and_overlap():
    short = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- 2+ years of hands-on experience building AI/ML solutions using Python\n",
        resume_text=(
            "Experience\n"
            "Machine Learning Engineer | Jan 2023 – Dec 2024\n"
            "Built production AI/ML solutions using Python.\n"
        ),
        job_title="ML Engineer",
        job_url="https://jobs.example.com/23mo",
    )
    item = next(i for i in short.items if "python" in i.requirement.lower())
    assert item.status != COVERED

    exact = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- 2+ years of hands-on experience building AI/ML solutions using Python\n",
        resume_text=(
            "Experience\n"
            "Machine Learning Engineer | Jan 2023 – Jan 2025\n"
            "Built production AI/ML solutions using Python.\n"
        ),
        job_title="ML Engineer",
        job_url="https://jobs.example.com/24mo",
    )
    exact_item = next(i for i in exact.items if "python" in i.requirement.lower())
    assert exact_item.status == COVERED
    assert exact_item.duration_verified

    overlap = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- 5+ years of experience delivering AI/ML solutions using Python\n",
        resume_text=(
            "Experience\n"
            "ML Engineer | Jan 2020 – Jan 2023\n"
            "Built production AI/ML solutions using Python.\n"
            "Data Scientist | Jan 2022 – Jan 2024\n"
            "Shipped Python ranking models and ETL jobs.\n"
        ),
        job_title="ML Engineer",
        job_url="https://jobs.example.com/overlap",
    )
    overlap_item = next(i for i in overlap.items if "python" in i.requirement.lower())
    assert overlap_item.status != COVERED
    assert overlap_item.duration_verified


def test_degree_provenance_merges_jsonld_and_field_sentence():
    raw = [
        {"requirement": "Bachelor's Degree", "category": "education", "source_method": "json-ld"},
        {
            "requirement": "Bachelor's or Master's degree in Computer Science, Engineering, Data Science, AI, or related field",
            "category": "required",
        },
    ]
    canon, stats = canonicalize_requirements(raw)
    degrees = [r for r in canon if r.category == "degree"]
    assert len(degrees) == 1
    req = degrees[0]
    assert stats.duplicates_removed >= 1
    assert len(req.source_texts) >= 2
    assert any("bachelor's degree" == normalize_text(s) for s in req.source_texts) or any(
        s.lower() == "bachelor's degree" for s in req.source_texts
    )
    assert any("computer science" in s.lower() for s in req.source_texts)
    assert req.degree_level == "bachelor"
    result = _compare()
    degree = next(i for i in result.items if i.category == "degree")
    assert degree.status in {PARTIAL, NEEDS_CONFIRMATION}
    assert "missing" not in degree.reason.lower() or "field" in degree.reason.lower()
    assert "finance" in (degree.reason + degree.resume_evidence).lower() or "economics" in (
        degree.reason + degree.resume_evidence
    ).lower()
    assert len(degree.source_texts) >= 2 or len(req.source_texts) >= 2


def test_sanitized_resume_shape_does_not_attach_unrelated_evidence():
    result = _compare(resume=SANITIZED_RESUME)
    technical = [
        i for i in result.items
        if i.category in {"cloud", "certification"}
        or "python" in i.requirement.lower()
        or "agentic" in i.requirement.lower()
        or "langgraph" in i.requirement.lower()
    ]
    for item in technical:
        blob = " ".join([item.resume_evidence or "", " ".join(item.resume_evidence_texts or [])]).lower()
        assert "investor outreach" not in blob
        assert "branding" not in blob
        assert "advisory guidance" not in blob
        if item.category == "cloud":
            assert item.status == NOT_FOUND
            assert not item.resume_evidence


def test_forbidden_output_never_becomes_a_requirement():
    result = _compare()
    blob = " ".join(i.requirement.lower() for i in result.items)
    assert "and/or" not in blob
    assert "wage" not in blob
    assert "122,000" not in blob
    assert "annual incentive" not in blob
    detailed = format_detailed(result).lower()
    assert "and/or" not in detailed
    assert "annual incentive" not in detailed
    assert "wage range" not in detailed


def test_leadership_workstream_matches_and_respects_duration():
    job = (
        "Requirements:\n"
        "- 1+ years experience leading project workstreams/engagements, translating business needs\n"
    )
    covered = JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text=(
            "Experience\n"
            "Engagement Lead | Jan 2023 – Jan 2025\n"
            "Led project workstreams and client engagements, translating business needs into delivery plans.\n"
        ),
        job_title="Consultant",
        job_url="https://jobs.example.com/lead-cover",
    )
    item = next(
        i for i in covered.items
        if i.category == "leadership" or "workstream" in i.requirement.lower()
    )
    assert item.status == COVERED
    assert item.duration_verified
    assert "workstream" in item.resume_evidence.lower() or "engagement" in item.resume_evidence.lower()

    undated = JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text=(
            "Experience\n"
            "Engagement Lead\n"
            "Led project workstreams and client engagements, translating business needs into delivery plans.\n"
        ),
        job_title="Consultant",
        job_url="https://jobs.example.com/lead-partial",
    )
    undated_item = next(
        i for i in undated.items
        if i.category == "leadership" or "workstream" in i.requirement.lower()
    )
    assert undated_item.status == PARTIAL
    assert undated_item.status != COVERED


def test_clearance_matches_ts_sci_evidence():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- Active TS/SCI clearance\n",
        resume_text=(
            "Experience\n"
            "Held an active Top Secret / TS/SCI clearance for classified programs.\n"
        ),
        job_title="Cleared Engineer",
        job_url="https://jobs.example.com/clearance",
    )
    item = next(i for i in result.items if i.category == "clearance" or "clearance" in i.requirement.lower())
    assert item.status == COVERED
    assert "ts/sci" in item.resume_evidence.lower() or "top secret" in item.resume_evidence.lower()


def test_aws_skill_does_not_satisfy_aws_certification():
    job = "Requirements:\n- AWS Certified Solutions Architect\n"
    skill_only = JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text="Skills\nAWS, Python, Azure\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/cert-neg",
    )
    cert = next(i for i in skill_only.items if i.category == "certification" or "certified" in i.requirement.lower())
    assert cert.status == NOT_FOUND
    assert "aws, python" not in (cert.resume_evidence or "").lower()

    named = JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text="Certifications\nAWS Certified Solutions Architect – Associate\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/cert-pos",
    )
    named_cert = next(i for i in named.items if i.category == "certification" or "certified" in i.requirement.lower())
    assert named_cert.status == COVERED
    assert "certified" in named_cert.resume_evidence.lower()


def test_generic_azure_usage_does_not_cover_foundry_branch():
    job = (
        "Requirements:\n"
        "- Azure: AI Foundry (design, deployment, orchestration of AI/agentic applications)\n"
        "and/or\n"
        "- AWS: Amazon Bedrock (foundation models, model evaluation, and agent orchestration)\n"
        "and/or\n"
        "- GCP: Vertex AI (e.g., Model Garden, Agent Builder, custom training); Gemini API\n"
    )
    generic = JobResumeComparisonEngine().compare(
        page_text=job,
        resume_text="Experience\nUsed Azure Storage to archive application logs.\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/azure-generic",
    )
    cloud = next(i for i in generic.items if i.category == "cloud")
    assert cloud.status == PARTIAL
    assert cloud.status != COVERED
    assert cloud.matched_alternative == "Azure"
    assert any("foundry" in m.lower() for m in cloud.missing_components)


def test_azure_foundry_agentic_branch_can_cover():
    result = JobResumeComparisonEngine().compare(
        page_text=(
            "Requirements:\n"
            "- Azure: AI Foundry (design, deployment, orchestration of AI/agentic applications)\n"
            "and/or\n"
            "- AWS: Amazon Bedrock\n"
            "and/or\n"
            "- GCP: Vertex AI\n"
        ),
        resume_text=(
            "Experience\n"
            "ML Engineer | Jan 2023 – Jan 2025\n"
            "Deployed agentic applications on Azure AI Foundry, including design and orchestration.\n"
        ),
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/azure-foundry",
    )
    cloud = next(i for i in result.items if i.category == "cloud")
    assert cloud.status == COVERED
    assert cloud.matched_alternative == "Azure"
    assert "foundry" in cloud.resume_evidence.lower()


def test_partial_requires_score_at_least_threshold():
    result = JobResumeComparisonEngine().compare(
        page_text=(
            "Requirements:\n"
            "- Azure: AI Foundry\n"
            "and/or\n"
            "- AWS: Amazon Bedrock\n"
            "and/or\n"
            "- GCP: Vertex AI\n"
        ),
        resume_text="Experience\nWrote documentation about orchestration patterns.\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/partial-gate",
    )
    cloud = next(i for i in result.items if i.category == "cloud")
    assert cloud.status == NOT_FOUND
    assert cloud.semantic_similarity < 0.45


def test_date_context_does_not_leak_across_role_headers():
    resume = (
        "Experience\n"
        "ML Engineer | Jan 2020 – Jan 2024\n"
        "Led workshops for stakeholders.\n"
        "Business Consultant\n"
        "Built Python dashboards for clients.\n"
    )
    profile = parse_resume(resume)
    workshop = next(sn for sn in profile.snippets if "workshop" in sn.text.lower())
    python = next(sn for sn in profile.snippets if "python" in sn.text.lower())
    assert workshop.start is not None
    assert python.start is None
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- 2+ years of hands-on experience building AI/ML solutions using Python\n",
        resume_text=resume,
        job_title="ML Engineer",
        job_url="https://jobs.example.com/date-leak",
    )
    item = next(i for i in result.items if "python" in i.requirement.lower())
    assert item.status != COVERED


DELOITTE_CLOUD_BRANCHES = (
    "Requirements:\n"
    "- Azure: AI Foundry (design, deployment, orchestration of AI/agentic applications)\n"
    "and/or\n"
    "- AWS: Amazon Bedrock (foundation models, model evaluation, and agent orchestration)\n"
    "and/or\n"
    "- GCP: Vertex AI (e.g., Model Garden, Agent Builder, custom training); Gemini API\n"
)


def test_foundry_mention_without_capability_is_partial():
    result = JobResumeComparisonEngine().compare(
        page_text=DELOITTE_CLOUD_BRANCHES,
        resume_text="Experience\nUsed Azure AI Foundry for simple prompt experiments.\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/foundry-only",
    )
    cloud = next(i for i in result.items if i.category == "cloud")
    assert cloud.status == PARTIAL
    assert cloud.status != COVERED
    assert cloud.matched_alternative == "Azure"
    assert "foundry" in cloud.resume_evidence.lower()
    assert any(
        tok in " ".join(cloud.missing_components).lower()
        for tok in ("agentic", "orchestration", "deployment", "design")
    )


def test_foundry_plus_branch_capability_is_covered():
    result = JobResumeComparisonEngine().compare(
        page_text=DELOITTE_CLOUD_BRANCHES,
        resume_text=(
            "Experience\n"
            "ML Engineer | Jan 2023 – Jan 2025\n"
            "Designed and deployed agentic applications on Azure AI Foundry with tool orchestration.\n"
        ),
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/foundry-capability",
    )
    cloud = next(i for i in result.items if i.category == "cloud")
    assert cloud.status == COVERED
    assert cloud.matched_alternative == "Azure"


def test_bedrock_mention_only_is_partial_capability_covers():
    mention = JobResumeComparisonEngine().compare(
        page_text=DELOITTE_CLOUD_BRANCHES,
        resume_text="Experience\nUsed Amazon Bedrock in a sandbox account.\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/bedrock-only",
    )
    cloud = next(i for i in mention.items if i.category == "cloud")
    assert cloud.status == PARTIAL
    assert cloud.matched_alternative == "AWS"

    capable = JobResumeComparisonEngine().compare(
        page_text=DELOITTE_CLOUD_BRANCHES,
        resume_text=(
            "Experience\n"
            "ML Engineer | Jan 2023 – Jan 2025\n"
            "Built agent orchestration on Amazon Bedrock for foundation-model evaluation.\n"
        ),
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/bedrock-capability",
    )
    covered = next(i for i in capable.items if i.category == "cloud")
    assert covered.status == COVERED
    assert covered.matched_alternative == "AWS"


def test_vertex_mention_only_is_partial_capability_covers():
    mention = JobResumeComparisonEngine().compare(
        page_text=DELOITTE_CLOUD_BRANCHES,
        resume_text="Experience\nExperimented with Gemini on Vertex AI.\n",
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/vertex-only",
    )
    cloud = next(i for i in mention.items if i.category == "cloud")
    assert cloud.status == PARTIAL
    assert cloud.matched_alternative == "GCP"

    capable = JobResumeComparisonEngine().compare(
        page_text=DELOITTE_CLOUD_BRANCHES,
        resume_text=(
            "Experience\n"
            "ML Engineer | Jan 2023 – Jan 2025\n"
            "Built Agent Builder workflows on Vertex AI with custom training for Gemini models.\n"
        ),
        job_title="Cloud Engineer",
        job_url="https://jobs.example.com/vertex-capability",
    )
    covered = next(i for i in capable.items if i.category == "cloud")
    assert covered.status == COVERED
    assert covered.matched_alternative == "GCP"


def test_unrecognized_certification_does_not_match_unrelated_certificate():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- Databricks Certified Generative AI Engineer Associate\n",
        resume_text="Certifications\nCompTIA Security+ Certification\n",
        job_title="Data Engineer",
        job_url="https://jobs.example.com/cert-unrelated",
    )
    cert = next(i for i in result.items if i.category == "certification" or "databricks" in i.requirement.lower())
    assert cert.status == NOT_FOUND
    assert "comptia" not in (cert.resume_evidence or "").lower()


def test_unrecognized_certification_covers_when_same_phrase_appears():
    result = JobResumeComparisonEngine().compare(
        page_text="Requirements:\n- Databricks Certified Generative AI Engineer Associate\n",
        resume_text="Certifications\nDatabricks Certified Generative AI Engineer Associate\n",
        job_title="Data Engineer",
        job_url="https://jobs.example.com/cert-same",
    )
    cert = next(i for i in result.items if i.category == "certification" or "databricks" in i.requirement.lower())
    assert cert.status == COVERED
    assert "databricks" in cert.resume_evidence.lower()


def test_date_line_before_role_header_preserves_duration():
    resume = (
        "Experience\n"
        "Jan 2023 – Jan 2025\n"
        "Engagement Lead\n"
        "Led project workstreams and client engagements, translating business needs into delivery plans.\n"
    )
    profile = parse_resume(resume)
    lead = next(sn for sn in profile.snippets if "workstream" in sn.text.lower())
    assert lead.start is not None
    result = JobResumeComparisonEngine().compare(
        page_text=(
            "Requirements:\n"
            "- 1+ years experience leading project workstreams/engagements, translating business needs\n"
        ),
        resume_text=resume,
        job_title="Consultant",
        job_url="https://jobs.example.com/date-before-title",
    )
    item = next(
        i for i in result.items
        if i.category == "leadership" or "workstream" in i.requirement.lower()
    )
    assert item.status == COVERED
    assert item.duration_verified


def test_new_undated_role_after_completed_dated_role_resets():
    resume = (
        "Experience\n"
        "Jan 2020 – Jan 2024\n"
        "ML Engineer\n"
        "Led workshops for stakeholders.\n"
        "Business Consultant\n"
        "Built Python dashboards for clients.\n"
    )
    profile = parse_resume(resume)
    workshop = next(sn for sn in profile.snippets if "workshop" in sn.text.lower())
    python = next(sn for sn in profile.snippets if "python" in sn.text.lower())
    assert workshop.start is not None
    assert python.start is None
