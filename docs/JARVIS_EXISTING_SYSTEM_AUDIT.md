# Jarvis Existing System Audit

**Repository:** AI-Screenshare-Helper  
**Workspace:** `c:\Users\herbi\AI Assistant`  
**Audit date:** 2026-08-12  
**Commit audited:** `1c5acf4` (initial)

---

## 1. Existing architecture

This is a **Windows-only PyQt6 system-tray desktop app** (Python 3.11+). There is no web frontend, no HTTP API server, no database, and no Electron shell.

```
User (hotkey / mic)
  → AppController (main.py)
      → ScreenCapture (mss)
      → AudioCapture + Transcriber (faster-whisper, local)
      → ContextBuilder
      → LLMClient (Anthropic primary / OpenAI fallback — cloud vision)
      → HumanizationEngine (optional)
      → Overlay / Clipboard / MonitorWindow / SpeechOutput
```

| Layer | Technology |
|--------|------------|
| Language | Python |
| Desktop UI | PyQt6 (tray, overlay, settings) |
| Hotkeys | pynput |
| Screen | mss + Pillow |
| Audio | sounddevice |
| STT | faster-whisper (local CPU) |
| TTS | pyttsx3 |
| LLM | anthropic + openai SDKs (cloud) |
| Config | config.yaml + env API keys |

**Entry point:** `main.py` → `AppController` + `SystemTray`.

---

## 2. Existing capabilities

| Capability | Status | Location |
|------------|--------|----------|
| Multi-monitor screenshots | Yes | `src/capture/screen.py` |
| Mic + system-audio capture | Yes | `src/capture/audio.py` |
| Local speech-to-text | Yes | `src/processing/transcription.py` |
| Cloud vision Q&A | Yes | `src/processing/llm.py` |
| TTS | Yes | `src/output/speech.py` |
| Overlay / clipboard / 2nd monitor | Yes | `src/output/*` |
| System tray + hotkeys | Yes | `src/ui/tray.py` |
| Settings dialog | Yes (in-memory) | `src/ui/settings.py` |
| Tool / function calling | No | — |
| Agent plan→act→verify loop | No | — |
| Mouse / keyboard / window control | No | — |
| Browser automation | No | — |
| File search / index | No | — |
| Email / Google / Outlook | No | — |
| Permissions / approvals / audit | No | — |
| Local LLM agent brain | No | — |
| Automated tests | No | — |

---

## 3. Components that can be reused

1. **`ScreenCapture`** → `computer.get_screenshot` and future observe loop  
2. **`ClipboardOutput` / pyperclip** → clipboard tools  
3. **`AudioCapture` + `Transcriber`** → voice commands into orchestrator (already local)  
4. **`SpeechOutput`** → spoken status / confirmations  
5. **`LLMClient`** → optional cloud fallback for legacy Capture & Analyze only  
6. **`SystemTray` / hotkeys** → Ask Jarvis, emergency stop, status  
7. **`ContextBuilder`** → seed conversation buffer (redesign for tasks later)  
8. **`config.yaml`** → extend with `agent:` block  

---

## 4. Missing capabilities (for Jarvis)

- AgentOrchestrator with multi-step planning  
- Persistent Task / Step engine  
- Typed tool registry + validation  
- Permission levels 0–3 + ApprovalRequest  
- Windows computer controller (UI Automation, mouse, keyboard, apps)  
- ScreenUnderstandingService / ScreenState (beyond raw pixels)  
- Browser agent (Playwright)  
- File system agent + local document index  
- Email / Google / Microsoft connectors  
- Long-term memory + secure profile vault  
- Emergency stop for automation  
- Prompt-injection defenses for untrusted content  
- Audit log  
- Conversation-first Jarvis UX / floating overlay  
- **Local LLM** for agent brain (Ollama) — STT is already local; planning is not  

---

## 5. Components requiring refactoring

| Component | Why |
|-----------|-----|
| `AppController` | Monolithic; split UI wiring from agent orchestration |
| `LLMClient` | Cloud-only, no tools; keep for legacy Q&A; agent uses `LocalOllamaProvider` |
| Settings persistence | Dialog mutates memory only; not written to disk |
| Hotkeys vs README | Code uses Ctrl+Shift+Z for audio; README says Ctrl+Shift+A |
| Humanization | Stealth Q&A path; agent replies should stay plain |
| Threading | Daemon threads + Qt signals need a safer command queue for control actions |

---

## 6. Security concerns

1. **Cloud leakage (legacy path):** Screenshots and transcripts go to Anthropic/OpenAI when Capture & Analyze runs.  
2. **No permission gate:** Any future automation would be unrestricted without a new engine.  
3. **Prompt injection:** Screen/audio text is inserted into prompts with no untrusted-data boundary.  
4. **Secrets:** API keys via env (good); no vault yet for OAuth/profile.  
5. **Stealth UX:** Overlay disguised as notes — separate from Jarvis transparency goals.  
6. **No audit trail** of actions.  

**Local-first decision:** Agent planning and tool selection run on **Ollama (localhost)** by default. `agent.cloud_fallback` defaults to `false`. Document index must stay local (Phase 4+).

---

## 7. Proposed architecture

```
USER VOICE/TEXT
  → AppController (existing tray app)
  → AgentOrchestrator
      → LocalOllamaProvider (localhost:11434)
      → TaskEngine (SQLite)
      → ToolRegistry → PermissionEngine → Tool
      → AuditLog / EmergencyStop
      → Observe result → continue / approve / complete
```

Preserve legacy Capture & Analyze beside the new agent path.

**Phase order:** (1) agent core + local AI + tools/permissions, (2) Windows computer control, (3) screen understanding, (4) files/index, (5) browser, (6) full orchestrator polish, (7) approvals UX, (8–9) Google/Microsoft, (10) job skill, (11) memory, (12) overlay UX, (13) hardening/tests.

---

## 8. Implementation order (Phase 1 scope)

1. This audit document  
2. Task / Step / ApprovalRequest models + SQLite store  
3. ToolRegistry, PermissionEngine, audit log, emergency stop  
4. Real tools only: screenshot, clipboard, status, list_tools, speak  
5. LocalOllamaProvider + AgentOrchestrator  
6. Wire tray “Ask Jarvis…”, config, Ctrl+Shift+Esc  
7. Pytest suite for validation, permissions, cancel, injection boundaries  

**Out of Phase 1:** Windows automation, Playwright, files, email, job skill, floating overlay.
