"""Authorize local intent execution. Recognition is not permission to act."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from src.agent.voice_echo import normalize_speech


EXECUTE_LOCAL = "EXECUTE_LOCAL"
ASK_CLARIFICATION = "ASK_CLARIFICATION"
ROUTE_PLANNER = "ROUTE_PLANNER"
DISCARD_LOW_CONFIDENCE = "DISCARD_LOW_CONFIDENCE"

READ_ONLY = {
    "task.list",
    "social.thanks",
    "social.goodbye",
    "social.greeting",
    "social.ack",
    "social.never_mind",
    "confirm.yes",
    "confirm.no",
    "browser.current_page",
    "browser.current_url",
    "browser.active_tab",
    "browser.list_tabs",
    "computer.active_window",
    "screen.capture_status",
    "screen.describe",
    "screen.find_element",
    "screen.dialog",
    "screen.error",
    "screen.verify",
    "screen.status_detail",
}

STATE_CHANGING = {
    "task.complete",
    "task.uncomplete",
    "task.delete",
    "task.add",
    "browser.close",
    "computer.close_window",
    "screen.click",
    "browser.click",
}

REQUIRED_ARGUMENTS: dict[str, list[str]] = {
    "task.list": [],
    "task.complete": ["task_entity"],
    "task.uncomplete": ["task_entity"],
    "task.delete": ["task_entity"],
    "task.add": ["task_text"],
    "task.ambiguous": [],
    "browser.open_and_goto": ["url"],
    "browser.goto": ["url"],
    "screen.click": ["target_element"],
    "browser.click": ["target_element"],
}

ENTITY_MIN = 0.70
STATE_STT_MIN = 0.45
STATE_COMBINED_MIN = 0.62
STATE_COHERENCE_MIN = 0.55
READ_INTENT_MIN = 0.62
LOW_STT = 0.40

_SCAFFOLD_ADD = re.compile(
    r"^(a |the |another |one more )?(new )?(task|todo|item)( to( the| my)?( task| todo)? list)?$",
    re.I,
)
_GARBLED = re.compile(r"\b(in-jar|in jar|six in|jar list)\b", re.I)


@dataclass
class CommandValidation:
    intent: str = ""
    intent_confidence: float = 0.0
    arguments: dict[str, Any] = field(default_factory=dict)
    arguments_valid: bool = True
    missing_arguments: list[str] = field(default_factory=list)
    conflicting_intents: list[str] = field(default_factory=list)
    semantic_coherence: float = 1.0
    execution_decision: str = ROUTE_PLANNER
    decision_reason: str = ""
    reply: str = ""
    stt_confidence: float = 1.0
    entity: str = ""
    entity_confidence: float = 0.0
    required_arguments: list[str] = field(default_factory=list)


class CommandValidator:
    def validate(
        self,
        resolved: Any,
        *,
        stt_confidence: float = 1.0,
        tasks: Optional[list[dict[str, Any]]] = None,
    ) -> CommandValidation:
        intent = getattr(resolved, "intent", "") or ""
        n = getattr(resolved, "normalized_transcript", "") or ""
        entity = getattr(resolved, "entity", "") or ""
        entity_conf = float(getattr(resolved, "entity_confidence", 0.0) or 0.0)
        intent_conf = float(getattr(resolved, "intent_confidence", 0.0) or 0.0)
        args = dict(getattr(resolved, "args", None) or {})
        conflicts = list(getattr(resolved, "conflicting_intents", None) or [])
        route = getattr(resolved, "route", "") or ""

        out = CommandValidation(
            intent=intent,
            intent_confidence=intent_conf,
            arguments=args,
            stt_confidence=float(stt_confidence),
            entity=entity,
            entity_confidence=entity_conf,
            required_arguments=list(REQUIRED_ARGUMENTS.get(intent, [])),
            conflicting_intents=conflicts,
        )

        missing = self._missing_args(intent, entity, entity_conf, args)
        out.missing_arguments = missing
        out.arguments_valid = not missing
        out.semantic_coherence = self._coherence(n, intent, entity, conflicts, stt_confidence)

        if not intent:
            if route == "planner":
                out.execution_decision = ROUTE_PLANNER
                out.decision_reason = getattr(resolved, "planner_fallback_reason", "") or "NO_LOCAL_INTENT"
            else:
                out.execution_decision = ROUTE_PLANNER
                out.decision_reason = "NO_LOCAL_INTENT"
            self._log(out)
            return out

        if intent.startswith("clarify."):
            out.execution_decision = EXECUTE_LOCAL
            out.decision_reason = "CONTEXTUAL_CLARIFICATION"
            out.reply = getattr(resolved, "reply", "") or ""
            self._log(out)
            return out

        if intent.startswith("social.") or intent.startswith("confirm."):
            if intent.startswith("confirm.") and (
                args.get("candidates") or getattr(resolved, "route", "") == "clarify"
            ):
                out.execution_decision = ASK_CLARIFICATION
                out.decision_reason = "PENDING_CLARIFICATION"
                out.reply = getattr(resolved, "reply", "") or "Which task do you mean?"
                self._log(out)
                return out
            out.execution_decision = EXECUTE_LOCAL
            out.decision_reason = "SOCIAL_OR_CONFIRM"
            out.reply = getattr(resolved, "reply", "") or ""
            self._log(out)
            return out

        if conflicts and self._unresolved_conflict(n, conflicts):
            out.execution_decision = ASK_CLARIFICATION
            out.decision_reason = "CONFLICTING_ACTION_VERBS"
            out.reply = (
                "I heard something about adding or completing a task, "
                "but I didn't catch it clearly. What would you like me to do?"
            )
            self._log(out)
            return out

        if intent in READ_ONLY or intent == "task.list":
            if intent_conf >= READ_INTENT_MIN:
                out.execution_decision = EXECUTE_LOCAL
                out.decision_reason = "READ_ONLY_LOCAL"
            else:
                out.execution_decision = ROUTE_PLANNER
                out.decision_reason = "WEAK_READ_INTENT"
            self._log(out)
            return out

        if intent == "task.ambiguous":
            names = args.get("candidates") or []
            out.execution_decision = ASK_CLARIFICATION
            out.decision_reason = "AMBIGUOUS_ENTITY"
            out.reply = f"Which task — {', '.join(str(n) for n in names[:3])}?" if names else "Which task do you mean?"
            self._log(out)
            return out

        combined = self._combined(stt_confidence, intent_conf, entity_conf if entity else 0.0, out.semantic_coherence)

        if intent in STATE_CHANGING:
            if float(stt_confidence) < LOW_STT and (missing or out.semantic_coherence < 0.6 or conflicts):
                out.execution_decision = DISCARD_LOW_CONFIDENCE
                out.decision_reason = "LOW_STT_AND_INCOMPLETE_COMMAND"
                out.reply = "I didn't catch that clearly. Could you repeat it?"
                if missing and "task_entity" in missing and float(stt_confidence) >= 0.28:
                    out.execution_decision = ASK_CLARIFICATION
                    out.decision_reason = "MISSING_REQUIRED_ENTITY"
                    out.reply = self._missing_reply(intent, n, garbled=True)
                self._log(out)
                return out

            if missing:
                out.execution_decision = ASK_CLARIFICATION
                out.decision_reason = (
                    "MISSING_REQUIRED_ENTITY" if "task_entity" in missing else "MISSING_REQUIRED_ARGUMENT"
                )
                out.reply = self._missing_reply(intent, n, garbled=out.semantic_coherence < 0.55)
                self._log(out)
                return out

            if out.semantic_coherence < STATE_COHERENCE_MIN:
                out.execution_decision = ASK_CLARIFICATION
                out.decision_reason = "LOW_SEMANTIC_COHERENCE"
                out.reply = self._missing_reply(intent, n) if intent == "task.add" else "I didn't catch that clearly. Could you repeat it?"
                self._log(out)
                return out

            if float(stt_confidence) < STATE_STT_MIN:
                out.execution_decision = ASK_CLARIFICATION
                out.decision_reason = "LOW_STT_STATE_CHANGE"
                out.reply = self._missing_reply(intent, n) if intent == "task.add" else "I didn't catch that clearly. Could you repeat it?"
                self._log(out)
                return out

            if combined < STATE_COMBINED_MIN:
                out.execution_decision = ASK_CLARIFICATION
                out.decision_reason = "INSUFFICIENT_COMBINED_EVIDENCE"
                out.reply = self._missing_reply(intent, n) if intent == "task.add" else "I didn't catch that clearly. Could you repeat it?"
                self._log(out)
                return out

            out.execution_decision = EXECUTE_LOCAL
            out.decision_reason = "ARGS_AND_EVIDENCE_OK"
            self._log(out)
            return out

        if missing:
            out.execution_decision = ASK_CLARIFICATION
            out.decision_reason = "MISSING_REQUIRED_ARGUMENT"
            out.reply = self._missing_reply(intent, n)
            self._log(out)
            return out

        if route == "fast" and intent_conf >= 0.72:
            out.execution_decision = EXECUTE_LOCAL
            out.decision_reason = "FAST_INTENT_OK"
        elif route == "clarify":
            out.execution_decision = ASK_CLARIFICATION
            out.decision_reason = "RESOLVER_CLARIFY"
            out.reply = getattr(resolved, "reply", "") or "Which one did you mean?"
        else:
            out.execution_decision = ROUTE_PLANNER
            out.decision_reason = getattr(resolved, "planner_fallback_reason", "") or "NO_LOCAL_INTENT"
        self._log(out)
        return out

    def _missing_args(self, intent: str, entity: str, entity_conf: float, args: dict[str, Any]) -> list[str]:
        required = REQUIRED_ARGUMENTS.get(intent, [])
        missing: list[str] = []
        for name in required:
            if name == "task_entity":
                tid = args.get("task_id")
                if not entity or not tid or entity_conf < ENTITY_MIN:
                    missing.append("task_entity")
            elif name == "task_text":
                text = str(args.get("text") or entity or "").strip()
                if not text or not valid_add_text(text):
                    missing.append("task_text")
            elif name == "url":
                if not str(args.get("url") or "").strip():
                    missing.append("url")
            elif name == "target_element":
                if not str(args.get("name") or args.get("target") or "").strip():
                    missing.append("target_element")
        return missing

    @staticmethod
    def _combined(stt: float, intent: float, entity: float, coherence: float) -> float:
        return 0.25 * float(stt) + 0.30 * float(intent) + 0.30 * float(entity) + 0.15 * float(coherence)

    @staticmethod
    def _coherence(
        n: str,
        intent: str,
        entity: str,
        conflicts: list[str],
        stt: float,
    ) -> float:
        score = 1.0
        if re.search(r"\b(add or complete|complete or add|add and complete)\b", n):
            score -= 0.5
        if n.count("add") >= 2 and "task" in n:
            score -= 0.25
        if "add a task" in n and "task list" in n and len(n.split()) > 10:
            score -= 0.2
        if conflicts and len(conflicts) >= 2:
            score -= 0.25
        if intent in STATE_CHANGING and not entity:
            score -= 0.2
        if float(stt) < LOW_STT:
            score -= 0.2
        if _GARBLED.search(n):
            score -= 0.25
        if re.search(r"\b(six|in-jar|jar list)\b", n) and intent.startswith("task."):
            score -= 0.15
        return max(0.0, min(1.0, round(score, 3)))

    @staticmethod
    def _unresolved_conflict(n: str, conflicts: list[str]) -> bool:
        if "task.add" in conflicts and "task.complete" in conflicts:
            if re.search(r"\b(add or complete|complete or add)\b", n):
                return True
            if re.match(r"^(please )?(add|put|create)\b", n) and _is_infinitive_complete(n):
                return False
            if re.match(r"^(please )?(add|put|create)\b", n):
                return "add or complete" in n or (n.count("add") >= 2 and "complete" in n)
        return len(set(conflicts)) >= 2 and not re.match(r"^(please )?(add|put|create)\b", n)

    @staticmethod
    def _missing_reply(intent: str, n: str, *, garbled: bool = False) -> str:
        if garbled or _GARBLED.search(n):
            return "I didn't catch the task name. Could you repeat it?"
        if intent == "task.complete":
            return "Which task should I complete?"
        if intent == "task.uncomplete":
            return "Which task do you want me to mark incomplete?"
        if intent == "task.delete":
            return "Which task should I remove?"
        if intent == "task.add":
            return "What task should I add?"
        if intent in {"screen.click", "browser.click"}:
            return "What should I click?"
        if intent in {"browser.open_and_goto", "browser.goto"}:
            return "Where should I go?"
        return "Which task do you mean?"

    @staticmethod
    def _log(out: CommandValidation) -> None:
        print(
            "[Validate] "
            f"stt_confidence={out.stt_confidence:.2f} "
            f"intent={out.intent or '-'} "
            f"intent_confidence={out.intent_confidence:.2f} "
            f"entity={out.entity or 'None'} "
            f"entity_confidence={out.entity_confidence:.2f} "
            f"required_arguments={out.required_arguments} "
            f"missing_arguments={out.missing_arguments} "
            f"semantic_coherence={out.semantic_coherence:.2f} "
            f"conflicting_intents={out.conflicting_intents} "
            f"execution_decision={out.execution_decision} "
            f"decision_reason={out.decision_reason}"
        )


def valid_add_text(text: str) -> bool:
    t = normalize_speech(text)
    if not t or _SCAFFOLD_ADD.match(t):
        return False
    if t in {
        "thats true",
        "that is true",
        "thats true by the way",
        "that is true by the way",
        "by the way",
        "anyway",
    }:
        return False
    if t.startswith("the task list") or t.startswith("my task list") or t.startswith("the list"):
        return False
    if re.search(r"\badd or complete\b", t):
        return False
    if len(t) < 2:
        return False
    return True


def _is_infinitive_complete(n: str) -> bool:
    return bool(re.search(r"\b(to|remind me to) (complete|finish)\b", n))
