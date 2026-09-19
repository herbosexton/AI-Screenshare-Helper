"""Transcript normalizer + local intent resolver. No planner for STT near-misses."""

from __future__ import annotations

from pathlib import Path

from src.agent.audit import AuditLog
from src.agent.emergency import EmergencyStop
from src.agent.local_intent import LocalIntentResolver, reset_nlu_context, resolve_intent
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine
from src.agent.providers.base import ProviderResponse
from src.agent.task_store import TaskStore
from src.agent.tools.base import ToolRegistry
from src.agent.transcript import TranscriptNormalizer
from src.agent.voice_session import VoiceSessionController
from src.agent.voice_state import DiscardReason
from src.agent.voice_tasks import route_task_intent
from src.ui.dashboard_data import DailyTaskStore, hud_spoken_reply
from tests.test_agent_phase1 import MockProvider


TASKS = [
    {"id": "1", "text": "Review morning emails", "done": False},
    {"id": "2", "text": "Check calendar & priorities", "done": False},
    {"id": "3", "text": "Continue Jarvis agent work", "done": False},
]


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


def test_normalizer_test_list_in_task_context():
    n = TranscriptNormalizer().normalize("What's on my test list again?", tasks=TASKS, hud_active=True)
    assert "task list" in n.normalized
    assert any("test -> task" in c for c in n.corrections)


def test_normalizer_does_not_rewrite_unrelated_test():
    n = TranscriptNormalizer().normalize("Run the unit test suite for transformers", hud_active=True)
    assert "unit test" in n.normalized
    assert "task" not in n.normalized.split() or "unit" in n.normalized


def test_mourning_to_morning_only_with_task_entity():
    n = TranscriptNormalizer().normalize(
        "Cross out the mourning email task",
        tasks=TASKS,
        hud_active=True,
    )
    assert "morning" in n.normalized
    assert any("mourning -> morning" in c for c in n.corrections)
    n2 = TranscriptNormalizer().normalize("The mourning dove sang", hud_active=True)
    assert "mourning" in n2.normalized


def test_a_misheard_site_name_is_repaired_before_anything_guesses_a_domain():
    """"chatgbt" reached the planner, which turned it into a live typosquat domain."""
    n = TranscriptNormalizer().normalize("open another tab and go to chat gbt")
    assert "chatgpt" in n.normalized
    assert "chat gbt" not in n.normalized


def test_a_command_verb_is_not_rewritten_into_a_word_from_the_task_list():
    """"switch back to Chrome" became "with back to Chrome" whenever a task said "with"."""
    tasks = [{"text": "Follow up with Greg about the NDA"}]
    n = TranscriptNormalizer().normalize(
        "Find my most recent PDF and open it, then switch back to Chrome",
        tasks=tasks,
        hud_active=True,
    )
    assert "switch back to chrome" in n.normalized
    assert not any("with" in c for c in n.corrections)


def test_stt_subs_calendar_jarvis_checkof():
    n = TranscriptNormalizer().normalize("Checkof the calender for Jervis", tasks=TASKS)
    assert "check off" in n.normalized
    assert "calendar" in n.normalized
    assert "jarvis" in n.normalized


def test_exact_task_list_intent():
    r = LocalIntentResolver().resolve("What's on my task list?", tasks=TASKS)
    assert r.intent == "task.list"
    assert r.route == "fast"
    assert r.intent_confidence >= 0.85
    assert r.planner_fallback_reason == ""


def test_real_failure_test_list_again():
    r = LocalIntentResolver().resolve("What's on my test list again?", tasks=TASKS)
    assert r.intent == "task.list"
    assert r.route == "fast"
    assert r.intent_confidence >= 0.85
    fi = route_task_intent("What's on my test list again?", TASKS)
    assert fi is not None
    assert fi.action == "task.list"


def test_natural_task_list_variants():
    phrases = [
        "What tasks do I have?",
        "What's left?",
        "What do I have left today?",
        "Read my list.",
        "What do I need to do?",
        "What's on today's list?",
        "What are my tests today?",
        "Show me today's tasks.",
        "Read my to-do list.",
        "What's on my todo list?",
    ]
    for p in phrases:
        r = LocalIntentResolver().resolve(p, tasks=TASKS)
        assert r.intent == "task.list", f"{p!r} -> {r.intent} {r.intent_confidence} {r.normalized_transcript}"
        assert r.route == "fast", f"{p!r} route={r.route}"


