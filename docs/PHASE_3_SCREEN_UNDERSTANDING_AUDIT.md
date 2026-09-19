# Phase 3 Screen Understanding — Audit (pre-rebuild)

**Date:** 2026-08-13  
**Rule:** Phase 5 browser automation must remain intact. Phase 6 is not started.

---

## 1. Existing components

| Component | Path | Role |
|---|---|---|
| `ScreenCapture` | `src/capture/screen.py` | mss multi-monitor grab, PNG base64, crude pixel-diff |
| `ScreenUnderstandingService` | `src/agent/screen/understanding.py` | Windows list + optional UIA + screenshot *metadata* |
| `ScreenState` / `VisibleElement` | same | Normalized observe model |
| `get_foreground_uia_elements` | `src/agent/screen/uia.py` | Foreground-window UIA walk (depth 6, max 80) |
| Screen tools | `src/agent/screen/tools.py` | `computer.get_screen_state`, `verify_window`, `verify_element` |
| `computer.get_screenshot` | `src/agent/tools/__init__.py` | Full-desktop screenshots for the planner |
| `ComputerController` | `src/agent/computer/controller.py` | Mouse/keyboard/focus (Phase 2) — must not be duplicated |
| `VisualFallback` | `src/agent/browser/fallback.py` | Phase 5 DOM→a11y→Phase 3→click |
| Fast paths | `src/agent/router.py` + orchestrator | `screen.capture_status`, `screen.describe`, `computer.active_window`, `screen.click` |
| Legacy Capture & Analyze | `main.py` `capture_and_analyze`, tray Ctrl+Shift+S | Older Anthropic/OpenAI vision path, separate from Jarvis agent |
| Hotkeys | `src/ui/tray.py` | Ctrl+Shift+S capture, Ctrl+Shift+A audio, overlay, Jarvis, emergency |

---

## 2. Existing visual models

- Agent path: Ollama `vision_model` (config default **`llava`**) via `LocalOllamaProvider.chat(..., images_base64=)`.
- Legacy path: `src/processing/llm.py` Anthropic Claude vision + OpenAI fallback (tray Capture & Analyze).
- No `VisionProvider` interface. Orchestrator hard-calls `self.provider.chat` with a screenshot.
- No OCR library (no Tesseract). OCR is not implemented.

---

## 3. Existing capture methods

`ScreenCapture` supports:

- `get_monitors()` — mss monitors[1:] (no origin, no scale)
- `status()` — cheap, no pixels
- `capture_all()` — all (or config-selected) monitors, always encodes PNG base64
- `capture_monitor(index)`
- `has_significant_change()` — 320×180 mean absolute difference vs previous `capture_all`

Missing: region capture, active-window capture, desktop virtual-screen capture, ephemeral in-memory refs without always base64-ing, monitor origin (including negative coords).

---

## 4. Existing APIs

```text
ScreenUnderstandingService.capture_status()
ScreenUnderstandingService.get_state(include_screenshot, include_uia)
ScreenUnderstandingService.find_click_target(name)   # UIA only
ScreenUnderstandingService.verify_window_appeared()
ScreenUnderstandingService.verify_element_present()
ComputerController.get_active_window / click / focus_window / get_screen_size
```

`get_state(include_screenshot=True)` stores **sizes only**, not pixels. Describe path captures separately via `capture_all()[:1]` (first monitor, full).

---

## 5. Existing UIA functionality

- Foreground control walk via `uiautomation` (comtypes fallback is empty).
- Depth 6, max 80 nodes.
- **No timeout** — a hung UIA tree can block the assistant.
- No targeted query by role/name/region (always full foreground walk).
- Bounds are raw `BoundingRectangle` (desktop pixels); no DPI conversion.

---

## 6. Existing browser visual fallback

Phase 5 `VisualFallback.click_named`:

1. Focus Chrome/Edge/Chromium/Playwright window  
2. Fresh `get_state(include_screenshot=True, include_uia=True)`  
3. `find_elements` on that state, else `find_click_target`  
4. `ComputerController.click(x, y)`

This must keep working. Do not invent a second vision stack.

---

## 7. Existing element detection

- UIA name substring (`ScreenState.find_elements`)
- First match wins — **no ambiguity handling**
- Color / relative geometry / “blue button” / “on the right” not supported
- Vision does not return structured bounding boxes

---

## 8. Known limitations

- ScreenState has no monitors, fingerprint, source, dialogs list, loading state, or text blocks with boxes.
- `get_screen_size` uses primary SM_CXSCREEN/SM_CYSCREEN only.
- Describe always uses vision even when UIA/window title would suffice for some questions.
- `find_click_target("that")` clicks the first named control — unsafe.
- Screenshot base64 is large; captured on every describe; not cached by fingerprint.
- No stale-coordinate check beyond “take a new screenshot now.”
- No visual before/after verifier besides `verify_window_appeared`.
- Observation levels in `observe.py` are A/B/C labels for browser, not a real screen ladder.
- Secrets on screen can be sent to the local vision model; not logged as files, but payloads are not redacted.

---

## 9. Performance problems

- UIA unbounded wall time.
- `capture_all` PNG-encodes every monitor.
- Describe: window enum + full monitor PNG + llava (can be many seconds; provider timeout 25s).
- No vision cache for back-to-back “what do you see?” / “what buttons?”.
- Fast paths for status/app exist and are good (~1–8 ms in Phase 5 work).

---

## 10. Missing capabilities (this sprint)

Multi-monitor model, DPI coords, region/window capture, fingerprint + change detector, observation levels 0–3, ScreenIntentRouter extras (find, dialog, error, change, other monitor), VisionProvider, structured visual elements, resolver + ambiguity, visual click pipeline with window-bounds check, before/after compare, dialog/error/loading heuristics, UIA timeout, screen/vision cache, normalized screen errors, SCREEN PERFORMANCE telemetry, privacy (no screenshot in logs).

---

## 11. Proposed final architecture

Reuse `ScreenUnderstandingService` as the hub (do not rename for callers).

```text
                 SCREEN UNDERSTANDING SERVICE
                           │
          ┌────────────────┼─────────────────┐
          │                │                 │
          ▼                ▼                 ▼
    ScreenCapture     Structured State    VisionProvider
    (mss, targeted)   UIA / Windows       (Ollama llava)
          │            Browser (Phase 5)        │
          └────────────────┬──────────────────┘
                           │
                      ScreenState
```

Cheapest source first: cache → windows/browser → UIA → targeted vision → full-monitor vision.

Phase 2 still clicks. Phase 5 still owns DOM.

---

## 12. Components to reuse

`ScreenCapture`, `ScreenUnderstandingService`, `ScreenState`, UIA walk, `ComputerController`, `VisualFallback`, FastCommandRouter screen status/describe/app, `LocalOllamaProvider` image chat, `PerfTrace`.

---

## 13. Components to refactor

Expand `ScreenState`. Add timeout to UIA. Enrich mss monitor metadata. Route more screen questions in a `ScreenIntentRouter` called from `FastCommandRouter`. Move describe/find through the service + VisionProvider. Cache + fingerprint.

---

## 14. Components to remove only if obsolete

Do **not** remove tray Capture & Analyze or `processing/llm.py` in this sprint (legacy helper, out of Jarvis voice agent path). Do not remove `computer.get_screenshot` (planner still uses it).

No extra pip dependencies required (numpy + Pillow + mss + uiautomation already present).
