# Phase 6 Completion Report — Multi-Step Agent Orchestrator

**Date:** 2026-09-08
**Workspace:** `c:\Users\herbi\AI Assistant`
**Planner model:** `qwen2.5:7b` via Ollama (CPU)
**Conversation model:** `qwen2.5:7b` on the `CONVERSATION_MODEL` tier, no tool schemas attached

**Declaration:** PHASE 6 STATUS: COMPLETE — PHASE 7 MAY BEGIN.

Supporting documents: `docs/PHASE_6_AGENT_ORCHESTRATOR_AUDIT.md` (Step 1 audit),
`docs/PHASE_6_PERFORMANCE_REPORT.md` (all timing and token figures),
`docs/PHASE_6_PROFILE.json` and `docs/PHASE_6_LIVE_PROFILE.json` (raw measurements).

---

## Completion matrix

Every PASS below names the evidence. Nothing is marked PASS because a class exists.

| Capability | Result | Evidence |
|---|---|---|
| Current architecture audited | PASS | `docs/PHASE_6_AGENT_ORCHESTRATOR_AUDIT.md`, 18 required sections |
| Fast paths preserved | PASS | 21 forbidden sentences, 0 admissions; 19.3 ms average route |
| Voice/NLU routing preserved | PASS | 115 voice/intent tests pass; routing order unchanged |
| UtteranceClassifier preserved | PASS | Reused, now runs before `FastCommandRouter`; 7 routing tests |
| PlannerAdmissionController | PASS | Admission reasons logged per task; `NO_LOCAL_INTENT` alone never admits |
| AI conversation separated from planner | PASS | "Why is the sky blue?" → `AI_CONVERSATION`, 0 tool schemas, no task |
| Task model | PASS | `AgentTask`, all 10 statuses reachable; live runs hit COMPLETED, BLOCKED, WAITING_FOR_USER, PAUSED, CANCELLED |
| TaskStep model | PASS | `AgentStep` with the full status set; verification and failure code per step |
| TaskContext | PASS | 180 characters after a multi-step task (Test 16) |
| Structured planning | PASS | JSON-only plans; 9 live plans produced and validated |
| PlanValidator | PASS | Rejected `UNKNOWN_TOOL` and `BAD_ARGUMENTS` live, then repaired |
| ToolRegistry | PASS | 87 tools, full Phase 7 metadata on each |
| Dynamic tool filtering | PASS | 87 → 4/9/34/41 by goal; 6,420 → 2,352 schema tokens live |
| ModelRouter | PASS | Five tiers; `PLANNER_MODEL` reached only on admission |
| DeterministicTaskExecutor | PASS | 5-step live task, 1 planner call, planner did not wake per step |
| ActionVerifier | PASS | Test 11: click reported successful only after the title actually changed |
| Failure classification | PASS | `ELEMENT_NOT_FOUND`, `TAB_NOT_FOUND`, `AUTHENTICATION_REQUIRED`, `PLAN_INVALIDATED` all raised live |
| Failure recovery | PASS | Test 12 ladder: refresh → retry → replan, all live |
| Bounded retries | PASS | Test 12 `bounded: true`; no step exceeded `max_attempts` |
| Dynamic replanning | PASS | Test 12 replanned once and kept completed work |
| Task pause | PASS | `PAUSED`, 3 tools called before pause, none after |
| Task resume | PASS | Resumed from the correct step, not from the start |
| Task cancel | PASS | `CANCELLED`, 0 tools called after cancel |
| Task skip | PASS | Step marked skipped, execution continued |
| Task modification | PASS | Selection changed to `resume_2024.pdf`, `correction_restarted: false` |
| Reference resolution | PASS | "the second one" resolved from 3 candidates in `TaskContext` |
| Context compression | PASS | Replan input 39–45 tokens after completed steps |
| Planner call limiting | PASS | 1 call for 1-, 2-, 3- and 5-step tasks; 0 for commands and chat |
| Model output boundary | PASS | Test 17: 3 of 4 synthetic outputs blocked, raw JSON spoken = 0 |
| HUD task progress | PASS | `task_progress_ready` drives the panel per step |
| Performance telemetry | PASS | Every field the spec lists is emitted per task |
| Phase 2 regression | PASS | 9/9 |
| Phase 3 regression | PASS | 22/22 plus 6/6 live vision |
| Phase 4 regression | PASS | 25/25 |
| Phase 5 regression | PASS | 41/41, real Chrome and Edge |
| Voice regression | PASS | 46 + 52 + 9 + 7 + 1 live speaker, all pass |
| Automated tests | PASS | 359 run, 359 passed, 0 failed, 0 skipped |
| Live runtime tests | PASS | 7 live scenarios against real Chromium and real files |
| End-to-end Phase 6 demo | PASS | Test 19 completed, 5/5 steps, 1 planner call |

