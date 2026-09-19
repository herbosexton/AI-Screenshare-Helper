"""Voice architecture repair: echo, quality gate, tasks, barge-in. No Phase 6."""

from __future__ import annotations

import time

from src.agent.router import FastCommandRouter
from src.agent.voice_echo import (
    TtsMemory,
    correct_command_stt,
    echo_score,
    normalize_speech,
    token_novelty,
)
from src.agent.voice_gate import CommandQualityGate, VoiceEvent
from src.agent.voice_session import VoiceSessionController
from src.agent.voice_state import DiscardReason, VoiceSource, VoiceState
from src.agent.voice_tasks import fuzzy_task_match, route_task_intent
from src.output.speech import SpeechOutput
from src.ui.dashboard_data import DailyTaskStore, hud_spoken_reply


TASKS = [
    {"id": "1", "text": "Review morning emails", "done": False},
    {"id": "2", "text": "Check calendar & priorities", "done": False},
    {"id": "3", "text": "Continue Jarvis agent work", "done": False},
]


def test_voice_states_exist():
    names = {s.value for s in VoiceState}
    assert names >= {
        "IDLE",
        "LISTENING",
        "USER_SPEAKING",
        "FINALIZING",
        "PROCESSING",
        "ACTING",
        "TTS_SPEAKING",
        "BARGE_IN",
        "PAUSED",
        "ERROR",
    }


def test_echo_score_fragment_of_tts():
    tts = "It seems you might need assistance with that request."
    assert echo_score("It seems...", tts) >= 0.62
    assert echo_score("It seems you might need assistance", tts) >= 0.8


def test_self_speech_echo_discarded():
    session = VoiceSessionController()
    session.on_tts_start("It seems you might need assistance with that request.")
    event = session.admit("It seems you might need assistance with that request.", confidence=0.9, duration_s=1.4)
    assert not event.accepted
    assert event.discard_reason == DiscardReason.SELF_SPEECH_ECHO.value
    assert session.stats["echo"] >= 1
    frag = session.admit("It seems you might need assistance", confidence=0.9, duration_s=1.2, tts_active=True)
    assert not frag.accepted
    assert frag.decision in {"SELF_SPEECH_ECHO", "PARTIAL"}


def test_echo_window_after_tts_end():
    mem = TtsMemory(echo_window_s=0.55)
    mem.on_start("Your four tasks are review morning emails")
    mem.on_end()
    assert mem.in_echo_window()
    assert mem.is_echo("Your four tasks are review morning emails")
    time.sleep(0.05)
    assert mem.is_echo("your four tasks")


def test_echo_still_caught_after_stt_latency():
    """Whisper often returns 1–4 s after TTS ends. Text match must still fire."""
    session = VoiceSessionController()
    session.on_tts_start("That's your today's task list, review morning emails.")
    session.on_tts_end()
    time.sleep(1.2)
    event = session.admit("That's your today's task list", confidence=0.91, duration_s=2.0)
    assert not event.accepted
    assert event.discard_reason == DiscardReason.SELF_SPEECH_ECHO.value


def test_garbled_speaker_echo_discarded():
    session = VoiceSessionController()
    session.on_tts_start("Jarvis echo probe 573f8eee alpha zulu.")
    event = session.admit("Let's Echo Pro 5704.", confidence=0.9, duration_s=3.0, tts_active=True)
    assert not event.accepted
    assert event.discard_reason in {
        DiscardReason.SELF_SPEECH_ECHO.value,
        DiscardReason.DURING_TTS.value,
    }
    session = VoiceSessionController()
    session.on_tts_start("Done.")
    event = session.admit("Done.", confidence=0.99, duration_s=0.4)
    assert not event.accepted
    assert event.discard_reason == DiscardReason.SELF_SPEECH_ECHO.value


def test_barge_in_stop_during_tts():
    cancelled = []
    accepted = []
    session = VoiceSessionController(
        on_command=accepted.append,
        cancel_tts=lambda: cancelled.append(True),
    )
    session.on_tts_start("Your remaining tasks are review morning emails, check calendar priorities, continue Jarvis")
    event = session.admit("Stop.", confidence=0.95, duration_s=0.4)
    assert event.accepted
    assert event.barge_in
    assert cancelled
    assert accepted[0].text == "Stop."


