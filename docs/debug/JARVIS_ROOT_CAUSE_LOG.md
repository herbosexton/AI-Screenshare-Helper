# JARVIS root-cause log

Append-only engineering memory. Newest entry at the top. Never overwrite an
old entry. Never paste passwords, tokens, cookies, or API keys.

Protocol: [`CURSOR_CHATGPT_DEBUG_BRIDGE.md`](CURSOR_CHATGPT_DEBUG_BRIDGE.md)
Routing: [`ROUTING_PRECEDENCE.md`](ROUTING_PRECEDENCE.md)
Shared thread: GitHub issue **JARVIS Root-Cause Debug Thread**

Architecture-audit cadence: every 5–10 fixes. Last audit: none yet (bridge
created 2026-09-18). Fixes logged below: 2.

---

## Template (copy for each new bug)

```
### YYYY-MM-DD — <short name>

BUG:
EXPECTED:
ACTUAL:
REPRODUCTION:
RAW USER COMMAND:
NORMALIZED COMMAND:
ROUTE:
INTENT:
PLANNER ADMISSION:
TOOLS / ACTIONS CALLED:
ROOT CAUSE:
ALTERNATIVE HYPOTHESES:
FILES / FUNCTIONS:
FIX:
AUTOMATED TESTS:
LIVE TEST:
COMMIT SHA:
REGRESSION TESTS:
REMAINING RISKS:
QUESTIONS FOR CHATGPT:
```

---

## 2026-09-18 — Job/resume comparison scored boilerplate and generic tokens

BUG:
After page extraction started returning real Deloitte qualifications,
`JobResumeComparisonEngine` still produced an untrustworthy comparison:
duplicated requirements, `and/or` as missing items, wage/incentive paragraphs
as partial matches, “Bachelor's Degree” as not-shown despite a bachelor’s on
the resume, and resume evidence attached by generic token overlap
(“experience”, consulting/sales bullets).

EXPECTED:
Canonical, de-duplicated WEB_DOCUMENT requirements. Compensation / fragments
excluded. Evidence must be requirement-specific. Degree logic must separate
level vs field. Year requirements COVERED only from chronology. “Why Python?”
answers one stored item. HUD uses STRONG / PARTIAL / NOT SHOWN / NEEDS
CONFIRMATION / BIGGEST GAPS.

ACTUAL (live Deloitte 359035, prior build):
- `job_requirements_count=31` `covered=0` `partial=26` `missing=3` `needs_confirmation=2`
- Requirements included `and/or`, repeated 4+/2+ year lines, wage-range
  paragraph, “Bachelor's Degree”
- Biggest gaps: `and/or`, `and/or`, `Bachelor's Degree`
- Python evidence: `experience`
- Azure/GCP evidence: SBDC consulting / investor-outreach bullets
- Degree evidence: data-mining / ML model bullets, not Education
- Follow-up “why Python” dumped the partial list

REPRODUCTION:
1. Open Deloitte Agentic AI / Data Science Engineer III (359035) in existing Chrome.
2. Say: look at the open job page, find newest resume, compare, list gaps.
3. Ask: “Why did you mark Python as covered/partial?” and “What matched well?”

RAW USER COMMAND:
Look at the job page I have open, find my newest resume, compare the resume to
the job requirements, and tell me what I’m missing.

NORMALIZED COMMAND:
(same intent; stored as Phase 6 `JOB_RESUME_COMPARISON`)

ROUTE:
Phase 6 planner (`MULTI_STEP_CROSS_TOOL_GOAL`) →
`JobResumeComparisonEngine.compare` → stored `ComparisonResult` →
`RecentAnalysisFollowupResolver` for later questions (`planner_calls=0`)

INTENT:
Complex cross-tool comparison goal. Follow-ups classified SHOW_EVIDENCE /
SHOW_MATCHES.

PLANNER ADMISSION:
Admitted for the original compare. Follow-ups not admitted (`planner_calls=0`).

TOOLS / ACTIONS CALLED:
`browser.getPageState` / `get_page_text` (PageModel + JSON-LD), resume file
open/read, `_compare_step` (local engine, no LLM reason step).

