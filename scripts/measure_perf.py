"""One-shot latency measurements for the performance sprint report."""

from __future__ import annotations

import time

import httpx

from src.agent.computer.controller import ComputerController
from src.agent.router import FastCommandRouter
from src.agent.screen.understanding import ScreenUnderstandingService


def main() -> None:
    print("=== FAST PATH (in-process) ===")
    r = FastCommandRouter()
    t0 = time.perf_counter()
    intent = r.route("What page am I on right now?")
    print(f"route: {(time.perf_counter() - t0) * 1000:.1f} ms action={intent.action if intent else None}")

    print()
    print("=== OLLAMA ===")
    base = "http://127.0.0.1:11434"
    try:
        t0 = time.perf_counter()
        tags = httpx.get(base + "/api/tags", timeout=3).json()
        ping_ms = (time.perf_counter() - t0) * 1000
        names = [m.get("name") for m in tags.get("models") or []]
        print(f"tags ping: {ping_ms:.0f} ms models={names[:8]}")
        body = {
            "model": "qwen2.5:7b",
            "messages": [{"role": "user", "content": "Reply with the single word pong."}],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 8},
        }
        t0 = time.perf_counter()
        data = httpx.post(base + "/v1/chat/completions", json=body, timeout=90).json()
        warm_ms = (time.perf_counter() - t0) * 1000
        usage = data.get("usage") or {}
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")[:80]
        print(f"first chat (may include load): {warm_ms:.0f} ms usage={usage} reply={content!r}")
        t0 = time.perf_counter()
        data2 = httpx.post(base + "/v1/chat/completions", json=body, timeout=90).json()
        warm2_ms = (time.perf_counter() - t0) * 1000
        usage2 = data2.get("usage") or {}
        print(f"warm chat: {warm2_ms:.0f} ms usage={usage2}")
        ct = usage2.get("completion_tokens") or 1
        print(f"approx completion tok/s: {ct / max(warm2_ms / 1000, 0.001):.1f}")

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "browser.status",
                    "description": "Current URL and title",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        body3 = {
            "model": "qwen2.5:7b",
            "messages": [
                {"role": "system", "content": "You are Jarvis. Use tools when needed."},
                {"role": "user", "content": "What page am I on right now?"},
            ],
            "tools": tools,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.3, "num_ctx": 4096},
        }
        t0 = time.perf_counter()
        data3 = httpx.post(base + "/v1/chat/completions", json=body3, timeout=90).json()
        planner_ms = (time.perf_counter() - t0) * 1000
        usage3 = data3.get("usage") or {}
        print(f"planner-style (tiny tools, page question): {planner_ms:.0f} ms usage={usage3}")
    except Exception as e:
        print(f"ollama unavailable: {type(e).__name__}: {e}")

    print()
    print("=== UIA vs cheap window ===")
    try:
        c = ComputerController()
        t0 = time.perf_counter()
        w = c.get_active_window()
        cheap_ms = (time.perf_counter() - t0) * 1000
        title = ((w or {}).get("title") or "")[:60]
        print(f"active window (level A): {cheap_ms:.0f} ms title={title!r}")
        svc = ScreenUnderstandingService(None, c)
        t0 = time.perf_counter()
        s = svc.get_state(include_screenshot=False, include_uia=False)
        a_ms = (time.perf_counter() - t0) * 1000
        print(f"get_state UIA=off: {a_ms:.0f} ms app={s.active_application!r}")
        t0 = time.perf_counter()
        s2 = svc.get_state(include_screenshot=False, include_uia=True)
        c_ms = (time.perf_counter() - t0) * 1000
        print(f"get_state UIA=on: {c_ms:.0f} ms interactive={len(s2.interactive_elements)}")
    except Exception as e:
        print(f"computer/UIA: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
