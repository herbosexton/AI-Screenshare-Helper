"""Classify and sanitize model output before it can reach the user or TTS."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from src.agent.providers.base import ToolCallRequest


USER_RESPONSE = "USER_RESPONSE"
TOOL_CALL = "TOOL_CALL"
STRUCTURED_PLAN = "STRUCTURED_PLAN"
ERROR = "ERROR"
RAW_MODEL_OUTPUT = "RAW_MODEL_OUTPUT"
DEBUG = "DEBUG"
CHAIN_OF_THOUGHT = "CHAIN_OF_THOUGHT"

SAFE_FALLBACK = "I had trouble processing that."

_TOOL_JSON = re.compile(
    r"\{[^{}]*\"name\"\s*:\s*\"[a-zA-Z0-9_.:-]+\"[^{}]*\"arguments\"\s*:\s*\{[^{}]*\}[^{}]*\}",
    re.S,
)
_TOOL_XML = re.compile(
    r"<(?:tool_call|function_call|invoke|tool)\b[^>]*>.*?</(?:tool_call|function_call|invoke|tool)>",
    re.I | re.S,
)
_FUNC_STYLE = re.compile(
    r"\b(?:tool|function)\s*call\s*:?\s*[a-zA-Z0-9_.]+",
    re.I,
)
_INTERNAL_NAMES = re.compile(
    r"\b(?:get_status|computer\.|browser\.|screen\.|speech\.speak|tool_call)\b",
    re.I,
)


@dataclass
class ClassifiedOutput:
    output_type: str
    spoken: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tts_allowed: bool = False
    tts_block_reason: str = ""
    response_sanitized: bool = False
    raw: str = ""


def extract_tool_calls_from_text(text: str) -> list[ToolCallRequest]:
    found: list[ToolCallRequest] = []
    seen: set[str] = set()
    blobs = [m.group(0) for m in _TOOL_JSON.finditer(text or "")]
    if not blobs:
        blobs = [m.group(0) for m in re.finditer(r"\{[^{}]*\}", text or "")]
    for i, blob in enumerate(blobs):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or data.get("tool") or data.get("function") or "").strip()
        args = data.get("arguments")
        if args is None:
            args = data.get("args") or data.get("parameters")
        if not name:
            continue
        if not isinstance(args, dict):
            args = {}
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if key in seen:
            continue
        seen.add(key)
        found.append(ToolCallRequest(id=f"parsed_{i}", name=name, arguments=args))
    return found


def classify_model_output(text: str) -> ClassifiedOutput:
    raw = (text or "").strip()
    out = ClassifiedOutput(output_type=USER_RESPONSE, raw=raw, spoken=raw, tts_allowed=True)
    if not raw:
        out.output_type = USER_RESPONSE
        out.spoken = ""
        out.tts_allowed = False
        out.tts_block_reason = "empty"
        return out

    if re.search(r"<think>", raw, re.I):
        out.output_type = CHAIN_OF_THOUGHT
        out.tts_allowed = False
        out.tts_block_reason = "chain_of_thought"
        out.spoken = SAFE_FALLBACK
        out.response_sanitized = True
        return out

    tools = extract_tool_calls_from_text(raw)
    if tools:
        out.output_type = TOOL_CALL
        out.tool_calls = tools
        out.tts_allowed = False
        out.tts_block_reason = "tool_call_json"
        out.spoken = SAFE_FALLBACK
        out.response_sanitized = True
        return out

    if _TOOL_XML.search(raw) or _FUNC_STYLE.search(raw):
        out.output_type = TOOL_CALL
        out.tts_allowed = False
        out.tts_block_reason = "tool_call_syntax"
        out.spoken = SAFE_FALLBACK
        out.response_sanitized = True
        return out

    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        out.output_type = RAW_MODEL_OUTPUT
        out.tts_allowed = False
        out.tts_block_reason = "raw_json"
        out.spoken = SAFE_FALLBACK
        out.response_sanitized = True
        return out

    if _INTERNAL_NAMES.search(raw) and ("{" in raw or "arguments" in raw.lower()):
        out.output_type = RAW_MODEL_OUTPUT
        out.tts_allowed = False
        out.tts_block_reason = "internal_tool_syntax"
        out.spoken = SAFE_FALLBACK
        out.response_sanitized = True
        return out

    if re.search(r'["\']name["\']\s*:', raw) and re.search(r'["\']arguments["\']\s*:', raw):
        out.output_type = RAW_MODEL_OUTPUT
        out.tts_allowed = False
        out.tts_block_reason = "malformed_tool_json"
        out.spoken = SAFE_FALLBACK
        out.response_sanitized = True
        return out

    return out


def sanitize_for_tts(text: str) -> ClassifiedOutput:
    classified = classify_model_output(text)
    if classified.output_type != USER_RESPONSE:
        classified.tts_allowed = False
        if not classified.tts_block_reason:
            classified.tts_block_reason = classified.output_type.lower()
        classified.spoken = SAFE_FALLBACK
        classified.response_sanitized = True
        return classified
    if classified.tts_block_reason == "empty":
        classified.tts_allowed = False
        classified.spoken = ""
        return classified
    classified.tts_allowed = True
    classified.tts_block_reason = ""
    return classified
