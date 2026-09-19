"""Conversational / pending interaction state for short voice follow-ups."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


CONTEXT_TTL_S = 90.0


@dataclass
class ConversationTurnContext:
    last_user_intent: str = ""
    last_jarvis_question: str = ""
    pending_intent: str = ""
    pending_argument: str = ""
    recent_entity: str = ""
    recent_domain: str = ""
    expires_at: float = 0.0
    last_added_at: float = 0.0
    recent_replies: list[str] = field(default_factory=list)

    def active(self, now: float | None = None) -> bool:
        ts = now if now is not None else time.time()
        return bool(self.expires_at and ts <= self.expires_at)

    def in_task_flow(self, now: float | None = None) -> bool:
        if not self.active(now):
            if self.last_added_at and (time.time() - self.last_added_at) <= CONTEXT_TTL_S:
                return self.recent_domain == "task" or (self.last_user_intent or "").startswith("task.")
            return False
        return (
            (self.pending_intent or "").startswith("task.")
            or (self.last_user_intent or "").startswith("task.")
            or self.recent_domain == "task"
        )

    def just_added(self, now: float | None = None) -> bool:
        ts = now if now is not None else time.time()
        return bool(self.last_added_at and (ts - self.last_added_at) <= CONTEXT_TTL_S)

    def snapshot(self) -> dict:
        return asdict(self)

    def touch(self, *, seconds: float = CONTEXT_TTL_S) -> None:
        self.expires_at = time.time() + seconds

    def clear_pending(self) -> None:
        self.pending_intent = ""
        self.pending_argument = ""
        self.last_jarvis_question = ""

    def close(self) -> None:
        self.clear_pending()
        self.expires_at = 0.0


TURN = ConversationTurnContext()


def reset_turn_context() -> None:
    TURN.last_user_intent = ""
    TURN.last_jarvis_question = ""
    TURN.pending_intent = ""
    TURN.pending_argument = ""
    TURN.recent_entity = ""
    TURN.recent_domain = ""
    TURN.expires_at = 0.0
    TURN.last_added_at = 0.0
    TURN.recent_replies = []


def sync_pending_from_nlu(*, pending_intent: str, pending_argument: str, question: str = "") -> None:
    TURN.pending_intent = pending_intent
    TURN.pending_argument = pending_argument
    if question:
        TURN.last_jarvis_question = question
    if pending_intent.startswith("task."):
        TURN.recent_domain = "task"
    TURN.touch()


def note_user_intent(intent: str, *, entity: str = "") -> None:
    if not intent:
        return
    TURN.last_user_intent = intent
    if entity:
        TURN.recent_entity = entity
    if intent.startswith("task."):
        TURN.recent_domain = "task"
    TURN.touch()


def note_jarvis_reply(text: str = "") -> None:
    spoken = (text or "").strip()
    if not spoken:
        return
    if "?" in spoken:
        TURN.last_jarvis_question = spoken
    replies = [spoken] + [r for r in TURN.recent_replies if r != spoken]
    TURN.recent_replies = replies[:4]
    TURN.touch()


def note_task_added(text: str = "") -> None:
    TURN.last_user_intent = "task.add"
    TURN.recent_domain = "task"
    TURN.recent_entity = text
    TURN.last_added_at = time.time()
    TURN.clear_pending()
    TURN.touch()