---

## Numbered tests from the specification

| # | Test | Result | What actually happened |
|---|---|---|---|
| 1 | Direct command regression | PASS | All 15 spec commands: `planner_admitted=false`, 0 planner calls |
| 2 | Social / comment regression | PASS | "That's fine.", "I see.", "Okay then.", "Maybe later." — no admissions |
| 3 | AI conversation | PASS | `AI_CONVERSATION`, 0 tool schemas, 4.8 s, no Phase 6 task |
| 4 | Complex browser task | PASS | 3 steps, 1 planner call, 2 real tabs, `planner_woke_per_step: false` |
| 5 | File + browser task | PASS | Files and browser in one task; selection held in `TaskContext` |
| 6 | Resume + job page | PASS | Exact wording: `COMPLEX_GOAL`, admitted, `MULTI_STEP_CROSS_TOOL_GOAL`; task completed |
| 7 | File reference follow-up | PASS | "Use the second one." resolved locally, no restart |
| 8 | User correction | PASS | Selection swapped, dependent steps invalidated, history kept |
| 9 | Pause / resume | PASS | 3 tools before pause, 0 during, correct step on resume |
| 10 | Cancel | PASS | `CANCELLED`, 0 tools after |
| 11 | Verify state change | PASS | Title moved "Jarvis Fixture Home" → "Next page LeafLink" before success |
| 12 | Failure recovery | PASS | Bounded ladder, one replan, then stopped and said so |
| 13 | Login interruption | PASS | `AUTHENTICATION_REQUIRED` → `WAITING_FOR_USER`, goal preserved |
| 14 | No planner loop | PASS | 5-step task, `planner_calls_per_task = 1` |
| 15 | Tool filtering | PASS | 87 → 9 files, 4 screen, 34 browser, 41 cross-tool |
| 16 | Context compression | PASS | 157–180 characters, 39–45 replan tokens |
| 17 | Model output safety | PASS | Raw JSON spoken = 0; tool-call syntax never reached TTS |
| 18 | Voice interruption | PASS | Interrupt returns in 0.0 ms; `scripts/check_phase6_bargein.py` |
| 19 | End-to-end demo | PASS | 5/5 steps, correct newest PDF from 3 candidates, 51.7 s |
| 20 | Natural complex goal | PASS | `COMPLEX_GOAL`, admitted, `MULTI_STEP_CROSS_TOOL_GOAL`; task completed |

Tests 1, 2, 3, 6 and 20 are re-runnable as a set with
`python scripts/check_admission_matrix.py`, which puts all 21 must-not-plan sentences
and 6 must-plan sentences from the specification through the real classifier and
admission controller: **27/27 correct**. Every sentence the specification lists as
forbidden stays local, including "Why is the sky blue?" and "Explain transformer
attention."

---

## Architecture discovered, and what changed

The audit found a working Phase 1–5 stack with a ReAct-style loop standing in for
orchestration: the planner was re-entered after every tool call, prompts grew without
bound, and there was no plan object, no verification and no recovery.

Phase 6 replaced that loop with plan-then-execute and left everything underneath it
alone. `LocalIntentResolver`, `FastCommandRouter`, `UtteranceClassifier`,
`CommandValidator`, `ConversationTurnContext` and `ModelOutputBoundary` are reused as
they were. The one routing change is ordering: the classifier now runs before the fast
router, so a sentence like "Open Chrome, go to Google, and then find my resume" is not
claimed by the "open Chrome" fast path.

### Files created

`src/agent/phase6/` — `models.py`, `planner.py`, `tool_catalog.py`, `verify.py`,
`recovery.py`, `references.py`, `step_executor.py`, `binding.py`, `runner.py`,
`__init__.py`; `tests/test_phase6_orchestrator.py` (88 tests);
`scripts/measure_phase6.py`, `scripts/measure_phase6_live.py`,
`scripts/check_phase6_bargein.py`, `scripts/check_admission_matrix.py`; this report plus
the audit and performance reports.

### Files modified

`src/agent/orchestrator.py` (Phase 6 entry, control routing, mid-task correction, HUD
publishing), `src/agent/model_router.py` (`CONVERSATION_MODEL` tier),
`src/agent/planner_admission.py` (`ENVIRONMENT_CHANGED`, `PLAN_INVALIDATED`),
`src/agent/providers/ollama.py` (JSON mode, `max_tokens`, per-call timeout),
`src/agent/utterance.py` (wider imperative verb set),
`src/agent/browser/agent.py` (interrupt check on `fill`, invalid selectors no longer
surface Playwright stack traces), `src/agent/browser/resolver.py` (role-noun stripping),
`src/output/speech.py` (two barge-in defects, below), `src/ui/jarvis_window.py` (task
panel), `main.py`, `config.yaml` (`planner_timeout_s: 45`), and the Phase 1–5 test files
that asserted the old routing.

