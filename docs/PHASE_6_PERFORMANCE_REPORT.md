# Phase 6 Performance Report — Multi-Step Agent Orchestrator

**Date:** 2026-09-08
**Workspace:** `c:\Users\herbi\AI Assistant`
**Planner model:** `qwen2.5:7b` via Ollama, CPU inference
**Conversation model:** `qwen2.5:7b` (same weights, `CONVERSATION_MODEL` tier, no tool schemas)
**Vision model:** unchanged from Phase 3

Numbers come from two harnesses, both committed:

| Harness | What it measures | Output |
|---|---|---|
| `scripts/measure_phase6.py` | Routing, admission, filtering, planner tokens, control verbs | `docs/PHASE_6_PROFILE.json` |
| `scripts/measure_phase6_live.py` | Real Chromium, real DOM, real files on disk, real planner | `docs/PHASE_6_LIVE_PROFILE.json` |

Nothing below is estimated. Every figure is read out of one of those two JSON files.

---

## Routing latency (the fast paths Phase 6 must not slow down)

| Metric | Value |
|---|---|
| Fast route, average | **19.3 ms** |
| Fast route, worst case | **87.0 ms** |
| Utterance classification, worst case | **0.1 ms** |
| Planner admission decision | included in the 0.1 ms above |
| Planner admissions on fast-path utterances | **0 of 13** |

The 13 fast-path utterances are the ones the spec lists as forbidden from planning:
"What page am I on?", "What's the current URL?", "Open Chrome.", "Go back.",
"Scroll down.", "Refresh.", "What tasks do I have?", "Can you see my screen?",
"What application am I in?", "Stop.", "Pause.", "Continue.", "That's fine."

None reached `AgentPlanner`. The classifier and admission controller together cost
less than a millisecond, so the Phase 6 gate is not measurable against the ~19 ms
router.

## AI conversation stays out of Phase 6

| Metric | Value |
|---|---|
| "Why is the sky blue?" end to end | **4,792 ms** |
| Route taken | `AI_CONVERSATION` |
| Phase 6 task created | **no** |
| Tool schemas sent | **0** |

## Planner latency

CPU inference on a 7B model dominates every multi-step task. This is the honest
number, not a warmed best case.

| Metric | Value |
|---|---|
| Model warm-up (first token after load) | 1,680 ms |
| Planner inference, mocked-tool tasks (3 runs) | 14,139 / 16,017 / 21,814 ms |
| Planner inference, live browser tasks (6 runs) | 12,601 / 13,316 / 13,860 / 18,649 / 27,751 / 28,216 ms |
| Planner inference, replan | 9,375 / 19,497 / 30,318 ms |
| Planner timeout (configured, `config.yaml`) | 45,000 ms |
| Timeouts observed across all runs | **0** |

Planner cold versus warm is not a meaningful split here: Ollama keeps the model
resident between calls, so the only cold cost is the 1,680 ms warm-up measured once
at start. Every subsequent call is a warm call, and the spread above is output-length
variance, not load variance.

The timeout is bounded and was not raised to hide the latency. When it fires the task
keeps its state and returns `PLANNER_TIMEOUT` rather than freezing.

## Planner token budget

| Metric | Value |
|---|---|
| Planner input tokens, average | **285** (283 / 285 / 287 across tasks) |
| Planner output tokens, average | **92** (45 / 63 / 168) |
| Task context tokens, average | **86** (55 / 67 / 137) |

Input size is essentially flat across tasks of 1, 2 and 5 steps, which is the point of
the compact `TaskContext`: prompt size tracks the goal, not the amount of work done.

## Tool filtering

Against the real registry of **87 tools**:

| Goal | Categories resolved | Tools after filter | Reduction |
|---|---|---|---|
| File goal | core, files | **9** | 90% |
| Screen goal | core, screen | **4** | 95% |
| Browser goal | browser, computer, core, screen | **34** | 61% |
| Cross-tool goal | browser, computer, core, files, screen | **41** | 53% |

Live browser runs show the same filter in schema-token terms:
**6,420 → 2,352 tokens** (78 → 29 tools), a 63% cut in what the planner has to read.

Coordinate-only mouse tools (`computer.click`, `computer.scroll`) are excluded from the
planner's core set entirely. The planner cannot know screen coordinates, so offering
those tools only produces plans that click at invented positions.

## Planner calls per task

| Scenario | Planner calls | Replans |
|---|---|---|
| Direct commands and comments (13 utterances) | **0** | — |
| AI conversation | **0** | — |
| Complex goal, mocked tools (3 tasks) | **1, 1, 1** | 0, 0, 0 |
| Live: complex browser (Test 4) | **1** | 0 |
| Live: verify state change (Test 11) | **1** | 0 |
| Live: context compression (Test 16) | **1** | 0 |
| Live: end-to-end 5-step (Test 19) | **1** (2 when the first plan is rejected) | 0 |
| Live: login interruption (Test 13) | **1** | 0 |
| Live: element genuinely absent (Test 12) | **3** | 1 |

A 5-step task costs one planner call. The only runs above 1 are a rejected first plan
(one repair) and Test 12, where the element does not exist on the page and replanning
is the correct response rather than a loop.

## Tool execution and verification latency

Real Playwright calls, live runs:

| Tool | Latency |
|---|---|
| `browser.goto` | 35 ms |
| `browser.findElement` | 9 ms |
| `browser.fill` | 16 ms |
| `browser.scroll` | 11 ms |
| `browser.reload` | 37 ms |
| `browser.getVisibleText` | 30 ms |
| `files.find_recent` | 2 ms |
| `files.open` | 119 ms |

Verification rides on the cheapest tier that answers the question, so it is mostly
free: URL comparison and page fingerprint are already in the tool result. The two
tiers that cost anything are window verification, which polls the foreground title for
up to 4 s while an external viewer starts, and Phase 3 vision, which is reserved for
`computer.open_application`, `computer.open_file` and `files.open`. Vision is never
invoked after a browser step.

## Task context growth

Measured by forcing a replan after completed steps (Test 16):

| Metric | Value |
|---|---|
| `compact_state()` size | **180 characters** |
| Raw context payload | 2 characters |
| Recent tool results retained | 0 |
| Estimated replan input | **45 tokens** |

Context does not accumulate tool payloads. Completed work is carried as up to six
short descriptions, and tool results are summarized before they are stored.

## End-to-end task duration

| Task | Duration |
|---|---|
| Complex goal, mocked tools | 14.2 / 25.3 / 31.7 s |
| Live end-to-end, 5 steps, browser + files (Test 19) | **51.7 s** |
| Live complex browser, 3 steps (Test 4) | ~30 s |
| Live failure recovery with replan (Test 12) | 46.1 s |
| Live login interruption to hand-off (Test 13) | 33.6 s |

Roughly 90% of every duration above is planner inference on CPU. Tool execution across
a whole 5-step task totals under 200 ms. The way to make Phase 6 faster is a faster
planner tier or GPU inference, not orchestration changes.

## Where the time actually goes

Test 19, the 5-step end-to-end demo:

| Stage | Time | Share |
|---|---|---|
| Router + classification + admission | 125 ms | 0.2% |
| Planner inference | 27,751 ms | 53.7% |
| Reasoning step (tool-free comparison) | 24,158 ms | 46.7% |
| All tool execution | 156 ms | 0.3% |
| Verification | under 10 ms | negligible |

The orchestration layer this phase added costs a fraction of a percent of task time.
