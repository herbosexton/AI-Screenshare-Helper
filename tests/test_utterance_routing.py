"""Utterance classification + planner admission. No phrase-specific planner patches."""

from __future__ import annotations

from pathlib import Path

from src.agent.audit import AuditLog
from src.agent.emergency import EmergencyStop
from src.agent.local_intent import reset_nlu_context
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine
from src.agent.planner_admission import MULTI_STEP_CROSS_TOOL_GOAL, admit_planner
from src.agent.providers.base import ProviderResponse
from src.agent.task_store import TaskStore
from src.agent.tools.base import ToolRegistry
from src.agent.utterance import (
    ACKNOWLEDGEMENT,
    AGENT_PLAN,
    AI_CONVERSATION,
    COMPLEX_GOAL,
    CORRECTION,
    DECLARATIVE_COMMENT,
    DIRECT_COMMAND,
    LOCAL_CONVERSATION,
    QUESTION,
    classify_utterance,
    has_action_evidence,
)
from src.agent.router import FastCommandRouter
from src.ui.dashboard_data import DailyTaskStore
from tests.test_agent_phase1 import MockProvider


def _orch(tmp_path: Path, provider=None) -> AgentOrchestrator:
    reset_nlu_context()
    store = TaskStore(tmp_path / "tasks.db")
    perms = PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True)
    registry = ToolRegistry(perms, AuditLog(), EmergencyStop())
    return AgentOrchestrator(
        provider or MockProvider([ProviderResponse(content="SHOULD NOT BE CALLED")]),
        registry,
        store,
        perms,
        emergency_stop=EmergencyStop(),
        hud_tasks_path=str(tmp_path / "daily_tasks.json"),
    )


def test_required_natural_language_classes():
    cases = [
        ("That's fine.", ACKNOWLEDGEMENT, LOCAL_CONVERSATION),
        ("That's an added task.", DECLARATIVE_COMMENT, LOCAL_CONVERSATION),
        ("Okay.", ACKNOWLEDGEMENT, LOCAL_CONVERSATION),
        ("I see.", ACKNOWLEDGEMENT, LOCAL_CONVERSATION),
        ("That's better.", ACKNOWLEDGEMENT, LOCAL_CONVERSATION),
        ("Maybe later.", ACKNOWLEDGEMENT, LOCAL_CONVERSATION),
        ("That's not right.", CORRECTION, LOCAL_CONVERSATION),
        ("I meant the other task.", CORRECTION, LOCAL_CONVERSATION),
        ("Why is the sky blue?", QUESTION, AI_CONVERSATION),
        (
            "Find my latest PDF and compare it with the website I'm viewing.",
            COMPLEX_GOAL,
            AGENT_PLAN,
        ),
    ]
    for spoken, expected_type, expected_route in cases:
        hit = classify_utterance(spoken)
        assert hit.utterance_type == expected_type, f"{spoken!r} → {hit.utterance_type}"
        assert hit.conceptual_route == expected_route, f"{spoken!r} → {hit.conceptual_route}"
        assert hit.classify_ms < 10.0, hit.classify_ms


def test_open_chrome_and_tasks_are_direct():
    chrome = classify_utterance("Open Chrome.", has_local_intent=False, command_clause="open chrome")
    assert chrome.utterance_type == DIRECT_COMMAND
    tasks = classify_utterance("What tasks do I have?", has_local_intent=True)
    assert tasks.utterance_type == DIRECT_COMMAND


def test_thats_an_added_task_never_admits_planner(tmp_path):
    spoken = "That's an added task."
    hit = classify_utterance(spoken)
    admission = admit_planner(hit)
    assert hit.utterance_type == DECLARATIVE_COMMENT
    assert admission.admitted is False
    orch = _orch(tmp_path)
    DailyTaskStore(tmp_path / "daily_tasks.json").save()
    before = list(DailyTaskStore(tmp_path / "daily_tasks.json").tasks)
    result = orch.handle_user_message(spoken)
    after = DailyTaskStore(tmp_path / "daily_tasks.json").tasks
    assert orch.planner_calls == 0
    assert result.get("planner_admitted") is False
    assert result.get("state_change") is False
    assert result.get("utterance_type") == DECLARATIVE_COMMENT
    assert len(after) == len(before)


