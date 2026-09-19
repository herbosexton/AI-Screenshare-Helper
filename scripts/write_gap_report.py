"""Write an in-depth JARVIS spec-vs-shipped gap report PDF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "reports"

NAVY = colors.HexColor("#0B1F33")
RED = colors.HexColor("#B42318")
AMBER = colors.HexColor("#B54708")
GREEN = colors.HexColor("#17663A")
SLATE = colors.HexColor("#334155")
RULE = colors.HexColor("#D0D5DD")
ROW = colors.HexColor("#F8FAFC")


def _styles():
    base = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker", parent=base["Normal"], fontName="Times-Bold",
            fontSize=10, textColor=RED, spaceAfter=8,
        ),
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Title"], fontName="Times-Bold",
            fontSize=24, leading=28, textColor=NAVY, alignment=TA_LEFT, spaceAfter=10,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"], fontName="Times-Roman",
            fontSize=12, leading=16, textColor=SLATE, spaceAfter=6,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName="Times-Bold",
            fontSize=16, textColor=NAVY, spaceBefore=16, spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Times-Bold",
            fontSize=13, textColor=NAVY, spaceBefore=12, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10.5, leading=14.5, textColor=SLATE, alignment=TA_JUSTIFY, spaceAfter=8,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8.5, leading=11.5, textColor=SLATE,
        ),
        "cell_h": ParagraphStyle(
            "cell_h", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8.5, leading=11.5, textColor=NAVY,
        ),
        "status_shipped": ParagraphStyle("st_s", fontName="Times-Bold", fontSize=8.5, textColor=GREEN),
        "status_partial": ParagraphStyle("st_p", fontName="Times-Bold", fontSize=8.5, textColor=AMBER),
        "status_gap": ParagraphStyle("st_g", fontName="Times-Bold", fontSize=8.5, textColor=RED),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8, textColor=colors.HexColor("#667085"),
        ),
    }


def _p(text: str, style) -> Paragraph:
    return Paragraph(text.replace("\n", "<br/>"), style)


def _bullets(items: list[str], styles) -> ListFlowable:
    return ListFlowable(
        [ListItem(_p(item, styles["body"]), leftIndent=8) for item in items],
        bulletType="bullet", start="•", leftIndent=16, spaceBefore=2, spaceAfter=8,
    )


def _status(label: str, styles) -> Paragraph:
    key = {
        "SHIPPED": "status_shipped",
        "PARTIAL": "status_partial",
        "GAP": "status_gap",
        "UNPROVEN": "status_partial",
    }[label]
    return Paragraph(label, styles[key])


def _table(headers: list[str], rows: list[list], styles, col_widths) -> Table:
    data = [[_p(h, styles["cell_h"]) for h in headers]]
    for row in rows:
        cells = []
        for i, val in enumerate(row):
            if i == len(row) - 1 and val in {"SHIPPED", "PARTIAL", "GAP", "UNPROVEN"}:
                cells.append(_status(val, styles))
            else:
                cells.append(_p(str(val), styles["cell"]))
        data.append(cells)
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW]),
            ]
        )
    )
    return table


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, letter[1] - 28, letter[0], 28, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Times-Bold", 8)
    canvas.drawString(54, letter[1] - 18, "JARVIS  ·  SPEC GAP REPORT  ·  CONFIDENTIAL / LOCAL")
    canvas.setFont("Times-Roman", 8)
    canvas.drawRightString(letter[0] - 54, letter[1] - 18, "Herberton Sexton  ·  18 Sep 2026")
    canvas.setFillColor(RED)
    canvas.rect(0, 0, letter[0], 6, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.setFont("Times-Roman", 8)
    canvas.drawString(54, 16, "Not live-certified. Deloitte 359035 semantic comparison is still required.")
    canvas.drawRightString(letter[0] - 54, 16, f"Page {doc.page}")
    canvas.restoreState()


def build(path: Path) -> Path:
    styles = _styles()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.55 * inch,
        title="JARVIS Spec Gap Report — Semantic Matching",
        author="JARVIS / Cursor",
    )
    story = []

    story.append(_p("UPDATE REPORT  ·  18 SEPTEMBER 2026  ·  21:25 PT", styles["cover_kicker"]))
    story.append(_p("JARVIS gap report: job/resume semantic matching vs. your spec", styles["cover_title"]))
    story.append(
        _p(
            "This is the exact distance between the 14-page <b>Semantic Matching Fix</b> and the "
            "running build. Page extraction is treated as already working. This report is about "
            "whether comparison is semantically trustworthy. Nothing is marked done without the "
            "same Deloitte 359035 live comparison passing all five required tests.",
            styles["cover_sub"],
        )
    )
    story.append(
        _p(
            "<b>Build:</b> restarted after this update &nbsp;|&nbsp; "
            "<b>Tests:</b> 188 automated passed &nbsp;|&nbsp; "
            "<b>Live Deloitte semantic proof:</b> not yet passed",
            styles["cover_sub"],
        )
    )
    story.append(Spacer(1, 8))

    story.append(_p("1. The gap in one paragraph", styles["h1"]))
    story.append(
        _p(
            "The last live Deloitte run extracted real qualifications — that part held. Comparison "
            "did not. JARVIS compared 31 raw lines, including two copies of most requirements, "
            "standalone <b>and/or</b>, a wage-range paragraph, and “Bachelor's Degree” as a gap "
            "even though a bachelor’s was on the resume. Evidence was token overlap: Python matched "
            "the word “experience”; Azure/GCP matched consulting/sales bullets; the degree matched "
            "data-mining lines. “What matched well?” returned zero strong matches for the wrong "
            "reason, and “Why Python?” dumped the partial list. This update replaces that keyword "
            "classifier with canonical requirements, fragment/boilerplate filters, a semantic "
            "retriever, degree/years logic, and RequirementLookup. That is shipped in code and "
            "covered by unit tests on a Deloitte-shaped fixture. It is <b>not</b> live-certified "
            "on posting 359035 against your actual resume.",
            styles["body"],
        )
    )

    story.append(_p("2. Scoreboard — you vs. this build", styles["h1"]))
    story.append(
        _table(
            ["Your requirement", "What the build does now", "Status"],
            [
                [
                    "Canonical Requirement objects before compare",
                    "CanonicalRequirement stores id, canonical_text, category, required/preferred, min_years, technologies, degree fields, travel %, certs, source provenance.",
                    "SHIPPED",
                ],
                [
                    "Deduplicate by normalized semantic identity",
                    "Repeated 4+ / 2+ year lines collapse. Short “Bachelor's Degree” merges into the full technical-degree requirement. Cloud Azure/AWS/GCP alternatives become one requirement.",
                    "SHIPPED",
                ],
                [
                    "Filter compensation / legal / benefits / navigation",
                    "Classified COMPENSATION_CONTEXT, LEGAL_CONTEXT, BENEFITS_CONTEXT, NON_REQUIREMENT and excluded from scoring. Wage and annual-incentive paragraphs cannot become gaps.",
                    "SHIPPED",
                ],
                [
                    "Reject fragments (and/or, or, including, etc.)",
                    "RequirementFragmentValidator. and/or never appears in missing / partial / biggest gaps in unit tests.",
                    "SHIPPED",
                ],
                [
                    "Split compound requirements as parent + subcomponents",
                    "Agentic line stays one parent; orchestration / frameworks become subcomponents, not orphan rows.",
                    "SHIPPED",
                ],
                [
                    "ResumeEvidenceRetriever with minimum threshold",
                    "Scores concept hits (python, azure, consulting, …). Generic tokens (experience, engineer, Herbert, high-growth) have near-zero weight. No forced match.",
                    "SHIPPED",
                ],
                [
                    "No generic-word matches",
                    "Azure stack vs consulting/investor-outreach is NOT_FOUND in tests. “experience” cannot cover Python.",
                    "SHIPPED",
                ],
                [
                    "Category-aware matching",
                    "Degree looks at education; cloud ignores salesy bullets; travel/sponsorship default to NEEDS_CONFIRMATION unless explicit.",
                    "SHIPPED",
                ],
                [
                    "Degree level vs field vs related-field ambiguity",
                    "Bachelor’s in Finance &amp; Economics is PARTIAL/NEEDS_CONFIRMATION with a field-fit reason. It is not “Bachelor's Degree — not shown.”",
                    "SHIPPED",
                ],
                [
                    "Years from resume chronology, not one bullet",
                    "Intervals are unioned from dated roles. COVERED only if timeline ≥ minimum. Unclear dates → PARTIAL, not COVERED.",
                    "SHIPPED",
                ],
                [
                    "Strong-match threshold; zero strong matches allowed",
                    "COVERED requires direct concepts, category fit, duration if required, ≥75% score. Empty strong list explains the threshold.",
                    "SHIPPED",
                ],
                [
                    "Partial = related evidence + missing components",
                    "Python + ML without verified 2-year timeline is PARTIAL and lists missing duration / leftover concepts.",
                    "SHIPPED",
                ],
                [
                    "“Not shown” is a meaningful label",
                    "Cloud becomes “Azure/AWS/GCP agentic AI stack experience”. Degree becomes “Technical degree field (… or related)”.",
                    "SHIPPED",
                ],
                [
                    "Biggest gaps are high-value required items",
                    "Ranks required technical / duration / credentials above preferred boilerplate. Fragments and salary cannot rank.",
                    "SHIPPED",
                ],
                [
                    "RequirementLookup for targeted follow-ups",
                    "“Why did you mark Python as covered/partial?” resolves one Python item and answers status / requirement / evidence / reason / missing proof. planner_calls=0.",
                    "SHIPPED",
                ],
                [
                    "Store source evidence on each item",
                    "Each RequirementItem stores requirement_id, evidence ids/text, status, reason, missing_components, confidence, semantic_similarity, duration_verified.",
                    "SHIPPED",
                ],
                [
                    "Clean HUD format",
                    "STRONG MATCHES / PARTIAL MATCHES / NOT SHOWN / NEEDS CONFIRMATION / BIGGEST GAPS. No duplicates, fragments, or wage text in tests.",
                    "SHIPPED",
                ],
                [
                    "Telemetry: raw vs canonical, duplicate groups, per-item scores",
                    "Logged on every compare. Follow-up still planner_calls=0.",
                    "SHIPPED",
                ],
                [
                    "Preserve page-extraction / Phase 6 / follow-up / permissions",
                    "WEB_DOCUMENT, JSON-LD, chrome filter, RecentAnalysisFollowupResolver, Phase 6 runner unchanged in contract. 188 related tests passed.",
                    "SHIPPED",
                ],
                [
                    "Live Test A — same Deloitte 359035 comparison",
                    "Required. Not run on this restarted build.",
                    "GAP",
                ],
                [
                    "Live Test B — targeted Python follow-up",
                    "Required after A. Unit-tested; not live-proven.",
                    "GAP",
                ],
                [
                    "Live Test C — “What matched well?”",
                    "Required after A. Empty strong list now explains the threshold.",
                    "GAP",
                ],
                [
                    "Live Test D — duplicate counts in logs",
                    "Code logs raw/canonical/duplicates_removed. Not captured live.",
                    "GAP",
                ],
                [
                    "Live Test E — forbidden items count=0",
                    "Unit tests assert and/or, wage, incentive, chrome UI are absent. Live log not captured.",
                    "GAP",
                ],
            ],
            styles,
            [2.2 * inch, 3.55 * inch, 0.95 * inch],
        )
    )

    story.append(_p("3. What I changed in this update", styles["h1"]))
    story.append(
        _p(
            "Root cause of the live failure: <b>JobResumeComparisonEngine._classify</b> scored "
            "requirements by token overlap against any resume line. Distinctive tokens were "
            "anything longer than two characters after a small stop list, so “experience”, "
            "“development”, and “engineer” counted. There was no canonicalization step, so "
            "JSON-LD plus educationRequirements plus repeated qualification blocks produced "
            "31 comparison rows.",
            styles["body"],
        )
    )
    story.append(_p("Correct path now:", styles["h2"]))
    story.append(
        _bullets(
            [
                "JobPostingModel still supplies WEB_DOCUMENT requirements (JSON-LD first).",
                "RequirementContentClassifier drops wage / legal / benefits / nav / fragments.",
                "canonicalize_requirements() builds CanonicalRequirement objects, merges and/or cloud alternatives, and dedupes semantic twins.",
                "ResumeEvidenceRetriever parses education / experience / skills and dated intervals.",
                "classify_requirement() applies category, degree, years, and 0.75 / 0.45 thresholds.",
                "RequirementLookup answers one stored item for a follow-up utterance.",
                "HUD prints STRONG / PARTIAL / NOT SHOWN / NEEDS CONFIRMATION / BIGGEST GAPS.",
            ],
            styles,
        )
    )
    story.append(_p("Files added or materially changed:", styles["h2"]))
    story.append(
        _bullets(
            [
                "<b>src/agent/phase6/requirements.py</b> — canonical objects, fragment validator, content filter, dedupe.",
                "<b>src/agent/phase6/evidence.py</b> — ResumeEvidenceRetriever, chronology, degree/years, thresholds.",
                "<b>src/agent/phase6/lookup.py</b> — RequirementLookup + targeted answer format.",
                "<b>src/agent/phase6/compare_types.py</b> — shared ComparisonResult / RequirementItem fields.",
                "<b>src/agent/phase6/compare.py</b> — engine now canonicalizes, retrieves, logs, and formats cleanly.",
                "<b>src/agent/phase6/job_posting.py</b> — validator rejects fragments and compensation before compare.",
                "<b>src/agent/corrections.py</b> — “Why did you mark Python as covered/partial?” is SHOW_EVIDENCE.",
                "<b>tests/test_semantic_matching.py</b> — Deloitte-shaped fixture covering the failure modes from the live run.",
            ],
            styles,
        )
    )

    story.append(_p("4. What the automated Deloitte-shaped fixture now does", styles["h1"]))
    story.append(
        _p(
            "This is not your live resume. It is a fixture built from the bad live output so the "
            "same failure modes cannot silently return. On that fixture the engine now produces:",
            styles["body"],
        )
    )
    story.append(
        _bullets(
            [
                "No and/or, wage range, annual incentive, Zoom, or standalone “Bachelor's Degree”.",
                "Duplicates removed (raw 25 → canonical 12 on the last fixture run; counts are logged).",
                "Python = PARTIAL with Python/ML evidence and missing 2+ year timeline verification.",
                "Azure/AWS/GCP stack = NOT_FOUND (consulting/sales bullets do not count).",
                "Technical degree = PARTIAL because Finance &amp; Economics is shown and field-fit is unclear.",
                "Travel and sponsorship = NEEDS_CONFIRMATION.",
                "Agentic frameworks = NOT_FOUND (no LangChain / LangGraph / orchestration evidence).",
                "“Why did you mark Python…?” returns one Python answer, not the whole partial list.",
            ],
            styles,
        )
    )
    story.append(
        _p(
            "Your real resume may produce a different mix of COVERED / PARTIAL. That is expected. "
            "The fixture only proves the engine no longer invents evidence or scores boilerplate.",
            styles["body"],
        )
    )

    story.append(PageBreak())
    story.append(_p("5. Required live tests — still yours to run", styles["h1"]))
    story.append(
        _p(
            "The spec is explicit: do not declare fixed until the same Deloitte 359035 live "
            "comparison passes all five tests below. JARVIS is restarted and waiting.",
            styles["body"],
        )
    )
    story.append(
        _table(
            ["Test", "What you do", "Pass looks like", "Current"],
            [
                [
                    "A — same Deloitte posting",
                    "Rerun the exact 359035 compare command.",
                    "No Chrome UI, no extension labels, no and/or, no salary disclaimer, no duplicates. Evidence is relevant. Degree handled as field-fit, not “degree missing.” Travel/sponsorship in NEEDS_CONFIRMATION if not explicit.",
                    "GAP",
                ],
                [
                    "B — Python follow-up",
                    "Ask: “Why did you mark Python as covered/partial?”",
                    "One targeted Python answer (status, exact requirement, snippet, reason, missing proof). planner_calls=0. No partial-list dump.",
                    "GAP",
                ],
                [
                    "C — strong matches",
                    "Ask: “What matched well?”",
                    "Only genuinely strong items with direct evidence — or none, plus a short threshold explanation.",
                    "GAP",
                ],
                [
                    "D — duplicate check",
                    "Read the compare logs.",
                    "raw_requirement_count, canonical_requirement_count, duplicate_groups, duplicates_removed. Repeated Deloitte lines collapse.",
                    "GAP",
                ],
                [
                    "E — noise check",
                    "Inspect HUD + logs.",
                    "Forbidden items (and/or, salary disclaimer, annual incentive, browser UI, extension labels) count = 0.",
                    "GAP",
                ],
            ],
            styles,
            [1.15 * inch, 1.7 * inch, 2.85 * inch, 0.95 * inch],
        )
    )

    story.append(_p("6. Carried-forward gaps (still true)", styles["h1"]))
    story.append(
        _table(
            ["Earlier requirement", "Code", "Live proof"],
            [
                [
                    "WEB_DOCUMENT-only extraction; no Chrome toolbar as requirements",
                    "SHIPPED",
                    "PROVEN on prior live Deloitte run — keep watching",
                ],
                [
                    "JSON-LD JobPosting parsing",
                    "SHIPPED",
                    "PROVEN on prior live run (structured_jobposting_found=true)",
                ],
                [
                    "Final answer is the comparison, never “Completed: Compare and list gaps.”",
                    "SHIPPED",
                    "UNPROVEN on this matching build",
                ],
                [
                    "HUD detailed list + short TTS",
                    "SHIPPED (new section layout)",
                    "UNPROVEN",
                ],
                [
                    "Local analysis follow-ups, planner_calls=0",
                    "SHIPPED + Python lookup",
                    "UNPROVEN on live Python follow-up",
                ],
                [
                    "Chrome extension / CDP / vision DOM bridges",
                    "Not built",
                    "GAP — unchanged from extraction spec",
                ],
            ],
            styles,
            [3.4 * inch, 1.4 * inch, 1.9 * inch],
        )
    )

    story.append(_p("7. Known remaining engineering gaps (honest)", styles["h1"]))
    story.append(
        _bullets(
            [
                "<b>No learned embeddings.</b> “Semantic” here is concept/alias retrieval plus IDF-style generic suppression, not a transformer. Close paraphrases without alias coverage can still miss.",
                "<b>Chronology is brittle.</b> Only obvious “Mon YYYY – Present/Mon YYYY” and year–year ranges are parsed. Unusual resume date formats will keep year requirements in PARTIAL.",
                "<b>Related-field degree is a policy call.</b> Finance &amp; Economics is treated as unclear, not automatically related. Deloitte may disagree either way.",
                "<b>Consulting vs delivery consulting.</b> SBDC / small-business advisory can score as related consulting. That may be PARTIAL rather than a hard miss.",
                "<b>Preferred cert laundry lists</b> are still one requirement if they appear as one bullet. They are not split into individual certs unless named.",
                "<b>Live resume text can differ</b> from PDF extraction (spacing, missing dates). The engine cannot invent dates that the reader did not extract.",
            ],
            styles,
        )
    )

    story.append(_p("8. Definition of done — still not met", styles["h1"]))
    story.append(
        _p(
            "Done means the Deloitte comparison is semantically trustworthy on your machine. It must not:",
            styles["body"],
        )
    )
    story.append(
        _bullets(
            [
                "compare browser UI",
                "score salary boilerplate",
                "emit conjunction fragments",
                "duplicate requirements",
                "attach unrelated resume bullets as evidence",
                "answer a specific follow-up with a broad dump",
            ],
            styles,
        )
    )
    story.append(
        _p(
            "It must produce clean canonical requirements, relevant resume evidence, accurate "
            "COVERED / PARTIAL / NOT_FOUND / NEEDS_CONFIRMATION statuses, precise biggest gaps, "
            "and targeted evidence follow-ups. Until Tests A–E pass on posting 359035, matching "
            "stays <b>open</b>.",
            styles["body"],
        )
    )

    story.append(_p("9. How I will report after every later update", styles["h1"]))
    story.append(
        _p(
            "Each JARVIS change set gets a dated file under <b>docs/reports/</b> with your "
            "requirement, what the build does, and SHIPPED / PARTIAL / UNPROVEN / GAP. Live tests "
            "stay GAP until you run them. Code written is not done.",
            styles["body"],
        )
    )
    story.append(
        _p(
            "This report’s file: docs/reports/JARVIS_GAP_REPORT_2026-09-18_semantic_matching.pdf",
            styles["body"],
        )
    )
    story.append(Spacer(1, 10))
    story.append(
        _p(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} PT from the semantic matching "
            "update. Automated tests: 188 passed. Live certification: not claimed.",
            styles["footer"],
        )
    )

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return path


def build_debug_bridge(path: Path) -> Path:
    styles = _styles()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.55 * inch,
        title="JARVIS Spec Gap Report — Cursor/ChatGPT Debug Bridge",
        author="JARVIS / Cursor",
    )
    story = []
    story.append(_p("UPDATE REPORT  ·  18 SEPTEMBER 2026  ·  21:35 PT", styles["cover_kicker"]))
    story.append(_p("JARVIS gap report: Cursor ↔ ChatGPT debug bridge vs. your spec", styles["cover_title"]))
    story.append(
        _p(
            "This is the distance between the 14-page debug-bridge spec and the repo. "
            "The bridge is the communication layer. It does not mark the Deloitte "
            "semantic-matching live tests done.",
            styles["cover_sub"],
        )
    )
    story.append(
        _p(
            "<b>Automated:</b> not a product test &nbsp;|&nbsp; "
            "<b>Live Deloitte matching:</b> NOT YET RUN &nbsp;|&nbsp; "
            "<b>Origin:</b> still the initial screenshare commit until this docs commit is pushed",
            styles["cover_sub"],
        )
    )
    story.append(_p("1. The gap in one paragraph", styles["h1"]))
    story.append(
        _p(
            "You asked for one permanent GitHub issue, an append-only root-cause log, "
            "explicit routing precedence, focused commits, and a review package ChatGPT "
            "can challenge. The log, protocol, and real router order are in "
            "<b>docs/debug/</b>. A Cursor always-apply rule points at that protocol. "
            "The standing GitHub issue is created as part of this update. "
            "<b>ChatGPT cannot yet independently inspect the semantic-matching source "
            "diff</b> because almost all of <b>src/agent/</b> is still untracked against "
            "origin/main (initial screenshare commit only). Dumping the whole tree into "
            "this commit would violate focused-commit discipline. That remaining gap is "
            "called out on the issue.",
            styles["body"],
        )
    )
    story.append(_p("2. Scoreboard", styles["h1"]))
    story.append(
        _table(
            ["Your requirement", "What the build does now", "Status"],
            [
                [
                    "One permanent GitHub issue: JARVIS Root-Cause Debug Thread",
                    "Created (or updated) in herbosexton/AI-Screenshare-Helper. Standing thread, not a one-off bug ticket.",
                    "SHIPPED",
                ],
                [
                    "docs/debug/JARVIS_ROOT_CAUSE_LOG.md append-only",
                    "Exists with template plus two dated entries: chrome-as-requirements and semantic matching. Old entries are not overwritten.",
                    "SHIPPED",
                ],
                [
                    "Diagnose before patching; evidence fields required",
                    "Protocol + Cursor rule. First two log entries include utterance, route, hypotheses, files/functions, automated vs live.",
                    "SHIPPED",
                ],
                [
                    "Document actual routing precedence, not a hidden fallback order",
                    "docs/debug/ROUTING_PRECEDENCE.md lists the live orchestrator order (20 steps). It differs from the spec example: fast page facts run before analysis follow-up.",
                    "SHIPPED",
                ],
                [
                    "Cursor posts ROOT-CAUSE REVIEW REQUEST packages",
                    "First package posted on the standing issue for the matching bug. No “Fixed.” / “Tests pass.” comments.",
                    "SHIPPED",
                ],
                [
                    "Focused commit ChatGPT can open",
                    "This update commits only debug-bridge docs/rule/report. Matching code remains local. ChatGPT can review the bridge commit, not yet the engine diff.",
                    "PARTIAL",
                ],
                [
                    "ChatGPT independent review format + disagreement loop",
                    "Documented. Waiting for ChatGPT’s first review after you point it at the issue.",
                    "UNPROVEN",
                ],
                [
                    "Never report DONE without live rerun",
                    "Matching live tests A–E remain NOT YET RUN in the log and issue.",
                    "SHIPPED",
                ],
                [
                    "Verify user outcomes / fail closed / no “Done.” cover-up",
                    "Already in compare/result synthesis from prior work. Restated in the protocol. Not a new runtime change this commit.",
                    "PARTIAL",
                ],
                [
                    "Architecture audit every 5–10 fixes",
                    "Cadence recorded. Zero audits yet (only two historical bugs logged).",
                    "GAP",
                ],
                [
                    "User no longer hand-translates code-level detail to ChatGPT",
                    "Works only after the issue exists and later code commits are on GitHub. Matching source is still the gap.",
                    "PARTIAL",
                ],
            ],
            styles,
            [2.2 * inch, 3.55 * inch, 0.95 * inch],
        )
    )
    story.append(_p("3. Files added", styles["h1"]))
    story.append(
        _bullets(
            [
                "<b>docs/debug/CURSOR_CHATGPT_DEBUG_BRIDGE.md</b> — workflow, package formats, definition of done.",
                "<b>docs/debug/ROUTING_PRECEDENCE.md</b> — actual orchestrator order vs spec example.",
                "<b>docs/debug/JARVIS_ROOT_CAUSE_LOG.md</b> — append-only memory.",
                "<b>.cursor/rules/jarvis-debug-bridge.mdc</b> — always-apply for Cursor (local; .cursor/ is gitignored).",
            ],
            styles,
        )
    )
    story.append(_p("4. Definition of done for the bridge", styles["h1"]))
    story.append(
        _bullets(
            [
                "Permanent GitHub issue exists.",
                "Root-cause log exists and is append-only.",
                "Cursor posts evidence packages.",
                "Fixes have focused commits ChatGPT can inspect.",
                "ChatGPT can challenge the diagnosis.",
                "Live tests remain the acceptance gate.",
            ],
            styles,
        )
    )
    story.append(
        _p(
            "Bridge communication is standing. Semantic matching on Deloitte 359035 is "
            "still <b>open</b> (LIVE NOT YET RUN). This report’s file: "
            "docs/reports/JARVIS_GAP_REPORT_2026-09-18_debug_bridge.pdf",
            styles["body"],
        )
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return path


if __name__ == "__main__":
    stamp = datetime.now().strftime("%Y-%m-%d")
    out = OUT_DIR / f"JARVIS_GAP_REPORT_{stamp}_debug_bridge.pdf"
    written = build_debug_bridge(out)
    print(written)
