# JARVIS Performance Audit

**Date:** 2026-08-13  
**Workspace:** `c:\Users\herbi\AI Assistant`

This document traces why interactive commands were taking **79–229 seconds**, then records measured results after the architecture correction.

---

## 1. Observed real execution (before)

From live HUD/terminal logs:

| Step | What happened | Time |
|------|----------------|------|
| Voice STT | Whisper medium/small on **CPU** | hundreds of ms–seconds |
| “Open a browser and go to …” | Planner LLM (`qwen2.5:7b` / previously `qwen3:8b`) + **all tool schemas** | **9–87 s** |
| Tools | `browser.open` + `browser.navigate` (correct) | ~1–5 s |
| “What page am I on?” | Planner LLM again | **~79 s** |
| Observation | `computer.get_screen_state(include_uia=True)` | extra 100ms–seconds |
| Follow-up LLM | Spoken “reasoning” answer | **~229 s** |

User also hit **Ctrl+Shift+Esc** during “Asking local model…”, which cancelled tools after the model had already chosen them.

---

## 2. Why it was slow (pipeline)

```
Voice/text
  → JarvisWindow speaks “On it.” (filler queue)
  → AgentOrchestrator.run_task
      → provider.health_check() every request
      → SYSTEM_PROMPT + HUD task dump + 20–80 tool JSON schemas
      → Ollama /chat/completions  (CPU, ~4k–12k prompt tokens)
      → tool execute
      → LOOP BACK TO OLLAMA for a spoken sentence
      → TTS
```

Bottlenecks, in order:

1. **Every command used the planner LLM** — including “what page am I on?” and “go back.”
2. **Second (and third) LLM calls** after tools already succeeded — the model was used as a ResponseFormatter.
3. **Tool-schema dump** — browser + computer + files tools on every turn (thousands of tokens of prefill on CPU).
4. **Wrong observation** — `computer.get_screen_state(include_uia=True)` to learn URL/title the Playwright session already had.
5. **System prompt rule 3** told the model to call screen state before UI actions.
6. **qwen3:8b thinking** (later switched to qwen2.5:7b) still tens of seconds for one tool-calling turn on CPU.
7. **No model timeout** — 300s httpx timeout allowed 229s foreground waits.
8. **Health check / possible reload** on every `run_task`.
9. **`_agent_lock`** held for the entire LLM call — new commands and stop could not preempt inference.
10. **Filler TTS** (“On it.”, “Listening.”) queued behind/over real answers.

The browser subsystem already knew `url` / `title` via `BrowserAgent._page`. That data was never queried on a fast path.

A live Ollama measurement on this PC (2026-08-13) confirms CPU planner cost even with a **tiny** tool list:

| Call | Wall time | Tokens |
|------|-----------|--------|
| Tags ping | 211 ms | — |
| Tiny chat, first (may include load) | **6,604 ms** | prompt 36 / completion 2 |
| Same chat, warm | **432 ms** | prompt 36 / completion 2 |
| Planner-style “what page am I on?” + 1 tool schema | **5,991 ms** | prompt 135 / completion 16 |
| Approx completion tok/s (tiny output) | ~4.6 | CPU |

So a 79s page question was not “logging.” It was CPU prefill of a huge prompt plus a second 229s spoken-answer call. A 6s planner call with 135 prompt tokens scales into tens–hundreds of seconds once tool schemas and history inflate the prompt.

---

## 3. Target architecture (implemented)

```
USER
  → FastCommandRouter
       ├─ known command → tools / BrowserAgent.get_session_state()
       │                    → ResponseFormatter → TTS
       └─ complex        → Planner LLM (timeout 25s, filtered tools)
                            → DeterministicTaskExecutor
                            → ResponseFormatter (no extra LLM if tools succeeded)
```

Observation order for web:

1. Browser session state (Level A)
2. DOM / a11y snapshot (Level B)
3. Browser screenshot (Level C)
4. Windows UIA (Level C, opt-in)
5. Global vision (Level C)

---

## 4. Local model facts (this PC)

| Item | Value |
|------|--------|
| Planner model | `qwen2.5:7b` (was `qwen3:8b`) |
| Other local models present | `llava:latest`, `qwen3:30b-a3b`, `gemma3:latest`, `qwen3:8b` |
| Device | CPU (no NVIDIA GPU in path) |
| Vision | `llava` — not used for page questions |
| STT | Whisper `small` on CPU |
| TTS | Windows SAPI, barge-in purge enabled |
| Keep-alive | `30m` on Ollama requests + factory `warmup()` |
| Planner timeout | **25 s** (was 300 s) |
| num_ctx | 4096 |
| Cold tiny chat | 6,604 ms |
| Warm tiny chat | 432 ms |

---

## 5. After (measured)

### Fast path — pytest, no Ollama (`tests/test_perf_router.py`)

