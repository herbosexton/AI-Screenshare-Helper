# JARVIS Voice Completion Report

**Date:** 2026-08-13  
**Sprint:** Voice architecture repair (Phase 6 features frozen)

---

## Root cause of the feedback loop

Traced in `docs/JARVIS_VOICE_ARCHITECTURE_AUDIT.md` (pre-repair):

1. Windows SAPI played TTS on the **speakers**.
2. `sounddevice.InputStream` captured the **default microphone**, which heard that playback in the room.
3. **No AEC** is attached to that stream.
4. VAD treated speaker energy as user speech. Whisper transcribed it (`It seems…` / garbled probe phrases).
5. That string was queued as a user command → agent → more TTS → loop.
6. `hold()` ran **after** commit; `release()` ran **immediately** when SAPI went idle, while room echo was still in the air.
7. There was **no TTS-text vs STT match** and **no quality gate**, so `Oh` / `Good.` / `Polo` hit Qwen.

Live reproduction this sprint (real speakers + mic):

```text
TTS:  Jarvis echo probe fa238991 alpha zulu.
STT:  Archie's Echo Pro.
rms:  0.0355
→ discard SELF_SPEECH_ECHO (echo=0.82)  accepted=False
```

Whisper often **garbles** speaker echo, so exact string matching is not enough. The gate now uses fuzzy/stem overlap **and** a post-TTS protected window that only admits interrupts and real commands.

---

## What shipped

| Piece | Behavior |
|---|---|
| `VoiceState` | IDLE → LISTENING → USER_SPEAKING → FINALIZING → PROCESSING → ACTING → TTS_SPEAKING → BARGE_IN → PAUSED → ERROR |
| `VoiceSessionController` | Sole admission path from STT to the agent |
| Self-speech | `TtsMemory` + fuzzy echo score; fragments of current/recent TTS discarded |
| Echo window | 550 ms acoustic + 2 s command-shaped soft lock after TTS end |
| Barge-in | Mic stays open during TTS. `Stop` / `Wait` / `Cancel` / `Pause` / `Actually…` and fast/task commands cancel TTS. Speaker energy alone does not |
| TTS cancel | `cancel_current()` + `clear_queue()`; FILLER priority is never spoken |
| Quality gate | Final only; fillers; partials; low confidence; duplicates; command IDs; source tags |
| Tasks | `cross out` / `check off` / `mark done` → `TASK_COMPLETE` with fuzzy HUD match. Planner calls = 0 |
| Listening UX | HUD shows LISTENING. TTS no longer says `"Listening."` or `"On it."` |
| AEC | **Unavailable** — documented, not faked |
| Duplex mode | **Controlled full duplex** (not true AEC full duplex) |

---

## Completion matrix

| Item | Result |
|---|---|
| Voice architecture audited | **PASS** |
| VoiceSessionController | **PASS** |
| Voice state machine | **PASS** |
| Self-speech suppression | **PASS** |
| TTS text echo matching | **PASS** |
| Echo suppression window | **PASS** |
| Barge-in | **PASS** (unit + cancel path) |
| TTS cancellation | **PASS** |
| Speech queue priority | **PASS** (CRITICAL / USER_RESPONSE / TASK_STATUS; FILLER dropped) |
| VAD tuning | **PASS** (config + min speech; adaptive profile still used) |
| Speech endpointing | **PASS** (configurable min/silence/max) |
| Partial/final separation | **PASS** (Whisper is utterance-final; trailing `…` / incomplete tails rejected) |
| CommandQualityGate | **PASS** |
| Low-confidence handling | **PASS** (no consequential action; HUD prompt to repeat) |
| Garbage transcript suppression | **PASS** |
| Duplicate suppression | **PASS** |
| Command IDs | **PASS** |
| Source tagging | **PASS** |
| Task fast commands | **PASS** |
| Cross-out / check-off | **PASS** |
| Fuzzy task matching | **PASS** (`morning email task` → Review morning emails) |
| Fast router preserved | **PASS** (`What page am I on?`, `Open Chrome.`) |
| Phase 3 regression | **PASS** (live named click / stale relocate / vision tests included) |
| Phase 5 regression | **PASS** |
| Phase 6 regression | **PASS** (no new Phase 6 features; existing fast paths kept) |
| Real speaker/microphone test | **PASS** (`tests/test_voice_live_speaker.py`) |
| 20-turn conversation stress | **PASS** at the gate; **not** 20 live acoustic turns |
| Automated suite | **PASS — 187 passed, 0 failed** |

---

## Required completion facts

| Fact | Value |
|---|---|
| Feedback-loop root cause | Acoustic speaker → mic → STT → agent; no AEC; no echo gate |
| Current STT model | local faster-whisper **small**, CPU int8 |
| VAD | Energy RMS vs calibrated threshold (base `0.008`, profile may raise it) |
| Endpointing | `min_speech_seconds=0.45`, `silence_seconds=1.15`, `max_utterance_seconds=45`; barge-in `0.28` / `0.40` |
| Self-echo strategy | TTS memory + fuzzy/stem match + 550 ms window + 2 s post-TTS command gate + utterance-overlapped-TTS flag |
| AEC | **available: no / enabled: no / unavailable: yes** |
| Barge-in | Admit only interrupt words or fast/task (or ≥4 distinct words with low echo score) while TTS-protected |
| Average STT latency | Not histogrammed in production; see performance report |
| Command finalization | Silence endpoint + quality gate; simple HUD/fast path avoids Qwen |
| TTS cancellation latency | Immediate cancel flag + SAPI purge |
| False echo rate | 0 in live probe (garbled STT discarded); 0 in 20-turn gate stress |
| False command rate | 0 for `Oh` / `Good.` / `Polo` / `It seems...` in tests |
| Duplicate command rate | 0 in unit test |
| 20-turn stress | Gate simulation PASS; live acoustic was a single speaker/mic probe |
| Task cross-out | `Cross out review morning emails.` → `Done.` planner **0** |
| Automated totals | **187 passed, 0 failed, 0 skipped** |
| Phase 3 | PASS (live harness included) |
| Phase 5 | PASS |
| Phase 6 | Frozen; no new features |

---

## Known limitations

- **No acoustic echo cancellation.** Windows Voice Capture DSP is not wired. Loud speakers + sensitive mics can still *physically* capture TTS; the gate must throw it away.
- Whisper **does not emit partials**. UI partials are unused; only finals enter the controller.

**False positive (fixed):** shared task-list nouns (`Review morning emails`) must not classify `Cross out review morning emails` as echo. Novel imperatives + recognized intents are evaluated **before** echo discard. JARVIS speaking the command itself is still echo.
- A **2 s** post-TTS window drops non-command transcripts. Immediate follow-ups should be real commands (`Stop`, `Open Chrome`, `Mark the first one done`) or a longer distinct sentence.
- **20 live spoken turns** were not run as an acoustic marathon; re-verify in the HUD with speakers on.
- Long 15–30 s user speech depends on `max_utterance_seconds=45` and not pausing longer than `silence_seconds`.
- Garbled echo that accidentally matches a fast command (very unlikely) could still barge in.

Do not start new Phase 6 work until the HUD conversation with real speakers feels stable.