def test_barge_in_open_chrome_during_tts():
    accepted = []
    session = VoiceSessionController(on_command=accepted.append, cancel_tts=lambda: None)
    session.on_tts_start("Your remaining tasks are review morning emails check calendar")
    event = session.admit("Open Chrome.", confidence=0.93, duration_s=0.8)
    assert event.accepted
    assert event.barge_in
    assert accepted[0].text == "Open Chrome."


def test_partial_not_admitted():
    session = VoiceSessionController(on_command=lambda e: (_ for _ in ()).throw(AssertionError("partial")))
    session.on_partial_transcript("Open Chrome and")
    event = session.admit("It seems...", confidence=0.8, duration_s=0.9)
    assert not event.accepted
    assert event.discard_reason == DiscardReason.PARTIAL.value


def test_quality_gate_filler_vs_stop():
    gate = CommandQualityGate()
    oh = gate.evaluate(VoiceEvent(text="Oh", confidence=0.9, duration_s=0.4))
    assert not oh.accepted
    assert oh.discard_reason == DiscardReason.FILLER.value
    stop = gate.evaluate(VoiceEvent(text="Stop.", confidence=0.7, duration_s=0.3))
    assert stop.accepted
    polo = gate.evaluate(VoiceEvent(text="Polo", confidence=0.8, duration_s=0.4))
    assert not polo.accepted
    good = gate.evaluate(VoiceEvent(text="Good.", confidence=0.8, duration_s=0.4))
    assert not good.accepted


def test_low_confidence_rejected():
    gate = CommandQualityGate()
    event = gate.evaluate(VoiceEvent(text="mark task something", confidence=0.1, duration_s=1.2))
    assert not event.accepted
    assert event.discard_reason == DiscardReason.LOW_CONFIDENCE.value


def test_duplicate_transcript_and_command_id():
    session = VoiceSessionController()
    a = session.admit("Open Chrome.", confidence=0.95, duration_s=0.8)
    b = session.admit("Open Chrome.", confidence=0.95, duration_s=0.8)
    assert a.accepted
    assert a.command_id
    assert not b.accepted
    assert b.discard_reason == DiscardReason.DUPLICATE.value
    assert b.command_id == a.command_id


def test_source_tagging():
    session = VoiceSessionController()
    event = session.admit("Stop.", confidence=0.9, duration_s=0.3)
    d = event.as_dict()
    assert d["source"] == VoiceSource.USER_VOICE.value
    assert d["isFinal"] is True
    assert "commandId" in d
    assert "echoScore" in d
    assert "audioSessionId" in d
    tts = session.admit("hello", source=VoiceSource.JARVIS_TTS.value, confidence=1.0, duration_s=1.0)
    assert not tts.accepted


def test_non_final_never_enters_agent():
    hits = []
    session = VoiceSessionController(on_command=hits.append)
    session.admit("Open Chrome", is_final=False, confidence=0.9, duration_s=0.8)
    assert hits == []


def test_cross_out_task_intent():
    intent = route_task_intent("Cross out review morning emails.", TASKS)
    assert intent is not None
    assert intent.action == "task.complete"
    assert intent.args.get("text") == "Review morning emails"


def test_fuzzy_morning_email_task():
    found = fuzzy_task_match(TASKS, "the morning email task")
    assert found["task"]["text"] == "Review morning emails"
    intent = route_task_intent("Check off the morning email task.", TASKS)
    assert intent.action == "task.complete"
    assert intent.args.get("text") == "Review morning emails"


def test_check_off_not_browser_check():
    r = FastCommandRouter()
    intent = r.route("Check off the morning email task.")
    assert intent is not None
    assert intent.action == "task.complete"
    assert r.route("What page am I on?").action == "browser.current_page"