| Command | Path | Planner calls | Measured |
|---------|------|----------------|----------|
| “What page am I on right now?” | `fast:browser.current_page` | 0 | **2.3 ms** total (Router 2 ms, browser 0 ms) |
| “Go back.” | `fast:browser.back` | 0 | **1 ms** |
| “Open a browser and go to https://loldispensary.com” | `fast:browser.open_and_goto` | 0 | **1 ms** (fake navigate; real Chrome launch is extra) |
| “Find the job I was looking at earlier…” | planner | 1 (mock) | mock 13 ms; real CPU planner still seconds |
| “Stop.” | control | 0 | **1 ms**, emergency stop engaged |
| Page query during slow planner | fast preempts | stale planner dropped | page **1 ms**; planner marked stale |

### Observation cost (this PC, 2026-08-13)

| Operation | Time |
|-----------|------|
| `ComputerController.get_active_window()` (Level A) | **0 ms** |
| `get_state(include_uia=False)` | **2 ms** |
| `get_state(include_uia=True)` | **101 ms** (6 interactive nodes on this window) |

Full UIA trees on busy desktops are slower; they are no longer the default for URL/title.

### Live HUD

Restart Jarvis. Each command prints `REQUEST PERFORMANCE` and the SYSTEM panel shows a `Total: N ms` line when `agent.perf_debug` is true.

Type (not voice) in order:

1. `Open a browser and go to https://loldispensary.com`
2. `What page am I on right now?`

Expected for (2): `Path fast:browser.current_page`, **Planner LLM omitted**, spoken “You're on LOL Dispensary.”

Real Chrome launch + navigate still takes 1–10 s (Playwright, not LLM). That is tool time, not planner time.

---

## 6. What changed in code

- `FastCommandRouter` before `AgentOrchestrator` (`src/agent/router.py`)
- `ResponseFormatter` (`src/agent/respond.py`) — no LLM for page/app/tool success
- `DeterministicTaskExecutor` (`src/agent/executor.py`)
- `WorldState` + browser/computer events (`browserOpened`, `navigationCompleted`, `tabChanged`, `windowFocused`, …)
- `BrowserAgent.get_session_state()` — sessionId, URL, title, tabs, fingerprint, lastNavigation
- Observation levels A/B/C (`src/agent/observe.py`); `include_uia` defaults **false**
- `ModelRouter` tiers: NO_MODEL / FAST_MODEL / PLANNER_MODEL / VISION_MODEL
- Filtered `_llm_tools()`; `files.find_recent` included on file requests
- Ollama `keep_alive: 30m`, `timeout_s: 25`, `cancel_inflight()` on Stop / new command
- Speech priorities + barge-in; HUD drops stale generations
- Perf traces + HUD perf line

---

## 7. Remaining bottlenecks

- CPU inference for **complex** tasks still several seconds (tiny planner-style ~6 s; large prompts worse, capped at 25 s).
- Whisper `small` on CPU still slower than GPU STT (not re-timed in this sprint; typically 0.5–3 s).
- Playwright first launch of Chrome remains 1–10 s (not LLM).
- Aborting an in-flight Ollama HTTP call closes the client; the model may still finish on the server until keep-alive/timeout.
- Google properties may still CAPTCHA the isolated profile; that is pause-and-wait, not a planner loop.

---

## 8. Final report

```
Previous latency (live, before)
  "What page am I on?"     ~79 s planner + UIA + ~229 s second LLM
  "Open browser and go to" ~9–87 s planner

New latency (measured 2026-08-13)
  "What page am I on?"     2.3 ms fast path, 0 planner calls
  "Go back."               1 ms fast path
  Open+goto (tools only)   1 ms fake / real Chrome extra 1–10 s
  Planner tiny chat warm   432 ms
  Planner-style + 1 tool   5,991 ms  (this path is no longer used for page questions)

Fast-path commands implemented
  open chrome/browser/edge, open cursor/explorer/outlook
  close window / close browser
  go back / go forward / refresh
  scroll up / scroll down
  switch to gmail/linkedin/youtube
  what page/website/url/tab
  what application/window
  stop / pause / continue / cancel (control path)

Commands still requiring LLM
  Ambiguous or multi-step work, e.g.
  "Find the job I was looking at earlier and start the application."

Average model latency (this PC, qwen2.5:7b CPU)
  Warm tiny completion     432 ms
  Tiny tool-calling turn   ~6 s
  Historical huge-prompt   79–229 s (eliminated from page queries)

Model currently used
  qwen2.5:7b   (vision: llava, unused for state questions)

Prompt token counts
  Warm ping                36
  Tiny planner+1 tool      135
  Fast path                0

Tool-schema token counts
  Fast path                0
  Tiny 1-tool experiment   included in 135 prompt tokens
  Filtered dynamic tools   browser vs files vs computer (not the full registry)

Browser state query latency
  Fake session / cached    <1 ms
  Live Playwright title/url typically tens of ms (no DOM)

UIA latency
  Off                      2 ms
  On (this window)         101 ms

Vision latency
  Not invoked for page/URL questions

STT latency
  Whisper small on CPU (not re-measured this run; previously hundreds of ms–seconds)

TTS latency
  Windows SAPI; start typically <300 ms; barge-in purges queue

Tests added
  tests/test_perf_router.py
  (page query, go back, open+goto, complex→planner, stop, preemption)

Tests passing
  110 passed

Remaining performance bottlenecks
  CPU planner for genuinely complex tasks (~6 s+), Whisper on CPU,
  first Chrome launch, Ollama server-side work after HTTP abort.
```