def test_short_comments_skip_planner(tmp_path):
    orch = _orch(tmp_path)
    for spoken in (
        "That's fine.",
        "That's an added task.",
        "Okay then.",
        "I see.",
        "Maybe later.",
    ):
        result = orch.handle_user_message(spoken)
        assert orch.planner_calls == 0, spoken
        assert result.get("planner_admitted") is False, spoken
        assert result.get("state_change") is False, spoken
        assert (result.get("perf") or {}).get("total_ms", 0) < 500, spoken


def test_knowledge_question_is_conversation_not_planner(tmp_path):
    provider = MockProvider([ProviderResponse(content="Because air scatters blue light.")])
    orch = _orch(tmp_path, provider=provider)
    result = orch.handle_user_message("Why is the sky blue?")
    assert orch.planner_calls == 0
    assert orch.conversation_calls == 1
    assert result.get("conceptual_route") == AI_CONVERSATION
    assert result.get("planner_admitted") is False
    assert "blue" in (result.get("message") or "").lower()
    assert provider.calls
    assert provider.calls[0]["tools"] in (None, [])


def test_complex_resume_goal_is_admitted(tmp_path):
    spoken = "Let's find my latest resume and compare it to the job page I have open."
    hit = classify_utterance(spoken)
    admission = admit_planner(hit)
    assert hit.utterance_type == COMPLEX_GOAL
    assert admission.admitted is True
    assert admission.reason == MULTI_STEP_CROSS_TOOL_GOAL
    provider = MockProvider([ProviderResponse(content="I would look up the resume first.")])
    orch = _orch(tmp_path, provider=provider)
    result = orch.handle_user_message(spoken)
    assert orch.planner_calls >= 1
    assert result.get("planner_admitted") is True
    assert result.get("planner_admission_reason") == MULTI_STEP_CROSS_TOOL_GOAL
    assert result.get("fast") is False


def test_no_local_intent_is_not_admission_reason():
    hit = classify_utterance("That's an added task.")
    admission = admit_planner(hit, local_intent="")
    assert admission.admitted is False
    assert admission.reason != "NO_LOCAL_INTENT"


def test_a_word_in_front_of_a_command_does_not_hide_it():
    """"now go to chatgpt in chrome" classified as UNKNOWN and was silently ignored."""
    for text in (
        "now go to chatgpt in chrome",
        "just open the downloads folder",
        "so find my newest invoice",
        "then close that tab",
    ):
        classified = classify_utterance(text)
        assert classified.utterance_type in {DIRECT_COMMAND, COMPLEX_GOAL}, text


def test_a_request_nobody_could_parse_still_gets_an_answer():
    """Silence is right for room noise, wrong for something that plainly asked for work."""
    classified = classify_utterance("the thing with the browser scroll whatever it was")
    assert has_action_evidence(classified) is True


def test_room_noise_stays_silent():
    assert has_action_evidence(classify_utterance("okay then")) is False
    assert has_action_evidence(classify_utterance("i see")) is False


def test_being_called_by_name_gets_an_answer(tmp_path):
    """A wake phrase is a summons. Silence reads as a dead assistant."""
    orch = _orch(tmp_path)
    for spoken in ("Hey Jarvis.", "Jarvis", "Hello Jarvis"):
        result = orch.handle_user_message(spoken)
        assert (result.get("message") or "").strip(), f"{spoken} got silence"
        assert result.get("planner_admitted") is False
        assert orch.planner_calls == 0


