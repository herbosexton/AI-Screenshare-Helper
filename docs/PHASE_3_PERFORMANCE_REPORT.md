# Phase 3 Performance Report

**Date:** 2026-08-13  
**Machine:** Windows 10, two 1920×1080 monitors (primary origin 0,0; secondary origin −1920,260). Both `scaleFactor` 1.0 (100%).  
**Vision:** local Ollama `llava` via native `/api/generate` (JPEG, max side 768). `cloud_fallback: false`.

Timings are wall-clock `perf_counter` unless noted. STT/TTS are excluded.

---

## Fast paths (no vision, no planner)

| Operation | Result | Notes |
|---|---:|---|
| Screen capture status (`mss.status`) | **38.6 ms** | Monitor list + DPI query, no pixels |
| Orchestrator “Can you see my screen?” | **0.5–2.3 ms** | `fast:screen.capture_status`, vision calls = 0 |
| Active window (`GetForegroundWindow`) | **0.2 ms** | Title from Win32 |
| Structured `get_state` (no UIA) | **5.0 ms** | Fingerprint + monitor model |
| “What page am I on?” | **0.6–3 ms** | Phase 5 `fast:browser.current_page`, vision = 0 |
| ScreenIntentRouter only | **&lt; 3 ms** | Measured as orchestrator Router span |

Target for status / active-app: under ~100 ms excluding STT/TTS. **Met.**

---

## Capture

| Operation | Result | Notes |
|---|---:|---|
| Primary monitor capture | **181.3 ms** | 1920×1080 PNG via mss |
| Second monitor capture | **186.5 ms** | origin (−1920, 260), 1920×1080 |
| Region 80×80 | **10.7 ms** | Targeted capture |
| Active-window capture | **186.9 ms** | Foreground window rect |

Full virtual-desktop grab is available (`capture_desktop`) but is not the default. Describe uses active-window region when possible.

---

## UI Automation

| Operation | Result | Notes |
|---|---:|---|
| Targeted foreground UIA `get_state(include_uia=True)` | **35.3–94.9 ms** | 6 interactive controls on Cursor |
| Named-window ButtonControl “Continue” | **included in 888 ms click** | Native Win32 harness in a subprocess |
| UIA hard timeout | **1.5 s** | Then escalate to targeted vision (does not abort) |

---

## Live acceptance (Win32 harness)

| Test | Wall time | Notes |
|---|---:|---|
| Named Continue click | **888.3 ms** | source=uia, mouse moved, `clicked=1`, fingerprint verified |
| Stale-target relocate | **1,157.2 ms** | (278,279) → (708,389), `relocated=True`, `clicked=1` |
| Dialog understanding | **109.7 ms** | message + Save/Cancel |
| Error understanding | **111.4 ms** | `Payment gateway timeout (code 504)` |
| Before/after change | **318.3 ms** | Ready → Submitted |

Simple screen-state questions still bypass vision.

---

## Vision (local llava) — cold vs warm

Timeout remains **40 s**. It was not increased to hide latency.

Isolated cold (model not resident): **25,888 ms** wall. `load_duration` 5,910 ms, first `prompt_eval` 18,339 ms (600 prompt tokens), `eval` 4 tokens in 238 ms, **16.8 tok/s**.

Warm (same 768×432 JPEG, llava kept alive 60m): **1,644 ms** wall. `load_duration` 23 ms, `prompt_eval` 301 ms, 15 tokens in 1,134 ms, **13.2 tok/s**.

Full-suite repeat with llava already in `/api/ps`: cold wall **36,115 ms** was almost entirely **first image prompt_eval (17,971 ms)**, not weight reload (`load_duration` 31 ms). Warm again **1,644 ms**.

| Field | Cold (unloaded) | Warm |
|---|---:|---:|
| Wall | 25,888 ms | 1,644 ms |
| Weight load | 5,910 ms | 23 ms |
| Image/prompt eval | 18,339 ms | 301 ms |
| Token generation | 238 ms (4 tok) | 1,134 ms (15 tok) |
| Tokens/sec | 16.8 | 13.2 |
| Image preprocess | 14–32 ms | (same JPEG path) |

Backend: Ollama native `/api/generate`, model `llava:latest` Q4_0 7B + CLIP. `/api/ps` reported **`size_vram: 0`** (CPU or GPU not reported). JPEG max side 768. `keep_alive: 60m`. Factory starts a **background warmup** with a 768×416 dummy JPEG so the first user describe can skip CLIP cold-start. Privacy unchanged (local only).

Raw dump: `docs/PHASE_3_VISION_PROFILE.json`.

---

## Other Phase 3 operations

| Operation | Result | Notes |
|---|---:|---|
| Screen fingerprint | included in 5.0 ms structured state | aHash + brightness bits + window/text |
| Screen-change detection | live 318 ms | title/text diff Ready → Submitted |
| Coordinate conversion | unit-tested | Capture → virtual desktop, including negative origins |
| Visual click | **888.3 ms live** | Focus → UIA → Phase 2 move+click → verify |

---

## Phase 5 (must not regress)

Sample live browser timings from the same suite run:

| Path | Total |
|---|---:|
| `fast:browser.open` | 327 ms |
| `fast:browser.open_and_goto` | 25–40 ms |
| `fast:browser.current_page` | 1–3 ms |
| `fast:browser.fill` | 42 ms |

Phase 5 regression tests remained green after Phase 3 changes.

---

## Bottlenecks

1. **First llava image encode (~18 s)** dominates a cold describe even after weights are loaded. Warm describe is ~1.6 s. Timeout stays 40 s.
2. **Full-monitor PNG encode (~180 ms)** is acceptable next to vision.
3. **UIA** is fast on native Win32 controls; timeout now falls through to vision instead of failing the request.

---

## Privacy / cost notes

- Screenshots are not stored on `ScreenState` (sizes/metadata only).
- Vision payloads are not written to debug logs.
- Agent vision is local Ollama only (`cloud_fallback: false`).
- Capture is on-demand, not a continuous recorder.
