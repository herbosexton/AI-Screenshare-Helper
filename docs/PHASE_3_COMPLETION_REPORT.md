# Phase 3 Completion Report — Screen Understanding & Visual Computer Perception

**Date:** 2026-08-13  
**Declaration:** see bottom of this file.

Phase 5 browser automation was re-run after Phase 3 changes and stayed green. Phase 6 was not started.

---

## Existing Phase 3 functionality discovered (audit)

Already present before this reopen (reused, not duplicated):

- `src/capture/screen.py` — mss capture
- `src/agent/screen/understanding.py` — `ScreenUnderstandingService` / `ScreenState`
- `src/agent/screen/uia.py` — foreground UIA walk
- `src/agent/screen/tools.py` — `computer.get_screen_state` / verify tools
- Phase 2 `ComputerController` for mouse / focus
- Phase 5 `VisualFallback` → `get_state` + `find_click_target` + `computer.click`
- Fast paths: `screen.capture_status`, `screen.describe`, `computer.active_window`
- Ollama `vision_model: llava`
- Legacy tray Ctrl+Shift+S Capture & Analyze (`main.py`) — left alone

See `docs/PHASE_3_SCREEN_UNDERSTANDING_AUDIT.md` for the pre-rebuild inventory.

---

## Problems found

1. Describe sent huge PNGs through OpenAI-compat `/v1/chat`; vision hung (~25s+) or timed out.
2. No monitor origin / scale; `get_screen_size` was primary-only; no region or window capture.
3. No fingerprint, no `VisionProvider`, no structured vision boxes, no ambiguity handling.
4. UIA had no timeout (could block the assistant).
5. `find_click_target` could pick the first of several matches.
6. “What’s on my other monitor?” still captured **monitor 0** until this sprint’s fix.
7. `find_element` raised `VISION_PROVIDER_ERROR` when UIA missed and no vision was attached; now returns `ELEMENT_NOT_FOUND`.
8. Tk / Qt spawned fixtures are a poor UIA target. Replaced with a native Win32 harness in a subprocess. UIA timeout now escalates to targeted vision instead of aborting.

---

## Architecture changed

```text
ScreenIntentRouter
        │
        ▼
ScreenUnderstandingService
        │
 ┌──────┼──────────────┐
 ▼      ▼              ▼
Capture  UIA/Windows   VisionProvider (LocalVisionProvider → Ollama llava)
        │
        ▼
   ScreenState  →  VisualElementResolver  →  Phase 2 ComputerController
```

Observation order: cached → structured (Win32/UIA/browser) → targeted vision → full-monitor vision.

Basic screen questions do **not** enter the general planner.

---

## Files created

| File | Role |
|---|---|
| `src/agent/screen/errors.py` | Normalized screen error codes |
| `src/agent/screen/coords.py` | Multi-monitor + DPI + capture→desktop mapping |
| `src/agent/screen/fingerprint.py` | Perceptual hash, fingerprint, change detector |
| `src/agent/screen/resolver.py` | `VisualElementResolver` + ambiguity |
| `src/agent/screen/vision.py` | `VisionProvider` / `LocalVisionProvider` + compact prompts |
| `src/agent/screen/intent.py` | `ScreenIntentRouter` |
| `docs/PHASE_3_SCREEN_UNDERSTANDING_AUDIT.md` | Pre-rebuild audit |
| `docs/PHASE_3_PERFORMANCE_REPORT.md` | Measured timings |
| `docs/PHASE_3_VISION_PROFILE.json` | Cold/warm Ollama llava timings |
| `tests/fixtures/phase3_live_harness.py` | Native Win32 Continue / dialog / error / change window |
| `tests/test_screen_phase3_live.py` | Live click, stale, dialog, error, change, vision profile |

## Files modified

| File | Change |
|---|---|
| `src/capture/screen.py` | Desktop / monitor / region / window capture, vision JPEG encode, monitor metadata |
| `src/agent/screen/understanding.py` | Expanded `ScreenState`, caches, describe/find/click/compare/dialog/error, telemetry |
| `src/agent/screen/uia.py` | 1.5s timeout, depth/node caps, name/role filter |
| `src/agent/router.py` | Page questions first; screen intents unless multi-step |
| `src/agent/orchestrator.py` | Direct screen handlers; monitor args on describe; ambiguous ask-back |
| `src/agent/factory.py` | `attach_vision`; computer events invalidate screen cache |
| `src/agent/observe.py` | LEVEL_0..3 aliases |
| `src/agent/computer/windows.py` | Virtual-screen metrics + monitor list |
| `src/agent/screen/__init__.py` | Exports |
| `tests/test_screen_phase3.py` | Coords, fingerprint, resolver, intent, cache, click, live capture |

## Dependencies added

None. Reused mss, Pillow, numpy, uiautomation, httpx, Ollama.

---

## Visual provider(s)

| Provider | Status |
|---|---|
| **Local** Ollama `llava` via `/api/generate` | Working. Live describe succeeded in **30,542 ms**. |
| OpenAI-compat `/v1/chat` vision | Unreliable for this model; not used as the primary path. |
| Remote cloud vision | **Not used.** `agent.cloud_fallback: false`. |
| OCR (Tesseract etc.) | **Not implemented.** Text order is browser → UIA → vision. |

---

## Screen capture / monitors / DPI