def test_task_entity_fuzziness():
    cases = [
        "Cross out the morning email task.",
        "Check off review emails.",
        "Mark the email one done.",
        "Complete the morning emails.",
        "Check off the morning email test.",
    ]
    for p in cases:
        r = LocalIntentResolver().resolve(p, tasks=TASKS)
        assert r.intent == "task.complete", f"{p!r} -> {r.intent}"
        assert r.route == "fast"
        assert "morning email" in (r.entity or "").lower()


def test_ambiguous_email_tasks_ask():
    tasks = [
        {"id": "1", "text": "Email Greg", "done": False},
        {"id": "2", "text": "Email Tony", "done": False},
    ]
    r = LocalIntentResolver().resolve("Mark the email task done.", tasks=tasks)
    assert r.intent == "task.ambiguous"
    assert r.route == "clarify"
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as d:
        store = DailyTaskStore(Path(d) / "t.json")
        store.tasks = [dict(t) for t in tasks]
        store.save()
        msg = hud_spoken_reply("Mark the email task done.", store)
        assert msg and msg.startswith("Which task")
        assert "Greg" in msg and "Tony" in msg


def test_unknown_complex_is_not_a_local_task():
    r = LocalIntentResolver().resolve(
        "Explain why a transformer attention mechanism scales quadratically.",
        tasks=TASKS,
    )
    assert r.intent != "task.list"
    assert r.intent != "task.add"
    assert r.route in {"unresolved", "planner"}


def test_hud_and_orchestrator_test_list_zero_planner(tmp_path):
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    reply = hud_spoken_reply("What's on my task list?", store)
    assert reply and "Review morning emails" in reply
    reply2 = hud_spoken_reply("What's on my test list again?", store)
    assert reply2 and "Review morning emails" in reply2
    orch = _orch(tmp_path)
    result = orch.handle_user_message("What's on my test list again?")
    assert orch.planner_calls == 0
    assert "Review morning emails" in (result.get("message") or "")
    assert result.get("fast") is True
    result3 = orch.handle_user_message("What's on my task list?")
    assert orch.planner_calls == 0
    assert result3.get("fast") is True


