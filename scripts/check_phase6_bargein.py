"""Test 18: barge-in while Jarvis is narrating a Phase 6 task.

Two things have to hold. Speech has to report itself as speaking for as long as audio
is playing, because that flag is what keeps the microphone shut and prevents Jarvis
hearing its own narration. And the interrupt has to return immediately, because the
caller is the input path.

Run with a real audio device; this speaks out loud.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.output.speech import SpeechOutput

NARRATION = (
    "I am working through the task now. I found three resumes in your documents "
    "folder and I am opening the most recent one before I read the job page."
)


def main() -> None:
    speech = SpeechOutput({})
    facts: dict[str, object] = {}

    speech.speak(NARRATION)
    time.sleep(1.2)
    facts["speaking_while_audio_plays"] = speech.is_speaking

    t0 = time.perf_counter()
    speech.stop_speaking()
    facts["interrupt_returns_in_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    while speech.is_speaking and time.perf_counter() - t0 < 3.0:
        time.sleep(0.01)
    facts["audio_stopped_after_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    facts["speaking_after_interrupt"] = speech.is_speaking

    # Nothing queued behind the interrupted line may leak out afterwards.
    time.sleep(0.5)
    facts["queue_drained"] = not speech.is_speaking

    for key, value in facts.items():
        print(f"{key:28} {value}")

    ok = (
        facts["speaking_while_audio_plays"] is True
        and facts["speaking_after_interrupt"] is False
        and facts["queue_drained"] is True
        and float(facts["interrupt_returns_in_ms"]) < 50
    )
    print(f"\nTEST 18 barge-in: {'PASS' if ok else 'FAIL'}")
    speech.shutdown()


if __name__ == "__main__":
    main()