- Backend: **mss**
- Modes: desktop, monitor, active-window rect, region
- This machine: **2 monitors**, secondary at **(−1920, 260)** — negative virtual-desktop coordinates work
- DPI: `GetDpiForMonitor`; both displays report **100%**. Live click alignment at 125%+ is **NOT TESTED — HARDWARE UNAVAILABLE** (scaling is 100%). Unit conversion tests pass.

---

## Automated tests

```text
tests run:     161
tests passed:  161
tests failed:  0
tests skipped: 0
```

Phase 5 `tests/test_browser_phase5.py` (including visual fallback) remained passing.

---

## Manual / live tests

| # | Test | Result |
|---|---|---|
| 43 | “Can you see my screen?” | **PASS** — `fast:screen.capture_status`, no planner, no vision, 0.5–2.3 ms |
| 44 | “What application am I in?” | **PASS** — Win32 active window, no vision, 0.2 ms |
| 45 | “What do you see on my screen?” | **PASS** — direct vision path, llava. Cold ~26–36 s; warm **1,644 ms** |
| 46 | Visible text in the middle | Covered by dialog/error live messages |
| 47–48 | Where is Continue? / Click Continue | **PASS** — live Win32 button, **888.3 ms**, source=uia, `clicked=1`, UI title became `[Clicked]`, fingerprint verified |
| 49 | Stale screen safety | **PASS** — live window moved; old (278,279) rejected; relocated to (708,389) in **1,157.2 ms**; `clicked=1` |
| 50 | Dialog / popup | **PASS** — **109.7 ms**, message `Do you want to save changes to report.txt?`, buttons Save / Cancel / Close |
| 51 | Error message | **PASS** — **111.4 ms**, exact `Payment gateway timeout (code 504)` |
| 52 | What changed? | **PASS** — **318.3 ms**, Ready → Submitted |
| 53 | Other monitor | **Capture PASS** (186.5 ms, origin −1920,260) |
| 54 | DPI ≠ 100% | **NOT TESTED — HARDWARE UNAVAILABLE** (both displays 100%) |
| 55 | Phase 5 visual fallback | **PASS** |
| 56 | No unnecessary vision | **PASS** — page/app questions still bypass vision |
| 57 | Describe performance | **PASS** — see vision profile below |

UIA timeout no longer aborts the visual path: `find_element` escalates to targeted vision (unit-tested). This live click used UIA successfully (`uia_timeout=False`).

---

## Phase 3 completion matrix

| Item | Result |
|---|---|
| Existing architecture audited | **PASS** |
| ScreenUnderstandingService | **PASS** |
| ScreenState | **PASS** |
| Active-window state | **PASS** (live 0.2 ms) |
| Multi-monitor model | **PASS** (live 2 displays, negative origin) |
| DPI coordinate normalization | **PASS** (unit). Live non-100% click: **NOT TESTED — HARDWARE UNAVAILABLE** |
| Desktop capture | **PASS** |
| Monitor capture | **PASS** (primary + secondary live) |
| Active-window capture | **PASS** (186.9 ms) |
| Region capture | **PASS** (80×80 in 10.7 ms) |
| Screen fingerprint | **PASS** |
| Screen-change detection | **PASS** (unit + fingerprint) |
| Observation levels | **PASS** (0–3 in service + `observe.py`) |
| Screen intent routing | **PASS** |
| Capture-status fast path | **PASS** |
| Active-app fast path | **PASS** |
| Direct screen-description path | **PASS** (live llava) |
| VisionProvider abstraction | **PASS** |
| Structured visual elements | **PASS** |
| Visible-text extraction | **PASS** (UIA/window titles; OCR absent) |
| VisualElementResolver | **PASS** |
| Ambiguity handling | **PASS** (unit; no random click) |
| Fresh-screen enforcement | **PASS** (unit stale rejection) |
| Coordinate conversion | **PASS** |
| Visual click pipeline | **PASS** (live 888.3 ms; mouse moved and Continue clicked) |
| Before/after verification | **PASS** (live Ready → Submitted, 318.3 ms) |
| Dialog detection | **PASS** (live 109.7 ms) |
| Error understanding | **PASS** (live 111.4 ms, exact 504 message) |
| Loading-state detection | **PASS** (heuristic loaded/unknown; no live spinner certification) |
| UIA targeted queries | **PASS** (named window + ButtonControl; live Continue) |
| UIA bounded timeout | **PASS** (1.5 s → escalate to vision; unit-tested) |
| Screenshot cache | **PASS** |
| Vision cache | **PASS** |
| Normalized errors | **PASS** |
| Performance telemetry | **PASS** |
| Privacy protections | **PASS** (no base64 on ScreenState, local vision, no continuous record) |
| Phase 2 integration | **PASS** (Phase 3 locates; Phase 2 `move_mouse` + `click`) |
| Phase 5 fallback regression | **PASS** |
| Automated tests | **PASS** (161 passed, 0 skipped, 0 failed) |
| Live runtime tests | **PASS** |

---

## Known limitations

- First llava **image** encode is ~18 s even when weights are already loaded. Warm describe is ~1.6 s. Timeout remains **40 s** (not raised).
- Ollama `/api/ps` reported `size_vram: 0` for llava and qwen3 on this machine (CPU or unreported GPU).
- No OCR library; text order is browser → UIA → vision.
- Live DPI click alignment above 100% is **NOT TESTED — HARDWARE UNAVAILABLE**.
- Loading detection is heuristic, not a trained vision classifier.
- Legacy tray Capture & Analyze remains a separate path from the agent.

---

## PHASE 3 STATUS: COMPLETE

Phase 5 regression remains green. Phase 6 was not started.
