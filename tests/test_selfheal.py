"""Self-correction loop: turn recording, deterministic detection, voice report, replay verification."""

from __future__ import annotations

import json
from pathlib import Path

from src.agent.orchestrator import match_control_command
from src.agent.providers.base import ProviderResponse
from src.agent.selfheal import FailureDetector, SelfHealLoop
from src.agent.selfheal.detector import (
    ACTION_ROUTED_TO_CONVERSATION,
    COMPOUND_COMMAND_DROPPED,
    PLANNER_TASK_FAILED,
    USER_CORRECTION,
    USER_REPORTED,
)
from src.agent.selfheal.incidents import APPROVED, OPEN, REOPENED, VERIFIED
from src.agent.selfheal.recorder import build_record
from tests.test_agent_phase1 import MockProvider
from tests.test_utterance_routing import _orch

# Recorded decisions from the live failure "what page am i on and open chatgbt in the tab".
COMPOUND_TO_CHAT_PERF = {
    "path": "ai_conversation",
    "command_segments": ["what page am i on", "open chatgbt in the tab"],
    "detected_actions": ["current_page", "open"],
    "detected_domains": ["browser", "other"],
    "coverage": "PARTIAL_COVERAGE",
    "planner_admitted": False,
    "planner_admission_reason": "NOT_ADMITTED",
}
COMPOUND_TO_CHAT_RESULT = {
    "ok": True,
    "message": "You are on a page.",
    "utterance_type": "QUESTION",
    "conceptual_route": "AI_CONVERSATION",
}
COMPOUND_UTTERANCE = "what page am i on and open chatgbt in the tab"


def _rec(utterance=COMPOUND_UTTERANCE, result=None, perf=None):
    return build_record(utterance, dict(result or COMPOUND_TO_CHAT_RESULT), dict(perf or COMPOUND_TO_CHAT_PERF))


def test_detector_flags_action_routed_to_no_tool_conversation():
    rules = {f.rule for f in FailureDetector().check(_rec())}
    assert ACTION_ROUTED_TO_CONVERSATION in rules
    assert COMPOUND_COMMAND_DROPPED in rules


def test_detector_ignores_pure_question_on_conversation_path():
    perf = {"path": "ai_conversation", "command_segments": ["why is the sky blue"],
            "detected_actions": [], "coverage": "FULL_COVERAGE"}
    assert FailureDetector().check(_rec("why is the sky blue", {"ok": True}, perf)) == []


def test_detector_ignores_read_only_action_on_conversation_path():
    perf = {"path": "ai_conversation", "command_segments": ["what page am i on"],
            "detected_actions": ["current_page"], "coverage": "FULL_COVERAGE"}
    assert FailureDetector().check(_rec("what page am i on", {"ok": True}, perf)) == []


def test_detector_flags_planner_failure_but_not_captcha_wait():
    perf = {"path": "planner", "detected_actions": ["compare"]}
    failed = _rec("compare my resume", {"ok": False, "message": "Could not verify."}, perf)
    captcha = _rec("compare my resume", {"ok": False, "code": "CAPTCHA_REQUIRED"}, perf)
    assert [f.rule for f in FailureDetector().check(failed)] == [PLANNER_TASK_FAILED]
    assert FailureDetector().check(captcha) == []


def test_recorder_redacts_secrets_and_writes_jsonl(tmp_path):
    loop = SelfHealLoop(tmp_path)
    loop.observe("use key sk-abcdefghijklmnopqrstuvwxyz123456", {"ok": True, "message": "ok"}, {"path": "hud"})
    files = list((tmp_path / "turns").glob("*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in line
    assert json.loads(line)["perf"]["path"] == "hud"


def test_observe_opens_incident_and_dedupes_repeats(tmp_path):
    loop = SelfHealLoop(tmp_path)
    first = loop.observe(COMPOUND_UTTERANCE, COMPOUND_TO_CHAT_RESULT, COMPOUND_TO_CHAT_PERF)
    second = loop.observe(COMPOUND_UTTERANCE, COMPOUND_TO_CHAT_RESULT, COMPOUND_TO_CHAT_PERF)
    assert first is not None and second is not None
    assert first.id == second.id
    assert second.occurrences == 2
    assert first.status == OPEN
    md = (tmp_path / "incidents" / first.id / "incident.md").read_text(encoding="utf-8")
    assert "RAW USER COMMAND: what page am i on and open chatgbt in the tab" in md
    assert "PLANNER ADMISSION REASON: NOT_ADMITTED" in md


def test_user_correction_flags_previous_turn(tmp_path):
    loop = SelfHealLoop(tmp_path)
    loop.observe("open chatgpt", {"ok": True, "message": "Done."}, {"path": "fast:open_url"})
    inc = loop.observe("that's not what I asked", {"ok": True, "message": "Sorry."}, {"path": "local_conversation"})
    assert inc is not None
    assert inc.utterance == "open chatgpt"
    assert inc.rules == [USER_CORRECTION]


def test_report_voice_command_matches_with_and_without_address():
    assert match_control_command("Jarvis, report that.") == "report"
    assert match_control_command("jarvis report that") == "report"
    assert match_control_command("report this please") == "report"
    assert match_control_command("report that the page is broken to my manager") is None
    assert match_control_command("Jarvis, open Chrome") is None
    assert match_control_command("jarvis stop") == "stop"
    assert match_control_command("Jarvis, pause") == "pause"


def test_report_that_through_orchestrator_flags_previous_turn(tmp_path):
    provider = MockProvider([ProviderResponse(content="Because air scatters blue light.")])
    orch = _orch(tmp_path, provider=provider)
    orch.selfheal = SelfHealLoop(tmp_path / "data")

    orch.handle_user_message("Why is the sky blue?")
    reply = orch.handle_user_message("Jarvis, report that.")

    assert reply["ok"] is True
    assert orch.planner_calls == 0
    inc = orch.selfheal.incidents.load(reply["incident_id"])
    assert inc is not None
    assert inc.utterance == "Why is the sky blue?"
    assert USER_REPORTED in inc.rules
    turns = list((tmp_path / "data" / "turns").glob("*.jsonl"))[0].read_text(encoding="utf-8").splitlines()
    assert len(turns) == 1


def test_replay_after_approval_marks_live_pass_or_fail(tmp_path):
    loop = SelfHealLoop(tmp_path)
    inc = loop.observe(COMPOUND_UTTERANCE, COMPOUND_TO_CHAT_RESULT, COMPOUND_TO_CHAT_PERF)
    loop.incidents.set_status(inc, APPROVED, "merged")

    fixed_perf = dict(COMPOUND_TO_CHAT_PERF, path="planner", planner_admitted=True)
    loop.observe(COMPOUND_UTTERANCE, {"ok": True, "message": "Opened ChatGPT."}, fixed_perf)
    verified = loop.incidents.load(inc.id)
    assert verified.status == VERIFIED and verified.live_test == "PASS"

    loop.incidents.set_status(verified, APPROVED, "merged again")
    loop.observe(COMPOUND_UTTERANCE, COMPOUND_TO_CHAT_RESULT, COMPOUND_TO_CHAT_PERF)
    reopened = loop.incidents.load(inc.id)
    assert reopened.status == REOPENED and reopened.live_test == "FAIL"


def test_observe_never_raises_into_voice_path(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    loop = SelfHealLoop(blocker)
    assert loop.observe(COMPOUND_UTTERANCE, COMPOUND_TO_CHAT_RESULT, COMPOUND_TO_CHAT_PERF) is None