ROOT CAUSE:
`src/agent/phase6/compare.py` `_classify` / `_best_resume_line` treated any
token longer than two characters after a small stop list as distinctive. There
was no canonical Requirement object, no fragment/compensation filter at
compare time, no category constraint, and no chronology. JSON-LD
`educationRequirements` (“Bachelor's Degree”) plus repeated qualification
blocks were compared as independent rows. `format_evidence` returned the first
COVERED/PARTIAL dump matching a substring instead of one resolved requirement.

ALTERNATIVE HYPOTHESES:
- A. Page text was still chrome-contaminated. Eliminated: live log showed
  `source_scope=WEB_DOCUMENT` and real Deloitte lines (Python, Azure, degree).
- B. JSON-LD description splitter created orphan `and/or` lines that the
  validator accepted. Confirmed as a contributing input bug
  (`JobRequirementValidator.classify` treated short required-section text as
  VALID after the single-word skill exception).
- C. Follow-up regex failed to classify the Python question, so the planner
  re-ran compare. Eliminated for the dump case: resolver ran locally but
  `format_evidence` was too broad.

FILES / FUNCTIONS:
- `src/agent/phase6/compare.py` — `_classify`, `_best_resume_line`, formatters
- `src/agent/phase6/job_posting.py` — `JobRequirementValidator.classify`,
  `extract_sections`
- Added: `requirements.py` (`canonicalize_requirements`,
  `RequirementFragmentValidator`, `RequirementContentClassifier`)
- Added: `evidence.py` (`ResumeEvidenceRetriever`, `classify_requirement`)
- Added: `lookup.py` (`RequirementLookup`)
- Added: `compare_types.py`
- `src/agent/corrections.py` — `_SHOW_EVIDENCE`

FIX:
Canonicalize and dedupe before compare. Reject fragments and compensation.
Retrieve evidence by weighted concepts and compatible resume sections. Degree
and years have dedicated logic. Strong match threshold 0.75. RequirementLookup
formats one item. HUD sections rewritten. Loose `_SKILLS` scan disabled when
section requirements already exist (`rag` no longer matches inside “average”).

AUTOMATED TESTS:
PASS — `tests/test_semantic_matching.py` plus comparison/follow-up tests.
188 related tests passed (`test_semantic_matching`, `test_phase6_orchestrator`,
`test_utterance_routing`).

LIVE TEST:
NOT YET RUN on this build for Deloitte 359035 tests A–E
(no `and/or` / wage / duplicates; targeted Python follow-up; strong-match
threshold; raw vs canonical log counts; forbidden-item count = 0).

COMMIT SHA:
8d18551 (debug bridge on origin/main). Matching engine source is still local /
untracked so ChatGPT can inspect this protocol commit without a whole-tree dump.

REGRESSION TESTS:
- fragments / wage never become requirements
- cloud does not match sales bullets
- generic tokens are not evidence
- years not COVERED from one undated bullet
- dated Python+ML timeline can COVER 2+ years
- zero strong matches explains the threshold
- Python follow-up is one item
- existing chrome-UI and “never invent experience” tests still pass

REMAINING RISKS:
- Retriever is alias/concept based, not embeddings. Unaliased paraphrases can miss.
- Chronology parser only handles common date ranges.
- Finance & Economics is treated as unclear related-field, which Deloitte may
  judge differently.
- Live PDF text/spacing may differ from the fixture.
- Matching implementation is not yet on `origin/main`, so ChatGPT cannot open
  that diff until a code commit is pushed.

QUESTIONS FOR CHATGPT:
1. Is collapsing Azure/AWS/GCP `and/or` alternatives into one cloud
   requirement the right abstraction, or should they stay siblings?
2. Is 0.75 / 0.45 the right strong/partial split, or will live resumes starve
   STRONG MATCHES for the wrong reason?
3. Should “Bachelor's Degree” from `educationRequirements` be ignored entirely
   once a longer degree sentence exists, or merged as we did?
4. Does `RequirementLookup` falling back to the first COVERED item on
   “that one” hide ambiguity?

---

## 2026-09-18 — Chrome toolbar text compared as job requirements

BUG:
The first live Deloitte compare treated Chrome UI as qualifications
(Zoom 110%, Bookmark this tab, Install Reddit, Energy Saver, Managed
bookmarks, Ask Google).

EXPECTED:
Only WEB_DOCUMENT job text. Toolbar / extension / OS chrome never become
requirements. Contaminated dumps abort.

ACTUAL:
`browser.getPageState` / `get_page_text` returned a flat UIA dump of the
entire Chrome HWND. Comparison engine scored those labels as requirements.

REPRODUCTION:
Open Deloitte 359035 with extensions enabled. Run the compare command.

RAW USER COMMAND:
Look at the job page I have open, find my newest resume, compare the resume to
the job requirements, and tell me what I’m missing.

NORMALIZED COMMAND:
(same)

ROUTE:
Phase 6 compare step → `get_page_text` → window UIA → `_classify`

INTENT:
JOB_RESUME_COMPARISON

PLANNER ADMISSION:
Admitted (`MULTI_STEP_CROSS_TOOL_GOAL`)

TOOLS / ACTIONS CALLED:
Existing-Chrome page text via UIA window walk.

ROOT CAUSE:
`collect_window_text(hwnd)` walked the top-level Chrome accessibility tree.
There was no `PageModel` source_scope and no JobPosting JSON-LD preference.

ALTERNATIVE HYPOTHESES:
- A. OCR / vision mixed overlay text into the page. Eliminated: the dump was
  UIA names from the Chrome window.
- B. A Chrome extension injected labels into the document DOM. Partially
  true as a contamination source, but the primary pipe was window UIA, not
  document text.

FILES / FUNCTIONS:
- `src/agent/browser/existing.py` — `get_page_text`
- `src/agent/screen/uia.py` — `collect_window_text` vs `collect_document_text`
- Added: `src/agent/browser/page_model.py`, `document.py`
- Added: `src/agent/phase6/job_posting.py`

FIX:
PageModel + document fetch / JSON-LD JobPosting first, document-subtree UIA
fallback, reject browser noise, abort compare on contamination.

AUTOMATED TESTS:
PASS — chrome UI never becomes a requirement; contaminated dump aborts.

LIVE TEST:
PASS for extraction on Deloitte 359035 (later run showed real qualifications
and `source_scope=WEB_DOCUMENT`). FAIL for comparison quality (see newer
entry). LIVE matching tests A–E still NOT YET RUN on the matching build.

COMMIT SHA:
(not on origin; same local-only tree)

REGRESSION TESTS:
`test_chrome_ui_is_not_a_job_requirement`,
`test_contaminated_chrome_dump_aborts_without_job_model`

REMAINING RISKS:
No in-page DOM bridge, no CDP, no vision fallback. Login-walled or
client-rendered listings can still be thin.

QUESTIONS FOR CHATGPT:
Was abort-on-contamination the right fail-closed behavior versus retrying a
second extractor (spec asked for rebuild; we abort and do not rebuild)?
