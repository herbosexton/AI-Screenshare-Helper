"""Voice interaction states. Components must not act without this."""

from __future__ import annotations

from enum import Enum


class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    USER_SPEAKING = "USER_SPEAKING"
    FINALIZING = "FINALIZING"
    PROCESSING = "PROCESSING"
    ACTING = "ACTING"
    TTS_SPEAKING = "TTS_SPEAKING"
    BARGE_IN = "BARGE_IN"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


class VoiceSource(str, Enum):
    USER_VOICE = "USER_VOICE"
    USER_TEXT = "USER_TEXT"
    JARVIS_TTS = "JARVIS_TTS"
    SYSTEM_EVENT = "SYSTEM_EVENT"


class VoiceDecision(str, Enum):
    """Final classifier output. echoScore is evidence, not the decision."""

    SELF_SPEECH_ECHO = "SELF_SPEECH_ECHO"
    USER_BARGE_IN = "USER_BARGE_IN"
    USER_COMMAND = "USER_COMMAND"
    LOW_CONFIDENCE_NOISE = "LOW_CONFIDENCE_NOISE"
    PARTIAL = "PARTIAL"
    FILLER = "FILLER"
    DUPLICATE = "DUPLICATE"
    TOO_SHORT = "TOO_SHORT"
    EMPTY = "EMPTY"
    STALE = "STALE"
    NOT_FINAL = "NOT_FINAL"
    DURING_TTS = "DURING_TTS"
    NOISE = "NOISE"


class DiscardReason(str, Enum):
    SELF_SPEECH_ECHO = "SELF_SPEECH_ECHO"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    TOO_SHORT = "TOO_SHORT"
    DUPLICATE = "DUPLICATE"
    PARTIAL = "PARTIAL"
    NOISE = "NOISE"
    STALE = "STALE"
    FILLER = "FILLER"
    NOT_FINAL = "NOT_FINAL"
    DURING_TTS = "DURING_TTS"
    EMPTY = "EMPTY"
