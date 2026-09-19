# Seed for the standing GitHub issue

`gh` and GitKraken were not authenticated on this machine when the bridge
was created. After `gh auth login`, create the issue with:

```
gh issue create --repo herbosexton/AI-Screenshare-Helper --title "JARVIS Root-Cause Debug Thread" --body-file docs/debug/GITHUB_ISSUE_SEED.md
```

Then post the review package in section 2 as the first comment.

Do not use this file as a substitute for the live issue once the issue exists.
The issue is the shared thread. This file is only the bootstrap text.

---

## 1. Issue body

Standing engineering thread for Cursor ↔ ChatGPT root-cause review.

**Purpose.** Do not treat Cursor and ChatGPT as two assistants that only receive screenshots. This issue is the shared communication layer. Cursor posts evidence. ChatGPT independently reviews the diagnosis, commit, diff, tests, and architecture. The goal is root cause, not symptom patches.

**Do not comment** “Fixed.” / “Working now.” / “Tests pass.” Every update must contain evidence.

**Canonical docs**

- `docs/debug/CURSOR_CHATGPT_DEBUG_BRIDGE.md` — workflow and review formats
- `docs/debug/ROUTING_PRECEDENCE.md` — actual orchestrator order (not the spec example)
- `docs/debug/JARVIS_ROOT_CAUSE_LOG.md` — append-only memory

**User workflow**

1. User runs the live microphone / Windows / Chrome test.
2. Cursor captures logs, diagnoses, fixes, commits, posts a ROOT-CAUSE REVIEW REQUEST here.
3. User tells ChatGPT: “Review the latest JARVIS root-cause update.”
4. ChatGPT rereads this issue + the commit + the diff and posts a review in the format below.
5. Cursor replies with evidence or a warranted fix. It does not immediately rewrite on disagreement.
6. User reruns the live acceptance test.

**ChatGPT review format**

```
REVIEW STATUS: ACCEPT / REVISE / NEEDS MORE EVIDENCE
ROOT-CAUSE ASSESSMENT:
ARCHITECTURAL RISKS:
MISSING TESTS:
REGRESSION RISKS:
NEXT EXPERIMENT:
ACCEPTANCE CONDITION:
```

**Honest repo constraint.** `origin/main` began as the initial screenshare commit. Almost all of `src/agent/` was local/untracked when the bridge was created. The first focused commit is the bridge only. ChatGPT can inspect the protocol and the written diagnosis; it cannot open a focused semantic-matching source diff until that code is committed and pushed as its own change.

**Live acceptance is still the gate.** Semantic matching on Deloitte 359035 tests A–E is NOT YET RUN. Automated PASS is not DONE.

---

## 2. First ROOT-CAUSE REVIEW REQUEST (post as a comment)

```
ROOT-CAUSE REVIEW REQUEST

BUG:
After WEB_DOCUMENT extraction started returning real Deloitte qualifications, JobResumeComparisonEngine still compared raw split lines with token overlap. Live output had 31 items, including and/or, wage/incentive text, duplicated year lines, and “Bachelor's Degree” as not-shown despite a bachelor’s on the resume. Python evidence was the word “experience”. Cloud lines matched consulting/sales bullets.

LIVE COMMAND:
Look at the job page I have open, find my newest resume, compare the resume to the job requirements, and tell me what I’m missing.
Then: “Why did you mark Python as covered/partial?” and “What matched well?”

EXPECTED:
Canonical de-duplicated requirements. No fragments or compensation. Requirement-specific evidence. Degree = level vs field. Years COVERED only from chronology. Targeted Python follow-up. Clean HUD sections.

ACTUAL:
job_requirements_count=31 covered=0 partial=26 missing=3 needs_confirmation=2
Biggest gaps: and/or, and/or, Bachelor's Degree
source_scope was WEB_DOCUMENT (extraction held; matching did not)

EARLIEST WRONG DECISION:
Phase 6 compare classification (`_classify` / `_best_resume_line`), after a valid PageModel extract. Not a routing bug. Not a planner-admission bug. Follow-up path was local (`planner_calls=0`) but `format_evidence` dumped a broad list.

ROOT CAUSE:
No CanonicalRequirement layer. Tokens longer than two characters after a small stop list counted as evidence. JobRequirementValidator accepted short required-section fragments (`and/or`) after the single-word skill exception. educationRequirements “Bachelor's Degree” was a separate row.

FILES / FUNCTIONS:
src/agent/phase6/compare.py (_classify, _best_resume_line, formatters)
src/agent/phase6/job_posting.py (JobRequirementValidator.classify, extract_sections)
added: requirements.py, evidence.py, lookup.py, compare_types.py
src/agent/corrections.py (_SHOW_EVIDENCE)
Full write-up: docs/debug/JARVIS_ROOT_CAUSE_LOG.md (entry 2026-09-18 semantic matching)

FIX (local, not on origin in the bridge commit):
canonicalize + dedupe; reject fragments/compensation; concept retriever; degree/years logic; 0.75 strong threshold; RequirementLookup; new HUD sections.

COMMIT:
Bridge commit only (this docs/debug change). Matching source remains untracked against origin on purpose so this commit stays focused.

AUTOMATED TESTS:
PASS — 188 related tests (test_semantic_matching, test_phase6_orchestrator, test_utterance_routing)

LIVE TEST:
NOT YET RUN — Deloitte 359035 A–E

KNOWN RISKS:
Alias retriever ≠ embeddings. Date parser is narrow. Related-field degree is a policy call. ChatGPT cannot inspect the matching diff until a later focused code commit is pushed.

QUESTIONS FOR CHATGPT:
1. Is merging Azure/AWS/GCP and/or alternatives into one cloud requirement correct?
2. Are 0.75 / 0.45 the right strong/partial thresholds?
3. Should JSON-LD “Bachelor's Degree” be dropped entirely once a longer degree sentence exists?
4. Is fallback of “that one” to the first COVERED item too ambiguous?
```
