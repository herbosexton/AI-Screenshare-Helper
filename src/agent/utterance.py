"""Classify what kind of utterance this is. Not which local action to run."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.agent.voice_echo import normalize_speech


DIRECT_COMMAND = "DIRECT_COMMAND"
COMPLEX_GOAL = "COMPLEX_GOAL"
QUESTION = "QUESTION"
SOCIAL = "SOCIAL"
ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
CORRECTION = "CORRECTION"
DECLARATIVE_COMMENT = "DECLARATIVE_COMMENT"
FRAGMENT = "FRAGMENT"
NOISE = "NOISE"
UNKNOWN = "UNKNOWN"

LOCAL_ACTION = "LOCAL_ACTION"
LOCAL_CONVERSATION = "LOCAL_CONVERSATION"
AI_CONVERSATION = "AI_CONVERSATION"
AGENT_PLAN = "AGENT_PLAN"

_WH = {"what", "why", "how", "when", "where", "who", "which", "whose"}
_AUX_Q = {"do", "does", "did", "can", "could", "would", "should", "is", "are", "am", "was", "were"}
_IMPERATIVE = {
    "open",
    "close",
    "click",
    "press",
    "tap",
    "type",
    "find",
    "search",
    "look",
    "add",
    "create",
    "put",
    "delete",
    "remove",
    "complete",
    "finish",
    "undo",
    "scroll",
    "go",
    "launch",
    "start",
    "save",
    "send",
    "compare",
    "apply",
    "fill",
    "mark",
    "cross",
    "read",
    "list",
    "show",
    "tell",
    "navigate",
    "switch",
    "check",
    "uncheck",
    "cancel",
    "stop",
    "pause",
    "resume",
    # Verbs a user reaches for mid-task. Bare "continue"/"stop" is caught earlier as a
    # control command; here they matter as the head of a longer instruction.
    "continue",
    "proceed",
    "enter",
    "select",
    "choose",
    "submit",
    "upload",
    "attach",
    "download",
    "copy",
    "move",
    "rename",
    "focus",
    "bring",
    "minimize",
    "maximize",
}
_KNOWLEDGE = {"explain", "describe", "summarize"}
_SOCIAL_HEAD = {"thanks", "thank", "bye", "goodbye", "hello", "hi", "hey"}
_STANCE_ADJ = {
    "fine",
    "good",
    "better",
    "okay",
    "ok",
    "alright",
    "cool",
    "great",
    "enough",
    "all",
}
_DEFER = {"later", "tomorrow", "tonight"}
_CORRECTION = {"meant", "mean", "instead", "wrong", "other"}
_DEMONSTRATIVE = re.compile(r"^(that|thats|this|those|it)(s| is| one| ones)?\b")
_MULTI = re.compile(r"\b(and then|then|after that|and also|and tell me)\b")
_FILE = re.compile(r"\b(pdf|resume|file|document|folder|docx|spreadsheet)\b")
_SURFACE = re.compile(r"\b(screen|page|website|site|browser|tab|window|job)\b")
_TOOLISH = re.compile(
    r"\b(find|search|look|compare|open|save|click|apply|send|download|upload|"
    r"fill|navigate|start|launch|research)\b"
)
_FILLER = {"uh", "um", "ah", "oh", "hmm", "huh", "er", "mm", "mmm"}
_LETS = re.compile(r"^(lets|let us|please)\b")

# Openers that carry no intent of their own. "now go to chatgpt" is a command with a word
# in front of it, and reading only the first word classified the whole thing as UNKNOWN,
# which meant Jarvis silently did nothing.
_FILLER_HEAD = {
    "now",
    "then",
    "also",
    "just",
    "so",
    "actually",
    "next",
    "and",
    "well",
    "first",
    "again",
    "quickly",
    "still",
}


@dataclass
class UtteranceClass:
    utterance_type: str = UNKNOWN
    conceptual_route: str = LOCAL_CONVERSATION
    reason: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    classify_ms: float = 0.0
    normalized: str = ""


def classify_utterance(
    text: str,
    *,
    pending_intent: str = "",
    recent_domain: str = "",
    has_local_intent: bool = False,
    command_clause: str = "",
) -> UtteranceClass:
    t0 = time.perf_counter()
    raw = (text or "").strip()
    n = normalize_speech(raw)
    words = n.split()
    out = UtteranceClass(normalized=n)
    feats = {
        "word_count": len(words),
        "has_question_mark": raw.endswith("?"),
        "has_imperative": bool(words and _content_head(words) in _IMPERATIVE),
        "has_wh": bool(words and words[0] in _WH),
        "multi_step": bool(_MULTI.search(n)),
        "file_cue": bool(_FILE.search(n)),
        "surface_cue": bool(_SURFACE.search(n)),
        "tool_verbs": len(_TOOLISH.findall(n)),
        "pending_intent": pending_intent,
        "recent_domain": recent_domain,
        "has_local_intent": has_local_intent,
        "has_command_clause": bool(command_clause),
    }
    out.features = feats

    if not n:
        out.utterance_type = NOISE
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "empty"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if n in _FILLER or (len(words) == 1 and words[0] in _FILLER):
        out.utterance_type = NOISE
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "filler"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if has_local_intent or command_clause:
        if _is_complex_goal(n, words, feats):
            out.utterance_type = COMPLEX_GOAL
            out.conceptual_route = AGENT_PLAN
            out.reason = "local_intent_but_multi_tool"
        else:
            out.utterance_type = DIRECT_COMMAND
            out.conceptual_route = LOCAL_ACTION
            out.reason = "resolved_or_command_clause"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if _is_correction(n, words):
        out.utterance_type = CORRECTION
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "correction_cues"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if pending_intent and _looks_like_clarification(n, words):
        out.utterance_type = CLARIFICATION_RESPONSE
        out.conceptual_route = LOCAL_ACTION
        out.reason = "pending_short_reply"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if _is_complex_goal(n, words, feats):
        out.utterance_type = COMPLEX_GOAL
        out.conceptual_route = AGENT_PLAN
        out.reason = "multi_step_or_cross_tool"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if _is_question(raw, n, words):
        if _is_knowledge_question(n, words, feats):
            out.utterance_type = QUESTION
            out.conceptual_route = AI_CONVERSATION
            out.reason = "knowledge_or_why_how"
        elif feats["tool_verbs"] >= 2 or feats["multi_step"]:
            out.utterance_type = COMPLEX_GOAL
            out.conceptual_route = AGENT_PLAN
            out.reason = "question_shaped_goal"
        else:
            out.utterance_type = QUESTION
            out.conceptual_route = AI_CONVERSATION
            out.reason = "interrogative"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if words and words[0] in _SOCIAL_HEAD:
        out.utterance_type = SOCIAL
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "social_head"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if _is_acknowledgement(n, words):
        out.utterance_type = ACKNOWLEDGEMENT
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "short_stance_or_deferral"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if _is_declarative_comment(n, words):
        out.utterance_type = DECLARATIVE_COMMENT
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "demonstrative_noun_phrase"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    head = _content_head(words)
    if head in _KNOWLEDGE and feats["tool_verbs"] == 0:
        out.utterance_type = QUESTION
        out.conceptual_route = AI_CONVERSATION
        out.reason = "explain_without_tools"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if head in _IMPERATIVE:
        if _is_complex_goal(n, words, feats):
            out.utterance_type = COMPLEX_GOAL
            out.conceptual_route = AGENT_PLAN
            out.reason = "imperative_multi_tool"
        else:
            out.utterance_type = DIRECT_COMMAND
            out.conceptual_route = LOCAL_ACTION
            out.reason = "leading_imperative"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    if len(words) <= 3 and not any(w in _IMPERATIVE for w in words):
        out.utterance_type = FRAGMENT
        out.conceptual_route = LOCAL_CONVERSATION
        out.reason = "short_noun_phrase"
        out.classify_ms = (time.perf_counter() - t0) * 1000
        return out

    out.utterance_type = UNKNOWN
    # Unclassified is not the same as unimportant. A whole sentence nobody could place —
    # "I don't see ChatGPT" — is usually the user reacting to what just happened, and
    # answering silence to it is how Jarvis ends up ignoring people. The type stays UNKNOWN
    # so this still cannot reach the planner; it only earns a reply.
    out.conceptual_route = AI_CONVERSATION if len(words) >= 3 else LOCAL_CONVERSATION
    out.reason = "no_goal_evidence"
    out.classify_ms = (time.perf_counter() - t0) * 1000
    return out


def _content_head(words: list[str]) -> str:
    """The first word that means something, past "let's", "please", "now", "just"."""
    i = 0
    while i < len(words) and (_LETS.match(words[i]) or words[i] in _FILLER_HEAD):
        i += 1
    if i < len(words) and words[i] in {"us"}:
        i += 1
    return words[i] if i < len(words) else ""


def has_action_evidence(classified: "UtteranceClass") -> bool:
    """Whether an utterance asked for something, even if nothing could parse what."""
    feats = classified.features or {}
    if feats.get("tool_verbs"):
        return True
    return any(w in _IMPERATIVE for w in (classified.normalized or "").split())


def _is_question(raw: str, n: str, words: list[str]) -> bool:
    if raw.endswith("?"):
        return True
    if not words:
        return False
    if words[0] in _WH or words[0] in _AUX_Q:
        return True
    if words[0] in _KNOWLEDGE:
        return True
    return False


def _is_knowledge_question(n: str, words: list[str], feats: dict[str, Any]) -> bool:
    if words and words[0] in {"why", "how"}:
        return feats["tool_verbs"] == 0
    if words and words[0] in _KNOWLEDGE:
        return feats["tool_verbs"] == 0 and not feats["multi_step"]
    if words and words[0] == "what" and feats["tool_verbs"] == 0 and not feats["file_cue"]:
        local_q = bool(re.search(r"\b(task|tasks|list|page|tab|url|window|screen)\b", n))
        return not local_q
    return False


def _is_complex_goal(n: str, words: list[str], feats: dict[str, Any]) -> bool:
    actions = [w for w in words if w in _IMPERATIVE]
    if feats["multi_step"] and (feats["tool_verbs"] >= 1 or len(actions) >= 1):
        return True
    if feats["tool_verbs"] >= 2 and (feats["file_cue"] or feats["surface_cue"] or "and" in words):
        return True
    if len(actions) >= 2 and "and" in words:
        return True
    if feats["file_cue"] and feats["surface_cue"] and feats["tool_verbs"] >= 1:
        return True
    if "compare" in words and feats["file_cue"]:
        return True
    if _LETS.match(n) and (feats["tool_verbs"] >= 2 or len(actions) >= 2):
        return True
    return False


def _is_correction(n: str, words: list[str]) -> bool:
    if any(w in _CORRECTION for w in words) and (
        "not" in words or "meant" in words or "mean" in words or "instead" in words or "other" in words
    ):
        return True
    if n.startswith("thats not") or n.startswith("that is not") or n.startswith("that isnt"):
        return True
    if re.search(r"\byou (didn'?t|did not)\b", n):
        return True
    if re.search(r"\b(wrong (resume|file|page)|you closed it|you opened the wrong)\b", n):
        return True
    return False


def _looks_like_clarification(n: str, words: list[str]) -> bool:
    if len(words) <= 6 and not n.endswith("?"):
        if words and words[0] not in _IMPERATIVE and words[0] not in _WH:
            return True
    return False


def _is_acknowledgement(n: str, words: list[str]) -> bool:
    if not words or len(words) > 6:
        return False
    if words[0] in {"ok", "okay", "alright", "got"}:
        return True
    if words[:2] == ["i", "see"] or words[:2] == ["got", "it"]:
        return True
    if words[0] in {"maybe", "not"} and any(w in _DEFER or w in {"now", "yet"} for w in words):
        return True
    if _DEMONSTRATIVE.match(n):
        rest = _after_demonstrative(n)
        rest_words = rest.split()
        if not rest_words:
            return True
        if len(rest_words) <= 2 and all(w in _STANCE_ADJ or w in {"then", "now", "it"} for w in rest_words):
            return True
    return False


def _is_declarative_comment(n: str, words: list[str]) -> bool:
    if not _DEMONSTRATIVE.match(n):
        return False
    if words and words[0] in _IMPERATIVE:
        return False
    rest = _after_demonstrative(n)
    rest_words = rest.split()
    if not rest_words:
        return False
    if rest_words[0] in _IMPERATIVE:
        return False
    if any(w in _IMPERATIVE for w in rest_words[:2]) and rest_words[0] in _IMPERATIVE:
        return False
    # Demonstrative + noun phrase / participle phrase, no request.
    has_nounish = any(len(w) >= 3 and w not in _STANCE_ADJ for w in rest_words)
    return has_nounish and not n.endswith("?")


def _after_demonstrative(n: str) -> str:
    return re.sub(r"^(thats|that is|that ones|that one|this is|those are|its|it is|that)\s+", "", n).strip()