def test_and_go_to_linkedin_followup():
    r = FastCommandRouter()
    intent = r.route("And go to LinkedIn.")
    assert intent is not None
    assert intent.action == "browser.open_and_goto"
    assert "linkedin" in (intent.args.get("url") or "")


def test_hud_cross_out_marks_done(tmp_path):
    store = DailyTaskStore(tmp_path / "daily_tasks.json")
    reply = hud_spoken_reply("Cross out review morning emails.", store)
    assert reply == "Done."
    done = [t for t in store.tasks if t.get("done")]
    assert any("morning email" in t["text"].lower() for t in done)
    assert hud_spoken_reply("open notepad", store) is None


def test_fast_router_open_chrome_preserved():
    r = FastCommandRouter()
    assert r.route("Open Chrome.").action == "browser.open"


def test_speech_filler_dropped_and_cancel():
    speech = SpeechOutput({"speech_rate": 160, "speech_volume": 0.1})
    try:
        speech.speak("On it.", priority="FILLER")
        assert list(speech._queue) == []
        speech.speak("Opening Chrome.", priority="TASK_STATUS")
        speech.speak("Done.", priority="USER_RESPONSE")
        time.sleep(0.05)
        with speech._lock:
            queued = list(speech._queue)
        # USER_RESPONSE should have dropped TASK_STATUS
        assert all(p != "TASK_STATUS" for p, _ in queued)
        speech.cancel_current()
        speech.clear_queue()
        assert list(speech._queue) == []
    finally:
        speech.shutdown()


def test_aec_documented_unavailable():
    status = VoiceSessionController().aec_status()
    assert status["AEC available"] == "no"
    assert status["AEC enabled"] == "no"
    assert "unavailable" in status["AEC unavailable"].lower() or status["AEC unavailable"].startswith("yes")
    assert "controlled full duplex" in status["mode"]


def test_twenty_turn_echo_stress():
    commands = []
    session = VoiceSessionController(on_command=commands.append)
    echoes = 0
    fillers = 0
    for i in range(20):
        phrase = f"Your remaining tasks are review morning emails item {i}."
        session.on_tts_start(phrase)
        ev = session.admit(phrase, confidence=0.92, duration_s=1.5)
        if not ev.accepted and ev.discard_reason == DiscardReason.SELF_SPEECH_ECHO.value:
            echoes += 1
        session.on_tts_end()
        frag = session.admit("It seems...", confidence=0.7, duration_s=0.6)
        if frag.discard_reason in {DiscardReason.SELF_SPEECH_ECHO.value, DiscardReason.PARTIAL.value}:
            echoes += 1
        oh = session.admit("Oh", confidence=0.8, duration_s=0.3)
        if oh.discard_reason == DiscardReason.FILLER.value:
            fillers += 1
        session.admit(f"Mark review morning emails complete round {i}", confidence=0.95, duration_s=1.2)
    assert echoes >= 20
    assert fillers == 20
    # Real user commands may be accepted; none of the echo phrases should be.
    for ev in commands:
        assert "it seems" not in ev.text.lower()
        assert ev.text.lower().strip(" .") not in {"oh", "good", "polo"}


def test_complex_command_is_one_final():
    hits = []
    session = VoiceSessionController(on_command=hits.append)
    text = "Open Chrome, go to Google, search for OpenAI and open the first relevant result."
    session.admit(text, confidence=0.94, duration_s=4.2)
    assert len(hits) == 1
    assert hits[0].text == text


def test_oh_does_not_call_planner(tmp_path):
    from src.agent.audit import AuditLog
    from src.agent.emergency import EmergencyStop
    from src.agent.orchestrator import AgentOrchestrator
    from src.agent.permissions import AutonomyMode, PermissionEngine
    from src.agent.task_store import TaskStore
    from src.agent.tools.base import ToolRegistry
    from tests.test_agent_phase1 import MockProvider

    store = TaskStore(tmp_path / "tasks.db")
    perms = PermissionEngine(autonomy_mode=AutonomyMode.ASSIST, computer_control_enabled=True)
    registry = ToolRegistry(perms, AuditLog(), EmergencyStop())
    orch = AgentOrchestrator(
        MockProvider([]),
        registry,
        store,
        perms,
        emergency_stop=EmergencyStop(),
        hud_tasks_path=str(tmp_path / "daily_tasks.json"),
    )
    result = orch.handle_user_message("Oh")
    assert result.get("discard") == "FILLER"
    assert orch.planner_calls == 0
    result2 = orch.handle_user_message("Cross out review morning emails.")
    assert result2.get("message") == "Done."
    assert orch.planner_calls == 0
    result3 = orch.handle_user_message("What page am I on?")
    assert result3.get("fast") is True
    assert orch.planner_calls == 0


