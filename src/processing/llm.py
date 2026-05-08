import os
from typing import Optional

import anthropic
import openai


class LLMClient:
    """
    LLM integration supporting Anthropic Claude (with vision) and OpenAI fallback.
    Handles both text-only and vision (screenshot analysis) queries.
    """

    SYSTEM_PROMPT = (
        "You are a helpful assistant analyzing screen content and audio transcriptions. "
        "Your job is to identify questions, problems, or tasks visible on screen or mentioned in conversation, "
        "and provide clear, accurate, concise answers. "
        "For coding questions, provide working code solutions. "
        "For interview questions, provide thoughtful answers that demonstrate understanding. "
        "Be direct and actionable. Do not add unnecessary preamble."
    )

    def __init__(self, config: dict):
        self.provider = config.get("provider", "anthropic")
        self.model = config.get("model", "claude-sonnet-4-20250514")
        self.fallback_provider = config.get("fallback_provider", "openai")
        self.fallback_model = config.get("fallback_model", "gpt-4o")

        self._anthropic: Optional[anthropic.Anthropic] = None
        self._openai: Optional[openai.OpenAI] = None

        self._init_clients()

    def _init_clients(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            self._anthropic = anthropic.Anthropic(api_key=api_key)

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            self._openai = openai.OpenAI(api_key=openai_key)

    def query(self, prompt: str, screenshots: Optional[list[dict]] = None) -> str:
        """
        Send a query to the LLM. If screenshots are provided, uses vision capabilities.
        Falls back to secondary provider on failure.
        """
        try:
            if self.provider == "anthropic":
                return self._query_anthropic(prompt, screenshots)
            else:
                return self._query_openai(prompt, screenshots)
        except Exception as e:
            print(f"[LLM] Primary provider ({self.provider}) failed: {e}")
            try:
                if self.fallback_provider == "anthropic":
                    return self._query_anthropic(prompt, screenshots)
                else:
                    return self._query_openai(prompt, screenshots)
            except Exception as fallback_error:
                return f"Error: Both LLM providers failed. Primary: {e} | Fallback: {fallback_error}"

    def query_stream(self, prompt: str, screenshots: Optional[list[dict]] = None):
        """
        Stream a response from the LLM. Yields text chunks as they arrive.
        """
        try:
            if self.provider == "anthropic":
                yield from self._stream_anthropic(prompt, screenshots)
            else:
                yield from self._stream_openai(prompt, screenshots)
        except Exception as e:
            yield f"[Error: {e}]"

    def _query_anthropic(self, prompt: str, screenshots: Optional[list[dict]] = None) -> str:
        if not self._anthropic:
            raise RuntimeError("Anthropic client not initialized (missing API key)")

        messages = self._build_anthropic_messages(prompt, screenshots)

        response = self._anthropic.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.SYSTEM_PROMPT,
            messages=messages,
        )

        return response.content[0].text

    def _stream_anthropic(self, prompt: str, screenshots: Optional[list[dict]] = None):
        if not self._anthropic:
            raise RuntimeError("Anthropic client not initialized (missing API key)")

        messages = self._build_anthropic_messages(prompt, screenshots)

        with self._anthropic.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=self.SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    def _build_anthropic_messages(self, prompt: str, screenshots: Optional[list[dict]] = None) -> list[dict]:
        content = []

        if screenshots:
            for shot in screenshots:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": shot["base64"],
                    },
                })

        content.append({"type": "text", "text": prompt})

        return [{"role": "user", "content": content}]

    def _query_openai(self, prompt: str, screenshots: Optional[list[dict]] = None) -> str:
        if not self._openai:
            raise RuntimeError("OpenAI client not initialized (missing API key)")

        messages = self._build_openai_messages(prompt, screenshots)

        response = self._openai.chat.completions.create(
            model=self.fallback_model if self.provider != "openai" else self.model,
            messages=messages,
            max_tokens=4096,
        )

        return response.choices[0].message.content

    def _stream_openai(self, prompt: str, screenshots: Optional[list[dict]] = None):
        if not self._openai:
            raise RuntimeError("OpenAI client not initialized (missing API key)")

        messages = self._build_openai_messages(prompt, screenshots)

        response = self._openai.chat.completions.create(
            model=self.fallback_model if self.provider != "openai" else self.model,
            messages=messages,
            max_tokens=4096,
            stream=True,
        )

        for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _build_openai_messages(self, prompt: str, screenshots: Optional[list[dict]] = None) -> list[dict]:
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        content = []
        if screenshots:
            for shot in screenshots:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{shot['base64']}",
                        "detail": "high",
                    },
                })

        content.append({"type": "text", "text": prompt})
        messages.append({"role": "user", "content": content})

        return messages