def test_a_name_in_front_of_a_real_request_is_not_a_summons(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message("Thanks Jarvis for that")
    assert result.get("message") != "Yes?"


def test_a_silent_acknowledgement_stays_silent(tmp_path):
    """An empty reply must never be padded with internal state."""
    orch = _orch(tmp_path)
    result = orch.handle_user_message("That's fine.")
    assert result.get("message") == ""
    assert result.get("conceptual_route") == LOCAL_CONVERSATION


def test_reading_a_sentence_aloud_is_not_a_checkbox_command():
    """"Check in with Tony..." is a phrasal verb, not the label of a checkbox."""
    router = FastCommandRouter()
    spoken = [
        "check in with Tony and Greg for NDAs. I just did that and the system looks fine.",
        "check in with Tony",
        "check out this page",
    ]
    for text in spoken:
        assert router.route(text) is None, text


def test_real_checkbox_commands_still_route_fast():
    router = FastCommandRouter()
    for text in ("check the terms box", "check the checkbox", "tick the newsletter box"):
        hit = router.route(text)
        assert hit is not None and hit.action == "browser.check", text


def test_conversation_model_is_told_it_has_no_personal_data(tmp_path):
    """It invented a calendar and a lunch with "Sarah". The prompt must forbid that."""
    provider = MockProvider([ProviderResponse(content="I do not have access to your calendar.")])
    orch = _orch(tmp_path, provider=provider)
    orch.handle_user_message("What is on my agenda today?")

    system = provider.calls[0]["messages"][0]
    assert system["role"] == "system"
    grounding = system["content"].lower()
    for forbidden in ("calendar", "email", "contacts"):
        assert forbidden in grounding, f"prompt does not mention {forbidden}"
    assert "no access" in grounding
    assert "never invent" in grounding


def test_a_typo_cannot_hand_a_five_part_goal_to_the_fast_router():
    """"thenopen" destroyed the only sequencing cue, so it answered five requests with one."""
    text = (
        "Open Chrome, go to google.com, open another tab, go to tradingview, "
        "thenopen another tab and open chatgbt"
    )
    assert AgentOrchestrator._is_agent_goal(classify_utterance(text), text) is True


def test_a_two_part_browser_command_still_takes_the_fast_path():
    """Opening a browser at an address is one intent, not a task worth planning."""
    for text in ("Open a browser and go to example.com", "Open Chrome", "Go back"):
        assert AgentOrchestrator._is_agent_goal(classify_utterance(text), text) is False


_RESUME_CHROME_GOAL = (
    "Find my newest resume, open it, then switch back to Chrome and tell me what page I'm on."
)

_JOB_COMPARE_GOAL = (
    "Look at the job page I have open, find my newest resume, compare the resume "
    "to the job requirements, and tell me what I'm missing."
)


def test_job_compare_goal_is_not_a_resume_followup(tmp_path):
    from src.agent.corrections import classify_task_correction, followup_scores, new_goal_override
    from src.agent.fast_coverage import analyze_fast_coverage, segment_compound_command
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod
    from src.agent.planner_admission import MULTI_STEP_CROSS_TOOL_GOAL

    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        filename="AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    segs = segment_compound_command(_JOB_COMPARE_GOAL)
    assert len(segs) >= 3
    report = analyze_fast_coverage(_JOB_COMPARE_GOAL)
    assert "compare" in report.detected_actions
    assert len(report.detected_domains) >= 2
    scores = followup_scores(_JOB_COMPARE_GOAL)
    assert scores["new_goal_override"] is True
    assert scores["task_correction_allowed"] is False
    assert new_goal_override(_JOB_COMPARE_GOAL) is True
    assert classify_task_correction(_JOB_COMPARE_GOAL) is None

    hit = classify_utterance(_JOB_COMPARE_GOAL)
    assert hit.utterance_type == COMPLEX_GOAL
    admission = admit_planner(hit)
    assert admission.admitted is True
    assert admission.reason == MULTI_STEP_CROSS_TOOL_GOAL

    orch = _orch(tmp_path)
    result = orch.handle_user_message(_JOB_COMPARE_GOAL)
    assert result.get("planner_admitted") is True
    assert result.get("planner_admission_reason") == MULTI_STEP_CROSS_TOOL_GOAL
    assert "restored the resume" not in (result.get("message") or "").lower()
    assert "minimized" not in (result.get("message") or "").lower()


def test_compare_job_to_resume_after_recent_artifact_is_new_goal(tmp_path):
    from src.agent.corrections import classify_task_correction
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod

    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    spoken = "Find the job page I have open and compare it to my resume."
    assert classify_task_correction(spoken) is None
    result = _orch(tmp_path).handle_user_message(spoken)
    assert result.get("planner_admitted") is True
    assert "restored" not in (result.get("message") or "").lower()


def test_resume_chrome_compound_is_not_fast_browser_open():
    from src.agent.fast_coverage import (
        FULL_COVERAGE,
        PARTIAL_COVERAGE,
        analyze_fast_coverage,
        segment_compound_command,
    )
    from src.agent.router import FastCommandRouter

    segs = segment_compound_command(_RESUME_CHROME_GOAL)
    assert len(segs) == 4
    assert "resume" in segs[0].lower()
    assert segs[1].lower().startswith("open")
    assert "chrome" in segs[2].lower()
    assert "page" in segs[3].lower()

    report = analyze_fast_coverage(_RESUME_CHROME_GOAL, intent_action="browser.open")
    assert report.coverage == PARTIAL_COVERAGE
    assert report.fast_route_allowed is False
    assert report.unconsumed_clauses

    router = FastCommandRouter()
    assert router.route(_RESUME_CHROME_GOAL) is None
    assert router.last_coverage.coverage == PARTIAL_COVERAGE
    assert router.last_coverage.fast_route_allowed is False

    hit = classify_utterance(_RESUME_CHROME_GOAL)
    admission = admit_planner(hit)
    assert hit.utterance_type == COMPLEX_GOAL
    assert admission.admitted is True
    assert admission.reason == MULTI_STEP_CROSS_TOOL_GOAL


def test_resume_chrome_compound_does_not_report_chrome_focused(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message(_RESUME_CHROME_GOAL)
    assert result.get("planner_admitted") is True
    assert result.get("planner_admission_reason") == MULTI_STEP_CROSS_TOOL_GOAL
    assert result.get("fast") is False
    assert "already open" not in (result.get("message") or "").lower()


def test_i_dont_see_the_resume_brings_it_forward(tmp_path):
    from src.agent.corrections import RECENT_ARTIFACT_SHOW, classify_task_correction
    from src.agent.phase6.artifacts import OpenedArtifact, remember
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod
    from tests.test_phase6_orchestrator import FakeComputer

    computer = FakeComputer()
    computer.windows.append(
        {
            "handle": 99,
            "hwnd": 99,
            "title": "AI Engineer Resume 2026.pdf and 12 more pages - Personal - Microsoft Edge",
            "process_id": 7,
            "process_name": "msedge.exe",
        }
    )
    computer.active = "AI Desktop Agent Build - Google Chrome"
    orch = _orch(tmp_path)
    orch.computer = computer
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        original_request=_RESUME_CHROME_GOAL,
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        filename="AI Engineer Resume 2026.pdf",
        hwnd=99,
        process_name="msedge.exe",
        completed_at=__import__("time").time(),
    )
    remember(
        OpenedArtifact(
            file_path="C:/docs/AI Engineer Resume 2026.pdf",
            hwnd=99,
            window_title="AI Engineer Resume 2026.pdf and 12 more pages - Personal - Microsoft Edge",
            verified_open=True,
            extra={"process_name": "msedge.exe"},
        )
    )
    assert classify_task_correction("I don't see the resume.") is not None
    result = orch.handle_user_message("I don't see the resume.")
    assert result.get("planner_admitted") is False
    assert result.get("utterance_type") == RECENT_ARTIFACT_SHOW
    assert "brought it forward" in (result.get("message") or "").lower()
    assert "resume" in computer.active.lower()
    assert orch.planner_calls == 0


def test_where_is_the_resume_answers_locally(tmp_path):
    from src.agent.corrections import RECENT_ARTIFACT_STATUS
    from src.agent.phase6.artifacts import OpenedArtifact, remember
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod
    from tests.test_phase6_orchestrator import FakeComputer

    computer = FakeComputer()
    computer.windows.append(
        {
            "handle": 99,
            "hwnd": 99,
            "title": "AI Engineer Resume 2026.pdf",
            "process_id": 7,
            "process_name": "msedge.exe",
        }
    )
    computer.active = "Chrome"
    orch = _orch(tmp_path)
    orch.computer = computer
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        filename="AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    remember(
        OpenedArtifact(
            file_path="C:/docs/AI Engineer Resume 2026.pdf",
            hwnd=99,
            window_title="AI Engineer Resume 2026.pdf",
            verified_open=True,
            extra={"process_name": "msedge.exe"},
        )
    )
    result = orch.handle_user_message("Where is the resume?")
    assert result.get("planner_admitted") is False
    assert result.get("utterance_type") == RECENT_ARTIFACT_STATUS
    assert "background" in (result.get("message") or "").lower()
    assert orch.planner_calls == 0