def test_cross_out_during_task_list_tts_is_user_barge_in():
    """Shared task names must not discard a new imperative command."""
    tts = (
        "You have 3 tasks remaining: Review morning emails, "
        "Check calendar and priorities, Continue Jarvis agent work."
    )
    cancelled = []
    accepted = []
    session = VoiceSessionController(on_command=accepted.append, cancel_tts=lambda: cancelled.append(True))
    session.on_tts_start(tts)
    event = session.admit("Cross out review morning emails.", confidence=0.64, duration_s=1.8, tts_active=True)
    assert event.accepted, (
        f"false echo discard: decision={event.decision} reason={event.decision_reason} "
        f"sim={event.tts_similarity:.2f} novel={event.novel_token_ratio:.2f}"
    )
    assert event.decision == "USER_BARGE_IN"
    assert event.recognized_intent == "task.complete"
    assert event.imperative_detected
    assert event.novel_token_ratio > 0
    assert cancelled
    assert accepted[0].text == "Cross out review morning emails."


def test_barge_in_task_and_fast_commands_during_tts():
    tts = (
        "You have 3 tasks remaining: Review morning emails, "
        "Check calendar and priorities, Continue Jarvis agent work."
    )
    session = VoiceSessionController()
    session.on_tts_start(tts)
    cases = [
        ("Check off the calendar task.", "task.complete"),
        ("Mark continue Jarvis agent work done.", "task.complete"),
        ("Open Chrome.", "browser.open"),
        ("Stop.", ""),
    ]
    for text, intent in cases:
        ev = session.admit(text, confidence=0.9, duration_s=1.0, tts_active=True)
        assert ev.accepted, f"{text!r} discarded as {ev.decision}/{ev.decision_reason}"
        assert ev.decision == "USER_BARGE_IN"
        if intent:
            assert ev.recognized_intent == intent, f"{text!r} intent={ev.recognized_intent}"


def test_exact_and_garbled_tts_still_echo():
    session = VoiceSessionController()
    tts = "That's what you're talking about."
    session.on_tts_start(tts)
    exact = session.admit(tts, confidence=0.95, duration_s=1.5, tts_active=True)
    assert not exact.accepted
    assert exact.decision == "SELF_SPEECH_ECHO"
    session.on_tts_start(
        "You have 3 tasks remaining: Review morning emails, Check calendar and priorities, Continue Jarvis agent work."
    )
    frag = session.admit("Review morning emails", confidence=0.9, duration_s=1.0, tts_active=True)
    assert not frag.accepted
    assert frag.decision == "SELF_SPEECH_ECHO"
    garbled = session.admit("Let's Echo Pro 5704.", confidence=0.9, duration_s=2.0, tts_active=True)
    assert not garbled.accepted


def test_done_after_task_complete_is_echo():
    session = VoiceSessionController()
    session.on_tts_start("Done.")
    ev = session.admit("Done.", confidence=0.99, duration_s=0.35, tts_active=True)
    assert not ev.accepted
    assert ev.decision == "SELF_SPEECH_ECHO"


def test_whisper_hallucination_during_tts_is_not_a_command():
    session = VoiceSessionController()
    session.on_tts_start("Jarvis echo probe alpha zulu.")
    ev = session.admit("I'll see you guys in the next video. Bye.", confidence=0.9, duration_s=2.0, tts_active=True)
    assert not ev.accepted
    assert ev.decision == "SELF_SPEECH_ECHO"
    """Do not whitelist command phrases that JARVIS itself just spoke."""
    session = VoiceSessionController()
    session.on_tts_start("Cross out review morning emails.")
    ev = session.admit("Cross out review morning emails.", confidence=0.9, duration_s=1.5, tts_active=True)
    assert not ev.accepted
    assert ev.decision == "SELF_SPEECH_ECHO"


