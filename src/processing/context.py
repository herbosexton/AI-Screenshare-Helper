import collections
from typing import Optional


class ContextBuilder:
    """
    Maintains rolling context of screen captures, audio transcriptions,
    and previous Q&A pairs. Builds structured prompts for the LLM.
    """

    def __init__(self, max_screenshots: int = 5, max_transcriptions: int = 20, max_qa: int = 10):
        self._screenshots: collections.deque = collections.deque(maxlen=max_screenshots)
        self._transcriptions: collections.deque = collections.deque(maxlen=max_transcriptions)
        self._qa_history: collections.deque = collections.deque(maxlen=max_qa)

    def add_screenshots(self, screenshots: list[dict]):
        """Add captured screenshots to context (stores metadata, not full images)."""
        self._screenshots.append({
            "count": len(screenshots),
            "monitors": [s["monitor_index"] for s in screenshots],
        })

    def add_transcription(self, text: str):
        """Add a transcribed audio segment to context."""
        if text.strip():
            self._transcriptions.append(text.strip())

    def add_qa_pair(self, question: str, answer: str):
        """Store a previous Q&A exchange for continuity."""
        self._qa_history.append({"q": question, "a": answer})

    def build_prompt(self, user_question: Optional[str] = None) -> str:
        """
        Build a structured prompt combining all context.
        If user_question is provided, asks that specific question.
        Otherwise, asks the LLM to identify and answer questions on screen.
        """
        parts = []

        if self._transcriptions:
            parts.append("## Recent Audio Transcription")
            recent_audio = list(self._transcriptions)[-10:]
            parts.append(" ".join(recent_audio))
            parts.append("")

        if self._qa_history:
            parts.append("## Previous Q&A in this session")
            for qa in list(self._qa_history)[-5:]:
                parts.append(f"Q: {qa['q'][:200]}")
                parts.append(f"A: {qa['a'][:300]}")
                parts.append("")

        parts.append("## Current Task")
        if user_question:
            parts.append(
                "I'm sharing my screen(s) with you. Based on what you see and the audio context above, "
                f"please answer this question:\n\n{user_question}"
            )
        else:
            parts.append(
                "I'm sharing my screen(s) with you. Please analyze what's visible and:\n"
                "1. Identify any questions, problems, or tasks shown on screen\n"
                "2. If there's a coding problem, provide a complete solution\n"
                "3. If there's an interview question, provide a clear, well-structured answer\n"
                "4. If there's a document, summarize the key points\n"
                "5. Be concise and actionable"
            )

        parts.append("")
        parts.append(
            "IMPORTANT: Give direct answers. No preamble like 'Based on the screen...' - "
            "just provide the answer as if you naturally know it."
        )

        return "\n".join(parts)

    def get_transcription_context(self) -> str:
        """Get all recent transcriptions as a single string."""
        return " ".join(list(self._transcriptions))

    def clear(self):
        """Clear all context."""
        self._screenshots.clear()
        self._transcriptions.clear()
        self._qa_history.clear()