def test_cross_out_still_local(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message("Cross out review morning emails.")
    assert result.get("message") == "Done."
    assert orch.planner_calls == 0


def test_planner_timeout_recovers_task_list(tmp_path):
    class Boom(MockProvider):
        def chat(self, *a, **k):
            raise TimeoutError("Planner timed out after 25s")

    orch = _orch(tmp_path, provider=Boom([]))
    # Force planner path by using a string that still recovers via HUD retry
    result = orch.run_task("What's on my test list again?")
    assert result.get("ok") is True
    assert "Review morning emails" in (result.get("message") or "")
    assert result.get("recovered_after_planner") is True


def test_echo_and_barge_in_not_regressed():
    tts = (
        "You have 3 tasks remaining: Review morning emails, "
        "Check calendar and priorities, Continue Jarvis agent work."
    )
    session = VoiceSessionController()
    session.on_tts_start(tts)
    echo = session.admit(tts, confidence=0.9, duration_s=2.0, tts_active=True)
    assert not echo.accepted
    assert echo.decision == DiscardReason.SELF_SPEECH_ECHO.value
    barge = session.admit("Cross out review morning emails.", confidence=0.64, duration_s=1.6, tts_active=True)
    assert barge.accepted
    assert barge.decision == "USER_BARGE_IN"
    assert barge.recognized_intent == "task.complete"
    listed = session.admit("What's on my test list again?", confidence=0.53, duration_s=1.4, tts_active=True)
    assert listed.accepted
    assert listed.recognized_intent == "task.list"


def test_undo_morning_email_local(tmp_path):
    reset_nlu_context()
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert hud_spoken_reply("Cross out reviewing morning emails.", store) == "Done."
    done = next(t for t in store.tasks if "morning email" in t["text"].lower())
    assert done["done"] is True
    r = resolve_intent("Undo the morning email task.", tasks=store.tasks)
    assert r.intent == "task.uncomplete"
    assert r.route == "fast"
    assert "morning email" in (r.entity or "").lower()
    assert r.intent_resolution_count == 1
    reply = hud_spoken_reply("Undo the morning email task.", store)
    assert reply == "Undone."
    undone = next(t for t in store.tasks if "morning email" in t["text"].lower())
    assert undone["done"] is False


def test_uncomplete_natural_variants():
    reset_nlu_context()
    phrases = [
        "Undo the morning email task",
        "Undo that task",
        "Mark that task incomplete",
        "Uncheck the morning email task",
        "Put the morning email task back",
        "Reopen the morning email task",
        "Mark review morning emails not done",
        "I didn't finish the morning email task",
    ]
    done_tasks = [{**t, "done": True} for t in TASKS]
    for p in phrases:
        r = LocalIntentResolver().resolve(p, tasks=done_tasks)
        assert r.intent == "task.uncomplete", f"{p!r} -> {r.intent} {r.normalized_transcript}"
        assert r.route == "fast", f"{p!r} route={r.route}"


def test_check_it_off_again_uses_last_task(tmp_path):
    orch = _orch(tmp_path)
    first = orch.handle_user_message("Cross out reviewing morning emails.")
    assert first.get("message") == "Done."
    assert orch.planner_calls == 0
    undone = orch.handle_user_message("Undo the morning email task.")
    assert undone.get("message") == "Undone."
    assert orch.planner_calls == 0
    again = orch.handle_user_message("Check it off again.")
    assert again.get("message") == "Done."
    assert orch.planner_calls == 0
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    hit = next(t for t in store.tasks if "morning email" in t["text"].lower())
    assert hit["done"] is True


def test_social_phrases_stay_local(tmp_path):
    orch = _orch(tmp_path)
    thanks = orch.handle_user_message("Thanks")
    assert orch.planner_calls == 0
    assert thanks.get("fast") is True
    assert "welcome" in (thanks.get("message") or "").lower()
    bye = orch.handle_user_message("See ya.")
    assert orch.planner_calls == 0
    assert bye.get("message") == "See you later."
    ack = orch.handle_user_message("Okay")
    assert orch.planner_calls == 0
    assert ack.get("message") == ""
    nm = orch.handle_user_message("Never mind")
    assert orch.planner_calls == 0
    assert nm.get("message") == "Okay."


def test_yes_no_prefer_pending_clarification(tmp_path):
    reset_nlu_context()
    tasks = [
        {"id": "1", "text": "Email Greg", "done": False},
        {"id": "2", "text": "Email Tony", "done": False},
    ]
    store = DailyTaskStore(tmp_path / "t.json")
    store.tasks = [dict(t) for t in tasks]
    store.save()
    msg = hud_spoken_reply("Mark the email task done.", store)
    assert msg and msg.startswith("Which task")
    r = resolve_intent("Yes", tasks=store.tasks)
    assert r.intent == "confirm.yes"
    assert r.route == "clarify"
    yes = hud_spoken_reply("Yes", store)
    assert yes and yes.startswith("Which task")
    assert all(not t.get("done") for t in store.tasks)


def test_never_mind_clears_pending(tmp_path):
    reset_nlu_context()
    tasks = [
        {"id": "1", "text": "Email Greg", "done": False},
        {"id": "2", "text": "Email Tony", "done": False},
    ]
    store = DailyTaskStore(tmp_path / "t.json")
    store.tasks = [dict(t) for t in tasks]
    store.save()
    hud_spoken_reply("Mark the email task done.", store)
    from src.agent.local_intent import CONTEXT

    assert CONTEXT.pending_candidates
    reply = hud_spoken_reply("Never mind", store)
    assert reply == "Okay."
    assert CONTEXT.pending_candidates == []


def test_intent_resolution_count_is_one(tmp_path, monkeypatch):
    from src.agent import local_intent as li

    calls = {"n": 0}
    orig = li.LocalIntentResolver.resolve

    def wrapped(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(li.LocalIntentResolver, "resolve", wrapped)
    orch = _orch(tmp_path)
    result = orch.handle_user_message("What's left?")
    assert "Review morning emails" in (result.get("message") or "")
    assert calls["n"] == 1
    assert result.get("perf", {}).get("intent_resolution_count_per_command", 1) == 1
    orch.handle_user_message("What's left?")
    assert calls["n"] == 1
    orch.handle_user_message("Thanks")
    assert calls["n"] == 2


def test_undone_is_not_uncomplete_intent():
    from src.agent.local_intent import peek_intent_name

    assert peek_intent_name("Undone.") != "task.uncomplete"
    assert peek_intent_name("Undo the morning email task.") == "task.uncomplete"


def test_garbled_low_stt_does_not_complete(tmp_path):
    """Exact live failure: low-confidence STT must not execute TASK_COMPLETE."""
    from src.agent.command_validator import ASK_CLARIFICATION, DISCARD_LOW_CONFIDENCE

    reset_nlu_context()
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    before = [(t["id"], t.get("done"), t.get("text")) for t in store.tasks]
    transcript = "Add a task to the task list to add or complete the six in-jar list."
    r = resolve_intent(transcript, tasks=store.tasks, stt_confidence=0.35)
    assert r.intent != "task.complete" or r.execution_decision != "EXECUTE_LOCAL"
    assert r.execution_decision in {ASK_CLARIFICATION, DISCARD_LOW_CONFIDENCE}
    assert r.execution_decision != "EXECUTE_LOCAL"
    reply = hud_spoken_reply(transcript, store, stt_confidence=0.35)
    assert reply
    assert "could not match" not in reply.lower()
    after = [(t["id"], t.get("done"), t.get("text")) for t in store.tasks]
    assert after == before
    orch = _orch(tmp_path)
    result = orch.handle_user_message(transcript, stt_confidence=0.35)
    assert orch.planner_calls == 0
    assert result.get("fast") is True
    store2 = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert all(not t.get("done") for t in store2.tasks)


def test_complete_the_task_asks_which(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message("Complete the task.")
    assert orch.planner_calls == 0
    msg = (result.get("message") or "").lower()
    assert "which task" in msg
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert all(not t.get("done") for t in store.tasks)
    r = resolve_intent("Complete the task.", tasks=store.tasks)
    assert r.execution_decision == "ASK_CLARIFICATION"
    assert "task_entity" in r.missing_arguments


def test_add_a_task_asks_for_content(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message("Add a task.")
    assert orch.planner_calls == 0
    assert "what task should i add" in (result.get("message") or "").lower()
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert len(store.tasks) == 3


def test_add_complete_report_is_add_not_complete(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message("Add a task to complete the report tomorrow.")
    assert orch.planner_calls == 0
    msg = (result.get("message") or "").lower()
    assert msg.startswith("added")
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert all(not t.get("done") for t in store.tasks)
    assert any("complete the report tomorrow" in (t.get("text") or "").lower() for t in store.tasks)
    r = resolve_intent("Add complete tax return to my task list.", tasks=TASKS)
    assert r.intent == "task.add"
    assert r.execution_decision == "EXECUTE_LOCAL"
    assert "complete tax return" in (r.args.get("text") or "").lower()


def test_clear_cross_out_still_executes(tmp_path):
    orch = _orch(tmp_path)
    r = resolve_intent("Cross out review morning emails.", tasks=DailyTaskStore(tmp_path / "daily_tasks.json").tasks)
    assert r.intent == "task.complete"
    assert "morning email" in (r.entity or "").lower()
    assert r.entity_confidence >= 0.7
    assert r.arguments_valid is True
    assert r.execution_decision == "EXECUTE_LOCAL"
    result = orch.handle_user_message("Cross out review morning emails.")
    assert result.get("message") == "Done."
    assert orch.planner_calls == 0
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    hit = next(t for t in store.tasks if "morning email" in t["text"].lower())
    assert hit["done"] is True


def test_low_confidence_uncertain_entity_does_not_write(tmp_path):
    reset_nlu_context()
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    before = [(t["id"], t.get("done")) for t in store.tasks]
    transcript = "Complete the something or other task."
    r = resolve_intent(transcript, tasks=store.tasks, stt_confidence=0.30)
    assert r.execution_decision in {"ASK_CLARIFICATION", "DISCARD_LOW_CONFIDENCE"}
    hud_spoken_reply(transcript, store, stt_confidence=0.30)
    after = [(t["id"], t.get("done")) for t in store.tasks]
    assert after == before


def test_readonly_fuzzy_list_still_works(tmp_path):
    orch = _orch(tmp_path)
    result = orch.handle_user_message("What's on my test list?", stt_confidence=0.53)
    assert orch.planner_calls == 0
    assert "Review morning emails" in (result.get("message") or "")
    r = resolve_intent("What's on my test list?", tasks=TASKS, stt_confidence=0.53)
    assert r.intent == "task.list"
    assert r.execution_decision == "EXECUTE_LOCAL"


def test_pending_uncomplete_entity_fill(tmp_path):
    orch = _orch(tmp_path)
    orch.handle_user_message("Cross out reviewing morning emails.")
    reset_nlu_context()
    ask = orch.handle_user_message("Undo that task.")
    assert orch.planner_calls == 0
    assert "incomplete" in (ask.get("message") or "").lower() or "which task" in (ask.get("message") or "").lower()
    follow = orch.handle_user_message("Morning emails.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Undone."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    hit = next(t for t in store.tasks if "morning email" in t["text"].lower())
    assert hit["done"] is False


def test_add_natural_patterns(tmp_path):
    orch = _orch(tmp_path)
    a = orch.handle_user_message("Add call Greg to my list.")
    assert orch.planner_calls == 0
    assert (a.get("message") or "").lower().startswith("added")
    b = orch.handle_user_message("Put buy groceries on today's tasks.")
    assert orch.planner_calls == 0
    assert (b.get("message") or "").lower().startswith("added")
    c = orch.handle_user_message("Add a task to call Tony.")
    assert orch.planner_calls == 0
    assert (c.get("message") or "").lower().startswith("added")
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    texts = " ".join(t.get("text") or "" for t in store.tasks).lower()
    assert "call greg" in texts
    assert "buy groceries" in texts
    assert "call tony" in texts


def test_complete_then_entity_followup(tmp_path):
    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Complete the task.")
    assert "which task" in (ask.get("message") or "").lower()
    follow = orch.handle_user_message("The morning email one.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Done."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    hit = next(t for t in store.tasks if "morning email" in t["text"].lower())
    assert hit["done"] is True


def test_jarvis_add_a_task_incomplete_intent(tmp_path):
    from src.agent.local_intent import peek_intent_name

    assert peek_intent_name("Jarvis, add a task.") == "task.add"
    assert peek_intent_name("Add a task") == "task.add"
    r = resolve_intent("Jarvis, add a task.", tasks=TASKS)
    assert r.intent == "task.add"
    assert r.execution_decision == "ASK_CLARIFICATION"
    assert r.missing_arguments == ["task_text"]
    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Jarvis, add a task.")
    assert orch.planner_calls == 0
    assert (ask.get("message") or "") == "What task should I add?"
    follow = orch.handle_user_message("Call Greg tomorrow.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert any("call greg tomorrow" in (t.get("text") or "").lower() for t in store.tasks)


def test_pending_add_consumes_finish_phrase(tmp_path):
    orch = _orch(tmp_path)
    orch.handle_user_message("Jarvis, add a task.")
    follow = orch.handle_user_message("Finish the Phase 6 testing.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert any("finish the phase 6 testing" in (t.get("text") or "").lower() for t in store.tasks)
    assert all(not t.get("done") for t in store.tasks if "phase 6" in (t.get("text") or "").lower())


def test_incomplete_task_actions_ask_locally(tmp_path):
    from src.agent.local_intent import peek_intent_name

    orch = _orch(tmp_path)
    cases = [
        ("Jarvis, complete a task.", "task.complete", "task_entity"),
        ("Jarvis, delete a task.", "task.delete", "task_entity"),
        ("Jarvis, undo a task.", "task.uncomplete", "task_entity"),
    ]
    for spoken, intent, missing in cases:
        reset_nlu_context()
        assert peek_intent_name(spoken) == intent, spoken
        r = resolve_intent(spoken, tasks=DailyTaskStore(tmp_path / "daily_tasks.json").tasks)
        assert r.intent == intent
        assert r.execution_decision == "ASK_CLARIFICATION"
        assert missing in r.missing_arguments
        result = orch.handle_user_message(spoken)
        assert orch.planner_calls == 0, spoken
        assert result.get("message")


def test_jarvis_prefix_list_and_chrome():
    from src.agent.local_intent import peek_intent_name
    from src.agent.router import FastCommandRouter

    assert peek_intent_name("Jarvis, what's left?") == "task.list"
    r = resolve_intent("Jarvis, what's left?", tasks=TASKS)
    assert r.intent == "task.list"
    assert r.execution_decision == "EXECUTE_LOCAL"
    intent = FastCommandRouter().route("Jarvis, open Chrome.")
    assert intent is not None
    assert intent.action == "browser.open"


def test_stt_i_had_a_task_resolves_to_add(tmp_path):
    from src.agent.local_intent import peek_intent_name

    for spoken in (
        "Hey Jarvis, I had a task.",
        "Jarvis, I had a task.",
        "I had a task.",
        "had a task",
        "at a task",
        "ad a task",
    ):
        assert peek_intent_name(spoken) == "task.add", spoken
        r = resolve_intent(spoken, tasks=TASKS)
        assert r.intent == "task.add", spoken
        assert r.execution_decision == "ASK_CLARIFICATION"
        assert r.missing_arguments == ["task_text"]
    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Hey Jarvis, I had a task.")
    assert orch.planner_calls == 0
    assert (ask.get("message") or "") == "What task should I add?"
    follow = orch.handle_user_message("Finish Phase 6 testing.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert any("finish phase 6 testing" in (t.get("text") or "").lower() for t in store.tasks)


def test_noisy_wake_and_fillers_still_add_a_task(tmp_path):
    from src.agent.command_clause import extract_command_clause
    from src.agent.local_intent import peek_intent_name

    spoken_list = (
        "Hey Jarvis, add a task.",
        "Hey, drivers, add a task.",
        "Hey drivers add a task",
        "Hey Jervis, add a task.",
        "Um Jarvis, add a task.",
        "Um Jarvis can you add a task",
        "Jarvis please add a task.",
        "Hey uh add a task",
        "Hey uh could you please add a task",
        "Could you add a task?",
        "Add a task.",
    )
    for spoken in spoken_list:
        ext = extract_command_clause(spoken)
        assert ext.command_clause == "add a task", f"{spoken!r} clause={ext.command_clause!r}"
        assert peek_intent_name(spoken) == "task.add", spoken
        r = resolve_intent(spoken, tasks=TASKS)
        assert r.intent == "task.add", f"{spoken!r} intent={r.intent}"
        assert r.missing_arguments == ["task_text"]
        assert r.execution_decision == "ASK_CLARIFICATION"
        assert r.route in {"fast", "clarify"}
        assert r.planner_fallback_reason != "NO_LOCAL_INTENT"
        assert r.command_clause == "add a task"
    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Hey, drivers, add a task.")
    assert orch.planner_calls == 0
    assert (ask.get("message") or "") == "What task should I add?"
    follow = orch.handle_user_message("Finish Phase 6 testing.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert any("finish phase 6 testing" in (t.get("text") or "").lower() for t in store.tasks)


def test_drivers_travis_in_narrative_are_not_add():
    from src.agent.command_clause import extract_command_clause
    from src.agent.local_intent import peek_intent_name

    for spoken in (
        "My driver had a task yesterday",
        "Travis had a task assigned to him",
        "I was talking to the drivers about a task",
    ):
        ext = extract_command_clause(spoken)
        assert ext.command_clause != "add a task", spoken
        assert peek_intent_name(spoken) != "task.add", spoken
        r = resolve_intent(spoken, tasks=TASKS)
        assert r.intent != "task.add", spoken


def test_narrative_i_had_is_not_rewritten_to_add():
    from src.agent.local_intent import peek_intent_name
    from src.agent.transcript import TranscriptNormalizer

    for spoken in (
        "I had a task yesterday.",
        "I had three meetings.",
        "He had a task assigned to him.",
        "I had a meeting yesterday.",
    ):
        n = TranscriptNormalizer().normalize(spoken, tasks=TASKS, hud_active=True)
        assert n.normalized != "add a task", spoken
        assert peek_intent_name(spoken) != "task.add", spoken
        r = resolve_intent(spoken, tasks=TASKS)
        assert r.intent != "task.add", spoken


def test_add_another_task_and_multi_clause(tmp_path):
    from src.agent.command_clause import extract_command_clause, segment_utterance
    from src.agent.local_intent import peek_intent_name

    ext = extract_command_clause("Ask another task. Add another task.")
    assert segment_utterance("Ask another task. Add another task.") == [
        "ask another task",
        "add another task",
    ]
    assert ext.selected_command_clause == "add another task"
    assert ext.command_intent == "task.add"
    assert "ask another task" in ext.utterance_segments
    assert ext.superseded_clauses

    spoken_list = (
        "Add another task.",
        "Ask another task. Add another task.",
        "Um, add another task.",
        "Add one more task.",
        "Could you add another task?",
        "Create another task",
        "Put another task on my list",
        "Ask another task.",
    )
    for spoken in spoken_list:
        assert peek_intent_name(spoken) == "task.add", spoken
        r = resolve_intent(spoken, tasks=TASKS)
        assert r.intent == "task.add", f"{spoken!r} -> {r.intent}"
        assert r.missing_arguments == ["task_text"]
        assert r.execution_decision == "ASK_CLARIFICATION"
        assert r.route in {"fast", "clarify"}
        assert r.planner_fallback_reason != "NO_LOCAL_INTENT"

    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Ask another task. Add another task.")
    assert orch.planner_calls == 0
    assert (ask.get("message") or "") == "What task should I add?"
    follow = orch.handle_user_message("Call Greg tomorrow.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    hits = [t for t in store.tasks if "call greg tomorrow" in (t.get("text") or "").lower()]
    assert len(hits) == 1


def test_restated_add_is_not_used_as_task_title(tmp_path):
    orch = _orch(tmp_path)
    first = orch.handle_user_message("Ask another task.")
    assert (first.get("message") or "") == "What task should I add?"
    again = orch.handle_user_message("Add another task.")
    assert orch.planner_calls == 0
    assert (again.get("message") or "") == "What task should I add?"
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert not any("add another task" in (t.get("text") or "").lower() for t in store.tasks)
    follow = orch.handle_user_message("Call Greg tomorrow.")
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert any("call greg tomorrow" in (t.get("text") or "").lower() for t in store.tasks)


def test_self_correction_undo_and_open_edge():
    from src.agent.command_clause import extract_command_clause
    from src.agent.local_intent import peek_intent_name
    from src.agent.router import FastCommandRouter

    r = resolve_intent("Complete—no, undo the morning email task.", tasks=TASKS)
    assert r.intent == "task.uncomplete"
    assert r.selected_command_clause
    assert "undo" in r.selected_command_clause
    assert r.intent != "task.complete"

    ext = extract_command_clause("Open Chrome—actually open Edge.")
    assert ext.selected_command_clause == "open edge"
    assert ext.command_intent == "browser.open"
    assert peek_intent_name("Open Chrome—actually open Edge.") == "browser.open"
    intent = FastCommandRouter().route("Open Chrome—actually open Edge.")
    assert intent is not None
    assert intent.action == "browser.open"


def test_ask_not_globally_rewritten_to_add():
    from src.agent.local_intent import peek_intent_name

    for spoken in (
        "Ask another question.",
        "Ask Greg about another task.",
        "I want to ask you about the task.",
    ):
        assert peek_intent_name(spoken) != "task.add", spoken
        r = resolve_intent(spoken, tasks=TASKS)
        assert r.intent != "task.add", spoken


def test_another_task_not_global_add():
    from src.agent.local_intent import peek_intent_name

    reset_nlu_context()
    assert peek_intent_name("Another task.") != "task.add"
    r = resolve_intent("Another task.", tasks=TASKS)
    assert r.intent != "task.add"
    assert r.route in {"unresolved", "planner"}


def test_another_task_after_add_stays_local(tmp_path):
    from src.agent.local_intent import peek_intent_name
    from src.agent.turn_context import TURN

    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Add a task.")
    assert ask.get("message") == "What task should I add?"
    assert orch.planner_calls == 0
    added = orch.handle_user_message("Call Greg tomorrow.")
    assert added.get("message") == "Added."
    assert orch.planner_calls == 0
    assert peek_intent_name("Another task.") == "task.add"
    again = orch.handle_user_message("Another task.")
    assert orch.planner_calls == 0
    assert again.get("message") == "What task should I add?"
    assert again.get("contextual_intent") == "task.add"
    assert again.get("context_resolution_reason") == "task_add_follow_up"
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert not any((t.get("text") or "").strip().lower() == "another task" for t in store.tasks)
    unknown = orch.handle_user_message("I don't know.")
    assert orch.planner_calls == 0
    assert unknown.get("contextual_intent") == "clarify.unknown"
    assert unknown.get("message")
    assert "{" not in (unknown.get("message") or "")
    done = orch.handle_user_message("That's it.")
    assert orch.planner_calls == 0
    assert done.get("contextual_intent") == "clarify.cancel"
    assert not TURN.pending_intent
    assert not TURN.last_jarvis_question


def test_pending_add_another_task_does_not_become_title(tmp_path):
    orch = _orch(tmp_path)
    orch.handle_user_message("Add a task.")
    again = orch.handle_user_message("Another task.")
    assert orch.planner_calls == 0
    assert again.get("message") == "What task should I add?"
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert not any("another task" in (t.get("text") or "").lower() for t in store.tasks)


def test_i_dont_know_the_task_stays_local(tmp_path):
    orch = _orch(tmp_path)
    orch.handle_user_message("Complete the task.")
    unknown = orch.handle_user_message("I don't know the task.")
    assert orch.planner_calls == 0
    assert unknown.get("contextual_intent") == "clarify.unknown"
    assert "list" in (unknown.get("message") or "").lower() or "okay" in (unknown.get("message") or "").lower()


def test_thats_it_without_task_context_is_not_forced_local():
    reset_nlu_context()
    r = resolve_intent("That's it.", tasks=TASKS)
    assert r.intent != "task.add"
    assert r.contextual_intent == ""


def test_pending_add_aside_does_not_go_to_planner(tmp_path):
    orch = _orch(tmp_path)
    ask = orch.handle_user_message("Jarvis, add task.")
    assert ask.get("message") == "What task should I add?"
    assert orch.planner_calls == 0
    aside = orch.handle_user_message("That's true, by the way.")
    assert orch.planner_calls == 0
    assert aside.get("message") == "What task should I add?"
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    assert not any("true" in (t.get("text") or "").lower() for t in store.tasks)
    from src.agent.local_intent import CONTEXT
    from src.agent.turn_context import TURN

    assert CONTEXT.pending_intent == "task.add"
    assert CONTEXT.missing_argument == "task_text"
    assert TURN.pending_argument == "task_text"


def test_pending_add_strips_jarvis_echo_and_keeps_title(tmp_path):
    from src.agent.turn_context import TURN, note_jarvis_reply
    from src.agent.voice_echo import peel_assistant_echo

    peeled = peel_assistant_echo(
        "I can catch that clearly. Paul, good tomorrow.",
        ["I didn't catch that clearly. Could you repeat it?", "What task should I add?"],
    )
    assert "paul" in peeled.lower() or "call" in peeled.lower()
    assert "catch that clearly" not in peeled.lower()

    orch = _orch(tmp_path)
    orch.handle_user_message("Jarvis, add task.")
    orch.handle_user_message("That's true, by the way.")
    note_jarvis_reply("I didn't catch that clearly. Could you repeat it?")
    follow = orch.handle_user_message("I can catch that clearly. Paul, good tomorrow.")
    assert orch.planner_calls == 0
    assert follow.get("message") == "Added."
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    texts = " ".join((t.get("text") or "").lower() for t in store.tasks)
    assert "call greg tomorrow" in texts or "paul" in texts or "good tomorrow" in texts
    assert TURN.pending_intent != "task.add" or not TURN.pending_intent


def test_synthetic_tool_json_never_spoken(tmp_path):
    from src.agent.response_boundary import SAFE_FALLBACK

    raw = 'ronics\n{"name": "get_status", "arguments": {}}'
    provider = MockProvider([ProviderResponse(content=raw)])
    orch = _orch(tmp_path, provider=provider)
    result = orch.handle_user_message("Explain why attention is quadratic in transformers.")
    assert orch.planner_calls == 0
    assert orch.conversation_calls >= 1
    msg = result.get("message") or ""
    assert "{" not in msg
    assert "get_status" not in msg
    assert "arguments" not in msg.lower()
    assert result.get("response_sanitized") is True or msg == SAFE_FALLBACK
    assert msg == SAFE_FALLBACK



def test_corrections_survive_on_the_whole_goal_not_just_its_first_clause():
    """The planner read the raw transcript, so "chatgbt" in the last clause reached it intact."""
    r = resolve_intent(
        "Open Chrome, go to google.com, then switch back to chatgbt.", tasks=[], hud_active=True
    )
    assert "chatgpt" in r.corrected_transcript.lower()
    assert "chatgbt" not in r.corrected_transcript.lower()


def test_a_single_clause_goal_is_still_corrected():
    r = resolve_intent("go to chat gbt", tasks=[], hud_active=True)
    assert "chatgpt" in r.corrected_transcript.lower()