def test_echo_classifier_telemetry_fields():
    session = VoiceSessionController()
    session.on_tts_start("You have 3 tasks remaining: Review morning emails.")
    ev = session.admit("Cross out review morning emails.", confidence=0.64, duration_s=1.6, tts_active=True)
    d = ev.as_dict()
    for key in (
        "tts_similarity",
        "novel_token_ratio",
        "recognized_intent",
        "imperative_detected",
        "duringTts",
        "decision",
        "decision_reason",
    ):
        assert key in d
    assert d["decision"] == "USER_BARGE_IN"


def test_twenty_alternating_speaker_user_turns():
    accepted = []
    session = VoiceSessionController(on_command=accepted.append)
    tts_list = (
        "You have 3 tasks remaining: Review morning emails, "
        "Check calendar and priorities, Continue Jarvis agent work."
    )
    user_cmds = [
        "Cross out review morning emails.",
        "Check off the calendar task.",
        "Mark continue Jarvis agent work done.",
        "Open Chrome.",
        "Stop.",
    ]
    false_discard = 0
    false_accept = 0
    barge_ok = 0
    for i in range(20):
        session.gate._recent.clear()
        session.executed_ids.clear()
        session.on_tts_start(tts_list)
        echo = session.admit(tts_list, confidence=0.9, duration_s=2.0, tts_active=True)
        if echo.accepted:
            false_accept += 1
        garbled = session.admit(
            "You have three tasks remaining review morning",
            confidence=0.7,
            duration_s=1.4,
            tts_active=True,
        )
        if garbled.accepted:
            false_accept += 1
        cmd = user_cmds[i % len(user_cmds)]
        user = session.admit(cmd, confidence=0.64, duration_s=1.5, tts_active=True)
        if not user.accepted:
            false_discard += 1
        elif user.decision == "USER_BARGE_IN":
            barge_ok += 1
        session.on_tts_end()
        session.on_tts_start("Done.")
        done = session.admit("Done.", confidence=0.95, duration_s=0.4, tts_active=True)
        if done.accepted:
            false_accept += 1
        session.on_tts_end()
    assert false_discard == 0, f"real user commands falsely discarded={false_discard}"
    assert false_accept == 0, f"speaker echoes falsely accepted={false_accept}"
    assert barge_ok == 20
    assert all(e.text.lower().strip(" .") != "done" for e in accepted)


def test_own_replies_are_echo_not_commands():
    session = VoiceSessionController()
    for spoken in ("Done.", "Undone.", "See you later.", "You're welcome."):
        session.gate._recent.clear()
        session.on_tts_start(spoken)
        event = session.admit(spoken, confidence=0.95, duration_s=0.6, tts_active=True)
        assert not event.accepted, f"{spoken!r} accepted as {event.decision} intent={event.recognized_intent}"
        assert event.decision == DiscardReason.SELF_SPEECH_ECHO.value
        session.on_tts_end()


def test_undo_barge_in_during_task_list_tts():
    tts = (
        "You have 3 tasks remaining: Review morning emails, "
        "Check calendar and priorities, Continue Jarvis agent work."
    )
    session = VoiceSessionController()
    session.on_tts_start(tts)
    event = session.admit("Undo the morning email task.", confidence=0.7, duration_s=1.6, tts_active=True)
    assert event.accepted
    assert event.decision == "USER_BARGE_IN"
    assert event.recognized_intent == "task.uncomplete"


