"""Per-request performance trace. Printed as REQUEST PERFORMANCE, never hidden inside 'Asking local model'."""

from __future__ import annotations

import time
from typing import Any


class PerfTrace:
    def __init__(self, request: str = ""):
        self.request = (request or "")[:120]
        self.t0 = time.perf_counter()
        self._marks: dict[str, float] = {}
        self.meta: dict[str, Any] = {}

    def span(self, name: str) -> "_Span":
        return _Span(self, name)

    def add(self, name: str, ms: float) -> None:
        self._marks[name] = self._marks.get(name, 0.0) + max(0.0, ms)

    def set(self, key: str, value: Any) -> None:
        self.meta[key] = value

    def total_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "marks": {k: round(v, 1) for k, v in self._marks.items()},
            "total_ms": round(self.total_ms(), 1),
            **self.meta,
        }

    def summary_line(self) -> str:
        parts = [f"Total: {self.total_ms():.0f} ms"]
        path = self.meta.get("path")
        if path:
            parts.append(f"Path: {path}")
        for name in ("STT", "Router", "Planner LLM", "Tool execution", "Browser execution", "Verification", "Response generation", "TTS"):
            if name in self._marks:
                parts.append(f"{name}: {self._marks[name]:.0f} ms")
        if "prompt_tokens_est" in self.meta:
            parts.append(f"prompt~{self.meta['prompt_tokens_est']}")
        return "  |  ".join(parts)

    def report(self) -> str:
        lines = ["", "REQUEST PERFORMANCE"]
        if self.request:
            lines.append(f"Request               {self.request}")
        order = [
            "STT",
            "Router",
            "Planner LLM",
            "Prompt build",
            "Tool execution",
            "Browser execution",
            "Verification",
            "UIA extraction",
            "Screenshot",
            "Vision",
            "Response generation",
            "TTS",
        ]
        seen = set()
        for name in order:
            if name in self._marks:
                lines.append(f"{name:<22}{self._marks[name]:,.0f} ms")
                seen.add(name)
        for name, ms in self._marks.items():
            if name not in seen:
                lines.append(f"{name:<22}{ms:,.0f} ms")
        path = self.meta.get("path", "")
        if path:
            lines.append(f"Path                  {path}")
        if "prompt_tokens_est" in self.meta:
            lines.append(f"Prompt tokens ~       {self.meta['prompt_tokens_est']}")
        if "tool_schema_tokens_est" in self.meta:
            lines.append(f"Tool schema tokens ~  {self.meta['tool_schema_tokens_est']}")
        if "tools_before_filter" in self.meta:
            lines.append(
                f"Tools                 {self.meta.get('tools_before_filter')} -> "
                f"{self.meta.get('tools_after_filter')}"
            )
        if "model" in self.meta:
            lines.append(f"Model                 {self.meta['model']}")
        if "intent_resolution_count_per_command" in self.meta:
            lines.append(f"Intent resolutions    {self.meta['intent_resolution_count_per_command']}")
        if "recognized_intent" in self.meta:
            lines.append(f"Recognized intent     {self.meta.get('recognized_intent') or '-'}")
        if "route" in self.meta:
            lines.append(f"Route                 {self.meta['route']}")
        lines.append(f"{'TOTAL':<22}{self.total_ms():,.0f} ms")
        lines.append("")
        text = "\n".join(lines)
        print(text)
        return text


class _Span:
    def __init__(self, trace: PerfTrace, name: str):
        self.trace = trace
        self.name = name
        self._t0 = 0.0

    def __enter__(self) -> "_Span":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.trace.add(self.name, (time.perf_counter() - self._t0) * 1000)
