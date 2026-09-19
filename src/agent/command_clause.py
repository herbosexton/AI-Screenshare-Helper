"""Extract a known JARVIS command clause from noisy STT intros.

Wake-word matching is fuzzy only at the start of an utterance, and only when a
known command follows. Ordinary sentences that mention drivers/Travis/tasks
are left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from src.agent.voice_echo import correct_command_stt, normalize_speech


WAKE_CANONICAL = "jarvis"
WAKE_VARIANTS = {
    "jarvis": 1.0,
    "jarviss": 0.96,  # Jarvis's after apostrophe strip
    "jervis": 0.92,
    "jarvus": 0.90,
    "jarvish": 0.88,
    "jarves": 0.86,
    "jarvice": 0.84,
    "jarvie": 0.80,
    "drivers": 0.80,  # common Whisper error for "Jarvis"
    "travis": 0.78,
}

# Leading conversational scaffolding. Never stripped after the command starts.
_FILLERS = {
    "hey",
    "hi",
    "hello",
    "yo",
    "um",
    "uh",
    "oh",
    "please",
    "just",
    "so",
    "like",
    "okay",
    "ok",
    "well",
}
_POLITE_PHRASES = (
    "i want you to",
    "i need you to",
    "i would like you to",
    "could you please",
    "would you please",
    "can you please",
    "could you",
    "would you",
    "can you",
    "will you",
)

_NARRATIVE = re.compile(
    r"\b(yesterday|tomorrow|tonight|last|ago|already|earlier|"
    r"meetings?|assigned|someone|everyone|talking|about|him|her|them)\b",
    re.I,
)
_NARRATIVE_PREFIX = {
    "my",
    "his",
    "her",
    "their",
    "our",
    "this",
    "that",
    "those",
    "these",
    "the",
}

# High-confidence local command grammars. Match from the start of a clause only.
_COMMAND_GRAMMAR: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"^(please )?add (a |the |another |one more )?(new )?(task|todo|item)\b"), "task.add", 0.96),
    (re.compile(r"^(please )?(create|put) (a |the |another |one more )?(new )?(task|todo|item)\b"), "task.add", 0.94),
    (re.compile(r"^(please )?(add|put|create)\b.+\b(task|tasks|todo|item|list)\b"), "task.add", 0.92),
    (re.compile(r"^(i )?(had|at|ad) a (task|todo|item)$"), "task.add", 0.90),
    (re.compile(r"^(please )?(complete|finish) (a |the |that )?(task|todo|item)\b"), "task.complete", 0.93),
    (re.compile(r"^(please )?(cross out|check off|tick off|cross off|cross it out|check it off)\b"), "task.complete", 0.93),
    (re.compile(r"^(please )?(delete|remove) (a |the |that )?(task|todo|item)\b"), "task.delete", 0.93),
    (re.compile(r"^(please )?(undo|uncheck|reopen)\b"), "task.uncomplete", 0.92),
    (re.compile(r"^(what is|whats) left\b"), "task.list", 0.95),
    (re.compile(r"^(show|read|list|tell me) (me )?(my |the )?(tasks|task list|todo|todos)\b"), "task.list", 0.92),
    (re.compile(r"^(open|launch|start) (up )?(the )?(chrome|browser|edge|chromium)\b"), "browser.open", 0.95),
    (re.compile(r"^go back\b"), "browser.back", 0.95),
)

_CORRECTION_MARK = re.compile(
    r"\b(?:actually|i mean|rather|instead|no wait|wait no)\b",
    re.I,
)
_REPEATED_ADD = re.compile(
    r"((?:ask|add|create|put) (?:another |one more |a |the )?(?:new )?(?:task|todo|item))",
    re.I,
)


@dataclass
class ClauseExtraction:
    original: str = ""
    wake_prefix_candidate: str = ""
    wake_prefix_confidence: float = 0.0
    command_clause: str = ""
    command_clause_confidence: float = 0.0
    command_intent: str = ""
    normalized_payload: str = ""
    leading_scaffold: list[str] = field(default_factory=list)
    addressed: bool = False
    utterance_segments: list[str] = field(default_factory=list)
    candidate_commands: list[str] = field(default_factory=list)
    candidate_intents: list[str] = field(default_factory=list)
    selected_command_clause: str = ""
    selection_reason: str = ""
    superseded_clauses: list[str] = field(default_factory=list)


def wake_token_score(token: str) -> float:
    t = (token or "").lower().strip()
    if not t:
        return 0.0
    if t in WAKE_VARIANTS:
        return WAKE_VARIANTS[t]
    if t.endswith("s") and t[:-1] in WAKE_VARIANTS and t != "drivers":
        return max(0.75, WAKE_VARIANTS[t[:-1]] - 0.04)
    if len(t) < 5 or len(t) > 9:
        return 0.0
    ratio = SequenceMatcher(None, t, WAKE_CANONICAL).ratio()
    return float(ratio) if ratio >= 0.72 else 0.0


def match_command_grammar(text: str) -> tuple[str, str, float]:
    n = normalize_speech(text).replace("whats", "what is")
    if not n:
        return "", "", 0.0
    if _NARRATIVE.search(n) and not n.startswith("add "):
        # "had a task yesterday" is not a command; "add a task" with no narrative is.
        if not re.match(r"^(please )?(add|put|create|complete|delete|undo|open|go)\b", n):
            return "", "", 0.0
    if re.search(r"\bput\b.+\bback\b", n):
        n_for_add = ""
    else:
        n_for_add = n
    for rx, intent, conf in _COMMAND_GRAMMAR:
        probe = n_for_add if intent == "task.add" else n
        if not probe:
            continue
        if rx.search(probe):
            if intent == "task.add" and rx.pattern.endswith("$"):
                if _NARRATIVE.search(n):
                    return "", "", 0.0
            return n, intent, conf
    return "", "", 0.0


def _consume_polite(words: list[str], i: int) -> int:
    rest = " ".join(words[i:])
    for phrase in _POLITE_PHRASES:
        plen = len(phrase.split())
        if rest.startswith(phrase) and i + plen <= len(words):
            return plen
    return 0


def _is_scaffold_token(token: str) -> bool:
    return token in _FILLERS


def segment_utterance(text: str) -> list[str]:
    """Split only on sentence punctuation, explicit corrections, or repeated add-task attempts."""
    raw = (text or "").strip()
    if not raw:
        return []
    t = raw.replace("—no", ". ").replace("–no", ". ")
    t = re.sub(r"—\s*actually", ". actually", t, flags=re.I)
    t = re.sub(r"–\s*actually", ". actually", t, flags=re.I)
    t = _CORRECTION_MARK.sub(". ", t)
    t = re.sub(r",?\s+no,\s+", ". ", t, flags=re.I)
    parts = re.split(r"[.!?]+", t)
    segs: list[str] = []
    for part in parts:
        n = normalize_speech(part).replace("whats", "what is")
        if not n or n in {"no", "actually", "rather", "instead"}:
            continue
        segs.extend(_split_repeated_add(n))
    return segs or ([normalize_speech(raw).replace("whats", "what is")] if normalize_speech(raw) else [])


def _split_repeated_add(n: str) -> list[str]:
    hits = list(_REPEATED_ADD.finditer(n))
    if len(hits) < 2:
        return [n]
    out: list[str] = []
    start = 0
    for m in hits:
        if m.start() > start:
            lead = n[start : m.start()].strip()
            if lead:
                out.append(lead)
        out.append(m.group(1))
        start = m.end()
    tail = n[start:].strip()
    if tail:
        out.append(tail)
    return [x for x in out if x] or [n]


def has_correction_marker(text: str) -> bool:
    raw = text or ""
    if _CORRECTION_MARK.search(raw) or re.search(r",?\s+no,\s+", raw, re.I):
        return True
    return bool(re.search(r"[—–]\s*(no|actually)\b", raw, re.I))


def _is_weak_false_start(c: ClauseExtraction) -> bool:
    src = normalize_speech(c.original or c.command_clause)
    if src.startswith("ask "):
        return True
    if src in {"complete", "finish", "check", "open", "add", "create"}:
        return True
    return c.command_clause_confidence < 0.88


def _select_segment_candidate(
    original: str, candidates: list[ClauseExtraction]
) -> tuple[ClauseExtraction, str]:
    if len(candidates) == 1:
        return candidates[0], "only_valid_local_command"
    last = candidates[-1]
    if has_correction_marker(original):
        return last, "later_self_correction"
    if all(_is_weak_false_start(c) for c in candidates[:-1]):
        return last, "later_explicit_command"
    if last.command_intent == candidates[-2].command_intent:
        return last, "later_explicit_command"
    return candidates[0], "first_strong_command"


def extract_command_clause(text: str) -> ClauseExtraction:
    original = (text or "").strip()
    segments = segment_utterance(original)
    if len(segments) > 1:
        return _extract_from_segments(original, segments)
    return _extract_single_clause(original)


def _extract_from_segments(original: str, segments: list[str]) -> ClauseExtraction:
    out = ClauseExtraction(
        original=original,
        utterance_segments=list(segments),
        normalized_payload=normalize_speech(original),
    )
    candidates: list[ClauseExtraction] = []
    for seg in segments:
        one = _extract_single_clause(seg)
        if one.command_intent and one.command_clause:
            one.original = seg
            candidates.append(one)
    out.candidate_commands = [c.command_clause for c in candidates]
    out.candidate_intents = [c.command_intent for c in candidates]
    if not candidates:
        for seg in segments:
            fixed, _ = correct_command_stt(seg, addressed=False)
            clause, intent, conf = match_command_grammar(fixed)
            if clause and intent:
                one = ClauseExtraction(
                    original=seg,
                    command_clause=clause,
                    command_clause_confidence=conf,
                    command_intent=intent,
                    normalized_payload=clause,
                )
                candidates.append(one)
        out.candidate_commands = [c.command_clause for c in candidates]
        out.candidate_intents = [c.command_intent for c in candidates]
    if not candidates:
        return out
    chosen, reason = _select_segment_candidate(original, candidates)
    chosen_n = normalize_speech(chosen.command_clause)
    superseded = [s for s in segments if normalize_speech(s) != chosen_n]
    out.wake_prefix_candidate = chosen.wake_prefix_candidate
    out.wake_prefix_confidence = chosen.wake_prefix_confidence
    out.addressed = chosen.addressed
    out.leading_scaffold = chosen.leading_scaffold
    out.command_clause = chosen.command_clause
    out.command_clause_confidence = chosen.command_clause_confidence
    out.command_intent = chosen.command_intent
    out.normalized_payload = chosen.command_clause
    out.selected_command_clause = chosen.command_clause
    out.selection_reason = reason
    out.superseded_clauses = superseded
    return out


def _extract_single_clause(text: str) -> ClauseExtraction:
    original = (text or "").strip()
    n = normalize_speech(original).replace("whats", "what is")
    out = ClauseExtraction(original=original, normalized_payload=n, utterance_segments=[n] if n else [])
    if not n:
        return out
    words = n.split()
    i = 0
    wake = ""
    wake_conf = 0.0
    scaffold: list[str] = []

    while i < len(words):
        polite = _consume_polite(words, i)
        if polite:
            scaffold.extend(words[i : i + polite])
            i += polite
            continue
        w = words[i]
        if _is_scaffold_token(w):
            scaffold.append(w)
            i += 1
            continue
        score = wake_token_score(w)
        if score >= 0.75 and not wake:
            after = words[i + 1 :]
            j = 0
            while j < len(after):
                skip = _consume_polite(after, j)
                if skip:
                    j += skip
                    continue
                if _is_scaffold_token(after[j]):
                    j += 1
                    continue
                break
            rest = " ".join(after[j:])
            clause, _intent, _c = match_command_grammar(rest)
            if not clause:
                fixed, _ = correct_command_stt(rest, addressed=True)
                clause, _intent, _c = match_command_grammar(fixed)
            if clause:
                wake = w
                wake_conf = score
                scaffold.append(w)
                i += 1
                continue
        break

    remainder = " ".join(words[i:])
    if remainder.split()[:1] and remainder.split()[0] in _NARRATIVE_PREFIX and not match_command_grammar(remainder)[0]:
        out.leading_scaffold = scaffold
        out.normalized_payload = n
        return out

    addressed = bool(wake)
    fixed, _corr = correct_command_stt(remainder, addressed=addressed)
    clause, intent, conf = match_command_grammar(fixed)
    if not clause:
        clause, intent, conf = match_command_grammar(remainder)
    if not clause:
        # Final positional scan: command may sit after leftover intro noise.
        for j in range(len(words)):
            if not _prefix_is_scaffold(words[:j], allow_wake=True):
                continue
            chunk = " ".join(words[j:])
            fixed_c, _ = correct_command_stt(chunk, addressed=addressed or bool(wake))
            hit, hit_intent, hit_conf = match_command_grammar(fixed_c)
            if not hit:
                hit, hit_intent, hit_conf = match_command_grammar(chunk)
            if hit:
                clause, intent, conf = hit, hit_intent, hit_conf
                remainder = chunk
                if not wake:
                    for tok in words[:j]:
                        s = wake_token_score(tok)
                        if s >= 0.75:
                            wake, wake_conf = tok, s
                            addressed = True
                            break
                break

    out.wake_prefix_candidate = wake
    out.wake_prefix_confidence = wake_conf
    out.addressed = addressed
    out.leading_scaffold = scaffold
    if clause:
        out.command_clause = clause
        out.command_clause_confidence = conf
        out.command_intent = intent
        out.normalized_payload = clause
        out.selected_command_clause = clause
        out.selection_reason = "known_command_clause"
        out.candidate_commands = [clause]
        out.candidate_intents = [intent]
    else:
        out.normalized_payload = remainder or n
    return out


def _prefix_is_scaffold(tokens: list[str], *, allow_wake: bool) -> bool:
    if any(t in _NARRATIVE_PREFIX for t in tokens):
        return False
    wakes = 0
    i = 0
    while i < len(tokens):
        polite = _consume_polite(tokens, i)
        if polite:
            i += polite
            continue
        t = tokens[i]
        if _is_scaffold_token(t):
            i += 1
            continue
        if allow_wake and wake_token_score(t) >= 0.75:
            wakes += 1
            if wakes > 1:
                return False
            i += 1
            continue
        return False
    return True


def strip_leading_address(text: str) -> tuple[str, str]:
    """Return (payload, prefix). Prefix is wake/scaffold only when a command follows."""
    ext = extract_command_clause(text)
    if ext.command_clause:
        prefix = ext.wake_prefix_candidate
        if not prefix and ext.leading_scaffold:
            prefix = " ".join(ext.leading_scaffold)
        return ext.command_clause, prefix
    if ext.normalized_payload and ext.wake_prefix_candidate:
        return ext.normalized_payload, ext.wake_prefix_candidate
    return (text or "").strip(), ""