### Dependencies added

None. `reportlab` is used by the live harness only and was already present.

---

## Behaviour worth calling out

**Planning is one call, not one per step.** A 5-step live task costs a single planner
call. Replanning happens only on genuine failure: across nine live tasks there were two
replans, both after a real element was missing.

**Tool filtering is enforced, not advisory.** The planner never sees more than 41 of 87
tools, and coordinate-only mouse tools are excluded entirely, because a planner cannot
know screen coordinates and offering them only produces clicks at invented positions.

**Verification is outcome-based.** A click is not successful because Playwright returned
cleanly; Test 11 passes only because the page title actually changed. A search that
matches nothing is a failed step, not an empty success. Window verification now waits up
to 4 seconds for a launched viewer to appear, since reading the foreground title the
instant a launch call returns reports the window that was already there.

**A task that stops short says so.** This was the most significant defect found during
live testing. A step could fail, get replanned around, disappear from the record, and
the task would report clean success — in one run the model narrated "The Checkout button
has been clicked" for a button that does not exist on the page. Failed actions now stay
in the task record, the final report is told what did and did not happen, and a task
that never performed a state-changing action ends `BLOCKED` with "I could not find and
click Checkout button" rather than "Done."

**Two barge-in defects were found and fixed** while verifying Test 18, both in code that
predates Phase 6. SAPI reports "not speaking" for roughly 150 ms after an asynchronous
`Speak`, so the wait loop exited immediately: `is_speaking` went false while audio was
still playing, and the idle callback reopened the microphone mid-sentence — the exact
self-echo condition the voice work removed. Separately, `stop_speaking` called SAPI from
the caller's thread, marshalling into the speech thread's COM apartment and putting
about 700 ms in front of every interruption. The purge now happens on the speech thread
and the interrupt call returns in 0.0 ms.

---

## Test totals

```text
tests run       359
tests passed    359
tests failed      0
tests skipped     0
```

No test is skipped or conditionally disabled. The live suites ran for real on this
machine: `test_screen_phase3_live` (6, real screen capture and vision),
`test_browser_phase5` (41, real Chrome and Edge), `test_voice_live_speaker` (1, real
audio device).

| Suite | Tests |
|---|---|
| `test_phase6_orchestrator` | 88 |
| `test_local_intent` | 52 |
| `test_voice_repair` | 46 |
| `test_browser_phase5` | 41 |
| `test_files_phase4` | 25 |
| `test_perf_router` | 24 |
| `test_agent_phase1` | 23 |
| `test_screen_phase3` | 22 |
| `test_computer_phase2` | 9 |
| `test_voice_and_phase3` | 9 |
| `test_utterance_routing` | 7 |
| `test_screen_phase3_live` | 6 |
| `test_response_boundary` | 4 |
| `test_voice_live_speaker` | 1 |
| Remaining suites | 2 |

---

## Known limitations

**Planner latency dominates.** 12–30 seconds per plan on CPU. Routing, filtering,
execution and verification together are under 200 ms; the wait is `qwen2.5:7b`
generating tokens. A GPU or a smaller planner tier is the only real fix.

**The 7B planner needs occasional repair.** Roughly one plan in five is rejected on the
first attempt — a tool name given as a list, an argument named `query` where the schema
says `name`, a jQuery `:contains()` selector. Aliasing, clamping and list-unwrapping
absorb most of these silently; the rest cost one repair round-trip. A larger planner
would reduce this.

**Abandoned-action detection is a heuristic.** An action is treated as unperformed
unless a later step completed with the same tool. If a replan achieves the same
objective through a *different* tool, the task can end `BLOCKED` and report a shortfall
that was in fact covered. This errs toward under-claiming, which is the safe direction,
but it is a heuristic and not a goal-level verifier.

**Audio takes about 670 ms to fall silent after a barge-in.** The interrupt itself is
instant and the microphone stays shut for the whole tail, so there is no echo risk, but
the last fraction of a word is still audible. This is the audio device draining and is
unchanged from earlier phases.

**Test 18's two halves were verified separately.** TTS cancellation was measured with a
real audio device; the routing half ("Wait." pauses the running task, "Use the other
file." retargets it rather than starting a new one) is covered by automated tests
against a live task. They were not exercised in one continuous spoken session.

**`WAITING_FOR_APPROVAL` exists but is inert.** It is reachable in the model and carried
through telemetry so Phase 7 has somewhere to land. No approval policy is implemented,
as instructed.

---

PHASE 6 STATUS: COMPLETE

PHASE 7 MAY BEGIN.
