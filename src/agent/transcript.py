"""Constrained STT/transcript normalization. Never a general rewriter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

from src.agent.voice_echo import correct_command_stt, normalize_speech, strip_assistant_prefix


# Phrase-level STT errors around known Jarvis commands. Not global rewrites.
_COMMAND_PHRASE_SUBS = (
    (re.compile(r"\bcheckof\b", re.I), "check off", "checkof -> check off"),
    (re.compile(r"\bcalender\b", re.I), "calendar", "calender -> calendar"),
    (re.compile(r"\b(jervis|jarvus|jarvish)\b", re.I), "jarvis", "stt -> jarvis"),
    (re.compile(r"\bto do list\b", re.I), "todo list", "to do list -> todo list"),
    (re.compile(r"\bto-do\b", re.I), "todo", "to-do -> todo"),
    # Site names spoken aloud. Whisper rarely gets "ChatGPT" right, and a misheard brand
    # becomes a guessed domain one letter off the real one.
    (
        re.compile(r"\bchat\s?(?:gbt|gtp|jpt|gpd|g\s?p\s?t)\b", re.I),
        "chatgpt",
        "stt -> chatgpt",
    ),
    (re.compile(r"\byou\s?tube\b", re.I), "youtube", "stt -> youtube"),
    (re.compile(r"\bgit\s?hub\b", re.I), "github", "stt -> github"),
)

_TASK_LIST_SUBS = (
    (re.compile(r"\btest list\b", re.I), "task list", "test -> task (task-list context)"),
    (re.compile(r"\btests list\b", re.I), "task list", "tests -> task (task-list context)"),
    (re.compile(r"\bmy tests\b", re.I), "my tasks", "tests -> tasks (task-list context)"),
    (re.compile(r"\btests today\b", re.I), "tasks today", "tests -> tasks (task-list context)"),
    (re.compile(r"\bon my test\b", re.I), "on my task", "test -> task (task-list context)"),
)

# Do not rewrite command verbs into nearby HUD task words (uncheck ≠ check).
_PROTECTED_TOKENS = {
    "uncheck",
    "undo",
    "undone",
    "reopen",
    "incomplete",
    "complete",
    "finish",
    "thanks",
    "thank",
    "goodbye",
    "never",
    "forget",
    # Command verbs. "switch back to Chrome" was being heard correctly and then rewritten
    # to "with back to Chrome", because a task on the HUD happened to contain "with".
    "switch",
    "close",
    "open",
    "click",
    "type",
    "read",
    "find",
    "search",
    "scroll",
    "compare",
    "download",
    "upload",
    "select",
}

# Words no transcript should ever be corrected *into*. Entity correction exists to repair
# misheard names and domain terms; a match onto a function word is always a false positive,
# and these are exactly the short words a real verb lands closest to.
_STOPWORD_TARGETS = frozenset(
    """with that this from have has been they them their there then than your yours
    what which when where will would could should about into over some more most only
    just very much like also same such each other another been were was are and but
    for the you not all any own too its his her him she""".split()
)

# Words that must never be *rewritten* during entity correction. These are JARVIS control
# commands and common verbs that happen to be edit-distance-1 from task entity words
# (e.g. "pause" ↔ "phase", "stop" ↔ "step", "open" ↔ "upon").
_PROTECTED_WORDS = frozenset(
    """pause resume cancel skip continue stop wait open close go find search read
    save send check click type start show tell get set move copy run play""".split()
)


def _looks_like_list_query(norm: str) -> bool:
    if re.search(r"\b(what|whats|show|read|tell|list|left|remaining)\b", norm):
        if re.search(r"\b(list|task|tasks|todo|test|tests|today|left)\b", norm):
            return True
    if re.search(r"\b(have to do|need to do|left to do)\b", norm):
        return True
    return False


def _edit_distance_ok(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    if abs(len(a) - len(b)) > 2:
        return False
    if min(len(a), len(b)) < 4:
        return a == b
    if SequenceMatcher(None, a, b).ratio() >= 0.8:
        return True
    if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
        return True
    return False


@dataclass
class NormalizationResult:
    original: str
    normalized: str
    corrections: list[str] = field(default_factory=list)
    # The whole utterance with corrections applied, still punctuated and cased. `normalized`
    # is stripped down for matching and, for a multi-clause goal, is only the clause the
    # local resolver kept — neither is the text to hand a planner.
    corrected: str = ""


class TranscriptNormalizer:
    def normalize(
        self,
        text: str,
        *,
        tasks: Optional[list[dict[str, Any]]] = None,
        hud_active: bool = True,
    ) -> NormalizationResult:
        original = (text or "").strip()
        corrections: list[str] = []
        t = original
        t = t.replace("'", "'").replace("'", "'")
        t = re.sub(r"\s+", " ", t).strip()

        for rx, repl, label in _COMMAND_PHRASE_SUBS:
            if rx.search(t):
                t = rx.sub(repl, t)
                corrections.append(label)

        norm_probe = normalize_speech(t)
        if hud_active and _looks_like_list_query(norm_probe):
            for rx, repl, label in _TASK_LIST_SUBS:
                if rx.search(t):
                    t = rx.sub(repl, t)
                    corrections.append(label)

        if tasks:
            t, extra = self._entity_corrections(t, tasks)
            corrections.extend(extra)

        payload, prefix = strip_assistant_prefix(t)
        fixed, extra = correct_command_stt(payload or t, addressed=bool(prefix))
        if extra:
            t = fixed
            corrections.extend(extra)

        normalized = normalize_speech(t)
        normalized = normalized.replace("whats", "what is").replace("what s", "what is")
        return NormalizationResult(
            original=original, normalized=normalized, corrections=corrections, corrected=t
        )

    def _entity_corrections(self, text: str, tasks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        vocab: set[str] = set()
        for task in tasks:
            for w in normalize_speech(str(task.get("text") or "")).split():
                if len(w) >= 4 and w not in _STOPWORD_TARGETS:
                    vocab.add(w)
        if not vocab:
            return text, []
        corrections: list[str] = []
        words = re.findall(r"[A-Za-z0-9']+", text)
        out: list[str] = []
        for w in words:
            low = w.lower().replace("'", "")
            if low in vocab or len(low) < 4 or low in _PROTECTED_TOKENS:
                out.append(w)
                continue
            hit = next((v for v in vocab if _edit_distance_ok(low, v)), "")
            if hit and hit != low and low not in _PROTECTED_WORDS and not any(low == prefix + hit for prefix in ("un", "re", "in")):
                out.append(hit)
                corrections.append(f"{low} -> {hit} (task entity)")
            else:
                out.append(w)
        if not corrections:
            return text, []
        rebuilt = " ".join(out)
        return rebuilt, corrections
