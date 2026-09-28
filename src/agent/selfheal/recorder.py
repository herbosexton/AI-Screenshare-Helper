"""Persist one structured record per handled utterance to a local, gitignored JSONL file."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.agent.audit import redact_secrets

_RESULT_KEYS = (
    "ok",
    "code",
    "error",
    "discard",
    "task_id",
    "utterance_type",
    "conceptual_route",
    "planner_admitted",
    "planner_admission_reason",
    "planner_calls",
    "state_change",
    "fast",
    "stale",
)
_PERF_KEYS = (
    "path",
    "route",
    "model_tier",
    "coverage",
    "command_segments",
    "detected_actions",
    "detected_domains",
    "recognized_intent",
    "normalized_transcript",
    "planner_admitted",
    "planner_admission_reason",
    "planner_fallback_reason",
    "utterance_type",
    "conceptual_route",
    "tools_after_filter",
    "total_ms",
)
_MAX_MESSAGE = 600


@dataclass
class TurnRecord:
    turn_id: str
    ts: float
    utterance: str
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    perf: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return str(self.perf.get("path") or "")

    @property
    def detected_actions(self) -> list[str]:
        return [str(a) for a in (self.perf.get("detected_actions") or [])]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TurnRecord":
        return cls(
            turn_id=str(raw.get("turn_id") or ""),
            ts=float(raw.get("ts") or 0.0),
            utterance=str(raw.get("utterance") or ""),
            message=str(raw.get("message") or ""),
            result=dict(raw.get("result") or {}),
            perf=dict(raw.get("perf") or {}),
        )


def build_record(utterance: str, result: dict[str, Any], perf: dict[str, Any]) -> TurnRecord:
    message = redact_secrets(str(result.get("message") or ""))[:_MAX_MESSAGE]
    kept_result = {k: result[k] for k in _RESULT_KEYS if k in result}
    kept_perf = {k: perf[k] for k in _PERF_KEYS if k in perf}
    if isinstance(kept_result.get("error"), str):
        kept_result["error"] = redact_secrets(kept_result["error"])[:_MAX_MESSAGE]
    return TurnRecord(
        turn_id=uuid.uuid4().hex[:12],
        ts=time.time(),
        utterance=redact_secrets(utterance or "")[:500],
        message=message,
        result=kept_result,
        perf=kept_perf,
    )


class TurnRecorder:
    def __init__(self, root: Path, *, keep: int = 20):
        self.root = Path(root)
        self._recent: deque[TurnRecord] = deque(maxlen=keep)

    def record(self, rec: TurnRecord) -> TurnRecord:
        self._recent.append(rec)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y-%m-%d", time.localtime(rec.ts))
            with (self.root / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.as_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[SelfHeal] turn record write failed: {exc}")
        return rec

    def previous(self, *, skip: int = 0) -> Optional[TurnRecord]:
        items = list(self._recent)
        idx = len(items) - 1 - skip
        return items[idx] if 0 <= idx < len(items) else None
