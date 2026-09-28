"""Incident files: the hand-off between JARVIS (detects) and the fixer agent (repairs)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.agent.selfheal.detector import Finding
from src.agent.selfheal.recorder import TurnRecord

OPEN = "OPEN"
FIXING = "FIXING"
FIX_READY = "FIX_READY"
FIX_FAILED = "FIX_FAILED"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
VERIFIED = "VERIFIED"
REOPENED = "REOPENED"

ACTIVE = frozenset({OPEN, FIXING, FIX_READY, FIX_FAILED, APPROVED, REOPENED})
FIXABLE = frozenset({OPEN, REOPENED})


def utterance_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (text or "").lower())).strip()


@dataclass
class Incident:
    id: str
    status: str
    created_at: float
    updated_at: float
    source: str
    rules: list[str]
    utterance: str
    utterance_key: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    occurrences: int = 1
    live_test: str = "NOT YET RUN"
    fix: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Incident":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in allowed})


class IncidentStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, incident_id: str) -> Path:
        return self.root / incident_id

    def load(self, incident_id: str) -> Optional[Incident]:
        path = self._dir(incident_id) / "incident.json"
        if not path.exists():
            return None
        try:
            return Incident.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def all(self) -> list[Incident]:
        if not self.root.exists():
            return []
        out = []
        for child in sorted(self.root.iterdir()):
            inc = self.load(child.name) if child.is_dir() else None
            if inc is not None:
                out.append(inc)
        return out

    def save(self, inc: Incident) -> Incident:
        inc.updated_at = time.time()
        folder = self._dir(inc.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "incident.json").write_text(
            json.dumps(inc.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (folder / "incident.md").write_text(render_markdown(inc), encoding="utf-8")
        return inc

    def set_status(self, inc: Incident, status: str, note: str = "", **fix: Any) -> Incident:
        inc.history.append({"ts": time.time(), "from": inc.status, "to": status, "note": note})
        inc.status = status
        if fix:
            inc.fix.update(fix)
        return self.save(inc)

    def open_or_bump(self, rec: TurnRecord, findings: list[Finding], *, source: str) -> Incident:
        key = utterance_key(rec.utterance)
        rules = sorted({f.rule for f in findings})
        for inc in self.all():
            if inc.status in ACTIVE and inc.utterance_key == key and set(rules) <= set(inc.rules):
                inc.occurrences += 1
                inc.turns = (inc.turns + [rec.as_dict()])[-5:]
                return self.save(inc)
        now = time.time()
        inc = Incident(
            id=time.strftime("inc-%Y%m%d-%H%M%S", time.localtime(now)) + f"-{rec.turn_id[:4]}",
            status=OPEN,
            created_at=now,
            updated_at=now,
            source=source,
            rules=rules,
            utterance=rec.utterance,
            utterance_key=key,
            findings=[asdict(f) for f in findings],
            turns=[rec.as_dict()],
        )
        inc.history.append({"ts": now, "from": "", "to": OPEN, "note": source})
        return self.save(inc)

    def approved_for(self, utterance: str) -> list[Incident]:
        key = utterance_key(utterance)
        return [i for i in self.all() if i.status == APPROVED and i.utterance_key == key]


def render_markdown(inc: Incident) -> str:
    last = inc.turns[-1] if inc.turns else {}
    perf = last.get("perf") or {}
    result = last.get("result") or {}
    lines = [
        f"# JARVIS incident {inc.id}",
        "",
        f"STATUS: {inc.status}",
        f"SOURCE: {inc.source}",
        f"RULES: {', '.join(inc.rules) or '-'}",
        f"OCCURRENCES: {inc.occurrences}",
        f"LIVE TEST: {inc.live_test}",
        "",
        "## Live command",
        "",
        f"RAW USER COMMAND: {inc.utterance}",
        f"NORMALIZED COMMAND: {perf.get('normalized_transcript') or '-'}",
        f"COMMAND SEGMENTS: {perf.get('command_segments') or []}",
        f"DETECTED ACTIONS: {perf.get('detected_actions') or []}",
        f"DETECTED DOMAINS: {perf.get('detected_domains') or []}",
        f"COVERAGE: {perf.get('coverage') or '-'}",
        f"PATH: {perf.get('path') or '-'}",
        f"UTTERANCE TYPE: {result.get('utterance_type') or '-'}",
        f"CONCEPTUAL ROUTE: {result.get('conceptual_route') or '-'}",
        f"PLANNER ADMITTED: {result.get('planner_admitted')}",
        f"PLANNER ADMISSION REASON: {result.get('planner_admission_reason') or perf.get('planner_admission_reason') or '-'}",
        f"FINAL RESPONSE: {last.get('message') or '-'}",
        "",
        "## Detector findings",
        "",
    ]
    for f in inc.findings:
        lines.append(f"- {f.get('rule')}: {f.get('detail')}")
        if f.get("earliest_wrong_decision"):
            lines.append(f"  - suspected earliest wrong decision: {f['earliest_wrong_decision']}")
    if not inc.findings:
        lines.append("- none (user-reported)")
    if inc.fix:
        lines += ["", "## Fix", ""]
        for k, v in inc.fix.items():
            lines.append(f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)