def test_show_me_the_resume_is_local(tmp_path):
    from src.agent.corrections import classify_task_correction
    from src.agent.phase6.artifacts import OpenedArtifact, remember
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod
    from tests.test_phase6_orchestrator import FakeComputer

    computer = FakeComputer()
    computer.windows.append(
        {"handle": 99, "hwnd": 99, "title": "AI Engineer Resume 2026.pdf", "process_name": "msedge.exe", "process_id": 7}
    )
    computer.active = "Chrome"
    orch = _orch(tmp_path)
    orch.computer = computer
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        filename="AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    remember(
        OpenedArtifact(
            file_path="C:/docs/AI Engineer Resume 2026.pdf",
            hwnd=99,
            window_title="AI Engineer Resume 2026.pdf",
            verified_open=True,
            extra={"process_name": "msedge.exe"},
        )
    )
    hit = classify_task_correction("Show me the resume.")
    assert hit is not None and hit.kind == "artifact_show"
    result = orch.handle_user_message("Show me the resume.")
    assert result.get("planner_admitted") is False
    assert "brought it forward" in (result.get("message") or "").lower()
    assert orch.planner_calls == 0


def test_you_didnt_leave_it_open_is_a_correction():
    from src.agent.corrections import classify_task_correction

    hit = classify_utterance("you didn't leave it open")
    assert hit.utterance_type == CORRECTION
    assert classify_task_correction("you didn't leave it open") is not None
    assert classify_task_correction("What page am I on?") is None


