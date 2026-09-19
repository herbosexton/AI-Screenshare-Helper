"""Fuzzy match of STT against current/recent TTS (self-speech echo)."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from src.agent.voice_state import VoiceDecision


def normalize_speech(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("'", "")
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


_ADDRESS_PREFIX = re.compile(
    r"^(?:(?:ok(?:ay)?|hey|hi|hello)[\s,]+)*jarvis\b[\s,:]*",
    re.I,
)


def strip_assistant_prefix(text: str) -> tuple[str, str]:
    """Strip a leading assistant-address only. Mid-sentence 'Jarvis' is kept."""
    raw = (text or "").strip()
    if not raw:
        return "", ""
    # Fuzzy wake + filler peel when a known command clause follows.
    try:
        from src.agent.command_clause import strip_leading_address

        payload, prefix = strip_leading_address(raw)
        if payload and normalize_speech(payload) != normalize_speech(raw):
            return payload, prefix
    except Exception:
        pass
    m = _ADDRESS_PREFIX.match(raw)
    if m and m.end() < len(raw):
        return raw[m.end() :].strip(), m.group(0).strip()
    n = normalize_speech(raw)
    m2 = re.match(r"^(?:(?:ok(?:ay)?|hey|hi|hello) )*jarvis(?: please)?(?: |$)", n)
    if m2 and m2.end() < len(n):
        return n[m2.end() :].strip(), m2.group(0).strip()
    return raw, ""


# True function words only. Do not hide command/content tokens from novelty.
_NOVELTY_STOPWORDS = {"a", "an", "the", "and", "or", "of", "to", "for", "with", "please"}

# Past-tense / narrative leftovers — never rewrite these into commands.
_NARRATIVE_CUES = re.compile(
    r"\b(yesterday|tomorrow|tonight|last|ago|already|earlier|before|"
    r"meetings?|assigned|someone|everyone|him|her|them)\b",
    re.I,
)
_THIRD_PERSON = re.compile(r"^(he|she|they|we|someone)\b", re.I)
# Whisper often hears "add a task" as had/at/ad a task. Constrained, not global.
_ADD_TASK_MISHEAR = re.compile(
    r"^(?P<head>i\s+)?(?P<verb>had|at|ad)\s+a\s+(?P<noun>task|todo|item)$",
    re.I,
)
# Whisper may hear "add another task" as "ask another task". Never a global ask->add.
_ASK_TASK_MISHEAR = re.compile(
    r"^(please )?(?P<verb>ask) (?P<det>another |one more |a |the )?(?P<noun>task|todo|item)$",
    re.I,
)
_ASK_NOT_ADD = re.compile(r"\b(question|questions|greg|about|you|him|her|them)\b", re.I)


def correct_command_stt(payload: str, *, addressed: bool = False) -> tuple[str, list[str]]:
    """Constrained command-domain STT fix. Never a general 'I had'/'ask' rewriter."""
    n = normalize_speech(payload)
    if not n:
        return n, []
    if _NARRATIVE_CUES.search(n) or _THIRD_PERSON.match(n):
        return n, []
    words = n.split()
    ask = _ASK_TASK_MISHEAR.fullmatch(n)
    if ask and not _ASK_NOT_ADD.search(n) and (addressed or 3 <= len(words) <= 5):
        det = (ask.group("det") or "a ").strip()
        if det in {"a", "the"}:
            det = "a"
        elif det == "one more":
            det = "one more"
        else:
            det = det or "another"
        return f"add {det} {ask.group('noun')}", ["ask -> add"]
    m = _ADD_TASK_MISHEAR.fullmatch(n)
    if not m:
        return n, []
    # Strong context: user addressed JARVIS, or the leftover is a short command shape.
    if not addressed and not (3 <= len(words) <= 4):
        return n, []
    verb = m.group("verb")
    src = f"{(m.group('head') or '').strip()} {verb}".strip()
    return f"add a {m.group('noun')}", [f"{src} -> add"]


def speech_tokens(text: str) -> list[str]:
    return [w for w in normalize_speech(text).split() if w]


def content_tokens(text: str) -> list[str]:
    return [w for w in speech_tokens(text) if w not in _NOVELTY_STOPWORDS]


def _plural_or_exact(token: str, other: str) -> bool:
    if token == other:
        return True
    # Require a real stem so "i"/"is" and "a"/"as" are not treated as plurals.
    if min(len(token), len(other)) < 3:
        return False
    if token + "s" == other or other + "s" == token:
        return True
    if token.endswith("es") and token[:-2] == other:
        return True
    if other.endswith("es") and other[:-2] == token:
        return True
    return False


def token_in_set(token: str, others: set[str]) -> bool:
    return any(_plural_or_exact(token, o) for o in others)


def token_novelty(payload: str, tts_text: str) -> dict[str, object]:
    """Novelty vs recent TTS using wake-stripped payload tokens. No stopword wipe."""
    payload_tokens = speech_tokens(payload)
    tts_token_list = speech_tokens(tts_text)
    tts_set = set(tts_token_list)
    payload_content = [w for w in payload_tokens if w not in _NOVELTY_STOPWORDS]
    shared = [t for t in payload_content if token_in_set(t, tts_set)]
    novel = [t for t in payload_content if not token_in_set(t, tts_set)]
    ratio = (len(novel) / len(payload_content)) if payload_content else 0.0
    return {
        "payload_tokens": payload_tokens,
        "tts_tokens": tts_token_list,
        "shared_tokens": shared,
        "novel_tokens": novel,
        "novel_token_ratio": float(ratio),
    }


ACTION_PHRASES = (
    "cross out",
    "check off",
    "tick off",
    "cross off",
    "mark done",
    "mark complete",
    "mark completed",
    "check-off",
    "check it off",
    "cross it out",
    "put back",
    "not done",
    "add a task",
    "add task",
    "complete a task",
    "delete a task",
    "undo a task",
)
ACTION_WORDS = {
    "cross",
    "mark",
    "complete",
    "add",
    "remove",
    "open",
    "close",
    "go",
    "click",
    "stop",
    "pause",
    "cancel",
    "delete",
    "undo",
    "uncheck",
    "reopen",
    "finish",
    "scroll",
    "continue",
}
# "check" alone is not an action — HUD tasks include "Check calendar".
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "for",
    "with",
    "you",
    "your",
    "have",
    "has",
    "had",
    "is",
    "are",
    "was",
    "were",
    "it",
    "this",
    "that",
    "those",
    "these",
    "remaining",
    "today",
    "task",
    "tasks",
    "list",
    "please",
    "out",
    "off",
    "my",
    "me",
    "on",
    "in",
    "at",
    "be",
}


def echo_score(stt_text: str, tts_text: str) -> float:
    a = normalize_speech(stt_text)
    b = normalize_speech(tts_text)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if len(shorter) >= 4:
            return 0.92 + 0.08 * (len(shorter) / max(1, len(longer)))
    ratio = SequenceMatcher(None, a, b).ratio()
    a_words = a.split()
    b_words = b.split()
    if a_words and set(a_words).issubset(set(b_words)) and len(a_words) >= 2:
        ratio = max(ratio, 0.88)
    # Stem/prefix overlap catches Whisper-garbled speaker echo ("echo pro" vs "echo probe").
    # Require a majority of long tokens to match so one shared stem (task/tasks)
    # cannot inflate a novel command to ~0.75 similarity.
    hits = 0
    for aw in a_words:
        if len(aw) < 3:
            continue
        if any(bw.startswith(aw) or aw.startswith(bw) for bw in b_words if len(bw) >= 3):
            hits += 1
    long_a = [w for w in a_words if len(w) >= 3]
    if long_a:
        hit_ratio = hits / len(long_a)
        # One shared noun (task/tasks) must not look like a near-exact echo.
        if hit_ratio >= 0.67 and hits >= 2:
            ratio = max(ratio, 0.50 + 0.45 * hit_ratio)
    return float(min(1.0, ratio))


def peel_assistant_echo(text: str, assistant_lines: list[str] | None = None) -> str:
    """Drop clauses that are a near-echo of recent JARVIS speech. Keep the novel remainder."""
    raw = (text or "").strip()
    if not raw:
        return ""
    raw_refs = [x for x in (assistant_lines or []) if (x or "").strip()]
    if not raw_refs:
        return raw
    try:
        from src.agent.command_clause import segment_utterance

        segs = segment_utterance(raw) or [normalize_speech(raw)]
        refs: list[str] = []
        for line in raw_refs:
            refs.append(normalize_speech(line))
            refs.extend(segment_utterance(line))
        refs = [normalize_speech(x) for x in refs if x]
    except Exception:
        segs = [p.strip() for p in re.split(r"[.!?]+", raw) if p.strip()] or [raw]
        refs = [normalize_speech(x) for x in raw_refs]
    kept: list[str] = []
    for seg in segs:
        n = normalize_speech(seg)
        if not n:
            continue
        if any(echo_score(n, ref) >= 0.68 for ref in refs):
            continue
        if any(_leading_echo_prefix(n, ref) for ref in refs):
            peeled = _strip_shared_prefix(n, refs)
            if peeled and not any(echo_score(peeled, ref) >= 0.68 for ref in refs):
                kept.append(peeled)
            continue
        kept.append(seg)
    return " ".join(kept).strip()


def _leading_echo_prefix(user: str, ref: str) -> bool:
    uw, rw = user.split(), ref.split()
    if len(uw) < 3 or len(rw) < 3:
        return False
    shared = 0
    for a, b in zip(uw, rw):
        if a == b or (len(a) >= 4 and (a.startswith(b) or b.startswith(a))):
            shared += 1
        else:
            break
    return shared >= 3


def _strip_shared_prefix(user: str, refs: list[str]) -> str:
    words = user.split()
    best = 0
    for ref in refs:
        rw = ref.split()
        n = 0
        for a, b in zip(words, rw):
            if a == b or (len(a) >= 4 and (a.startswith(b) or b.startswith(a))):
                n += 1
            else:
                break
        best = max(best, n)
    if best < 3:
        return user
    return " ".join(words[best:]).strip()


def _stem_in(token: str, others: set[str]) -> bool:
    if token in others:
        return True
    if len(token) < 3:
        return False
    return any(o.startswith(token) or token.startswith(o) for o in others if len(o) >= 3)


def meaningful_tokens(text: str) -> list[str]:
    words = normalize_speech(text).split()
    out: list[str] = []
    for w in words:
        if w in STOPWORDS:
            continue
        if len(w) >= 3 or w in ACTION_WORDS:
            out.append(w)
    return out


def detect_imperatives(stt_text: str) -> list[str]:
    payload, _removed = strip_assistant_prefix(stt_text)
    n = normalize_speech(payload or stt_text)
    if not n:
        return []
    found: list[str] = []
    for phrase in ACTION_PHRASES:
        if " " in phrase or "-" in phrase:
            if phrase in n:
                found.append(phrase)
        elif re.search(rf"\b{re.escape(phrase)}\b", n):
            found.append(phrase)
    words = n.split()
    if words and words[0] in ACTION_WORDS:
        if words[0] not in found:
            found.append(words[0])
    return found


def _phrase_in_tts(phrase: str, tts_norm: str, tts_tokens: set[str]) -> bool:
    if phrase in tts_norm:
        return True
    # Multi-word actions must appear as the phrase. "check" in a task name
    # must not count as the user saying "check off".
    if " " in phrase or "-" in phrase:
        return False
    return _stem_in(phrase, tts_tokens)


@dataclass
class EchoAnalysis:
    tts_similarity: float = 0.0
    raw_tts_similarity: float = 0.0
    payload_tts_similarity: float = 0.0
    novel_token_ratio: float = 0.0
    novel_tokens: list[str] = field(default_factory=list)
    payload_tokens: list[str] = field(default_factory=list)
    tts_tokens: list[str] = field(default_factory=list)
    shared_tokens: list[str] = field(default_factory=list)
    recognized_intent: str = ""
    imperative_detected: bool = False
    novel_imperative: bool = False
    during_tts: bool = False
    decision: str = ""
    decision_reason: str = ""
    assistant_prefix_removed: str = ""
    normalized_payload: str = ""
    original_transcript: str = ""
    pre_correction_payload: str = ""
    corrections_applied: list[str] = field(default_factory=list)


def analyze_against_tts(
    stt_text: str,
    tts_text: str,
    *,
    recognized_intent: str = "",
    during_tts: bool = False,
    is_interrupt: bool = False,
    confidence: float = 1.0,
) -> EchoAnalysis:
    """
    Combined evidence. echoScore alone is never the final decision.

    SELF_SPEECH_ECHO: substantially reproduces TTS with no new user-directed intent.
    USER_BARGE_IN / USER_COMMAND: novel imperative or novel command tokens + intent.
    """
    payload, prefix = strip_assistant_prefix(stt_text)
    pre_norm = normalize_speech(payload or stt_text)
    corrected, corrections = correct_command_stt(payload or stt_text, addressed=bool(prefix))
    payload_norm = corrected or pre_norm
    analysis = EchoAnalysis(
        during_tts=during_tts,
        recognized_intent=recognized_intent or "",
        original_transcript=(stt_text or "").strip(),
        assistant_prefix_removed=prefix,
        normalized_payload=payload_norm,
        pre_correction_payload=pre_norm,
        corrections_applied=list(corrections),
    )
    tts_norm = normalize_speech(tts_text)
    stt_norm = normalize_speech(stt_text)
    analysis.raw_tts_similarity = echo_score(stt_text, tts_text) if tts_norm else 0.0
    analysis.payload_tts_similarity = echo_score(payload_norm, tts_text) if tts_norm else 0.0
    # Wake-word-stripped (and corrected) similarity drives the decision; raw is telemetry.
    analysis.tts_similarity = analysis.payload_tts_similarity

    novelty = token_novelty(payload_norm, tts_text)
    analysis.payload_tokens = list(novelty["payload_tokens"])
    analysis.tts_tokens = list(novelty["tts_tokens"])
    analysis.shared_tokens = list(novelty["shared_tokens"])
    analysis.novel_tokens = list(novelty["novel_tokens"])
    analysis.novel_token_ratio = float(novelty["novel_token_ratio"])

    tts_tokens = set(analysis.tts_tokens)
    imperatives = detect_imperatives(payload_norm or stt_text)
    analysis.imperative_detected = bool(imperatives) or is_interrupt
    analysis.novel_imperative = any(not _phrase_in_tts(p, tts_norm, tts_tokens) for p in imperatives)
    if is_interrupt:
        tts_same = tts_norm == stt_norm or tts_norm.rstrip("ing") == stt_norm
        if not tts_same and stt_norm not in {normalize_speech(tts_text)}:
            if not (len(stt_norm.split()) == 1 and _stem_in(stt_norm, tts_tokens) and analysis.tts_similarity >= 0.85):
                analysis.novel_imperative = True
                analysis.imperative_detected = True

    intent = (recognized_intent or "").strip()
    protected = during_tts
    social = intent.startswith("social.") or intent.startswith("confirm.")
    sim = analysis.payload_tts_similarity

    def user_decision() -> str:
        return VoiceDecision.USER_BARGE_IN.value if protected else VoiceDecision.USER_COMMAND.value

    if analysis.novel_imperative and intent:
        analysis.decision = user_decision()
        analysis.decision_reason = "novel_imperative+intent"
        return analysis

    if analysis.novel_imperative and is_interrupt:
        analysis.decision = user_decision()
        analysis.decision_reason = "novel_interrupt"
        return analysis

    if protected and social and not analysis.novel_imperative:
        if sim >= 0.45 or analysis.novel_token_ratio < 0.55:
            analysis.decision = VoiceDecision.SELF_SPEECH_ECHO.value
            analysis.decision_reason = "social_or_ack_during_tts"
            return analysis

    # Exact reproduction of what JARVIS just said is echo even if it looks like a command.
    if tts_norm and sim >= 0.88 and not analysis.novel_imperative:
        analysis.decision = VoiceDecision.SELF_SPEECH_ECHO.value
        analysis.decision_reason = "near_exact_tts"
        return analysis

    if prefix and intent and sim < 0.88:
        analysis.decision = user_decision()
        analysis.decision_reason = "addressed_assistant+intent"
        return analysis

    if intent and analysis.novel_token_ratio >= 0.20 and sim < 0.92:
        analysis.decision = user_decision()
        analysis.decision_reason = "intent+novel_tokens"
        return analysis

    if intent and analysis.novel_imperative:
        analysis.decision = user_decision()
        analysis.decision_reason = "intent+imperative"
        return analysis

    if intent and analysis.novel_token_ratio >= 0.35:
        analysis.decision = user_decision()
        analysis.decision_reason = "intent+strong_novel_tokens"
        return analysis

    if prefix and analysis.novel_token_ratio >= 0.35 and sim < 0.80:
        analysis.decision = user_decision()
        analysis.decision_reason = "addressed_assistant+novel_payload"
        return analysis

    # High fuzzy similarity alone is not enough — need low novelty too.
    if tts_norm and sim >= 0.62 and analysis.novel_token_ratio < 0.22 and not analysis.novel_imperative:
        analysis.decision = VoiceDecision.SELF_SPEECH_ECHO.value
        analysis.decision_reason = "reproduces_tts_without_new_intent"
        return analysis

    if protected and not intent and not analysis.novel_imperative:
        analysis.decision = VoiceDecision.SELF_SPEECH_ECHO.value
        analysis.decision_reason = "protected_window_no_new_intent"
        return analysis

    if tts_norm and sim >= 0.62 and analysis.novel_token_ratio < 0.25 and not analysis.novel_imperative and not intent:
        analysis.decision = VoiceDecision.SELF_SPEECH_ECHO.value
        analysis.decision_reason = "high_similarity_no_intent"
        return analysis

    analysis.decision = user_decision()
    analysis.decision_reason = "meaningful_user_speech"
    return analysis


class TtsMemory:
    def __init__(self, *, echo_window_s: float = 0.55, match_threshold: float = 0.62, keep: int = 8):
        self.echo_window_s = echo_window_s
        self.match_threshold = match_threshold
        self.current_text = ""
        self.started_at = 0.0
        self.ended_at = 0.0
        self.speaking = False
        self.recent: deque[tuple[float, str]] = deque(maxlen=keep)

    def on_start(self, text: str) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        self.current_text = clean
        self.started_at = time.time()
        self.speaking = True
        self.recent.append((self.started_at, clean))

    def on_end(self) -> None:
        self.speaking = False
        self.ended_at = time.time()

    def in_echo_window(self, now: Optional[float] = None) -> bool:
        ts = now if now is not None else time.time()
        if self.speaking:
            return True
        if not self.ended_at:
            return False
        return (ts - self.ended_at) <= self.echo_window_s

    def blob(self, now: Optional[float] = None) -> str:
        ts = now if now is not None else time.time()
        parts: list[str] = []
        if self.current_text:
            parts.append(self.current_text)
        for started, text in self.recent:
            if ts - started <= 12.0:
                parts.append(text)
        return " ".join(parts)

    def score(self, stt_text: str) -> float:
        best = 0.0
        if self.current_text:
            best = max(best, echo_score(stt_text, self.current_text))
        now = time.time()
        for ts, text in self.recent:
            if now - ts > 12.0:
                continue
            best = max(best, echo_score(stt_text, text))
        return best

    def is_echo(self, stt_text: str) -> bool:
        """Raw similarity helper. The quality gate must not use this as the final decision."""
        score = self.score(stt_text)
        if score < self.match_threshold:
            return False
        now = time.time()
        if self.speaking or self.in_echo_window():
            return True
        if self.ended_at and (now - self.ended_at) < 8.0:
            return True
        if self.started_at and (now - self.started_at) < 12.0 and score >= 0.78:
            return True
        return False
