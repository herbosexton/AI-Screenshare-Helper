"""Tests 1, 2, 3, 6 and 20: who gets the planner and who does not.

The exact sentences from the specification, run through the real classifier and the
real admission controller.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.agent.planner_admission import admit_planner
from src.agent.utterance import classify_utterance

MUST_NOT_PLAN = [
    "What page am I on?",
    "What's the current URL?",
    "Open Chrome.",
    "Go back.",
    "Scroll down.",
    "Refresh.",
    "What tasks do I have?",
    "Cross out review morning emails.",
    "Undo the morning email task.",
    "Add a task.",
    "Can you see my screen?",
    "What application am I in?",
    "Stop.",
    "Pause.",
    "Continue.",
    "That's fine.",
    "I see.",
    "Okay then.",
    "Maybe later.",
    "Why is the sky blue?",
    "Explain transformer attention.",
]

MUST_PLAN = [
    "Find my newest resume and compare it with the job page I currently have open.",
    "Let's find my latest resume and compare it to the job page I have open.",
    "Open Chrome, research transformer attention, save the best explanation, and open the saved file.",
    "Open Chrome, go to Google, search for OpenAI, open a useful result, then find my latest PDF and open it.",
    "Find my latest PDF related to Attnex, open it, and tell me what it says.",
    "Look at the browser page I'm on, find the best resume for it, and open both.",
]


def main() -> None:
    failures = 0

    print("MUST NOT reach the planner")
    for text in MUST_NOT_PLAN:
        decision = admit_planner(classify_utterance(text))
        bad = decision.admitted
        failures += bad
        print(f"  {'FAIL' if bad else 'ok  '}  {decision.reason:26} {text}")

    print("\nMUST reach the planner")
    for text in MUST_PLAN:
        classified = classify_utterance(text)
        decision = admit_planner(classified)
        bad = not decision.admitted
        failures += bad
        print(
            f"  {'FAIL' if bad else 'ok  '}  {classified.utterance_type:14} "
            f"{decision.reason:26} {text}"
        )

    total = len(MUST_NOT_PLAN) + len(MUST_PLAN)
    print(f"\n{total - failures}/{total} correct — {'PASS' if not failures else 'FAIL'}")


if __name__ == "__main__":
    main()
