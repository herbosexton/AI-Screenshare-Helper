# Phase 5 — Browser Automation Audit

**Repository:** AI-Screenshare-Helper / Jarvis  
**Workspace:** `c:\Users\herbi\AI Assistant`  
**Date:** 2026-08-12

---

## 1. Current browser capabilities

A first Playwright slice is already wired:

| Capability | Status |
|------------|--------|
| Isolated headed/headless Chromium | Yes (`src/agent/browser/session.py`) |
| Persistent profile `data/browser_profile` | Yes |
| URL scheme allow/deny | Yes (`policy.py`) |
| `browser.goto` / snapshot / click / type / press / wait | Yes |
| New/close tab, screenshot, status, close | Yes |
| Emergency-stop closes session | Yes |
| Chrome/Edge installed-channel launch | No (bundled Chromium only) |
| Forms (fill/select/check), uploads, downloads | No |
| Semantic findElement / findTab | No |
| Dialogs, CAPTCHA/auth pause | No |
| Visual fallback (Phase 2/3) | No |
| Attach to user’s daily Chrome | No (intentionally) |

There is no Selenium, Puppeteer, CDP attach, browser extension, WebView, or Electron control.

---

## 2. Current dependencies

- `playwright>=1.49.0` in `requirements.txt`
- Chromium via `playwright install chromium`
- Existing: PyQt6 HUD, ToolRegistry, PermissionEngine L0–L3, FilePathPolicy, ComputerController, ScreenUnderstandingService

---

## 3. Existing browser-related code

- [`src/agent/browser/policy.py`](../src/agent/browser/policy.py)
- [`src/agent/browser/session.py`](../src/agent/browser/session.py)
- [`src/agent/browser/tools.py`](../src/agent/browser/tools.py)
- [`src/agent/factory.py`](../src/agent/factory.py) (registers tools)
- [`tests/test_browser_phase5.py`](../tests/test_browser_phase5.py)
- File tools block Chrome User Data: `config.yaml` `files.blocked_directories`

---

## 4. Browser limitations (pre-expansion)

- Tools talk to Playwright locators inside `BrowserSession` (no driver interface)
- No back/forward/reload, form discovery, or structured errors
- No download/upload path gating
- Snapshot refs die after navigation
- No Chrome/Edge preference, no SPA fingerprint, no visual recovery

---

## 5. Recommended architecture

```
AgentOrchestrator → browser.* tools → BrowserAgent
  → BrowserController → PlaywrightBrowserDriver → Chrome | Edge | Chromium
  → FilePathPolicy (uploads)
  → ScreenUnderstandingService + ComputerController (visual fallback)
```

Agent-managed headed browser with `data/browser_profile`. Do **not** attach to the user’s live Chrome profile.

---

## 6. Components that can be reused

- ToolRegistry, PermissionEngine, wrap_untrusted, EmergencyStop
- FilePathPolicy / FileSystemService for upload sources and download destinations
- ComputerController.focus_window / click for visual fallback
- ScreenUnderstandingService for visual targets
- UrlPolicy (keep and extend)
- HUD `set_hearing` / agent state for “Opening Chrome…”

---

## 7. Components that require modification

- `session.py` → facade over BrowserAgent (keep old method names)
- `tools.py` → expanded `browser.*` catalog
- `factory.py` → pass file policy, optional computer/screen into BrowserAgent
- `orchestrator.py` system prompt → new tool names
- `config.yaml` → optional `channel: chrome|msedge|chromium`

---

## 8. New components required

`errors.py`, `models.py`, `driver.py`, `agent.py` (session/tabs/page/actions), `resolver.py`, `fallback.py`, `files_io.py`, local HTML fixtures, expanded tests.

---

## 9. Security considerations

- Webpage content is UNTRUSTED_DATA; cannot raise permissions
- Deny `file://`, `javascript:`, `data:`
- Never log passwords, cookies, tokens
- Uploads only via authorized Phase 4 paths
- Downloads land in an allowed folder; never auto-execute
- CAPTCHA/login → pause (`CAPTCHA_REQUIRED` / `AUTHENTICATION_REQUIRED`); no bypass
- Do not read Chrome password DBs or attach to daily profile

---

## 10. Phase 5 implementation sequence

1. Audit (this document)
2. Driver + Chrome/Edge/Chromium launch
3. Navigation + tabs + findTab
4. Page state, resolver, click/fill/verify
5. Upload/download/dialogs/CAPTCHA
6. Visual fallback
7. Tools + HUD + prompt
8. Local-fixture tests

**Out of Phase 5:** Gmail OAuth, job-apply skill, Phase 7 approval UI, attaching to daily Chrome, solving CAPTCHAs.
