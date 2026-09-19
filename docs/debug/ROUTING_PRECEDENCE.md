# JARVIS routing precedence (actual)

This is the live order in `src/agent/orchestrator.py` (`handle_user_message`).
It is **not** the idealized list from the debug-bridge spec. Hidden fallbacks
still exist; they are listed at the bottom.

Do not invent a second precedence. If code changes, update this file in the
same commit.

## Order

1. **Phase 7 voice approval** — `_try_voice_approval(text)`
2. **Control commands** — `match_control_command(text)` (emergency continue / stop)
3. **Filler discard** — `normalize_speech(text) in FILLERS`
4. **Emergency-stop gate** — refuse work while engaged
5. **Transcript normalization (fast-fact pass)** — `TranscriptNormalizer().normalize`
6. **Fast fact router** — `FastCommandRouter.route(..., skip_tasks=True)` for page / URL / tab / monitor / close / nav facts
   - Rejected when `_is_agent_goal(classify_utterance(text), text)` is true
7. **Active Phase 6 task modification** — `_maybe_modify_agent_task` if a task is running
8. **Intent resolution** — `resolve_intent` (normalization, segmentation, local intent, pending context)
9. **Apply resolved local intent** — HUD / social / discard via `apply_resolved_intent`
10. **HUD reply fallback** — `_try_hud_reply` if no resolved local reply
11. **Utterance classification** — `_classify` / `classify_utterance`
12. **Follow-up gate** — `followup_scores` (`new_goal_override`, `task_correction_allowed`)
13. **Recent analysis follow-up** — `classify_analysis_followup` then `apply_analysis_followup` unless `new_goal_override`
14. **Recent task / artifact correction** — `classify_task_correction` only if `task_correction_allowed`
15. **Second FastCommandRouter pass** — if still no local reply (`skip_tasks=True`)
16. **Agent-goal veto of fast intent** — `_is_agent_goal` can nullify the second fast hit
17. **Return local HUD result** if one was produced in step 9–10
18. **Run fast intent** if step 15 produced one
19. **Planner admission** — `admit_planner(classified, local_intent=...)`
20. **Planner or conversational fallback** — `run_agent_task` vs chat / unknown

## Spec example vs reality

The spec example was:

1. transcript normalization
2. segmentation
3. explicit local intent
4. full-utterance coverage
5. recent task/artifact follow-up
6. new-goal override
7. utterance classification
8. planner admission
9. safe fallback

Reality differs:

- Fast page/URL facts run **before** full intent resolution and **before** analysis follow-up.
- `new_goal_override` is a gate on follow-up, not its own numbered router step.
- Classification happens **before** analysis follow-up, but planner admission happens **after**.
- There are two FastCommandRouter calls (facts first, then general fast path).

## Known hidden fallbacks

- Fast-fact miss retries the **raw** transcript if normalization changed the text.
- `_try_hud_reply` can still answer after `resolve_intent` produced no local route.
- `classify_task_correction` returning a hit with no repair yields “Which task do you mean?”
- Planner admission can still fire for `COMPLEX_GOAL` even when a local noun looks like a follow-up, if `new_goal_override` is true.

## When a routing bug fires

Name the **earliest numbered step that made the wrong decision**, then the
function and condition. Do not patch a later symptom if step 6 or 13 already
consumed the utterance.