def test_jarvis_add_a_task_not_echo_of_time_tts():
    session = VoiceSessionController()
    session.on_tts_start("Jarvis online. It is 4:54 PM.")
    session.on_tts_end()
    session.gate.tts.ended_at = time.time() - 3.0
    event = session.admit("Jarvis, add a task.", confidence=0.85, duration_s=1.4, tts_active=False)
    assert event.accepted, f"{event.decision} {event.decision_reason} intent={event.recognized_intent}"
    assert event.decision in {"USER_COMMAND", "USER_BARGE_IN"}
    assert event.decision != DiscardReason.SELF_SPEECH_ECHO.value
    assert event.recognized_intent == "task.add"
    assert event.normalized_payload
    assert "add a task" in event.normalized_payload
    assert "task_text" in event.missing_arguments
    d = event.as_dict()
    for key in (
        "original_transcript",
        "assistant_prefix_removed",
        "normalized_payload",
        "recognized_intent",
        "missing_arguments",
        "raw_tts_similarity",
        "payload_tts_similarity",
        "novel_token_ratio",
        "decision",
        "decision_reason",
    ):
        assert key in d
    assert d["payload_tts_similarity"] < d["raw_tts_similarity"] or d["payload_tts_similarity"] < 0.7


def test_own_add_a_task_tts_still_echo():
    session = VoiceSessionController()
    session.on_tts_start("Jarvis, add a task.")
    event = session.admit("Jarvis, add a task.", confidence=0.9, duration_s=1.2, tts_active=True)
    assert not event.accepted
    assert event.decision == DiscardReason.SELF_SPEECH_ECHO.value


def test_clarification_and_added_are_echo():
    session = VoiceSessionController()
    session.on_tts_start("What task should I add?")
    ev = session.admit("What task should I add?", confidence=0.95, duration_s=1.0, tts_active=True)
    assert not ev.accepted
    assert ev.decision == DiscardReason.SELF_SPEECH_ECHO.value
    session.on_tts_end()
    session.on_tts_start("Added.")
    ev2 = session.admit("Added.", confidence=0.95, duration_s=0.4, tts_active=True)
    assert not ev2.accepted
    assert ev2.decision == DiscardReason.SELF_SPEECH_ECHO.value


TTS_STATUS = "Jarvis online. It is 5:31 PM. You have 2 tasks remaining today."


def test_had_a_task_is_not_zero_novelty_against_status_tts():
    novelty = token_novelty("i had a task", TTS_STATUS)
    assert "had" in novelty["novel_tokens"]
    assert "i" in novelty["novel_tokens"]
    assert novelty["novel_token_ratio"] > 0
    assert set(novelty["payload_tokens"]) >= {"i", "had", "a", "task"}
    assert "tasks" in novelty["tts_tokens"] or "task" in novelty["tts_tokens"]


def test_correct_command_stt_add_a_task_variants():
    for spoken, addressed in (
        ("i had a task", True),
        ("had a task", True),
        ("at a task", True),
        ("ad a task", True),
        ("I had a task", False),
        ("had a task", False),
    ):
        fixed, labels = correct_command_stt(spoken, addressed=addressed)
        assert fixed == "add a task", spoken
        assert labels


def test_correct_command_stt_does_not_rewrite_narrative():
    for spoken in (
        "I had a task yesterday",
        "I had three meetings",
        "He had a task assigned to him",
        "I had a meeting yesterday",
    ):
        fixed, labels = correct_command_stt(spoken, addressed=True)
        assert "add a task" not in fixed, spoken
        assert not labels, spoken


def test_hey_jarvis_i_had_a_task_is_user_command():
    session = VoiceSessionController()
    session.on_tts_start(TTS_STATUS)
    session.on_tts_end()
    session.gate.tts.ended_at = time.time() - 3.0
    event = session.admit("Hey Jarvis, I had a task.", confidence=0.85, duration_s=1.5, tts_active=False)
    assert event.accepted, f"{event.decision} {event.decision_reason} intent={event.recognized_intent}"
    assert event.decision != DiscardReason.SELF_SPEECH_ECHO.value
    assert event.recognized_intent == "task.add"
    assert event.normalized_payload == "add a task"
    assert event.command_clause == "add a task"
    assert event.pre_correction_payload in {"i had a task", "add a task"}
    assert "task_text" in event.missing_arguments
    assert event.novel_token_ratio > 0
    d = event.as_dict()
    for key in (
        "original_transcript",
        "assistant_prefix_removed",
        "pre_correction_payload",
        "corrections_applied",
        "normalized_payload",
        "recognized_intent",
        "missing_arguments",
        "payload_tokens",
        "tts_tokens",
        "shared_tokens",
        "novel_tokens",
        "raw_tts_similarity",
        "payload_tts_similarity",
        "novel_token_ratio",
        "decision",
        "decision_reason",
    ):
        assert key in d, key


