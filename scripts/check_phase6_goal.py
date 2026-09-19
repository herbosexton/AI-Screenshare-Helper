"""Run one real goal through the live orchestrator, exactly as the app does."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from src.agent.factory import build_agent_stack


class _NullCapture:
    def capture(self, *a, **k):
        return None


def main() -> None:
    goal = " ".join(sys.argv[1:]) or "Find my most recent PDF and open it, then switch back to Chrome."
    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    agent = build_agent_stack(
        config,
        screen_capture=_NullCapture(),
        clipboard_out=None,
        speech_out_getter=lambda: None,
    )

    # Startup fires a vision warmup on a background thread. In the app that is long done
    # before anyone speaks; here it would race the planner for the same CPU.
    settle = float(os.environ.get("JARVIS_SETTLE_S", "35"))
    print(f"(settling {settle:.0f}s for warmup)")
    time.sleep(settle)

    print(f"\n=== GOAL: {goal}\n")
    t0 = time.time()
    result = agent.handle_user_message(goal)
    elapsed = round((time.time() - t0) * 1000)

    task = agent.agent_runner.active
    print(f"\n=== RESULT after {elapsed} ms")
    print(f"spoken : {result.get('message')!r}")
    print(f"ok     : {result.get('ok')}  waiting_for_user={result.get('waiting_for_user')}")
    if task is not None:
        print(f"status : {task.status.value}")
        print(f"planner_calls={task.planner_calls} replans={task.replan_count}")
        for s in sorted(task.steps, key=lambda s: s.order):
            print(f"  [{s.status.value:9}] {s.description}  ({s.preferred_tool})")
            if s.tool_arguments:
                print(f"              args={json.dumps(s.tool_arguments)[:160]}")
            if s.error:
                print(f"              error={s.error}")
        print(f"not_done: {task.abandoned_descriptions()}")
        print(f"context : {json.dumps(task.context.compact(), default=str)[:400]}")


if __name__ == "__main__":
    main()
