"""Single owner of mic, VAD, STT, TTS, and agent admission."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional

from src.agent.voice_gate import CommandQualityGate, VoiceEvent
from src.agent.voice_state import DiscardReason, VoiceSource, VoiceState


INTERRUPT_PREFIXES = ("stop", "cancel", "wait", "pause")


class VoiceSessionController:
    """
    Microphone / STT / TTS must not push the agent on their own.
    Only USER_VOICE finals that pass the quality gate become commands.
    """

    def __init__(
        self,
        *,
        on_command: Optional[Callable[[VoiceEvent], None]] = None,
        on_state: Optional[Callable[[str], None]] = None,
        on_hearing: Optional[Callable[[str], None]] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        cancel_tts: Optional[Callable[[], None]] = None,
        echo_window_s: float = 0.55,
    ):
        self.on_command = on_command or (lambda _e: None)
        self._on_state = on_state or (lambda _s: None)
        self._on_hearing = on_hearing or (lambda _s: None)
        self._on_partial = on_partial or (lambda _s: None)
        self.cancel_tts = cancel_tts or (lambda: None)
        self.gate = CommandQualityGate()
        self.gate.tts.echo_window_s = echo_window_s
        self.state = VoiceState.IDLE
        self.session_id = str(uuid.uuid4())
        self.last_event: Optional[VoiceEvent] = None
        self.executed_ids: set[str] = set()
        self.stats: dict[str, Any] = {
            "accepted": 0,
            "discarded": 0,
            "echo": 0,
            "duplicates": 0,
            "barge_in": 0,
            "planner_blocked": 0,
        }
        self.last_perf: dict[str, float] = {}
        self._lock = threading.Lock()
        self._utterance_started = 0.0

    def set_state(self, state: VoiceState) -> None:
        self.state = state
        label = {
            VoiceState.IDLE: "idle",
            VoiceState.LISTENING: "listening",
            VoiceState.USER_SPEAKING: "listening",
            VoiceState.FINALIZING: "understanding",
            VoiceState.PROCESSING: "working",
            VoiceState.ACTING: "working",
            VoiceState.TTS_SPEAKING: "speaking",
            VoiceState.BARGE_IN: "listening",
            VoiceState.PAUSED: "paused",
            VoiceState.ERROR: "error",
        }.get(state, "idle")
        try:
            self._on_state(label)
        except Exception:
            pass

    def on_listen_started(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.set_state(VoiceState.LISTENING)

    def on_listen_stopped(self) -> None:
        self.set_state(VoiceState.IDLE)

    def on_user_speech_start(self) -> bool:
        """Return True if TTS should be cancelled (confirmed barge-in energy)."""
        self._utterance_started = time.time()
        if self.state == VoiceState.TTS_SPEAKING:
            return False
        self.set_state(VoiceState.USER_SPEAKING)
        return False

    def on_tts_start(self, text: str) -> None:
        self.gate.tts.on_start(text)
        self.set_state(VoiceState.TTS_SPEAKING)
        print(f"[Voice] TTS_SPEAKING {text[:80]!r}")

    def on_tts_end(self) -> None:
        self.gate.tts.on_end()
        self.set_state(VoiceState.LISTENING)
        print("[Voice] echo window — LISTENING")

    def on_partial_transcript(self, text: str) -> None:
        if not (text or "").strip():
            return
        try:
            self._on_partial(text)
        except Exception:
            pass
        self._on_hearing(f"Hearing: {(text or '')[:80]}")

    def admit(
        self,
        text: str,
        *,
        confidence: float = 1.0,
        is_final: bool = True,
        duration_s: float = 0.0,
        source: str = VoiceSource.USER_VOICE.value,
        tts_active: Optional[bool] = None,
        stt_ms: float = 0.0,
        endpoint_ms: float = 0.0,
    ) -> VoiceEvent:
        t0 = time.perf_counter()
        during = self.gate.tts.speaking if tts_active is None else bool(tts_active)
        event = VoiceEvent(
            text=text,
            source=source,
            confidence=float(confidence),
            is_final=bool(is_final),
            during_tts=during,
            duration_s=float(duration_s),
            audio_session_id=self.session_id,
        )
        if during and self._looks_like_interrupt(text):
            event.barge_in = True
        event = self.gate.evaluate(event)
        gate_ms = (time.perf_counter() - t0) * 1000
        self.last_event = event
        self.last_perf = {
            "stt_ms": round(stt_ms, 1),
            "endpoint_ms": round(endpoint_ms, 1),
            "quality_gate_ms": round(gate_ms, 1),
            "confidence": round(event.confidence, 3),
            "echo_score": round(event.echo_score, 3),
            "tts_similarity": round(event.tts_similarity, 3),
            "raw_tts_similarity": round(event.raw_tts_similarity, 3),
            "payload_tts_similarity": round(event.payload_tts_similarity, 3),
            "novel_token_ratio": round(event.novel_token_ratio, 3),
            "recognized_intent": event.recognized_intent,
            "missing_arguments": list(event.missing_arguments),
            "original_transcript": event.text,
            "wake_prefix_candidate": event.wake_prefix_candidate,
            "wake_prefix_confidence": event.wake_prefix_confidence,
            "command_clause": event.command_clause,
            "command_clause_confidence": event.command_clause_confidence,
            "utterance_segments": list(event.utterance_segments),
            "candidate_commands": list(event.candidate_commands),
            "candidate_intents": list(event.candidate_intents),
            "selected_command_clause": event.selected_command_clause,
            "selection_reason": event.selection_reason,
            "superseded_clauses": list(event.superseded_clauses),
            "assistant_prefix_removed": event.assistant_prefix_removed,
            "pre_correction_payload": event.pre_correction_payload,
            "corrections_applied": list(event.corrections_applied),
            "normalized_payload": event.normalized_payload,
            "payload_tokens": list(event.payload_tokens),
            "tts_tokens": list(event.tts_tokens),
            "shared_tokens": list(event.shared_tokens),
            "novel_tokens": list(event.novel_tokens),
            "imperative_detected": event.imperative_detected,
            "during_tts": event.during_tts,
            "decision": event.decision,
            "decision_reason": event.decision_reason,
        }
        if not event.accepted:
            self.stats["discarded"] += 1
            if event.discard_reason == DiscardReason.SELF_SPEECH_ECHO.value:
                self.stats["echo"] += 1
            if event.discard_reason == DiscardReason.DUPLICATE.value:
                self.stats["duplicates"] += 1
            if event.discard_reason in {
                DiscardReason.FILLER.value,
                DiscardReason.PARTIAL.value,
                DiscardReason.TOO_SHORT.value,
                DiscardReason.LOW_CONFIDENCE.value,
            }:
                self.stats["planner_blocked"] += 1
            print(
                f"[Voice] discard {event.decision or event.discard_reason} "
                f"echo={event.tts_similarity:.2f} novel={event.novel_token_ratio:.2f} "
                f"intent={event.recognized_intent or '-'} "
                f"imperative={event.imperative_detected} "
                f"reason={event.decision_reason} {event.text[:80]!r}"
            )
            self._log_pipeline(event)
            if event.discard_reason == DiscardReason.LOW_CONFIDENCE.value:
                self._on_hearing("I didn't catch that. Could you repeat it?")
            elif self.state != VoiceState.TTS_SPEAKING:
                self.set_state(VoiceState.LISTENING)
            return event

        if event.command_id in self.executed_ids:
            event.accepted = False
            event.discard_reason = DiscardReason.DUPLICATE.value
            self.stats["duplicates"] += 1
            print(f"[Voice] discard DUPLICATE id={event.command_id}")
            return event
        self.executed_ids.add(event.command_id)
        self.stats["accepted"] += 1
        if event.barge_in:
            self.stats["barge_in"] += 1
            self.set_state(VoiceState.BARGE_IN)
            try:
                self.cancel_tts()
            except Exception:
                pass
        print(
            f"[Voice] accept {event.decision} intent={event.recognized_intent or '-'} "
            f"echo={event.tts_similarity:.2f} novel={event.novel_token_ratio:.2f} "
            f"reason={event.decision_reason} {event.text[:80]!r}"
        )
        self.set_state(VoiceState.PROCESSING)
        self._log_pipeline(event)
        try:
            self.on_command(event)
        except Exception as e:
            print(f"[Voice] command callback error: {e}")
            self.set_state(VoiceState.ERROR)
        return event

    def _looks_like_interrupt(self, text: str) -> bool:
        n = (text or "").strip().lower().rstrip(".!")
        return n in INTERRUPT_PREFIXES or n.startswith("stop ") or n.startswith("jarvis stop")

    def _log_pipeline(self, event: VoiceEvent) -> None:
        p = self.last_perf
        print(
            "\nVOICE PIPELINE\n"
            f"STT                    {p.get('stt_ms', 0):,.0f} ms\n"
            f"Endpointing            {p.get('endpoint_ms', 0):,.0f} ms\n"
            f"Quality gate           {p.get('quality_gate_ms', 0):,.0f} ms\n"
            f"Confidence             {p.get('confidence', 0):.2f}\n"
            f"original_transcript    {p.get('original_transcript') or event.text!r}\n"
            f"wake_prefix_candidate  {p.get('wake_prefix_candidate') or '-'}\n"
            f"wake_prefix_confidence {p.get('wake_prefix_confidence', 0):.2f}\n"
            f"command_clause         {p.get('command_clause') or '-'}\n"
            f"command_clause_confidence {p.get('command_clause_confidence', 0):.2f}\n"
            f"utterance_segments     {p.get('utterance_segments') or []}\n"
            f"candidate_commands     {p.get('candidate_commands') or []}\n"
            f"candidate_intents      {p.get('candidate_intents') or []}\n"
            f"selected_command_clause {p.get('selected_command_clause') or '-'}\n"
            f"selection_reason       {p.get('selection_reason') or '-'}\n"
            f"superseded_clauses     {p.get('superseded_clauses') or []}\n"
            f"assistant_prefix_removed {p.get('assistant_prefix_removed') or '-'}\n"
            f"pre_correction_payload {p.get('pre_correction_payload') or '-'}\n"
            f"corrections_applied    {p.get('corrections_applied') or []}\n"
            f"normalized_payload     {p.get('normalized_payload') or '-'}\n"
            f"recognized_intent      {p.get('recognized_intent') or '(none)'}\n"
            f"missing_arguments      {p.get('missing_arguments') or []}\n"
            f"payload_tokens         {p.get('payload_tokens') or []}\n"
            f"tts_tokens             {p.get('tts_tokens') or []}\n"
            f"shared_tokens          {p.get('shared_tokens') or []}\n"
            f"novel_tokens           {p.get('novel_tokens') or []}\n"
            f"raw_tts_similarity     {p.get('raw_tts_similarity', 0):.2f}\n"
            f"payload_tts_similarity {p.get('payload_tts_similarity', 0):.2f}\n"
            f"novel_token_ratio      {p.get('novel_token_ratio', 0):.2f}\n"
            f"imperative_detected    {p.get('imperative_detected')}\n"
            f"during_tts             {p.get('during_tts')}\n"
            f"decision               {p.get('decision') or '(none)'}\n"
            f"decision_reason        {p.get('decision_reason') or '(none)'}\n"
            f"discard_reason         {event.discard_reason or '(none)'}\n"
            f"commandId              {event.command_id}\n"
            f"source                 {event.source}\n"
        )

    def aec_status(self) -> dict[str, str]:
        return {
            "AEC available": "no",
            "AEC enabled": "no",
            "AEC unavailable": "yes — sounddevice capture has no Voice Capture DSP / AEC",
            "mode": "controlled full duplex (echo gate + post-TTS window)",
        }