def test_didnt_leave_it_open_reopens_without_planner(tmp_path):
    from src.agent.corrections import RECENT_TASK_CORRECTION
    from src.agent.phase6.artifacts import OpenedArtifact, remember
    from src.agent.phase6.recent import RecentTaskResultContext, clear_recent
    from src.agent.phase6 import recent as recent_mod
    from src.agent.tools.base import ToolResult
    from tests.test_phase6_orchestrator import FakeComputer, RequiredPathParams, ScriptedTool, _open_file

    clear_recent()
    orch = _orch(tmp_path)
    computer = FakeComputer()
    orch.computer = computer
    orch.registry.register(
        ScriptedTool("files.open", RequiredPathParams, lambda tool, **kw: _open_file(computer, **kw))
    )
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        original_request=_RESUME_CHROME_GOAL,
        selected_resume="AI Engineer Resume 2026.pdf",
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    remember(
        OpenedArtifact(
            file_path="C:/docs/AI Engineer Resume 2026.pdf",
            hwnd=88,
            window_title="AI Engineer Resume 2026.pdf",
            verified_open=True,
        )
    )
    result = orch.handle_user_message("you didn't leave it open")
    assert result.get("planner_admitted") is False
    assert result.get("utterance_type") == RECENT_TASK_CORRECTION
    assert "reopened" in (result.get("message") or "").lower()
    assert orch.planner_calls == 0
    assert any("resume" in str(w.get("title", "")).lower() for w in computer.windows)


def test_didnt_leave_it_open_explains_when_still_open(tmp_path):
    from src.agent.phase6.artifacts import OpenedArtifact, remember
    from src.agent.phase6.recent import RecentTaskResultContext, clear_recent
    from src.agent.phase6 import recent as recent_mod
    from tests.test_phase6_orchestrator import FakeComputer

    clear_recent()
    orch = _orch(tmp_path)
    computer = FakeComputer()
    computer.windows.append(
        {
            "handle": 99,
            "hwnd": 99,
            "title": "AI Engineer Resume 2026.pdf",
            "process_id": 7,
            "process_name": "msedge.exe",
        }
    )
    orch.computer = computer
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        original_request=_RESUME_CHROME_GOAL,
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    remember(
        OpenedArtifact(
            file_path="C:/docs/AI Engineer Resume 2026.pdf",
            hwnd=99,
            window_title="AI Engineer Resume 2026.pdf",
            verified_open=True,
        )
    )
    result = orch.handle_user_message("you didn't leave it open")
    assert result.get("planner_admitted") is False
    assert "still open" in (result.get("message") or "").lower()
    assert orch.planner_calls == 0


