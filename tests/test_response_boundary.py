"""Model output must never reach TTS as raw JSON or tool syntax."""

from src.agent.response_boundary import (
    SAFE_FALLBACK,
    TOOL_CALL,
    USER_RESPONSE,
    classify_model_output,
    extract_tool_calls_from_text,
    sanitize_for_tts,
)
from src.output.speech import SpeechOutput


def test_classify_tool_json_with_parser_prefix():
    raw = 'ronics\n{"name": "get_status", "arguments": {}}'
    classified = classify_model_output(raw)
    assert classified.output_type == TOOL_CALL
    assert classified.tts_allowed is False
    assert classified.tool_calls
    assert classified.tool_calls[0].name == "get_status"
    spoken = sanitize_for_tts(raw)
    assert spoken.tts_allowed is False
    assert spoken.spoken == SAFE_FALLBACK
    assert "{" not in spoken.spoken


def test_extract_and_block_bare_json():
    raw = '{"name": "get_status", "arguments": {}}'
    tools = extract_tool_calls_from_text(raw)
    assert len(tools) == 1
    assert tools[0].name == "get_status"
    spoken = sanitize_for_tts(raw)
    assert spoken.output_type == TOOL_CALL
    assert spoken.tts_allowed is False
    assert spoken.spoken == SAFE_FALLBACK


def test_user_response_still_allowed():
    spoken = sanitize_for_tts("Added.")
    assert spoken.output_type == USER_RESPONSE
    assert spoken.tts_allowed is True
    assert spoken.spoken == "Added."


def test_speech_queue_never_gets_raw_json():
    out = SpeechOutput.__new__(SpeechOutput)
    out._queue = __import__("collections").deque()
    out._lock = __import__("threading").Lock()
    out._event = __import__("threading").Event()
    out.speak('ronics\n{"name": "get_status", "arguments": {}}')
    assert list(out._queue)
    _prio, text = out._queue[0]
    assert "{" not in text
    assert "get_status" not in text
    assert text == SAFE_FALLBACK
