"""Admit only meaningful FINAL user commands. Never send garbage to the planner."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from src.agent.voice_echo import TtsMemory, analyze_against_tts, normalize_speech
from src.agent.voice_state import DiscardReason, VoiceDecision, VoiceSource


INTERRUPT_WORDS = {
    "stop",
    "cancel",
    "wait",
    "pause",
    "jarvis stop",
    "never mind",
    "hold on",
    "actually",
    "no",
}
SHORT_OK = {
    "stop",
    "pause",
    "continue",
    "back",
    "yes",
    "no",
    "wait",
    "cancel",
    "go",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "bye",
    "see ya",
    "see you",
    "hi",
    "hey",
    "hello",
    "yeah",
    "yep",
    "nah",
    "never mind",
    "forget it",
    "another task",
    "one more",
    "one more task",
    "thats it",
    "thats all",
    "im done",
    "i am done",
    "i dont know",
    "im not sure",
    "which ones",
    "which one",
}
FILLERS = {
    "oh",
    "uh",
    "um",
    "ah",
    "er",
    "hmm",
    "huh",
    "good",
    "polo",
    "mm",
    "mmm",
    "huhuh",
}
PARTIAL_TAILS = {"and", "to", "the", "a", "an", "or", "of", "for", "with", "my"}

_REJECT = {
    VoiceDecision.SELF_SPEECH_ECHO.value: DiscardReason.SELF_SPEECH_ECHO.value,
    VoiceDecision.LOW_CONFIDENCE_NOISE.value: DiscardReason.LOW_CONFIDENCE.value,
    VoiceDecision.PARTIAL.value: DiscardReason.PARTIAL.value,
    VoiceDecision.FILLER.value: DiscardReason.FILLER.value,
    VoiceDecision.DUPLICATE.value: DiscardReason.DUPLICATE.value,
    VoiceDecision.TOO_SHORT.value: DiscardReason.TOO_SHORT.value,
    VoiceDecision.EMPTY.value: DiscardReason.EMPTY.value,
    VoiceDecision.STALE.value: DiscardReason.STALE.value,
    VoiceDecision.NOT_FINAL.value: DiscardReason.NOT_FINAL.value,
    VoiceDecision.DURING_TTS.value: DiscardReason.DURING_TTS.value,
    VoiceDecision.NOISE.value: DiscardReason.NOISE.value,
}


@dataclass
class VoiceEvent:
    text: str
    source: str = VoiceSource.USER_VOICE.value
    timestamp: float = field(default_factory=time.time)
    audio_session_id: str = ""
    confidence: float = 1.0
    is_final: bool = True
    during_tts: bool = False
    echo_score: float = 0.0
    command_id: str = ""
    duration_s: float = 0.0
    discard_reason: str = ""
    accepted: bool = False
    barge_in: bool = False
    tts_similarity: float = 0.0
    novel_token_ratio: float = 0.0
    recognized_intent: str = ""
    imperative_detected: bool = False
    decision: str = ""
    decision_reason: str = ""
    assistant_prefix_removed: str = ""
    normalized_payload: str = ""
    pre_correction_payload: str = ""
    corrections_applied: list = field(default_factory=list)
    payload_tokens: list = field(default_factory=list)
    tts_tokens: list = field(default_factory=list)
    shared_tokens: list = field(default_factory=list)
    novel_tokens: list = field(default_factory=list)
    raw_tts_similarity: float = 0.0
    payload_tts_similarity: float = 0.0
    missing_arguments: list = field(default_factory=list)
    wake_prefix_candidate: str = ""
    wake_prefix_confidence: float = 0.0
    command_clause: str = ""
    command_clause_confidence: float = 0.0
    utterance_segments: list = field(default_factory=list)
    candidate_commands: list = field(default_factory=list)
    candidate_intents: list = field(default_factory=list)
    selected_command_clause: str = ""
    selection_reason: str = ""
    superseded_clauses: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "original_transcript": self.text,
            "source": self.source,
            "timestamp": self.timestamp,
            "audioSessionId": self.audio_session_id,
            "confidence": self.confidence,
            "isFinal": self.is_final,
            "duringTts": self.during_tts,
            "echoScore": self.echo_score,
            "tts_similarity": self.tts_similarity,
            "raw_tts_similarity": self.raw_tts_similarity,
            "payload_tts_similarity": self.payload_tts_similarity,
            "novel_token_ratio": self.novel_token_ratio,
            "recognized_intent": self.recognized_intent,
            "missing_arguments": list(self.missing_arguments),
            "assistant_prefix_removed": self.assistant_prefix_removed,
            "pre_correction_payload": self.pre_correction_payload,
            "corrections_applied": list(self.corrections_applied),
            "normalized_payload": self.normalized_payload,
            "payload_tokens": list(self.payload_tokens),
            "tts_tokens": list(self.tts_tokens),
            "shared_tokens": list(self.shared_tokens),
            "novel_tokens": list(self.novel_tokens),
            "imperative_detected": self.imperative_detected,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "commandId": self.command_id,
            "duration_s": self.duration_s,
            "discard_reason": self.discard_reason,
            "accepted": self.accepted,
            "barge_in": self.barge_in,
            "wake_prefix_candidate": self.wake_prefix_candidate,
            "wake_prefix_confidence": self.wake_prefix_confidence,
            "command_clause": self.command_clause,
            "command_clause_confidence": self.command_clause_confidence,
            "utterance_segments": list(self.utterance_segments),
            "candidate_commands": list(self.candidate_commands),
            "candidate_intents": list(self.candidate_intents),
            "selected_command_clause": self.selected_command_clause,
            "selection_reason": self.selection_reason,
            "superseded_clauses": list(self.superseded_clauses),
        }


class CommandQualityGate:
    def __init__(
        self,
        *,
        min_confidence: float = 0.38,
        duplicate_window_s: float = 2.2,
        min_duration_s: float = 0.22,
    ):
        self.min_confidence = min_confidence
        self.duplicate_window_s = duplicate_window_s
        self.min_duration_s = min_duration_s
        self._recent: list[tuple[float, str, str]] = []  # ts, norm, command_id
        self.tts = TtsMemory()

    def evaluate(self, event: VoiceEvent) -> VoiceEvent:
        text = (event.text or "").strip()
        event.text = text
        if not event.command_id:
            event.command_id = str(uuid.uuid4())
        if event.source != VoiceSource.USER_VOICE.value:
            return self._reject(event, VoiceDecision.STALE.value, "non_user_source")
        if not event.is_final:
            return self._reject(event, VoiceDecision.NOT_FINAL.value, "partial_not_final")
        if not text:
            return self._reject(event, VoiceDecision.EMPTY.value, "empty")

        norm = normalize_speech(text)
        now = time.time()
        words = norm.split()

        self._recent = [r for r in self._recent if now - r[0] <= 8.0]
        for ts, prev, cid in self._recent:
            if prev == norm and now - ts <= self.duplicate_window_s:
                event.command_id = cid
                return self._reject(event, VoiceDecision.DUPLICATE.value, "duplicate_final")

        if event.duration_s and event.duration_s < self.min_duration_s and norm not in SHORT_OK:
            return self._reject(event, VoiceDecision.TOO_SHORT.value, "too_short")

        if norm in FILLERS:
            return self._reject(event, VoiceDecision.FILLER.value, "filler")
        if len(words) == 1 and words[0] not in SHORT_OK and len(words[0]) < 4:
            return self._reject(event, VoiceDecision.FILLER.value, "short_filler")

        if text.rstrip().endswith("...") or (words and words[-1] in PARTIAL_TAILS and len(words) <= 5):
            return self._reject(event, VoiceDecision.PARTIAL.value, "incomplete_utterance")

        from src.agent.command_clause import extract_command_clause

        extracted = extract_command_clause(text)
        event.wake_prefix_candidate = extracted.wake_prefix_candidate
        event.wake_prefix_confidence = extracted.wake_prefix_confidence
        event.command_clause = extracted.command_clause
        event.command_clause_confidence = extracted.command_clause_confidence
        event.utterance_segments = list(extracted.utterance_segments)
        event.candidate_commands = list(extracted.candidate_commands)
        event.candidate_intents = list(extracted.candidate_intents)
        event.selected_command_clause = extracted.selected_command_clause or extracted.command_clause
        event.selection_reason = extracted.selection_reason
        event.superseded_clauses = list(extracted.superseded_clauses)
        intent = extracted.command_intent or self._recognized_intent(text)
        is_interrupt = self._is_interrupt(norm)
        # Intent/barge-in evidence is collected BEFORE echo discard.
        if event.confidence < self.min_confidence and norm not in SHORT_OK and not intent and not is_interrupt:
            return self._reject(event, VoiceDecision.LOW_CONFIDENCE_NOISE.value, "low_confidence")

        protected = bool(
            event.during_tts
            or self.tts.speaking
            or self.tts.in_echo_window()
            or (self.tts.ended_at and (now - self.tts.ended_at) < 2.0)
        )
        analysis = analyze_against_tts(
            text,
            self.tts.blob(),
            recognized_intent=intent,
            during_tts=protected,
            is_interrupt=is_interrupt,
            confidence=event.confidence,
        )
        event.echo_score = analysis.tts_similarity
        event.tts_similarity = analysis.tts_similarity
        event.raw_tts_similarity = analysis.raw_tts_similarity
        event.payload_tts_similarity = analysis.payload_tts_similarity
        event.novel_token_ratio = analysis.novel_token_ratio
        event.recognized_intent = analysis.recognized_intent
        event.imperative_detected = analysis.imperative_detected
        event.decision = analysis.decision
        event.decision_reason = analysis.decision_reason
        event.assistant_prefix_removed = analysis.assistant_prefix_removed
        event.normalized_payload = analysis.normalized_payload
        event.pre_correction_payload = analysis.pre_correction_payload
        event.corrections_applied = list(analysis.corrections_applied)
        event.payload_tokens = list(analysis.payload_tokens)
        event.tts_tokens = list(analysis.tts_tokens)
        event.shared_tokens = list(analysis.shared_tokens)
        event.novel_tokens = list(analysis.novel_tokens)
        event.missing_arguments = _missing_args_for_peek(
            analysis.recognized_intent or intent,
            event.selected_command_clause or event.command_clause or event.normalized_payload or text,
        )

        if analysis.decision in {
            VoiceDecision.USER_BARGE_IN.value,
            VoiceDecision.USER_COMMAND.value,
        }:
            event.accepted = True
            event.barge_in = analysis.decision == VoiceDecision.USER_BARGE_IN.value or is_interrupt
            if is_interrupt:
                event.barge_in = True
            self._recent.append((now, norm, event.command_id))
            return event

        return self._reject(event, analysis.decision or VoiceDecision.SELF_SPEECH_ECHO.value, analysis.decision_reason)

    def _reject(self, event: VoiceEvent, decision: str, reason: str) -> VoiceEvent:
        event.accepted = False
        event.decision = decision
        event.decision_reason = reason
        event.discard_reason = _REJECT.get(decision, decision)
        return event

    @staticmethod
    def _recognized_intent(text: str) -> str:
        from src.agent.local_intent import peek_intent_name
        from src.agent.router import FastCommandRouter

        name = peek_intent_name(text)
        if name:
            return name
        intent = FastCommandRouter().route(text, skip_tasks=True)
        return intent.action if intent is not None else ""

    @staticmethod
    def _is_interrupt(norm: str) -> bool:
        if not norm:
            return False
        from src.agent.voice_echo import strip_assistant_prefix

        payload, _ = strip_assistant_prefix(norm)
        n = normalize_speech(payload or norm)
        if n in INTERRUPT_WORDS or norm in INTERRUPT_WORDS:
            return True
        first = (n or norm).split()[0]
        return first in {"stop", "cancel", "wait", "pause", "actually"}

    def seen_command(self, command_id: str) -> bool:
        return any(cid == command_id for _, _, cid in self._recent)


def _missing_args_for_peek(intent: str, text: str) -> list[str]:
    from src.agent.voice_echo import normalize_speech, strip_assistant_prefix

    payload, _ = strip_assistant_prefix(text)
    n = normalize_speech(payload or text)
    if intent == "task.add" and re.fullmatch(
        r"(please )?(add|put|create)( a| the| another| one more)?( new)?( task| todo| item)?( on my list| to my list)?",
        n,
    ):
        return ["task_text"]
    if intent == "task.complete" and re.fullmatch(
        r"(please )?(complete|finish|cross out|check off)( a| the| that)?( task| todo| item)?",
        n,
    ):
        return ["task_entity"]
    if intent == "task.uncomplete" and re.fullmatch(
        r"(please )?(undo|uncheck|reopen)( a| the| that)?( task| todo| item)?",
        n,
    ):
        return ["task_entity"]
    if intent == "task.delete" and re.fullmatch(
        r"(please )?(delete|remove)( a| the| that)?( task| todo| item)?",
        n,
    ):
        return ["task_entity"]
    return []
