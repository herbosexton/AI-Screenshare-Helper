"""Glue called from the orchestrator after every turn. Never raises into the voice path."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.agent.selfheal.detector import (
    USER_CORRECTION,
    USER_REPORTED,
    FailureDetector,
    Finding,
    is_user_correction,
)
from src.agent.selfheal.incidents import REOPENED, VERIFIED, Incident, IncidentStore
from src.agent.selfheal.recorder import TurnRecorder, build_record


class SelfHealLoop:
    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        self.recorder = TurnRecorder(data_dir / "turns")
        self.incidents = IncidentStore(data_dir / "incidents")
        self.detector = FailureDetector()
        try:
            self.recorder.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[SelfHeal] cannot create {self.recorder.root}: {exc}")
        print(f"[SelfHeal] recording turns to {self.recorder.root}")

    def observe(self, utterance: str, result: dict[str, Any], perf: dict[str, Any]) -> Optional[Incident]:
        try:
            return self._observe(utterance, result, perf)
        except Exception as exc:
            print(f"[SelfHeal] observe failed: {exc}")
            return None

    def _observe(self, utterance: str, result: dict[str, Any], perf: dict[str, Any]) -> Optional[Incident]:
        if not (utterance or "").strip() or result.get("discard") or result.get("selfheal_report"):
            return None
        rec = self.recorder.record(build_record(utterance, result, perf))
        findings = self.detector.check(rec)
        print(
            f"[SelfHeal] turn path={rec.path or '-'} actions={rec.detected_actions} "
            f"findings={[f.rule for f in findings]}"
        )
        self._verify_approved(rec, findings)

        if is_user_correction(utterance):
            prev = self.recorder.previous(skip=1)
            if prev is not None:
                inc = self.incidents.open_or_bump(
                    prev,
                    [Finding(USER_CORRECTION, f"User said {utterance!r} right after this turn.")],
                    source="user_correction",
                )
                print(f"[SelfHeal] incident={inc.id} rule={USER_CORRECTION} utterance={prev.utterance!r}")
                return inc

        if not findings:
            return None
        inc = self.incidents.open_or_bump(rec, findings, source="detector")
        print(
            f"[SelfHeal] incident={inc.id} rules={inc.rules} occurrences={inc.occurrences} "
            f"utterance={rec.utterance!r}"
        )
        return inc

    def report_last(self) -> dict[str, Any]:
        prev = self.recorder.previous()
        if prev is None:
            return {"ok": False, "message": "There is no earlier command to report.", "selfheal_report": True}
        findings = self.detector.check(prev) or [
            Finding(USER_REPORTED, "User flagged this turn by voice; no automatic rule fired.")
        ]
        if not any(f.rule == USER_REPORTED for f in findings):
            findings.append(Finding(USER_REPORTED, "User flagged this turn by voice."))
        inc = self.incidents.open_or_bump(prev, findings, source="voice_report")
        print(f"[SelfHeal] incident={inc.id} source=voice_report utterance={prev.utterance!r}")
        return {
            "ok": True,
            "message": "Logged it. The fixer will pick it up and ask you before anything changes.",
            "incident_id": inc.id,
            "selfheal_report": True,
        }

    def _verify_approved(self, rec, findings: list[Finding]) -> None:
        for inc in self.incidents.approved_for(rec.utterance):
            if findings:
                inc.live_test = "FAIL"
                self.incidents.set_status(inc, REOPENED, f"replay failed: {[f.rule for f in findings]}")
                print(f"[SelfHeal] incident={inc.id} LIVE=FAIL reopened")
            else:
                inc.live_test = "PASS"
                self.incidents.set_status(inc, VERIFIED, "replay passed detector checks")
                print(f"[SelfHeal] incident={inc.id} LIVE=PASS verified")