def test_wrong_resume_opens_second_candidate(tmp_path):
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod
    from tests.test_phase6_orchestrator import FakeComputer, RequiredPathParams, ScriptedTool, _open_file

    orch = _orch(tmp_path)
    computer = FakeComputer()
    orch.computer = computer
    orch.registry.register(
        ScriptedTool("files.open", RequiredPathParams, lambda tool, **kw: _open_file(computer, **kw))
    )
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        original_request=_RESUME_CHROME_GOAL,
        selected_resume_path="C:/docs/resume_2026.pdf",
        candidate_files=[
            {"name": "resume_2026.pdf", "path": "C:/docs/resume_2026.pdf"},
            {"name": "resume_2024.pdf", "path": "C:/docs/resume_2024.pdf"},
        ],
        completed_at=__import__("time").time(),
    )
    result = orch.handle_user_message("That's the wrong resume. Use the second newest one.")
    assert result.get("planner_admitted") is False
    assert "resume_2024.pdf" in (result.get("message") or "")
    assert orch.planner_calls == 0


def test_ambiguous_correction_asks_locally(tmp_path):
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod

    orch = _orch(tmp_path)
    recent_mod._LAST = RecentTaskResultContext(
        task_id="t1",
        original_request=_RESUME_CHROME_GOAL,
        completed_at=__import__("time").time(),
    )
    result = orch.handle_user_message("That's wrong.")
    assert result.get("planner_admitted") is False
    assert "resume" in (result.get("message") or "").lower()
    assert "page" in (result.get("message") or "").lower()
    assert orch.planner_calls == 0


def test_close_the_resume_routes_to_close_window():
    router = FastCommandRouter()
    intent = router.route("Close the resume.")
    assert intent is not None
    assert intent.action == "computer.close_window"
    assert intent.args.get("kind") == "resume"


def test_simple_fast_commands_still_have_full_coverage():
    from src.agent.fast_coverage import FULL_COVERAGE
    from src.agent.router import FastCommandRouter

    router = FastCommandRouter()
    cases = {
        "Open Chrome.": "browser.open",
        "Go to Google.": "browser.open_and_goto",
        "What page am I on?": "browser.current_page",
        "Open a new tab and go to Google.": "browser.new_tab",
        "now go to chatgbt": "browser.open_and_goto",
    }
    for spoken, action in cases.items():
        intent = router.route(spoken)
        assert intent is not None, spoken
        assert intent.action == action, (spoken, intent.action)
        assert router.last_coverage.coverage == FULL_COVERAGE
        assert router.last_coverage.fast_route_allowed is True


def _stored_comparison() -> dict:
    return {
        "task_id": "t-compare",
        "job_title": "Agentic AI Engineer, Senior",
        "company": "Deloitte",
        "covered_requirements": [
            {
                "requirement": "Python",
                "status": "COVERED",
                "resume_evidence": "Built Python agents for document review",
                "job_evidence": "Python",
                "confidence": 0.9,
                "category": "skills",
            }
        ],
        "partial_requirements": [
            {
                "requirement": "LangGraph",
                "status": "PARTIAL",
                "resume_evidence": "Used LangChain for RAG prototypes",
                "job_evidence": "LangGraph",
                "confidence": 0.55,
                "category": "skills",
            }
        ],
        "missing_requirements": [
            {
                "requirement": "TS/SCI clearance",
                "status": "NOT_FOUND",
                "resume_evidence": "",
                "job_evidence": "TS/SCI",
                "confidence": 0.8,
                "category": "clearance",
            }
        ],
        "needs_confirmation": [
            {
                "requirement": "Authorized to work in the US",
                "status": "NEEDS_CONFIRMATION",
                "resume_evidence": "",
                "job_evidence": "work authorization",
                "confidence": 0.4,
                "category": "other",
            }
        ],
        "notable_strengths": ["Python"],
        "source_evidence": [
            {"requirement": "Python", "resume_evidence": "Built Python agents for document review"}
        ],
        "generated_at": 1.0,
        "job_page_url": "https://apply.deloitte.com/job/359035",
        "job_page_title": "Agentic AI Engineer, Senior",
        "resume_path": "C:/docs/AI Engineer Resume 2026.pdf",
        "job_page_model_id": "apply.deloitte.com|agentic ai engineer, senior",
    }


