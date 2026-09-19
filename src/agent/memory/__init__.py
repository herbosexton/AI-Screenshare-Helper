from __future__ import annotations

from typing import Any


class ConversationMemory:
    """Short-term conversation turns kept in process memory."""

    def __init__(self, max_turns: int = 40):
        self.max_turns = max_turns
        self._turns: list[dict[str, str]] = []

    def add(self, role: str, content: str) -> None:
        self._turns.append({"role": role, "content": content})
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    def as_messages(self) -> list[dict[str, str]]:
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()


class TaskMemory:
    """Lightweight notes attached to the active task context."""

    def __init__(self):
        self.notes: list[str] = []

    def remember(self, note: str) -> None:
        self.notes.append(note)

    def forget_all(self) -> None:
        self.notes.clear()