def test_whisper_add_task_variants_not_echo():
    session = VoiceSessionController()
    session.on_tts_start(TTS_STATUS)
    session.on_tts_end()
    session.gate.tts.ended_at = time.time() - 3.0
    for spoken in (
        "Hey Jarvis, add a task.",
        "Jarvis, add a task.",
        "Add a task.",
        "Hey Jarvis, I had a task.",
        "had a task",
        "at a task",
    ):
        session.gate._recent.clear()
        session.executed_ids.clear()
        event = session.admit(spoken, confidence=0.84, duration_s=1.3, tts_active=False)
        assert event.accepted, f"{spoken!r} → {event.decision}/{event.decision_reason}"
        assert event.recognized_intent == "task.add", spoken
        assert "task_text" in event.missing_arguments


def test_hey_drivers_add_a_task_is_user_command():
    session = VoiceSessionController()
    session.on_tts_start(TTS_STATUS)
    session.on_tts_end()
    session.gate.tts.ended_at = time.time() - 3.0
    for spoken in (
        "Hey, drivers, add a task.",
        "Hey Jervis, add a task.",
        "Um Jarvis, add a task.",
        "Could you add a task?",
    ):
        session.gate._recent.clear()
        session.executed_ids.clear()
        event = session.admit(spoken, confidence=0.84, duration_s=1.4, tts_active=False)
        assert event.accepted, f"{spoken!r} → {event.decision}/{event.decision_reason}"
        assert event.recognized_intent == "task.add", spoken
        assert event.command_clause == "add a task", spoken
        assert "task_text" in event.missing_arguments
        d = event.as_dict()
        for key in (
            "original_transcript",
            "wake_prefix_candidate",
            "wake_prefix_confidence",
            "command_clause",
            "command_clause_confidence",
            "normalized_payload",
            "recognized_intent",
            "missing_arguments",
        ):
            assert key in d, key


def test_ask_then_add_another_task_is_user_command():
    session = VoiceSessionController()
    session.on_tts_start(TTS_STATUS)
    session.on_tts_end()
    session.gate.tts.ended_at = time.time() - 3.0
    event = session.admit("Ask another task. Add another task.", confidence=0.84, duration_s=2.0, tts_active=False)
    assert event.accepted, f"{event.decision}/{event.decision_reason}"
    assert event.recognized_intent == "task.add"
    assert event.selected_command_clause == "add another task"
    assert event.utterance_segments == ["ask another task", "add another task"]
    assert "task_text" in event.missing_arguments


def test_own_add_a_task_tts_still_echo_after_correction():
    session = VoiceSessionController()
    session.on_tts_start("Jarvis, add a task.")
    event = session.admit("Hey Jarvis, I had a task.", confidence=0.9, duration_s=1.2, tts_active=True)
    assert not event.accepted
    assert event.decision == DiscardReason.SELF_SPEECH_ECHO.value


def test_another_task_after_added_tts_is_command():
    from src.agent.local_intent import reset_nlu_context
    from src.agent.turn_context import note_task_added

    reset_nlu_context()
    note_task_added("Call Greg tomorrow")
    session = VoiceSessionController()
    session.on_tts_start("Added.")
    session.on_tts_end()
    session.gate.tts.ended_at = time.time() - 3.0
    event = session.admit("Another task.", confidence=0.9, duration_s=0.6, tts_active=False)
    assert event.accepted, f"{event.decision}/{event.decision_reason}"
    assert event.recognized_intent == "task.add"
    assert event.decision != DiscardReason.SELF_SPEECH_ECHO.value