def test_gaps_followup_is_local_after_comparison(tmp_path):
    from src.agent.corrections import RECENT_ANALYSIS_FOLLOWUP, SHOW_GAPS, classify_analysis_followup
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod

    recent_mod._LAST = RecentTaskResultContext(
        task_id="t-compare",
        task_type="JOB_RESUME_COMPARISON",
        comparison_result=_stored_comparison(),
        selected_resume_path="C:/docs/AI Engineer Resume 2026.pdf",
        completed_at=__import__("time").time(),
    )
    hit = classify_analysis_followup("Okay, what are the gaps?")
    assert hit is not None
    assert hit.kind == SHOW_GAPS
    orch = _orch(tmp_path)
    result = orch.handle_user_message("Okay, what are the gaps?")
    assert result.get("planner_admitted") is False
    assert result.get("utterance_type") == RECENT_ANALYSIS_FOLLOWUP
    assert result.get("planner_calls") == 0
    assert orch.planner_calls == 0
    msg = (result.get("message") or "").lower()
    assert "ts/sci" in msg or "clearance" in msg
    assert "langgraph" in msg
    assert "completed:" not in msg


def test_what_matched_well_is_local_after_comparison(tmp_path):
    from src.agent.corrections import SHOW_MATCHES, classify_analysis_followup
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod

    recent_mod._LAST = RecentTaskResultContext(
        task_id="t-compare",
        task_type="JOB_RESUME_COMPARISON",
        comparison_result=_stored_comparison(),
        completed_at=__import__("time").time(),
    )
    assert classify_analysis_followup("What matched well?").kind == SHOW_MATCHES
    result = _orch(tmp_path).handle_user_message("What matched well?")
    assert result.get("planner_admitted") is False
    assert "python" in (result.get("message") or "").lower()
    assert "built python agents" in (result.get("message") or "").lower()


def test_why_covered_uses_stored_evidence(tmp_path):
    from src.agent.corrections import SHOW_EVIDENCE, classify_analysis_followup
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod

    recent_mod._LAST = RecentTaskResultContext(
        task_id="t-compare",
        task_type="JOB_RESUME_COMPARISON",
        comparison_result=_stored_comparison(),
        completed_at=__import__("time").time(),
    )
    assert classify_analysis_followup("Why did you say that one was covered?").kind == SHOW_EVIDENCE
    result = _orch(tmp_path).handle_user_message("Why did you say that one was covered?")
    assert result.get("planner_admitted") is False
    assert "built python agents" in (result.get("message") or "").lower()


def test_compare_this_one_is_new_phase6_not_reuse(tmp_path):
    from src.agent.corrections import classify_analysis_followup, new_goal_override
    from src.agent.phase6.recent import RecentTaskResultContext
    from src.agent.phase6 import recent as recent_mod

    recent_mod._LAST = RecentTaskResultContext(
        task_id="t-compare",
        task_type="JOB_RESUME_COMPARISON",
        comparison_result=_stored_comparison(),
        job_page_model_id="apply.deloitte.com|old job",
        completed_at=__import__("time").time(),
    )
    spoken = "Compare this one to my resume."
    assert new_goal_override(spoken) is True
    assert classify_analysis_followup(spoken) is None
    result = _orch(tmp_path).handle_user_message(spoken)
    assert result.get("planner_admitted") is True
    assert "ts/sci" not in (result.get("message") or "").lower()
