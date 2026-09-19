# Phase 3 + Voice — Local-first

## Policy
Everything runs on this PC by default:

- Agent brain: local Ollama
- Speech-to-text: local faster-whisper (`medium`)
- TTS: Windows SAPI
- Screen understanding: local window + UI Automation APIs
- Computer control: local Win32 / pynput

Cloud STT/LLM is **not** used unless you explicitly change config
(e.g. `voice.stt_provider: openai` or `agent.cloud_fallback: true`).

## Voice fix (local)
1. Continuously record the full spoken utterance
2. Endpoint on pause
3. Transcribe once with local Whisper `medium` (not tiny `base` scraps)

For even better local accuracy (slower/heavier), set in `config.yaml`:

```yaml
voice:
  stt_provider: local
  local_model: large-v3
```

## Phase 3 (local)
- `ScreenState` + UI Automation controls
- `computer.get_screen_state` / `verify_window` / `verify_element`
- Observe → act → verify after actions
