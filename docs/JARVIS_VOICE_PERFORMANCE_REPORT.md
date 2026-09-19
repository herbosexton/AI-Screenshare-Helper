# JARVIS Voice Performance Report

**Date:** 2026-08-13  
**Mode:** controlled full duplex (mic stays open for barge-in; commands are gated)  
**AEC:** unavailable (sounddevice capture has no Voice Capture DSP)

Measurements below are from this repair sprint. Where a production distribution was not collected, that is stated.

---

## Pipeline timings

| Stage | Measured | Notes |
|---|---|---|
| Speech detection (VAD start) | ~48–96 ms | 3 consecutive 16 kHz blocks of 512 samples (`start_voiced_frames: 3`) after energy exceeds the calibrated threshold |
| Endpointing | **1.15 s** silence (user); **0.40 s** during TTS | `silence_seconds` / `barge_in_silence_seconds` |
| Min speech | **0.45 s** user; **0.28 s** barge-in | Short noise bursts are dropped before STT |
| Average STT latency | **not a production distribution** | Local faster-whisper **small**, CPU int8. Live 4 s capture + transcribe in a cold pytest process was ~5–9 s wall including SAPI playback and model load. Warm short-command STT is expected in the **300–800 ms** range on this machine; target after speech end remains **<500–1000 ms** where hardware allows |
| Quality gate | **< 2 ms** | Unit runs report 0 ms at 1 ms resolution |
| Fast command / HUD task | **< 10 ms** typical | `Cross out review morning emails` → `Done.` with **0 planner calls** |
| TTS startup | SAPI async `Speak(..., 1)` | Queue → COM speak; not separately histogrammed this sprint |
| TTS cancellation | **immediate flag + `Speak("", 2)` purge** | `cancel_current()` / `clear_queue()`; barge-in Stop uses this path |

---

## Echo / command quality

| Metric | Result |
|---|---|
| Echo detection (live speakers + mic) | **PASS** — SAPI spoke `Jarvis echo probe …`; mic RMS **0.0355**; Whisper returned `Archie's Echo Pro.`; **discard_reason=SELF_SPEECH_ECHO**, echo score **0.82**, **accepted=False** |
| False positive echo (unit) | Interrupt `Stop.` / `Open Chrome.` during TTS still admitted |
| False VAD | Mitigated by min speech 0.45 s + start frames; not fully measured in a noisy room |
| Duplicate command rate (unit) | **0** — second identical final in 2.2 s dropped, same `commandId` |
| Planner calls from bad transcripts | **0** in tests for `Oh`, `Good.`, `Polo`, TTS fragments |
| Barge-in response | Cancel TTS on admitted interrupt; no stale queue resume (USER_RESPONSE also purges TASK_STATUS/FILLER) |
| 20-turn stress | **Gate-level PASS** (20 alternating TTS fragments through `admit()`; 0 echo/filler commands executed). Not 20 live spoken turns |

---

## Configuration used

```text
STT              local faster-whisper small (CPU int8)
VAD              energy RMS vs calibrated threshold (base 0.008)
silenceFinalize  1.15 s (0.40 s while TTS_SPEAKING)
minSpeechMs      450 (280 during TTS)
maxUtteranceMs   45000
echo_window_ms   550
echo_match       0.62 fuzzy + stem overlap
post-TTS soft    2.0 s — non-commands captured in this window are dropped
```

---

## Privacy

Microphone audio is **not** written to disk. The live speaker test kept the buffer in memory only.
