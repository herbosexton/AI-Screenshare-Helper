import random
import re


class HumanizationEngine:
    """
    Post-processes AI output to avoid detection by AI content detectors.
    Applies sentence variation, filler words, imperfections, and style matching.
    """

    FILLERS = [
        "basically", "honestly", "I think", "like", "you know",
        "so yeah", "right", "I mean", "pretty much", "sort of",
        "kind of", "actually", "in my experience", "from what I know",
        "if I remember correctly", "I believe", "as far as I know",
    ]

    CONTRACTIONS = {
        "I am": "I'm",
        "I have": "I've",
        "I will": "I'll",
        "I would": "I'd",
        "it is": "it's",
        "it has": "it's",
        "that is": "that's",
        "there is": "there's",
        "they are": "they're",
        "we are": "we're",
        "do not": "don't",
        "does not": "doesn't",
        "did not": "didn't",
        "is not": "isn't",
        "are not": "aren't",
        "was not": "wasn't",
        "cannot": "can't",
        "could not": "couldn't",
        "would not": "wouldn't",
        "should not": "shouldn't",
        "will not": "won't",
        "have not": "haven't",
        "has not": "hasn't",
        "had not": "hadn't",
    }

    SENTENCE_STARTERS_CASUAL = [
        "So ", "Well, ", "Yeah so ", "Basically, ", "Right, so ",
        "Ok so ", "Hmm, ", "Let me think... ", "",
    ]

    CODE_VARIABLE_STYLES = [
        "camelCase", "snake_case", "short", "descriptive",
    ]

    def __init__(self, config: dict):
        self.style = config.get("style", "casual_technical")
        self.imperfection_level = config.get("imperfection_level", 0.3)
        self.code_style = config.get("code_style", "slightly_messy")
        self.enabled = config.get("enabled", True)

    def process(self, text: str) -> str:
        """Apply full humanization pipeline to text."""
        if not self.enabled:
            return text

        if self._is_code_response(text):
            return self._humanize_code_response(text)
        else:
            return self._humanize_text_response(text)

    def _humanize_text_response(self, text: str) -> str:
        """Humanize a natural language response."""
        text = self._apply_contractions(text)
        text = self._vary_sentence_structure(text)
        text = self._insert_fillers(text)
        text = self._add_imperfections(text)
        text = self._vary_punctuation(text)
        text = self._remove_ai_patterns(text)
        return text.strip()

    def _humanize_code_response(self, text: str) -> str:
        """Humanize a response containing code."""
        parts = self._split_code_blocks(text)
        result = []

        for part_type, content in parts:
            if part_type == "text":
                result.append(self._humanize_text_response(content))
            elif part_type == "code":
                result.append(self._humanize_code(content))

        return "\n".join(result)

    def _apply_contractions(self, text: str) -> str:
        """Convert formal language to contractions."""
        for formal, contracted in self.CONTRACTIONS.items():
            if random.random() < 0.8:
                text = re.sub(
                    r'\b' + re.escape(formal) + r'\b',
                    contracted,
                    text,
                    flags=re.IGNORECASE,
                )
        return text

    def _vary_sentence_structure(self, text: str) -> str:
        """Break up uniform sentence patterns."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        if len(sentences) <= 2:
            return text

        result = []
        for i, sentence in enumerate(sentences):
            if random.random() < 0.15 and i > 0:
                long_sentence = sentences[i - 1] if result else ""
                if len(long_sentence) > 80:
                    pass

            if random.random() < 0.1 and len(sentence) > 60:
                mid = len(sentence) // 2
                split_point = sentence.find(", ", mid - 20, mid + 20)
                if split_point > 0:
                    result.append(sentence[:split_point + 1])
                    result.append(sentence[split_point + 2:])
                    continue

            result.append(sentence)

        return " ".join(result)

    def _insert_fillers(self, text: str) -> str:
        """Insert natural filler words/phrases."""
        if self.style == "formal":
            return text

        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = []

        for i, sentence in enumerate(sentences):
            if random.random() < self.imperfection_level * 0.5 and i > 0:
                filler = random.choice(self.FILLERS)
                if random.random() < 0.5:
                    sentence = f"{filler}, {sentence[0].lower()}{sentence[1:]}"
                else:
                    words = sentence.split()
                    if len(words) > 4:
                        pos = random.randint(2, min(5, len(words) - 1))
                        words.insert(pos, f"{filler},")
                        sentence = " ".join(words)

            result.append(sentence)

        return " ".join(result)

    def _add_imperfections(self, text: str) -> str:
        """Add subtle human-like imperfections."""
        if random.random() > self.imperfection_level:
            return text

        imperfections = [
            self._double_space,
            self._missing_comma,
            self._informal_dash,
            self._trailing_thought,
        ]

        chosen = random.choice(imperfections)
        return chosen(text)

    def _double_space(self, text: str) -> str:
        """Occasionally add a double space (common human typo)."""
        words = text.split(" ")
        if len(words) > 10:
            pos = random.randint(3, len(words) - 3)
            words[pos] = words[pos] + " "
        return " ".join(words)

    def _missing_comma(self, text: str) -> str:
        """Remove a comma (common in fast typing)."""
        commas = [m.start() for m in re.finditer(r',', text)]
        if commas:
            pos = random.choice(commas)
            text = text[:pos] + text[pos + 1:]
        return text

    def _informal_dash(self, text: str) -> str:
        """Replace a period with a dash for informal flow."""
        sentences = text.split(". ")
        if len(sentences) > 3:
            pos = random.randint(1, len(sentences) - 2)
            sentences[pos] = sentences[pos].rstrip(".") + " -"
            if pos + 1 < len(sentences):
                sentences[pos + 1] = sentences[pos + 1][0].lower() + sentences[pos + 1][1:] if sentences[pos + 1] else ""
        return ". ".join(sentences)

    def _trailing_thought(self, text: str) -> str:
        """Add a trailing thought at the end."""
        trails = [
            " - hope that makes sense",
            " if that helps",
            " - let me know if you need more detail",
            "",
            " basically",
        ]
        if not text.endswith((".", "!", "?")):
            return text
        return text.rstrip(".!?") + random.choice(trails)

    def _vary_punctuation(self, text: str) -> str:
        """Occasionally vary punctuation style."""
        if random.random() < 0.2:
            text = text.replace("...", "…")

        if random.random() < self.imperfection_level * 0.3:
            sentences = text.split(". ")
            if sentences:
                last = sentences[-1]
                if last.endswith("."):
                    sentences[-1] = last[:-1]
                text = ". ".join(sentences)

        return text

    def _remove_ai_patterns(self, text: str) -> str:
        """Remove common AI writing patterns that detectors look for."""
        ai_phrases = [
            r"(?i)^(certainly|absolutely|of course)[,!.]?\s*",
            r"(?i)^(I'd be happy to|I'd love to|let me)\s+",
            r"(?i)\b(delve|tapestry|myriad|plethora|paramount|utilize)\b",
            r"(?i)\b(furthermore|moreover|additionally|consequently)\b",
            r"(?i)^(here'?s?|below is)\s+(a|an|the)\s+",
            r"(?i)\b(it'?s worth noting that|it'?s important to note)\b",
        ]

        for pattern in ai_phrases:
            if random.random() < 0.7:
                text = re.sub(pattern, "", text, count=1)

        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'^\s+', '', text)

        return text

    def _humanize_code(self, code: str) -> str:
        """Make code look less AI-generated."""
        if self.code_style == "clean":
            return f"```\n{code}\n```"

        lines = code.split("\n")
        result = []

        for line in lines:
            if random.random() < 0.1 and line.strip().startswith("#"):
                comments = [
                    "# this works",
                    "# handles the edge case",
                    "# lol",
                    "# fix later maybe",
                    "# good enough",
                    "# tested this",
                ]
                result.append(random.choice(comments))
                continue

            if random.random() < 0.05 and self.code_style == "slightly_messy":
                if line.strip() == "":
                    continue

            result.append(line)

        if random.random() < 0.3:
            result.append("")

        return f"```\n{chr(10).join(result)}\n```"

    @staticmethod
    def _is_code_response(text: str) -> bool:
        """Detect if the response contains code blocks."""
        return "```" in text or text.count("    ") > 3

    @staticmethod
    def _split_code_blocks(text: str) -> list[tuple[str, str]]:
        """Split text into (type, content) tuples of 'text' and 'code' parts."""
        parts = []
        code_pattern = re.compile(r'```[\w]*\n?(.*?)```', re.DOTALL)

        last_end = 0
        for match in code_pattern.finditer(text):
            if match.start() > last_end:
                parts.append(("text", text[last_end:match.start()]))
            parts.append(("code", match.group(1)))
            last_end = match.end()

        if last_end < len(text):
            parts.append(("text", text[last_end:]))

        return parts if parts else [("text", text)]
