# JARVIS root-cause log

Append-only engineering memory. Newest entry at the top. Never overwrite an
old entry. Never paste passwords, tokens, cookies, or API keys.

Protocol: [`CURSOR_CHATGPT_DEBUG_BRIDGE.md`](CURSOR_CHATGPT_DEBUG_BRIDGE.md)
Routing: [`ROUTING_PRECEDENCE.md`](ROUTING_PRECEDENCE.md)
Shared thread: GitHub issue **JARVIS Root-Cause Debug Thread**

Architecture-audit cadence: every 5–10 fixes. Last audit: none yet (bridge
created 2026-09-18). Fixes logged below: 3.

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

## 2026-09-18 — ChatGPT review: cloud OR-group, gates, degree provenance, “that one”

BUG:
The first matching pass still flattened Azure/AWS/GCP into one vague string,
scored 0.75/0.45 before category/concept/duration gates, kept JSON-LD
“Bachelor's Degree” as a separate gap, and resolved “that one” to the first
COVERED item. ChatGPT marked the shared thread NEEDS MORE EVIDENCE because
those behaviors were not independently reviewable.

EXPECTED:
One cloud parent with alternatives; one valid branch may satisfy the parent
and must be named. Hard gates first (category → required concepts →
duration/credential → score). JSON-LD degree + field sentence merge into one
canonical requirement with both source texts. Ambiguous “that one” asks a
short local clarification (`planner_calls=0`). No private resume/PII in git.

ACTUAL (pre-this-commit):
Cloud label was a flat “Azure/AWS/GCP” string. “Why that one was covered?”
returned the first COVERED Python item. Missing regression tests 1–7. Matching
code was buried in the whole-tree publish commit.

REPRODUCTION:
Automated tests 1–7 in `tests/test_semantic_matching.py`. Live Deloitte 359035
A–E are prepared but not claimed.

RAW USER COMMAND:
ChatGPT review follow-up: make the semantic-matching fix independently
reviewable; do not start another broad refactor.

NORMALIZED COMMAND:
Implement review items A–D; add tests 1–7; focused matching commit only.

ROUTE:
N/A (code review / matching layer). Follow-up path remains local HUD.

INTENT:
comparison_followup / SHOW_EVIDENCE when the user asks about a stored item.

PLANNER ADMISSION:
Follow-up tests assert `planner_calls=0`.

TOOLS / ACTIONS CALLED:
None for the ambiguous-pronoun path.

ROOT CAUSE:
Cloud grouping collapsed alternatives into one string and treated sibling
platforms as missing subcomponents. Thresholds were applied as if they were
the first gate. Degree identity merged texts but follow-up/lookup still
guessed. Deictic lookup fell through to the first COVERED item.

ALTERNATIVE HYPOTHESES:
1. Extraction still emitting separate cloud bullets (rejected: canonicalize
   now builds one parent with `alternatives`).
2. Score denominator made a single Azure hit look weak (confirmed: one
   matched branch now satisfies the parent after hard gates).

FILES / FUNCTIONS:
`src/agent/phase6/requirements.py` (`_merge_cloud_group`, degree provenance)
`src/agent/phase6/evidence.py` (`classify_requirement`, `duration_meets`)
`src/agent/phase6/lookup.py` (`RequirementLookup._resolve`)
`src/agent/phase6/compare.py` (`format_evidence` → `answer`)
`src/agent/phase6/compare_types.py`
`tests/test_semantic_matching.py` (tests 1–7)
`tests/test_utterance_routing.py` (`test_why_covered_uses_stored_evidence`)

FIX:
Parent cloud requirement with alternatives; one branch may COVERED and is
recorded as `matched_alternative`. Gates: category → required concepts →
duration in calendar months → 0.75/0.45. Degree sources kept; field-fit
evaluated separately. “That one” clarifies unless one salient recent item.

AUTOMATED TESTS:
PASS — `test_semantic_matching` + `test_phase6_orchestrator` +
`test_utterance_routing` = 195 passed.

LIVE TEST:
NOT YET RUN — Deloitte 359035 A–E. Do not treat this commit as live PASS.

COMMIT SHA:
Focused matching commit on this change set (full SHA recorded on issue #1
after push).

REGRESSION TESTS:
Cloud OR-group; consulting/sales category mismatch; ambiguous pronoun;
23-month vs 24-month vs overlapping roles; degree provenance; sanitized
resume shape; forbidden and/or + wage + incentive output.

REMAINING RISKS:
Date parser is still line-local. Related-field degree remains a policy call.
Live Deloitte A–E can still fail even if these unit tests pass.

QUESTIONS FOR CHATGPT:
1. Cloud alternatives are a parent `CanonicalRequirement` with
   `alternatives=["Azure","AWS","GCP"]` and `matched_alternative` when one
   branch hits. Is that the representation you wanted?
2. Gates are category → required concepts/branch → duration/credential →
   0.75/0.45. Thresholds were not lowered.
3. JSON-LD “Bachelor's Degree” is provenance on the longer field sentence,
   not a separate gap. Field-fit is evaluated separately from degree level.
4. “That one” no longer auto-selects the first COVERED item. Multiple recent
   items → local clarification, `planner_calls=0`.

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
