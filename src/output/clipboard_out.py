import pyperclip


class ClipboardOutput:
    """Silently copies answers to the system clipboard."""

    def __init__(self):
        self._last_copied = ""

    def copy(self, text: str):
        """Copy text to clipboard without any notifications."""
        try:
            pyperclip.copy(text)
            self._last_copied = text
        except Exception as e:
            print(f"[Clipboard] Failed to copy: {e}")

    def get_last(self) -> str:
        """Return the last text that was copied."""
        return self._last_copied

    def append(self, text: str):
        """Append text to whatever is currently in the clipboard."""
        try:
            current = pyperclip.paste()
            new_text = f"{current}\n\n---\n\n{text}"
            pyperclip.copy(new_text)
            self._last_copied = new_text
        except Exception as e:
            print(f"[Clipboard] Failed to append: {e}")
