# Cursor ↔ ChatGPT root-cause debug bridge

GitHub is the shared engineering layer. Cursor posts evidence. ChatGPT reviews
the issue, commit, and diff independently. The goal is root cause, not a
symptom patch.

Permanent thread: GitHub issue **JARVIS Root-Cause Debug Thread**.
Permanent log: [`JARVIS_ROOT_CAUSE_LOG.md`](JARVIS_ROOT_CAUSE_LOG.md).
Actual router order: [`ROUTING_PRECEDENCE.md`](ROUTING_PRECEDENCE.md).

## User workflow

1. User runs the live microphone / Windows / Chrome test.
2. Cursor captures logs, diagnoses, fixes, commits, posts a review package.
3. User tells ChatGPT: “Review the latest JARVIS root-cause update.”
4. ChatGPT rereads the issue + commit + diff and posts findings.
5. Cursor replies with evidence or a warranted fix.
6. User reruns the live acceptance test.
7. Repeat until the live test passes.

## Cursor rules

- Diagnose before patching. Reproduce, capture the path, find the earliest
  wrong pipeline decision, explain why, name the architectural cause.
- List at least two hypotheses when the cause is not proven. Prove or eliminate
  them with logs or tests. Do not guess.
- Prefer shared abstractions, provenance, coverage, structured state, and
  explicit precedence. Do not add one-off regexes, phrase patches, or
  site-specific special cases (ChatGPT / Coursera / YouTube).
- Do not bypass validation. Do not route everything to the planner or the
  local fast path.
- If several bugs share a pattern, stop stacking patches. Extract the shared
  object (for example PageEntityQuery, RecentArtifactContext).
- Protect Phase 2–7, voice echo, existing-Chrome targeting, multi-monitor,
  PageModel, task / recent-task / artifact context, and result synthesis.
- Shared routing changes require the related regression suite.
- Every fix needs a regression test that would fail before the fix.
- Automated PASS is not DONE. Live proof is PASS / FAIL / NOT YET RUN.
- Fail closed. Never hide a technical failure behind “Done.”
- Verify requested user outcomes, not internal step success.
- Analysis tasks must verify output quality (clean requirements, provenance,
  no browser chrome, stored ComparisonResult, follow-up retrieval).
- Every 5–10 fixes, audit duplicate routing, unused fallbacks, planner
  overuse, stale caches, and state ownership. Post a health summary.

## Failure evidence (required)

Collect when available. Never log passwords, tokens, secrets, or cookie values.

- original / normalized utterance, segments, actions, domains
- recent context used, recognized intent, intent confidence
- route, fast-route coverage, planner admission + reason
- tool filter result, tool calls, task steps, verification, final response, timings
- HWND, tab/page identity, file path, artifact state
- PageModel id, task id, comparison result id

## Commit discipline

One focused commit per root-cause fix. Record SHA, files, why each file
changed, automated result, live result. Do not mix unrelated features.

## Cursor → ChatGPT package

Post this block on the shared issue after each fix:

```
ROOT-CAUSE REVIEW REQUEST
BUG:
LIVE COMMAND:
EXPECTED:
ACTUAL:
EARLIEST WRONG DECISION:
ROOT CAUSE:
FILES / FUNCTIONS:
FIX:
COMMIT:
AUTOMATED TESTS:
LIVE TEST:
KNOWN RISKS:
QUESTIONS FOR CHATGPT:
```

## ChatGPT review format

```
REVIEW STATUS: ACCEPT / REVISE / NEEDS MORE EVIDENCE
ROOT-CAUSE ASSESSMENT:
ARCHITECTURAL RISKS:
MISSING TESTS:
REGRESSION RISKS:
NEXT EXPERIMENT:
ACCEPTANCE CONDITION:
```

If ChatGPT disagrees, Cursor does not immediately rewrite. It inspects the
challenge, collects missing telemetry, proves or disproves it on the issue,
then changes code only if warranted.

## Definition of done for this bridge

- One permanent GitHub issue exists
- `JARVIS_ROOT_CAUSE_LOG.md` exists and is append-only
- Cursor posts evidence packages, not “Fixed.”
- Fixes have focused commits ChatGPT can open
- ChatGPT can challenge the diagnosis
- Live tests remain the acceptance gate
