# Phase 6 — Agent Orchestrator Audit

**Scope:** audit the existing orchestration code before adding multi-step intelligence.
**Rule:** Phases 1–5 and the voice/NLU architecture must not be rebuilt or regressed.

---

## 1. Existing orchestration architecture

`src/agent/orchestrator.py` (`AgentOrchestrator`) is the single entry point.

`handle_user_message()` routes in this order:

1. `match_control_command()` — stop / cancel / pause / continue / status
2. filler discard (`voice_gate.FILLERS`)
3. emergency stop check
4. `resolve_intent()` (LocalIntentResolver + CommandValidator)
5. `apply_resolved_intent()` → local HUD task actions
6. `FastCommandRouter.route()` → deterministic browser/computer/screen intents
7. `classify_utterance()` → `PlannerAdmissionController`
8. admitted → `run_task()`; question → `run_conversation()`; else local no-op

Steps 1–6 never touch a model. That is the fast path and it works.

## 2. Existing planner implementation

`run_task()` is a **ReAct-style loop**, not a planner:

```
for step_i in range(max_steps):
    provider.chat(messages, tools)      # planner wakes
    if no tool_calls: return spoken answer
    execute each tool call
    append tool results to messages     # context grows
```

Problems for Phase 6:

| Problem | Impact |
|---|---|
| Planner wakes after every tool batch | `planner_calls_per_task` scales with steps |
| Full tool payload appended to `messages` | prompt grows unbounded |
| No structured plan | no plan validation, no progress model, no HUD steps |
| No verification between steps | tool success treated as goal success |
| Failures return raw strings | no classification, no recovery strategy |
| `max_steps=20` | worst case 20 planner calls |

## 3. Existing local routing

Preserved and reused as-is:

- `LocalIntentResolver` (`local_intent.py`) — task intents, pending fill, contextual follow-ups
- `CommandValidator` — execute vs clarify vs discard
- `FastCommandRouter` (`router.py`) — browser/computer fast intents
- `ScreenIntentRouter` — Phase 3 fast paths
- `command_clause.py`, `transcript.py`, `voice_echo.py`, `voice_gate.py`, `voice_session.py`

## 4. Existing planner admission logic

`utterance.py` + `planner_admission.py` (built in the voice/NLU sprint) already implement
`UtteranceClassifier` and `PlannerAdmissionController`. Admission reasons exist for
`MULTI_STEP_GOAL`, `TOOL_REASONING_REQUIRED`, `CROSS_APPLICATION_TASK`,
`MULTI_STEP_CROSS_TOOL_GOAL`, `RECOVERY_REPLAN_REQUIRED`. `NO_LOCAL_INTENT` is explicitly
not an admission reason. **Keep, extend with `ENVIRONMENT_CHANGED` / `PLAN_INVALIDATED`.**

## 5. Existing task state

`src/agent/models/task.py`:

- `Task`: id, title, user_request, status, current_step, steps, context, results, errors, summary
- `Step`: id, description, status, tool, arguments, result, error, attempts, dependencies
- `TaskStatus`: PENDING, RUNNING, WAITING_FOR_USER, WAITING_FOR_APPROVAL, COMPLETED, FAILED, CANCELLED, PAUSED

Missing: `PLANNING`, `BLOCKED`, `normalized_goal`, step `order`, `max_attempts`,
`verification`, `SKIPPED`/`READY`/`VERIFYING` step states, and any `TaskContext`.

`TaskStore` (SQLite) persists `Task` as JSON — reusable.

## 6. Existing tool registry

`tools/base.py` `ToolRegistry` — register / get / list_tools / openai_tools / validate_arguments /
execute with permission check, audit, bounded retries (`tool.max_retries`).

`ToolResult`: success, data, error, requires_approval, approval_id.
Missing: `retryable`, `state_changed`, `metadata`, and per-tool category /
`state_changing` / `external_effect` / `future_permission_level` metadata.

Registered tool count is ~87 across `browser.*` (44), `computer.*` (24), `files.*` (15),
`screen`/`agent`/`speech`.

## 7. Existing context handling

- `ConversationMemory` (8 turns) — conversation, not task state
- `WorldState` — browser/computer event cache
- `ConversationTurnContext` (`turn_context.py`) — pending interaction, recent replies
- No task-scoped context (selected_file, candidate_files, selected_tab, last_result)

## 8. Existing cancellation

- `EmergencyStop` checked inside `ToolRegistry.execute` and between steps
- `cancel_stale()` bumps `_generation`; `_if_stale()` aborts superseded work
- `provider.cancel_inflight()` aborts in-flight HTTP to Ollama
- Control commands map to `_handle_control` (stop/cancel/pause/continue/status)

Gap: pause/resume/skip do not apply to a structured plan because no plan exists.

## 9. Existing retry behavior

Only `ToolRegistry` per-tool retries (`max_retries`, default 2). There is no failure
classification, no alternative-method fallback, and no replanning.

## 10. Current performance

| Path | Model | Observed |
|---|---|---|
| Control / filler | none | < 5 ms |
| HUD task intents | none | 3–8 ms |
| Fast browser/screen | none | < 500 ms |
| Utterance classification | none | < 1 ms |
| AI conversation | conversation model | 1 call, 0 tool schemas |
| `run_task` planner | qwen2.5:7b | 13–25 s per call, previously 1 call **per step** |

Tool filtering already exists (`tool_filter.filter_tools`) — 87 → ~1–15 tools.

## 11. Components to reuse

`ToolRegistry`, `TaskStore`, `EmergencyStop`, `PermissionEngine`, `AuditLog`,
`LocalIntentResolver`, `CommandValidator`, `FastCommandRouter`, `UtteranceClassifier`,
`PlannerAdmissionController`, `ModelOutputBoundary` (`response_boundary.py`),
`ConversationTurnContext`, `WorldState`, `PerfTrace`, all Phase 2–5 tools.

## 12. Components to refactor

| Component | Change |
|---|---|
| `run_task()` | keep as legacy ReAct; new goals go through plan-then-execute |
| `executor.py` | trivial name/args runner → real step executor with verification |
| `models/task.py` | add PLANNING/BLOCKED + step order/verification/max_attempts |
| `tool_filter.py` | wrap in `ToolCategoryResolver` with registry-derived categories |
| `model_router.py` | add `CONVERSATION_MODEL` |
| `planner_admission.py` | add `ENVIRONMENT_CHANGED`, `PLAN_INVALIDATED` |

## 13. Components that are missing

`AgentPlanner` (structured JSON plan), `PlanValidator`, `TaskContext`,
`ActionVerifier`, failure classification, `FailureRecoveryEngine`, bounded replanning,
task-scoped reference resolution, context compression, HUD task progress,
planner-call telemetry per task.

## 14–18. Phase integration points

| Phase | Integration |
|---|---|
| **2 — Computer** | Phase 6 orchestrates `computer.*`; no duplicate mouse/keyboard/window code |
| **3 — Screen** | `ScreenUnderstandingService` used only as the *last* verification tier |
| **4 — Files** | `files.find_recent` / `find_by_name` / `search` / `read` / `open` reused |
| **5 — Browser** | `BrowserSession` tools reused; fast paths must stay planner-free |
| **Voice/NLU** | Phase 6 sits *below* PlannerAdmission; barge-in, echo, sanitizer untouched |

---

## Conclusion

The routing layer above the planner is sound. The gap is everything below admission:
there is no plan, no verification, no recovery, and the planner wakes per step.
Phase 6 adds a plan-then-execute layer between `PlannerAdmissionController` and the tools.
