# AI Screenshare Helper

A stealthy system tray application that captures your screen and audio, analyzes content using AI, and delivers humanized answers.

## Setup

### Prerequisites

- Python 3.11 or higher
- Windows 10/11
- An Anthropic API key (primary) and/or OpenAI API key (fallback)

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

1. Set your API keys as environment variables:

```bash
set ANTHROPIC_API_KEY=your-key-here
set OPENAI_API_KEY=your-key-here
```

2. Edit `config.yaml` to customize behavior (output mode, capture mode, humanization settings, etc.)

### Running

```bash
python main.py
```

The app will start minimized in your system tray.

### Local browser (Phase 5)

Jarvis uses an isolated Playwright Chromium window (not your daily Chrome profile).

```bash
pip install playwright
playwright install chromium
```

Then type in the HUD: `Open https://example.com and tell me the heading`

## Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+S` | Capture screen and analyze |
| `Ctrl+Shift+A` | Toggle audio listening |
| `Ctrl+Shift+H` | Hide/show overlay |
| `Ctrl+Shift+Q` | Quick question mode |

## Output Modes

- **overlay** - Transparent overlay on your screen (risky if screen sharing)
- **clipboard** - Silently copy answers to clipboard
- **second_monitor** - Display on a non-shared monitor (safest)
- **all** - All output methods simultaneously

## Notes

- Audio transcription runs locally via faster-whisper (no data sent to cloud for transcription)
- Screen content is sent to the LLM API for analysis
- The humanization engine adds natural imperfections to avoid AI detection
