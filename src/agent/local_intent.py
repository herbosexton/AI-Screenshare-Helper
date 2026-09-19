"""Local intent resolver. Planner is last resort, not a regex-miss fallback."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

from src.agent.router import FastIntent
from src.agent.transcript import TranscriptNormalizer
from src.agent.turn_context import (
    TURN,
    note_jarvis_reply,
    note_task_added,
    note_user_intent,
    reset_turn_context,
    sync_pending_from_nlu,
)
from src.agent.voice_echo import normalize_speech, peel_assistant_echo, strip_assistant_prefix
from src.agent.voice_tasks import _task_query, fuzzy_task_match


EXECUTE_SAFE = 0.72
EXECUTE_DEFAULT = 0.82
EXECUTE_DESTRUCTIVE = 0.90
CLARIFY_MIN = 0.60

SAFE_INTENTS = {
    "task.list",
    "browser.current_page",
    "browser.current_url",
    "computer.active_window",
    "browser.scroll",
}

DESTRUCTIVE_INTENTS = {
    "task.delete",
    "computer.close_window",
    "browser.close",
}

_FILLER = {
    "again",
    "please",
    "now",
    "just",
    "really",
    "today",
    "the",
    "a",
    "an",
    "my",
    "me",
    "to",
    "do",
    "i",
    "is",
    "on",
    "of",
    "for",
    "and",
}

_LIST_CANONICAL = (
    "what is on my task list",
    "what is on my task list again",
    "what is on my list",
    "what is on my list again",
    "what are my tasks",
    "what tasks do i have",
    "what is left today",
    "what is left",
    "whats left",
    "what do i have left",
    "what do i have left today",
    "what do i need to do today",
    "what do i need to do",
    "what do i have to do today",
    "what are the three tasks i have to do today",
    "what are the tasks i have to do today",
    "show me todays tasks",
    "show me today tasks",
    "read my task list",
    "read my todo list",
    "read my list",
    "what is on my todo list",
    "what is on todays list",
    "what is on today list",
    "what are my tasks today",
    "show my tasks",
    "list my tasks",
    "read me my tasks",
)

_COMPLETE_VERBS = (
    "cross out",
    "check off",
    "check it off",
    "tick off",
    "cross off",
    "cross it out",
    "mark done",
    "mark it done",
    "mark complete",
    "mark it complete",
    "complete",
    "finish",
)
_UNCOMPLETE_VERBS = (
    "undo",
    "uncheck",
    "reopen",
    "put back",
    "put it back",
    "put that back",
    "mark incomplete",
    "incomplete",
    "not done",
    "didnt finish",
    "did not finish",
)
_COMPLEX_HINTS = (
    "explain",
    "why",
    "how does",
    "attention",
    "transformer",
    "quadratic",
    "because",
    "compare",
    "analyze",
)
_SOCIAL = (
    (("thanks", "thank you", "thank you jarvis", "thanks jarvis", "appreciate it"), "social.thanks", "You're welcome."),
    (("see ya", "see you", "see you later", "see ya later", "bye", "goodbye", "good bye", "talk to you later"), "social.goodbye", "See you later."),
    (("good morning", "good afternoon", "good evening", "hey jarvis", "hi jarvis", "hello jarvis", "hello", "hey", "hi"), "social.greeting", "Hello."),
    (("okay", "ok", "got it", "sounds good", "alright", "all right", "cool"), "social.ack", ""),
    (("never mind", "nevermind", "forget it", "forget that"), "social.never_mind", "Okay."),
    (("yes", "yep", "yeah", "yup", "affirmative", "do it"), "confirm.yes", "Okay."),
    (("no", "nope", "nah", "negative"), "confirm.no", "Okay."),
)

_ANOTHER_TASK = re.compile(
    r"^(please )?(add )?(another( one)?|one more)( task| todo| item)?$",
    re.I,
)
_CLARIFY_UNKNOWN = {
    "i dont know",
    "i don't know",
    "i do not know",
    "i dont know the task",
    "i don't know the task",
    "i do not know the task",
    "im not sure",
    "i am not sure",
    "i'm not sure",
    "no idea",
    "dunno",
    "i dunno",
}
_CLARIFY_CANCEL = {
    "thats it",
    "that's it",
    "that is it",
    "thats all",
    "that's all",
    "that is all",
    "im done",
    "i am done",
    "i'm done",
    "never mind",
    "nevermind",
    "forget it",
    "forget that",
}
_CLARIFY_LIST = {
    "which ones",
    "which one",
    "what are the options",
    "what are my options",
    "what are the tasks",
    "list them",
    "list the tasks",
}
_CLARIFY_REPEAT = {
    "what did you say",
    "say that again",
    "repeat that",
    "come again",
}
_UNKNOWN_RE = re.compile(
    r"^(i )?(dont know|do not know|dunno|am not sure|im not sure)(\b.*)?$",
    re.I,
)
_ADD_TITLE_NOISE = {
    "thats true",
    "that is true",
    "thats true by the way",
    "that is true by the way",
    "by the way",
    "anyway",
    "i see",
    "i know",
    "right",
    "sure",
    "maybe",
    "wait",
    "hold on",
    "hmm",
    "huh",
    "yeah",
    "yep",
    "yup",
    "ok",
    "okay",
}
_CALL_GREG_STT = re.compile(
    r"^(paul|pall|pol|called|call) (good|great|greg) (tomorrow|today)$",
    re.I,
)


@dataclass
class NluContext:
    last_task_id: str = ""
    last_task_text: str = ""
    last_intent: str = ""
    pending_candidates: list[str] = field(default_factory=list)
    pending_intent: str = ""
    missing_argument: str = ""
    candidate_entities: list[str] = field(default_factory=list)
    pending_created_at: float = 0.0
    last_key: tuple = ()
    last_resolved: Optional["ResolvedIntent"] = None


CONTEXT = NluContext()
_RESOLVE_CACHE: dict[tuple, "ResolvedIntent"] = {}


def reset_nlu_context() -> None:
    CONTEXT.last_task_id = ""
    CONTEXT.last_task_text = ""
    CONTEXT.last_intent = ""
    CONTEXT.pending_candidates = []
    CONTEXT.pending_intent = ""
    CONTEXT.missing_argument = ""
    CONTEXT.candidate_entities = []
    CONTEXT.pending_created_at = 0.0
    CONTEXT.last_key = ()
    CONTEXT.last_resolved = None
    _RESOLVE_CACHE.clear()
    reset_turn_context()


def clear_pending_clarification() -> None:
    CONTEXT.pending_candidates = []
    CONTEXT.pending_intent = ""
    CONTEXT.missing_argument = ""
    CONTEXT.candidate_entities = []
    CONTEXT.pending_created_at = 0.0
    TURN.clear_pending()


@dataclass
class ResolvedIntent:
    intent: str = ""
    intent_confidence: float = 0.0
    normalized_transcript: str = ""
    original_transcript: str = ""
    # The whole utterance with speech corrections applied. `normalized_transcript` is only
    # the clause the local resolver kept, so a multi-step goal loses everything after the
    # first comma; `original_transcript` keeps the goal but not the corrections.
    corrected_transcript: str = ""
    matched_pattern: str = ""
    entity: str = ""
    entity_confidence: float = 0.0
    entity_id: str = ""
    corrections_applied: list[str] = field(default_factory=list)
    args: dict[str, Any] = field(default_factory=dict)
    route: str = "planner"
    planner_fallback_reason: str = ""
    safe: bool = False
    reply: str = ""
    intent_resolution_count: int = 1
    stt_confidence: float = 1.0
    arguments_valid: bool = True
    missing_arguments: list[str] = field(default_factory=list)
    conflicting_intents: list[str] = field(default_factory=list)
    semantic_coherence: float = 1.0
    execution_decision: str = ""
    decision_reason: str = ""
    wake_prefix_candidate: str = ""
    wake_prefix_confidence: float = 0.0
    command_clause: str = ""
    command_clause_confidence: float = 0.0
    utterance_segments: list[str] = field(default_factory=list)
    candidate_commands: list[str] = field(default_factory=list)
    candidate_intents: list[str] = field(default_factory=list)
    selected_command_clause: str = ""
    selection_reason: str = ""
    superseded_clauses: list[str] = field(default_factory=list)
    pending_context_before: dict[str, Any] = field(default_factory=dict)
    pending_context_after: dict[str, Any] = field(default_factory=dict)
    contextual_intent: str = ""
    context_resolution_reason: str = ""

    def to_fast_intent(self) -> Optional[FastIntent]:
        if not self.intent:
            return None
        args = dict(self.args)
        if self.entity_id:
            args.setdefault("task_id", self.entity_id)
        if self.entity:
            args.setdefault("text", self.entity)
        return FastIntent(self.intent, args=args, confidence=self.intent_confidence)


class LocalIntentResolver:
    def __init__(self, normalizer: Optional[TranscriptNormalizer] = None):
        self.normalizer = normalizer or TranscriptNormalizer()

    def resolve(
        self,
        text: str,
        *,
        tasks: Optional[list[dict[str, Any]]] = None,
        hud_active: bool = True,
    ) -> ResolvedIntent:
        t0 = time.perf_counter()
        items = list(tasks or [])
        from src.agent.command_clause import extract_command_clause

        extracted = extract_command_clause(text)
        payload, _prefix = strip_assistant_prefix(text)
        work = extracted.command_clause or payload or (text or "").strip()
        normed = self.normalizer.normalize(work, tasks=items, hud_active=hud_active)
        n = normed.normalized
        # Correct the whole utterance as well. A goal like "go to X, then switch back to Y"
        # is clipped to its first clause above, and anything a planner later needs to read —
        # a misheard site name in the last clause — would never have been repaired.
        # From `text`, not `payload`: stripping the assistant prefix also drops everything
        # past the first clause, which is exactly the part being rescued here.
        entire = (text or "").strip()
        whole = (
            self.normalizer.normalize(entire, tasks=items, hud_active=hud_active)
            if entire and entire != work
            else normed
        )
        stripped_n, _ = strip_assistant_prefix(n)
        if stripped_n:
            n = stripped_n
        if extracted.command_clause:
            n = extracted.command_clause
        before = TURN.snapshot()
        result = ResolvedIntent(
            original_transcript=(text or "").strip(),
            normalized_transcript=n,
            corrected_transcript=whole.corrected or (text or "").strip(),
            corrections_applied=sorted(set(normed.corrections) | set(whole.corrections)),
            pending_context_before=before,
            wake_prefix_candidate=extracted.wake_prefix_candidate,
            wake_prefix_confidence=extracted.wake_prefix_confidence,
            command_clause=extracted.command_clause,
            command_clause_confidence=extracted.command_clause_confidence,
            utterance_segments=list(extracted.utterance_segments),
            candidate_commands=list(extracted.candidate_commands),
            candidate_intents=list(extracted.candidate_intents),
            selected_command_clause=extracted.selected_command_clause or extracted.command_clause,
            selection_reason=extracted.selection_reason,
            superseded_clauses=list(extracted.superseded_clauses),
        )
        if not n:
            result.route = "unresolved"
            result.planner_fallback_reason = "NO_LOCAL_INTENT"
            return result

        contextual = self._score_contextual(n)
        if contextual:
            result.intent = contextual["intent"]
            result.intent_confidence = contextual["confidence"]
            result.matched_pattern = contextual.get("pattern") or "contextual follow-up"
            result.reply = contextual.get("reply") or ""
            result.args = contextual.get("args") or {}
            result.safe = True
            result.contextual_intent = contextual["intent"]
            result.context_resolution_reason = contextual.get("reason") or ""
            result.missing_arguments = list(contextual.get("missing") or [])
            if contextual.get("clear_pending"):
                clear_pending_clarification()
            self._route(result)
            if contextual["intent"] == "task.add" and contextual.get("missing"):
                result.route = "clarify"
                result.planner_fallback_reason = "AMBIGUOUS_INTENT"
            result.pending_context_after = TURN.snapshot()
            self._log(result, t0)
            return result

        yesno = self._score_yes_no(n)
        if yesno and (CONTEXT.pending_candidates or CONTEXT.pending_intent):
            result.intent = yesno
            result.intent_confidence = 0.95
            result.matched_pattern = "confirm pending"
            result.safe = True
            result.route = "clarify"
            result.planner_fallback_reason = "AMBIGUOUS_INTENT"
            names = CONTEXT.pending_candidates or CONTEXT.candidate_entities
            result.reply = f"Which task — {', '.join(names[:3])}?" if names else "Which task do you mean?"
            result.args = {"candidates": list(names)}
            self._log(result, t0)
            return result

        social = self._score_social(n)
        if social:
            result.intent = social[0]
            result.intent_confidence = 0.99
            result.matched_pattern = social[0]
            result.reply = social[1]
            result.safe = True
            result.route = "fast"
            if result.intent == "social.never_mind":
                clear_pending_clarification()
            self._log(result, t0)
            return result

        pending_fill = self._fill_pending_entity(n, items, original=work)
        if pending_fill:
            result.intent = pending_fill["intent"]
            result.intent_confidence = pending_fill["confidence"]
            result.matched_pattern = "pending entity fill"
            result.entity = pending_fill.get("entity") or ""
            result.entity_id = pending_fill.get("entity_id") or ""
            result.entity_confidence = pending_fill.get("entity_confidence") or 0.0
            result.args = pending_fill.get("args") or {}
            result.safe = True
            if pending_fill.get("reask") or (result.intent == "task.add" and not result.entity):
                result.reply = "What task should I add?"
                result.missing_arguments = ["task_text"]
                result.contextual_intent = "task.add"
                result.context_resolution_reason = "pending_add_reask"
                result.route = "clarify"
                result.planner_fallback_reason = "AMBIGUOUS_INTENT"
            else:
                self._route(result)
            self._log(result, t0)
            return result

        if any(h in n for h in _COMPLEX_HINTS) and len(n.split()) >= 8:
            if CONTEXT.pending_intent == "task.add" or TURN.pending_intent == "task.add":
                pass
            else:
                result.planner_fallback_reason = "COMPLEX_GOAL"

        list_hit = self._score_task_list(n)
        if list_hit and not _is_add_command(n) and not _is_leading_complete(n):
            result.intent = "task.list"
            result.intent_confidence = list_hit[0]
            result.matched_pattern = list_hit[1]
            result.safe = True
            self._route(result)
            self._log(result, t0)
            return result

        result.conflicting_intents = _action_signals(n)

        add = self._score_add(n, work)
        if add:
            result.intent = add["intent"]
            result.intent_confidence = add["confidence"]
            result.matched_pattern = add.get("pattern") or ""
            result.entity = add.get("entity") or ""
            result.entity_confidence = add.get("entity_confidence") or 0.0
            result.args = add.get("args") or {}
            result.safe = True
            self._route(result)
            self._log(result, t0)
            return result

        uncomplete = self._score_uncomplete(n, work, items)
        if uncomplete:
            result.intent = uncomplete["intent"]
            result.intent_confidence = uncomplete["confidence"]
            result.matched_pattern = uncomplete.get("pattern") or ""
            result.entity = uncomplete.get("entity") or ""
            result.entity_id = uncomplete.get("entity_id") or ""
            result.entity_confidence = uncomplete.get("entity_confidence") or 0.0
            result.args = uncomplete.get("args") or {}
            result.safe = True
            self._route(result)
            self._log(result, t0)
            return result

        complete = self._score_complete(n, work, items)
        if complete:
            result.intent = complete["intent"]
            result.intent_confidence = complete["confidence"]
            result.matched_pattern = complete.get("pattern") or ""
            result.entity = complete.get("entity") or ""
            result.entity_id = complete.get("entity_id") or ""
            result.entity_confidence = complete.get("entity_confidence") or 0.0
            result.args = complete.get("args") or {}
            result.safe = result.intent not in DESTRUCTIVE_INTENTS
            self._route(result)
            self._log(result, t0)
            return result

        delete = self._score_delete(n, work, items)
        if delete:
            result.intent = delete["intent"]
            result.intent_confidence = delete["confidence"]
            result.matched_pattern = delete.get("pattern") or ""
            result.entity = delete.get("entity") or ""
            result.entity_id = delete.get("entity_id") or ""
            result.entity_confidence = delete.get("entity_confidence") or 0.0
            result.args = delete.get("args") or {}
            result.safe = False
            self._route(result)
            self._log(result, t0)
            return result

        from src.agent.voice_tasks import _DELETE

        raw = work
        m = _DELETE.search(raw)
        if m:
            q = m.group(1).strip()
            result.intent = "task.delete"
            result.intent_confidence = 0.9
            result.args = {"query": q}
            result.matched_pattern = "remove/delete task"
            if items:
                found = fuzzy_task_match(items, q)
                if found.get("ambiguous"):
                    result.intent = "task.ambiguous"
                    result.intent_confidence = 0.7
                    names = [c.get("text") for c in found["candidates"] if c.get("text")]
                    result.args = {"candidates": names, "query": q}
                elif found.get("task"):
                    result.entity = found["task"].get("text") or ""
                    result.entity_id = str(found["task"].get("id") or "")
                    result.entity_confidence = float(found.get("score") or 0.86)
            self._route(result)
            self._log(result, t0)
            return result

        recovered = self._recover_known_clause(extracted, items, work)
        if recovered:
            result.intent = recovered["intent"]
            result.intent_confidence = recovered["confidence"]
            result.matched_pattern = recovered.get("pattern") or "known command clause"
            result.entity = recovered.get("entity") or ""
            result.entity_id = recovered.get("entity_id") or ""
            result.entity_confidence = recovered.get("entity_confidence") or 0.0
            result.args = recovered.get("args") or {}
            result.command_clause = recovered.get("clause") or result.command_clause
            result.command_clause_confidence = recovered.get("clause_confidence") or result.command_clause_confidence
            result.normalized_transcript = result.command_clause or result.normalized_transcript
            result.safe = result.intent not in DESTRUCTIVE_INTENTS
            self._route(result)
            self._log(result, t0)
            return result

        if CONTEXT.pending_intent == "task.add" or TURN.pending_intent == "task.add":
            result.intent = "task.add"
            result.intent_confidence = 0.88
            result.matched_pattern = "pending add reask"
            result.reply = "What task should I add?"
            result.safe = True
            result.missing_arguments = ["task_text"]
            result.contextual_intent = "task.add"
            result.context_resolution_reason = "pending_add_reask"
            result.route = "clarify"
            result.planner_fallback_reason = "AMBIGUOUS_INTENT"
            result.pending_context_after = TURN.snapshot()
            self._log(result, t0)
            return result

        if result.planner_fallback_reason == "COMPLEX_GOAL":
            result.route = "unresolved"
            self._log(result, t0)
            return result
        result.route = "unresolved"
        result.planner_fallback_reason = (
            "MULTI_STEP_REASONING_REQUIRED" if re.search(r"\b(and then|then|after that)\b", n) else "NO_LOCAL_INTENT"
        )
        self._log(result, t0)
        return result

    def _recover_known_clause(self, extracted, items: list[dict[str, Any]], work: str) -> Optional[dict[str, Any]]:
        """Last local check before NO_LOCAL_INTENT. Deterministic, not an LLM rewrite."""
        clause = (getattr(extracted, "command_clause", "") or "").strip()
        if not clause:
            from src.agent.command_clause import extract_command_clause

            extracted = extract_command_clause(work)
            clause = extracted.command_clause
        if not clause:
            from src.agent.command_clause import extract_command_clause, segment_utterance

            for seg in segment_utterance(work):
                extracted = extract_command_clause(seg)
                if extracted.command_clause:
                    clause = extracted.command_clause
                    break
        if not clause:
            return None
        add = self._score_add(clause, clause)
        if add:
            add["clause"] = clause
            add["clause_confidence"] = getattr(extracted, "command_clause_confidence", 0.9)
            add["pattern"] = add.get("pattern") or "known command clause"
            return add
        complete = self._score_complete(clause, clause, items)
        if complete:
            complete["clause"] = clause
            complete["clause_confidence"] = getattr(extracted, "command_clause_confidence", 0.9)
            return complete
        delete = self._score_delete(clause, clause, items)
        if delete:
            delete["clause"] = clause
            delete["clause_confidence"] = getattr(extracted, "command_clause_confidence", 0.9)
            return delete
        uncomplete = self._score_uncomplete(clause, clause, items)
        if uncomplete:
            uncomplete["clause"] = clause
            uncomplete["clause_confidence"] = getattr(extracted, "command_clause_confidence", 0.9)
            return uncomplete
        list_hit = self._score_task_list(clause)
        if list_hit:
            return {
                "intent": "task.list",
                "confidence": list_hit[0],
                "pattern": list_hit[1],
                "clause": clause,
                "clause_confidence": getattr(extracted, "command_clause_confidence", 0.9),
            }
        return None

    def _route(self, result: ResolvedIntent) -> None:
        conf = result.intent_confidence
        threshold = EXECUTE_DEFAULT
        if result.intent in SAFE_INTENTS:
            threshold = EXECUTE_SAFE
        if result.intent in DESTRUCTIVE_INTENTS:
            threshold = EXECUTE_DESTRUCTIVE
        if result.intent == "task.ambiguous" or (
            result.intent == "task.complete" and result.args.get("candidates")
        ):
            result.route = "clarify"
            result.planner_fallback_reason = "AMBIGUOUS_INTENT"
            return
        if (result.intent or "").startswith("clarify."):
            result.route = "fast"
            result.planner_fallback_reason = ""
            return
        if conf >= threshold:
            result.route = "fast"
            result.planner_fallback_reason = ""
            return
        if conf >= CLARIFY_MIN:
            result.route = "clarify"
            result.planner_fallback_reason = "AMBIGUOUS_INTENT"
            return
        result.route = "unresolved"
        result.planner_fallback_reason = "NO_LOCAL_INTENT"

    def _score_task_list(self, n: str) -> Optional[tuple[float, str]]:
        best = 0.0
        pattern = ""
        tokens = n.split()
        for canon in _LIST_CANONICAL:
            ratio = SequenceMatcher(None, n, canon).ratio()
            coverage = _token_coverage(tokens, canon.split())
            score = ratio * 0.55 + coverage * 0.45
            if n == canon:
                score = 1.0
            if score > best:
                best = score
                pattern = canon
        leftover = [w for w in tokens if w not in _FILLER and w not in set(pattern.split())]
        leftover = [w for w in leftover if not _fuzzy_in(w, pattern.split())]
        leftover = [w for w in leftover if w not in {"one", "two", "three", "four", "five"} and not w.isdigit()]
        if leftover and best < 0.96:
            best *= max(0.4, 1.0 - 0.2 * len(leftover))
        if best >= 0.72:
            return (round(min(1.0, best), 3), pattern)
        # Keyword fallback for very short list asks
        if n in {"tasks", "my tasks", "todo", "todos", "my list", "whats left", "what is left"}:
            return (0.95, n)
        has_q = bool(re.search(r"\b(what|show|read|tell|list)\b", n))
        has_list = bool(re.search(r"\b(task|tasks|todo|list|left|remaining)\b", n))
        if has_q and has_list and len(tokens) <= 10 and best >= 0.62:
            return (round(max(best, 0.8), 3), pattern or n)
        return None

    def _score_complete(self, n: str, raw: str, items: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if _has_uncomplete_cue(n) or _is_add_command(n):
            return None
        if not _is_leading_complete(n):
            return None
        verb = next((v for v in _COMPLETE_VERBS if _phrase_in(n, v)), "")
        mark_done = bool(re.search(r"\bmark .+\b(done|complete|completed)\b", n))
        query = _task_query(raw or n)
        if query in {"a", "an", "the"}:
            query = ""
        if (not query or query in {"it", "that", "this", "again"}) and CONTEXT.last_task_id:
            payload = {
                "intent": "task.complete",
                "confidence": 0.92,
                "pattern": verb or "check off again",
                "args": {"query": query, "task_id": CONTEXT.last_task_id, "text": CONTEXT.last_task_text},
                "entity": CONTEXT.last_task_text,
                "entity_id": CONTEXT.last_task_id,
                "entity_confidence": 0.9,
            }
            return payload
        ordinal = next(
            (k for k in ("first", "second", "third", "fourth", "1st", "2nd", "3rd") if k in n.split()),
            "",
        )
        pattern = verb or ("mark done" if mark_done else "complete")
        conf = 0.93 if verb in {"cross out", "check off", "tick off", "cross off", "check it off", "cross it out"} else 0.88
        args: dict[str, Any] = {"query": query}
        payload: dict[str, Any] = {
            "intent": "task.complete",
            "confidence": conf,
            "pattern": pattern,
            "args": args,
        }
        if not items:
            return payload
        pool = [x for x in items if not x.get("done")] or items
        if ordinal:
            idx = {"first": 0, "second": 1, "third": 2, "fourth": 3, "1st": 0, "2nd": 1, "3rd": 2}.get(ordinal, 0)
            if 0 <= idx < len(pool):
                hit = pool[idx]
                payload["entity"] = hit.get("text") or ""
                payload["entity_id"] = str(hit.get("id") or "")
                payload["entity_confidence"] = 0.95
                args["task_id"] = hit.get("id")
                args["text"] = hit.get("text")
                args["index"] = idx
                return payload
        if not query:
            remaining = [x for x in items if not x.get("done")]
            if len(remaining) == 1:
                hit = remaining[0]
                payload["entity"] = hit.get("text") or ""
                payload["entity_id"] = str(hit.get("id") or "")
                payload["entity_confidence"] = 0.8
                args["task_id"] = hit.get("id")
                args["text"] = hit.get("text")
            return payload
        found = fuzzy_task_match(items, query)
        if found.get("ambiguous"):
            names = [c.get("text") for c in found["candidates"] if c.get("text")]
            payload["intent"] = "task.ambiguous"
            payload["confidence"] = 0.7
            payload["args"] = {"candidates": names, "query": query}
            return payload
        hit = found.get("task")
        if hit:
            payload["entity"] = hit.get("text") or ""
            payload["entity_id"] = str(hit.get("id") or "")
            payload["entity_confidence"] = max(float(found.get("score") or 0.0), 0.78)
            args["task_id"] = hit.get("id")
            args["text"] = hit.get("text")
        return payload

    def _score_delete(self, n: str, raw: str, items: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not _is_delete_command(n):
            return None
        query = _task_query(raw or n)
        if query in {"a", "an", "the"}:
            query = ""
        payload: dict[str, Any] = {
            "intent": "task.delete",
            "confidence": 0.9,
            "pattern": "delete task",
            "args": {"query": query},
        }
        if not query:
            return payload
        if items:
            found = fuzzy_task_match(items, query)
            if found.get("ambiguous"):
                names = [c.get("text") for c in found["candidates"] if c.get("text")]
                payload["intent"] = "task.ambiguous"
                payload["confidence"] = 0.7
                payload["args"] = {"candidates": names, "query": query}
                return payload
            hit = found.get("task")
            if hit:
                payload["entity"] = hit.get("text") or ""
                payload["entity_id"] = str(hit.get("id") or "")
                payload["entity_confidence"] = max(float(found.get("score") or 0.0), 0.78)
                payload["args"]["task_id"] = hit.get("id")
                payload["args"]["text"] = hit.get("text")
        return payload

    def _score_add(self, n: str, raw: str) -> Optional[dict[str, Any]]:
        if not _is_add_command(n):
            return None
        text = extract_add_text(n, raw)
        valid = bool(text) and _valid_add_text(text)
        return {
            "intent": "task.add",
            "confidence": 0.94 if valid else 0.88,
            "pattern": "add task",
            "args": {"text": text or ""},
            "entity": text or "",
            "entity_confidence": 0.9 if valid else 0.0,
        }

    def _fill_pending_entity(
        self,
        n: str,
        items: list[dict[str, Any]],
        *,
        original: str = "",
    ) -> Optional[dict[str, Any]]:
        if not CONTEXT.pending_intent:
            return None
        if CONTEXT.pending_created_at and (time.time() - CONTEXT.pending_created_at) > 90:
            clear_pending_clarification()
            return None
        pending_add = CONTEXT.pending_intent == "task.add" or TURN.pending_intent == "task.add"
        if pending_add:
            if self._score_task_list(n) and not _is_add_command(n):
                return None
            if re.search(r"\b(open|launch|start)\b.+\b(chrome|browser|edge|notepad)\b", n):
                return None
            if _is_non_entity_followup(n):
                return None
            # A restated "add another task" is another command, not the task title.
            if _is_add_command(n) and not extract_add_text(n, original or n):
                return None
            refs = [TURN.last_jarvis_question, *list(TURN.recent_replies or [])]
            peeled = peel_assistant_echo(original or n, refs)
            text = (peeled or original or n or "").strip(" .")
            payload, _ = strip_assistant_prefix(text)
            text = (payload or text).strip(" .")
            text = _repair_pending_add_title(text)
            compact = normalize_speech(text)
            if not text or len(compact) < 2 or compact in _ADD_TITLE_NOISE:
                return {
                    "intent": "task.add",
                    "confidence": 0.9,
                    "entity": "",
                    "entity_confidence": 0.0,
                    "args": {"text": ""},
                    "reask": True,
                }
            return {
                "intent": "task.add",
                "confidence": 0.92,
                "entity": text,
                "entity_confidence": 0.9,
                "args": {"text": text},
            }
        if CONTEXT.pending_intent not in {
            "task.complete",
            "task.uncomplete",
            "task.delete",
        }:
            return None
        if _is_non_entity_followup(n):
            return None
        if CONTEXT.pending_created_at and (time.time() - CONTEXT.pending_created_at) > 90:
            clear_pending_clarification()
            return None
        if not _looks_like_entity_fragment(n):
            return None
        pool = items
        names = CONTEXT.candidate_entities or CONTEXT.pending_candidates
        if CONTEXT.pending_intent == "task.uncomplete":
            done = [x for x in items if x.get("done")]
            if done:
                pool = done
        elif names:
            named = [x for x in items if (x.get("text") or "") in names]
            if named:
                pool = named
        found = fuzzy_task_match(pool, n)
        if found.get("ambiguous"):
            names = [c.get("text") for c in found["candidates"] if c.get("text")]
            return {
                "intent": "task.ambiguous",
                "confidence": 0.7,
                "args": {"candidates": names, "query": n},
            }
        hit = found.get("task")
        if not hit or float(found.get("score") or 0) < 0.5:
            return None
        return {
            "intent": CONTEXT.pending_intent,
            "confidence": 0.9,
            "entity": hit.get("text") or "",
            "entity_id": str(hit.get("id") or ""),
            "entity_confidence": max(float(found.get("score") or 0.0), 0.78),
            "args": {"task_id": hit.get("id"), "text": hit.get("text"), "query": n},
        }

    def _score_uncomplete(self, n: str, raw: str, items: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        verb = next((v for v in _UNCOMPLETE_VERBS if _phrase_in(n, v)), "")
        if not verb and not _has_uncomplete_cue(n):
            return None
        query = _task_query(raw or n)
        if query in {"a", "an", "the"}:
            query = ""
        payload: dict[str, Any] = {
            "intent": "task.uncomplete",
            "confidence": 0.93,
            "pattern": verb or "undo",
            "args": {"query": query},
        }
        pool = [x for x in items if x.get("done")] or items
        if (not query or query in {"it", "that", "this"}) and CONTEXT.last_task_id:
            payload["entity"] = CONTEXT.last_task_text
            payload["entity_id"] = CONTEXT.last_task_id
            payload["entity_confidence"] = 0.92
            payload["args"]["task_id"] = CONTEXT.last_task_id
            payload["args"]["text"] = CONTEXT.last_task_text
            return payload
        if not query:
            return payload
        if pool:
            found = fuzzy_task_match(pool, query)
            if found.get("ambiguous"):
                names = [c.get("text") for c in found["candidates"] if c.get("text")]
                payload["intent"] = "task.ambiguous"
                payload["confidence"] = 0.7
                payload["args"] = {"candidates": names, "query": query}
                return payload
            hit = found.get("task")
            if hit:
                payload["entity"] = hit.get("text") or ""
                payload["entity_id"] = str(hit.get("id") or "")
                payload["entity_confidence"] = max(float(found.get("score") or 0.0), 0.78)
                payload["args"]["task_id"] = hit.get("id")
                payload["args"]["text"] = hit.get("text")
        return payload

    @staticmethod
    def _score_social(n: str) -> Optional[tuple[str, str]]:
        for phrases, intent, reply in _SOCIAL:
            if n in phrases:
                return (intent, reply)
        return None

    @staticmethod
    def _score_contextual(n: str) -> Optional[dict[str, Any]]:
        compact = (n or "").replace("'", "")
        in_task = TURN.in_task_flow() or bool(CONTEXT.pending_intent)
        pending_add = CONTEXT.pending_intent == "task.add" or (
            TURN.pending_intent == "task.add" and TURN.active()
        )
        just_added = TURN.just_added() and (TURN.last_user_intent == "task.add" or TURN.recent_domain == "task")

        if _ANOTHER_TASK.fullmatch(n) or _ANOTHER_TASK.fullmatch(compact):
            if pending_add or just_added or (
                TURN.in_task_flow() and TURN.last_user_intent == "task.add"
            ):
                return {
                    "intent": "task.add",
                    "confidence": 0.93,
                    "pattern": "another task follow-up",
                    "reply": "What task should I add?",
                    "reason": "task_add_follow_up",
                    "missing": ["task_text"],
                    "args": {"text": ""},
                }
            return None

        if not in_task:
            return None

        if compact in _CLARIFY_UNKNOWN or n in _CLARIFY_UNKNOWN or _UNKNOWN_RE.fullmatch(n) or _UNKNOWN_RE.fullmatch(compact):
            if CONTEXT.pending_intent == "task.add" or pending_add:
                return {
                    "intent": "clarify.unknown",
                    "confidence": 0.95,
                    "pattern": "clarification unknown",
                    "reply": "Okay. What task should I add?",
                    "reason": "pending_add_unknown",
                }
            return {
                "intent": "clarify.unknown",
                "confidence": 0.95,
                "pattern": "clarification unknown",
                "reply": "I can list your tasks if you'd like.",
                "reason": "pending_entity_unknown",
            }
        if compact in _CLARIFY_CANCEL or n in _CLARIFY_CANCEL:
            return {
                "intent": "clarify.cancel",
                "confidence": 0.96,
                "pattern": "clarification cancel",
                "reply": "Okay.",
                "reason": "close_pending_interaction",
                "clear_pending": True,
            }
        if compact in _CLARIFY_LIST or n in _CLARIFY_LIST:
            return {
                "intent": "task.list",
                "confidence": 0.92,
                "pattern": "clarification list options",
                "reason": "list_pending_options",
            }
        if compact in _CLARIFY_REPEAT or n in _CLARIFY_REPEAT:
            q = TURN.last_jarvis_question or "What task should I add?"
            return {
                "intent": "clarify.repeat",
                "confidence": 0.95,
                "pattern": "clarification repeat",
                "reply": q,
                "reason": "repeat_last_question",
            }
        return None

    @staticmethod
    def _score_yes_no(n: str) -> str:
        if n in {"yes", "yep", "yeah", "yup", "affirmative", "do it"}:
            return "confirm.yes"
        if n in {"no", "nope", "nah", "negative"}:
            return "confirm.no"
        return ""

    @staticmethod
    def _log(result: ResolvedIntent, t0: float) -> None:
        ms = (time.perf_counter() - t0) * 1000
        print(
            "[Intent] "
            f"original={result.original_transcript!r} "
            f"normalized={result.normalized_transcript!r} "
            f"intent={result.intent or '-'} "
            f"intent_confidence={result.intent_confidence:.2f} "
            f"entity={result.entity or '-'} "
            f"entity_confidence={result.entity_confidence:.2f} "
            f"corrections={result.corrections_applied or []} "
            f"wake_prefix_candidate={result.wake_prefix_candidate or '-'} "
            f"wake_prefix_confidence={result.wake_prefix_confidence:.2f} "
            f"command_clause={result.command_clause or '-'} "
            f"command_clause_confidence={result.command_clause_confidence:.2f} "
            f"utterance_segments={result.utterance_segments or []} "
            f"candidate_commands={result.candidate_commands or []} "
            f"candidate_intents={result.candidate_intents or []} "
            f"selected_command_clause={result.selected_command_clause or '-'} "
            f"selection_reason={result.selection_reason or '-'} "
            f"superseded_clauses={result.superseded_clauses or []} "
            f"route={result.route} "
            f"planner_fallback_reason={result.planner_fallback_reason or '-'} "
            f"contextual_intent={result.contextual_intent or '-'} "
            f"context_resolution_reason={result.context_resolution_reason or '-'} "
            f"pending_context_before={result.pending_context_before or {}} "
            f"pending_context_after={result.pending_context_after or TURN.snapshot()} "
            f"resolve_ms={ms:.1f}"
        )
        if not result.pending_context_after:
            result.pending_context_after = TURN.snapshot()


def _phrase_in(n: str, phrase: str) -> bool:
    if not phrase:
        return False
    if " " in phrase:
        return phrase in n
    return bool(re.search(rf"\b{re.escape(phrase)}\b", n))


def _has_uncomplete_cue(n: str) -> bool:
    if re.search(r"\b(undo|uncheck|reopen)\b", n):
        return True
    if re.search(r"\bput\b.+\bback\b", n):
        return True
    if re.search(r"\bincomplete\b|\bnot done\b|\bdidnt finish\b|\bdid not finish\b", n):
        return True
    return False


def _fuzzy_in(word: str, others: list[str]) -> bool:
    if word in others:
        return True
    return any(SequenceMatcher(None, word, o).ratio() >= 0.8 for o in others if abs(len(word) - len(o)) <= 2)


def _token_coverage(utt: list[str], canon: list[str]) -> float:
    if not utt:
        return 0.0
    hits = 0
    for w in utt:
        if w in _FILLER:
            hits += 1
            continue
        if _fuzzy_in(w, canon):
            hits += 1
    return hits / max(1, len(utt))


def _is_delete_command(n: str) -> bool:
    if not n:
        return False
    return bool(
        re.match(r"^(please )?(delete|remove)\b", n)
        and re.search(r"\b(task|tasks|todo|item|list)\b", n)
    )


def _is_add_command(n: str) -> bool:
    if not n:
        return False
    if re.search(r"\bput\b.+\bback\b", n):
        return False
    if re.match(r"^(please )?(add|put|create)\b", n) and re.search(r"\b(task|tasks|todo|list)\b", n):
        return True
    if re.match(r"^(please )?(add|put|create)\b.+\b(to|on)\b.+\b(list|tasks?|todo)\b", n):
        return True
    return bool(re.match(r"^(please )?add (a |the |another |one more )?(new )?(task|todo|item)\b", n))


def _is_leading_complete(n: str) -> bool:
    if not n or _is_add_command(n) or _has_uncomplete_cue(n):
        return False
    if re.match(
        r"^(please )?(cross out|check off|check it off|tick off|cross off|cross it out)\b",
        n,
    ):
        return True
    if re.match(r"^(please )?(complete|finish)\b", n):
        return True
    if re.search(r"\bmark .+\b(done|complete|completed)\b", n):
        return True
    return False


def _action_signals(n: str) -> list[str]:
    hits: list[str] = []
    if _is_add_command(n):
        hits.append("task.add")
        if re.search(r"\b(add or complete|to add or complete)\b", n) or (
            n.count("add") >= 2 and re.search(r"\b(complete|finish)\b", n)
        ):
            hits.append("task.complete")
    if _is_leading_complete(n):
        hits.append("task.complete")
    elif _phrase_in(n, "complete") and not _is_add_command(n) and not _has_uncomplete_cue(n):
        hits.append("task.complete")
    if _has_uncomplete_cue(n):
        hits.append("task.uncomplete")
    if re.search(r"\b(delete|remove)\b.+\b(task|list)\b", n) or _is_delete_command(n):
        hits.append("task.delete")
    return hits


def extract_add_text(n: str, raw: str = "") -> str:
    src = (raw or n or "").strip()
    src_n = normalize_speech(src)
    bare = re.match(
        r"^(?:please )?(?:add|put|create) (?:a |the |another |one more )?(?:new )?(?:task|todo|item)"
        r"(?: (?:to|on)(?: my| the)?(?: task| todo)? list)?\s*$",
        src_n,
    )
    if bare:
        return ""
    m = re.match(
        r"^(?:please )?(?:add|put|create)\s+(.+?)\s+(?:to|on)\s+(?:my |the |todays |today s |today )?(?:task |todo )?lists?\s*$",
        src_n,
    )
    if m:
        return _clean_add_capture(m.group(1))
    m = re.match(
        r"^(?:please )?(?:add|put|create)\s+(.+?)\s+(?:to|on)\s+(?:my |todays |today s |today )?(?:daily )?tasks?\s*$",
        src_n,
    )
    if m:
        return _clean_add_capture(m.group(1))
    m = re.match(r"^(?:please )?add (?:a |the )?(?:new )?(?:task|todo|item)(?: to|:)\s+(.+)$", src_n)
    if m:
        return _clean_add_capture(m.group(1))
    m = re.match(r"^(?:please )?add (?:a |the )?(?:new )?(?:task|todo|item)\s+(.+)$", src_n)
    if m:
        return _clean_add_capture(m.group(1))
    return ""


def _clean_add_capture(text: str) -> str:
    t = " ".join((text or "").split()).strip(" .")
    if re.match(r"^(another |one more |a |the )?(new )?(task|todo|item)$", t, re.I):
        return ""
    t = re.sub(r"^(a |the |another |one more )?(new )?(task|todo|item) to ", "", t, flags=re.I).strip()
    if re.match(r"^(the |my )?(task |todo )?list\b", t, re.I):
        rest = re.sub(r"^(the |my )?(task |todo )?list( to)?\s*", "", t, flags=re.I).strip()
        return rest
    return t


def _repair_pending_add_title(text: str) -> str:
    raw = (text or "").strip(" .")
    n = normalize_speech(raw)
    if _CALL_GREG_STT.fullmatch(n):
        when = "tomorrow" if n.endswith("tomorrow") else "today"
        return f"Call Greg {when}"
    return raw


def _valid_add_text(text: str) -> bool:
    from src.agent.command_validator import valid_add_text

    return valid_add_text(text)


def _looks_like_entity_fragment(n: str) -> bool:
    if not n or len(n.split()) > 8:
        return False
    if _is_add_command(n) or _is_leading_complete(n) or _has_uncomplete_cue(n):
        return False
    if re.match(r"^(what|show|read|list|delete|remove|open|go|click|explain)\b", n):
        return False
    if n in {"yes", "no", "yep", "yeah", "nope", "ok", "okay"}:
        return False
    return True


def _is_non_entity_followup(n: str) -> bool:
    compact = (n or "").replace("'", "")
    if _ANOTHER_TASK.fullmatch(n or "") or _ANOTHER_TASK.fullmatch(compact):
        return True
    if compact in _CLARIFY_UNKNOWN or (n or "") in _CLARIFY_UNKNOWN:
        return True
    if compact in _CLARIFY_CANCEL or (n or "") in _CLARIFY_CANCEL:
        return True
    if compact in _CLARIFY_LIST or (n or "") in _CLARIFY_LIST:
        return True
    if compact in _CLARIFY_REPEAT or (n or "") in _CLARIFY_REPEAT:
        return True
    if _UNKNOWN_RE.fullmatch(n or "") or _UNKNOWN_RE.fullmatch(compact):
        return True
    return False


def peek_intent_name(text: str) -> str:
    """Cheap intent label for the voice gate. Does not log or cache a full resolution."""
    from src.agent.command_clause import extract_command_clause
    from src.agent.voice_echo import correct_command_stt

    extracted = extract_command_clause(text)
    if extracted.command_intent:
        return extracted.command_intent
    payload, prefix = strip_assistant_prefix(text)
    n = extracted.command_clause or normalize_speech(payload or text)
    n, _corr = correct_command_stt(n, addressed=bool(prefix or extracted.addressed))
    n = n.replace("whats", "what is")
    contextual = LocalIntentResolver._score_contextual(n)
    if contextual:
        return contextual["intent"]
    for phrases, intent, _reply in _SOCIAL:
        if n in phrases:
            return intent
    if _has_uncomplete_cue(n):
        return "task.uncomplete"
    if _is_add_command(n):
        return "task.add"
    if _is_leading_complete(n):
        return "task.complete"
    if _is_delete_command(n):
        return "task.delete"
    hit = LocalIntentResolver()._score_task_list(n)
    return "task.list" if hit else ""


def resolve_intent(
    text: str,
    *,
    tasks: Optional[list[dict[str, Any]]] = None,
    hud_active: bool = True,
    stt_confidence: float = 1.0,
) -> ResolvedIntent:
    """Single cached resolution per command+context. Downstream must reuse this object."""
    from src.agent.command_validator import CommandValidator

    items = list(tasks or [])
    stt = float(stt_confidence)
    key = (
        (text or "").strip(),
        round(stt, 2),
        tuple((str(t.get("id")), bool(t.get("done")), str(t.get("text") or "")) for t in items),
        CONTEXT.last_task_id,
        tuple(CONTEXT.pending_candidates),
        CONTEXT.pending_intent,
        TURN.pending_intent,
        bool(TURN.just_added()),
        hud_active,
    )
    cached = _RESOLVE_CACHE.get(key)
    if cached is not None:
        cached.intent_resolution_count = 1
        return cached
    result = LocalIntentResolver().resolve(text, tasks=items, hud_active=hud_active)
    result.stt_confidence = stt
    result.intent_resolution_count = 1
    validated = CommandValidator().validate(result, stt_confidence=stt, tasks=items)
    _apply_validation(result, validated)
    _RESOLVE_CACHE.clear()
    _RESOLVE_CACHE[key] = result
    print(f"[Intent] intent_resolution_count_per_command={result.intent_resolution_count}")
    return result


def _apply_validation(result: ResolvedIntent, validated) -> None:
    from src.agent.command_validator import ASK_CLARIFICATION, DISCARD_LOW_CONFIDENCE, EXECUTE_LOCAL, ROUTE_PLANNER

    result.arguments_valid = validated.arguments_valid
    result.missing_arguments = list(validated.missing_arguments)
    result.conflicting_intents = list(validated.conflicting_intents)
    result.semantic_coherence = validated.semantic_coherence
    result.execution_decision = validated.execution_decision
    result.decision_reason = validated.decision_reason
    result.stt_confidence = validated.stt_confidence
    if validated.reply:
        result.reply = validated.reply
    if validated.execution_decision == EXECUTE_LOCAL:
        result.route = "fast"
        result.planner_fallback_reason = ""
    elif validated.execution_decision == ASK_CLARIFICATION:
        result.route = "clarify"
        result.planner_fallback_reason = "AMBIGUOUS_INTENT"
    elif validated.execution_decision == DISCARD_LOW_CONFIDENCE:
        result.route = "discard"
        result.planner_fallback_reason = "LOW_STT_CONFIDENCE"
    elif validated.execution_decision == ROUTE_PLANNER:
        result.route = "planner"


def apply_resolved_intent(resolved: ResolvedIntent, tasks, weather=None) -> Optional[str]:
    """Execute a precomputed intent. Returns spoken reply, '' for silence, None if unhandled."""
    from src.agent.command_validator import ASK_CLARIFICATION, DISCARD_LOW_CONFIDENCE, EXECUTE_LOCAL

    decision = resolved.execution_decision or ""
    intent = resolved.intent or ""
    if decision == DISCARD_LOW_CONFIDENCE:
        if CONTEXT.pending_intent == "task.add" or TURN.pending_intent == "task.add":
            reply = _keep_pending_add("What task should I add?")
            note_jarvis_reply(reply)
            return reply
        note_jarvis_reply(resolved.reply or "")
        return resolved.reply
    if intent.startswith("social.") or intent.startswith("confirm.") or intent.startswith("clarify."):
        if intent in {"social.never_mind", "clarify.cancel"}:
            clear_pending_clarification()
        elif intent == "clarify.unknown" and (
            CONTEXT.pending_intent == "task.add" or TURN.pending_intent == "task.add"
        ):
            sync_pending_from_nlu(
                pending_intent="task.add",
                pending_argument="task_text",
                question=resolved.reply or "What task should I add?",
            )
        note_user_intent(intent)
        note_jarvis_reply(resolved.reply or "")
        return resolved.reply
    if decision == ASK_CLARIFICATION or resolved.route == "clarify":
        if resolved.decision_reason == "CONFLICTING_ACTION_VERBS":
            clear_pending_clarification()
            reply = resolved.reply or "What would you like me to do?"
            note_jarvis_reply(reply)
            return reply
        if (CONTEXT.pending_intent == "task.add" or TURN.pending_intent == "task.add") and intent in {
            "",
            "task.add",
            "task.ambiguous",
        }:
            reply = _keep_pending_add(resolved.reply if intent == "task.add" and resolved.reply else "What task should I add?")
            note_jarvis_reply(reply)
            return reply
        _store_pending(resolved, tasks)
        reply = resolved.reply or "Which task do you mean?"
        if CONTEXT.pending_intent == "task.add":
            reply = "What task should I add?"
        note_jarvis_reply(reply)
        return reply
    if decision and decision != EXECUTE_LOCAL:
        return None
    if not intent.startswith("task."):
        return None
    remaining = tasks.remaining()
    if intent == "task.list":
        if not remaining:
            return "You have no remaining tasks today."
        names = ", ".join(item["text"] for item in remaining if item.get("text"))
        return f"You have {len(remaining)} {('task' if len(remaining) == 1 else 'tasks')} remaining: {names}."
    if intent == "task.add":
        text = str(resolved.args.get("text") or resolved.entity or "").strip()
        from src.agent.command_validator import valid_add_text

        if not valid_add_text(text):
            CONTEXT.pending_intent = "task.add"
            CONTEXT.missing_argument = "task_text"
            CONTEXT.pending_created_at = time.time()
            sync_pending_from_nlu(
                pending_intent="task.add",
                pending_argument="task_text",
                question="What task should I add?",
            )
            note_user_intent("task.add")
            note_jarvis_reply("What task should I add?")
            return "What task should I add?"
        item = tasks.add(text)
        CONTEXT.last_intent = "task.add"
        note_task_added(text)
        clear_pending_clarification()
        note_jarvis_reply("Added.")
        return "Added."
    if intent == "task.ambiguous":
        names = resolved.args.get("candidates") or []
        CONTEXT.pending_candidates = [str(n) for n in names]
        CONTEXT.candidate_entities = list(CONTEXT.pending_candidates)
        CONTEXT.pending_intent = "task.complete"
        CONTEXT.missing_argument = "task_entity"
        CONTEXT.pending_created_at = time.time()
        return f"Which task — {', '.join(str(n) for n in names[:3])}?"
    if intent == "task.complete":
        tid = resolved.entity_id or resolved.args.get("task_id")
        hit = tasks.complete(str(tid)) if tid else None
        if hit:
            CONTEXT.last_task_id = str(hit.get("id") or "")
            CONTEXT.last_task_text = str(hit.get("text") or "")
            CONTEXT.last_intent = "task.complete"
            clear_pending_clarification()
            return "Done."
        _store_pending(resolved, tasks)
        return resolved.reply or "Which task should I complete?"
    if intent == "task.uncomplete":
        tid = resolved.entity_id or resolved.args.get("task_id")
        hit = tasks.complete(str(tid), done=False) if tid else None
        if hit:
            CONTEXT.last_task_id = str(hit.get("id") or "")
            CONTEXT.last_task_text = str(hit.get("text") or "")
            CONTEXT.last_intent = "task.uncomplete"
            clear_pending_clarification()
            return "Undone."
        _store_pending(resolved, tasks)
        return resolved.reply or "Which task do you want me to mark incomplete?"
    if intent == "task.delete":
        tid = resolved.entity_id or resolved.args.get("task_id")
        hit = tasks.delete(str(tid)) if tid else None
        if hit:
            clear_pending_clarification()
            return f"Removed {hit['text']}."
        return resolved.reply or "Which task should I remove?"
    return None


def _keep_pending_add(question: str = "What task should I add?") -> str:
    CONTEXT.pending_intent = "task.add"
    CONTEXT.missing_argument = "task_text"
    CONTEXT.pending_created_at = time.time()
    q = question or "What task should I add?"
    if "didn't catch" in q.lower() or "did not catch" in q.lower():
        q = "What task should I add?"
    sync_pending_from_nlu(pending_intent="task.add", pending_argument="task_text", question=q)
    return q


def _store_pending(resolved: ResolvedIntent, tasks) -> None:
    intent = resolved.intent if resolved.intent != "task.ambiguous" else (CONTEXT.pending_intent or "task.complete")
    CONTEXT.pending_intent = intent if intent.startswith("task.") else "task.complete"
    if CONTEXT.pending_intent == "task.add":
        CONTEXT.missing_argument = "task_text"
    else:
        CONTEXT.missing_argument = (
            (resolved.missing_arguments or ["task_entity"])[0] if resolved.missing_arguments else "task_entity"
        )
    names = list(resolved.args.get("candidates") or [])
    if not names:
        all_tasks = list(getattr(tasks, "tasks", []) or [])
        if intent == "task.uncomplete":
            names = [t.get("text") or "" for t in all_tasks if t.get("done")]
        else:
            try:
                names = [t.get("text") or "" for t in tasks.remaining()]
            except Exception:
                names = [t.get("text") or "" for t in all_tasks if not t.get("done")]
        if not names:
            names = [t.get("text") or "" for t in all_tasks]
    CONTEXT.pending_candidates = [n for n in names if n]
    CONTEXT.candidate_entities = list(CONTEXT.pending_candidates)
    CONTEXT.pending_created_at = time.time()
    q = ""
    if CONTEXT.pending_intent == "task.add":
        q = "What task should I add?"
    elif CONTEXT.pending_intent == "task.complete":
        q = "Which task should I complete?"
    else:
        q = "Which task do you mean?"
    sync_pending_from_nlu(
        pending_intent=CONTEXT.pending_intent,
        pending_argument=CONTEXT.missing_argument,
        question=q,
    )

