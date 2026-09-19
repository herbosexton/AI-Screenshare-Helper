# JARVIS Voice Architecture Audit

**Date:** 2026-08-13  
**Rule:** Trace the running pipeline. Do not guess. Phase 3 / 5 must stay intact.

This file is the **pre-repair** trace of why JARVIS heard itself. The repair is described in `docs/JARVIS_VOICE_COMPLETION_REPORT.md`.

---

## Runtime pipeline (as wired today)

```text
sounddevice InputStream (default capture device, 16 kHz mono)
        ↓ energy VAD in VoiceCommander._audio_callback
        ↓ on voiced start: on_barge_in() → SpeechOutput.stop_speaking()
        ↓ after silence: concatenate utterance
        ↓ LocalWhisperSTT.transcribe()  (faster-whisper "small", CPU int8)
        ↓ VoiceCommander.hold()
        ↓ JarvisWindow.on_voice_transcript → Qt queue
        ↓ "[Jarvis] Voice command queued"
        ↓ hud_spoken_reply OR AgentOrchestrator.handle_user_message
        ↓ "[Jarvis] Sending to agent"  (planner if FastCommandRouter misses)
        ↓ JarvisWindow._on_reply → SpeechOutput.speak()  (Windows SAPI, speakers)
        ↓ SAPI idle callback → VoiceCommander.release()
        ↓ LISTENING again immediately
```

There is **no** `VoiceSessionController`. Microphone, VAD, STT, TTS, and the HUD each push work independently.

---

## Why JARVIS hears itself (root cause)

This is acoustic feedback, not a software “second microphone” bug.

1. **TTS output device** is Windows SAPI `SpVoice` (`src/output/speech.py`). It plays through the default **render** device (PC speakers / headphones). Logs: `[Speech] Spoke (sapi): …`

2. **Capture device** is `sounddevice.InputStream` with **no device override** (`src/agent/voice.py`). That is the default **recording** device — almost always the physical microphone. It is not WASAPI loopback, but it **does** hear loudspeaker audio in the room.

3. **No acoustic echo cancellation** is enabled on that stream. `sounddevice` is opened as a plain shared-mode capture. Windows Voice Capture DSP / AEC is **not** attached. (See AEC section below.)

4. **Hold is too late and release is too early.**
   - `hold()` is only called **after** a *user* transcript is committed. It does **not** cover TTS that was not preceded by a held command (greeting, “Listening.”).
   - When SAPI’s queue empties, `_on_idle` runs **immediately** and `release()` re-enables VAD. Room echo of the last sentence is still in the air (hundreds of ms). VAD treats it as a new utterance.

5. **Barge-in currently fires on any VAD start** (`on_barge_in` → `stop_speaking`). Speaker energy from TTS therefore:
   - can cut TTS off, and
   - starts an utterance that Whisper then transcribes as `"It seems..."` / `"I want you to..."`.

6. **No comparison to current TTS text.** The system knows `SpeechOutput` just said `"It seems you might need assistance..."` but STT `"It seems..."` is treated as a new user command.

7. **Quality gate is almost empty.** `min_chars=2` and a small hallucination set. `"Oh"`, `"Good."`, `"Polo"` all pass and hit `handle_user_message` → planner (`Path: planner`).

Observed log pattern matches this trace exactly:

```text
[Speech] Spoke: It seems you might need assistance...
[Voice] Heard (local): It seems...
[Jarvis] Voice command queued: It seems...
[Jarvis] Sending to agent: It seems...
```

---

## Component inventory

| Piece | Location | Behavior |
|---|---|---|
| Mic capture | `VoiceCommander` + `sounddevice` | Default input, 16 kHz, block 512 |
| Legacy loopback | `src/capture/audio.py` | Separate Capture & Analyze path; **not** used by Jarvis voice |
| VAD | Energy RMS vs calibrated threshold (log: `threshold=0.0144`) | 3 consecutive voiced frames (`start_voiced_frames`) |
| Endpointing | `silence_seconds: 2.2`, `min_speech_seconds: 0.75`, `max_utterance_seconds: 45` | Finalize on silence after min duration |
| STT | `LocalWhisperSTT` faster-whisper **small**, CPU int8 | Returns a string only — **no confidence**, no partials |
| TTS | Windows SAPI async `Speak(..., 1)` | Queue with crude priority; `stop_speaking` purges |
| Speech queue | `deque[(priority, text)]` | USER_RESPONSE clears non-CRITICAL |
| Barge-in | Any VAD start | Stops TTS; does **not** distinguish echo vs user |
| Wake word | None | Hands-free always-on while Listening |
| Command IDs | None | Duplicates can execute twice |
| Source tags | None | TTS echo looks like USER_VOICE |
| Mic mute during TTS | `hold()` flag drops callbacks | Half-duplex only after a committed command; **not** during greeting / “Listening.” |
| AEC | Not used | See below |

---

## AEC availability

| Claim | Result |
|---|---|
| AEC available in current capture stack | **No** |
| AEC enabled | **No** |
| AEC unavailable | **Yes** (for this app) |

Windows has a Voice Capture DSP (`CLSID_CWMAudioAEC`) and a Communications capture role. This process never activates them. `sounddevice.InputStream` has no AEC flag in the version we use. We must not claim AEC is on.

**Mode in use today:** accidental full duplex (mic open while speakers play) with **no** echo canceller → **uncontrolled full duplex**.

**Target after repair:** **controlled full duplex** — mic stays open for barge-in, but STT cannot become a command unless it fails echo matching and passes the quality gate. Fallback: smart half-duplex echo window (300–800 ms) after TTS ends.

---

## Other failure modes in the same pipeline

- **`JarvisWindow._on_mic` speaks `"Listening."`** into SAPI while the mic is starting. That phrase is a prime self-echo.
- **Greeting TTS** at HUD open (`_greet`) is not registered as TTS for echo matching.
- **Planner filler** `"On it."` exists in `AgentOrchestrator.handle_user_message` (only if `on_filler` is set). HUD already shows WORKING.
- **Partial vs final:** every Whisper result is treated as FINAL. Trailing fragments (`"It seems..."`, `"I want you to"`) are not classified as incomplete.
- **Duplicates:** no recent-transcript cache.
- **Task “cross out”:** `hud_spoken_reply` only *lists* tasks. Complete/check-off is not a fast intent, so it can fall through to Qwen.

---

## What must not be “fixed” by cheating

- Permanently muting the mic for all TTS (kills barge-in).
- Rejecting every short transcript (would block `"Stop."`).
- Turning TTS off.
- Raising planner timeout to hide garbage commands.

---

## Repair target

Single `VoiceSessionController` owns mic / VAD / STT / TTS / agent admission. Only `source=USER_VOICE` + `isFinal` + quality gate PASS may enter `FastCommandRouter` / orchestrator.
